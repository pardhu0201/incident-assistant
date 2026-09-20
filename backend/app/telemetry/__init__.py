"""OpenTelemetry instrumentation.

Real spans, real `tracer.start_as_current_span()` calls around every agent
stage - not a logging shim dressed up as tracing. Two span processors are
always attached:

1. A **database-backed processor** (`DbSpanExporter`) that writes every
   finished span to the `otel_spans` table, which is what the dashboard's
   trace view reads. This is what lets the demo show real distributed-tracing
   data with zero external services - a genuine, inspectable trace, not a
   simulation of one.
2. An **OTLP exporter**, attached only when `OTEL_EXPORTER_OTLP_ENDPOINT` is
   set, so the same instrumentation can ship spans to a real collector
   (Honeycomb, Jaeger, Grafana Tempo, ...) in a real deployment without any
   code change - only an environment variable.
"""

from __future__ import annotations

from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import OtelSpan
from app.logging_config import get_logger

log = get_logger(__name__)

_initialised = False


class DbSpanExporter(SpanExporter):
    """Persists finished spans to `otel_spans` so the dashboard can read them
    without depending on an external tracing backend being configured."""

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        db = SessionLocal()
        try:
            for span in spans:
                ctx = span.get_span_context()
                parent = span.parent
                duration_ns = (span.end_time or 0) - (span.start_time or 0)
                db.add(
                    OtelSpan(
                        trace_id=format(ctx.trace_id, "032x"),
                        span_id=format(ctx.span_id, "016x"),
                        parent_span_id=format(parent.span_id, "016x") if parent else "",
                        name=span.name,
                        status="error" if span.status.is_ok is False else "ok",
                        start_time=_ns_to_dt(span.start_time),
                        duration_ms=round(duration_ns / 1_000_000, 3),
                        attributes=dict(span.attributes or {}),
                    )
                )
            db.commit()
        except Exception as exc:  # pragma: no cover - persistence must never break tracing
            log.warning("Could not persist spans (%s)", exc)
            db.rollback()
        finally:
            db.close()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - trivial
        pass


def _ns_to_dt(ns: int):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)


def configure_telemetry() -> None:
    global _initialised
    if _initialised:
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: settings.otel_service_name}))
    provider.add_span_processor(SimpleSpanProcessor(DbSpanExporter()))

    if settings.otel_exporter_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
            )
            log.info("OTLP export enabled -> %s", settings.otel_exporter_otlp_endpoint)
        except Exception as exc:  # pragma: no cover - optional dependency/network
            log.warning("Could not configure OTLP exporter (%s)", exc)

    trace.set_tracer_provider(provider)
    _initialised = True
    log.info("OpenTelemetry configured (service=%s)", settings.otel_service_name)


def get_tracer(name: str = "incident-assistant"):
    return trace.get_tracer(name)


@contextmanager
def span(name: str, **attributes):
    """Convenience wrapper: `with span("agent.diagnosis", incident_id=x): ...`"""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            current.set_attribute(
                key, value if isinstance(value, (str, int, float, bool)) else str(value)
            )
        yield current


def current_trace_id() -> str:
    span_ctx = trace.get_current_span().get_span_context()
    if span_ctx is None or span_ctx.trace_id == 0:
        return ""
    return format(span_ctx.trace_id, "032x")

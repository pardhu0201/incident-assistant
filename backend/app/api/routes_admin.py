"""Health, service state, the agent graph topology, and the observability
dashboard - latency, test-gate pass rate, token usage and captured OTel spans."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.agents.graph import graph_topology
from app.config import settings
from app.db.base import engine, get_session
from app.db.models import AgentRun, Approval, Chunk, Document, Incident, OtelSpan, ServiceState
from app.llm.client import get_llm
from app.rag.embeddings import get_embedder
from app.schemas import DashboardResponse, HealthResponse, ServiceStateOut
from app.tools.registry import tool_catalogue

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_session)):
    documents = db.execute(select(func.count(Document.id))).scalar() or 0
    chunks = db.execute(select(func.count(Chunk.id))).scalar() or 0
    open_incidents = (
        db.execute(select(func.count(Incident.id)).where(Incident.status != "resolved")).scalar()
        or 0
    )
    return HealthResponse(
        status="ok",
        version=__version__,
        llm_mode=get_llm().mode,
        llm_model=settings.anthropic_model if get_llm().available else "deterministic-fallback",
        database=engine.url.get_backend_name(),
        embedding_provider=get_embedder().name,
        documents=documents,
        chunks=chunks,
        open_incidents=open_incidents,
        otel_service_name=settings.otel_service_name,
    )


@router.get("/graph")
def graph():
    return graph_topology()


@router.get("/tools")
def tools():
    return tool_catalogue()


@router.get("/services", response_model=list[ServiceStateOut])
def services(db: Session = Depends(get_session)):
    rows = db.execute(select(ServiceState).order_by(ServiceState.name)).scalars()
    return [
        ServiceStateOut(
            name=s.name,
            version=s.version,
            previous_version=s.previous_version,
            replica_count=s.replica_count,
            status=s.status,
        )
        for s in rows
    ]


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(limit: int = 200, db: Session = Depends(get_session)):
    incidents = list(db.execute(select(Incident)).scalars())
    runs = list(
        db.execute(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)).scalars()
    )
    approvals = list(db.execute(select(Approval)).scalars())

    confidences = [r.confidence for r in runs if r.confidence]
    latencies = [r.latency_ms for r in runs if r.latency_ms]
    gated_runs = [r for r in runs if r.test_gate is not None]
    test_pass_rate = (
        sum(1 for r in gated_runs if r.test_gate.get("passed")) / len(gated_runs)
        if gated_runs
        else 0.0
    )

    flag_counter: Counter = Counter()
    for r in runs:
        flag_counter.update(r.flags or [])

    total_tokens = sum(
        (r.token_usage or {}).get("input_tokens", 0) + (r.token_usage or {}).get("output_tokens", 0)
        for r in runs
    )

    span_rows = list(
        db.execute(select(OtelSpan).order_by(OtelSpan.start_time.desc()).limit(50)).scalars()
    )
    span_summary: Counter = Counter()
    span_totals: dict[str, float] = {}
    for s in span_rows:
        span_summary[s.name] += 1
        span_totals[s.name] = span_totals.get(s.name, 0.0) + s.duration_ms

    return DashboardResponse(
        incidents_total=len(incidents),
        incidents_by_status=dict(Counter(i.status for i in incidents)),
        runs_total=len(runs),
        approvals_by_status=dict(Counter(a.status for a in approvals)),
        average_confidence=round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
        average_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        test_gate_pass_rate=round(test_pass_rate, 3),
        fixes_executed=sum(1 for a in approvals if a.status == "approved"),
        total_estimated_tokens=total_tokens,
        flag_counts=dict(flag_counter),
        recent_runs=[
            {
                "id": r.id,
                "incident_id": r.incident_id,
                "status": r.status,
                "confidence": r.confidence,
                "latency_ms": r.latency_ms,
                "llm_mode": r.llm_mode,
                "created_at": r.created_at,
            }
            for r in runs[:15]
        ],
        span_summary=[
            {
                "name": name,
                "count": count,
                "avg_duration_ms": round(span_totals[name] / count, 2),
            }
            for name, count in span_summary.most_common(10)
        ],
    )

"""Relational schema: log events, incidents, agent runs/traces, approvals,
the runbook knowledge base, simulated service state, and captured OTel spans.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Return `value` as an aware UTC datetime.

    SQLite stores no timezone: a `DateTime(timezone=True)` column written as
    aware comes back *naive* once re-read in a new session, while Postgres
    returns it aware. Arithmetic against `utcnow()` then raises
    `TypeError: can't subtract offset-naive and offset-aware datetimes`. Every
    timestamp in this app is written in UTC, so a naive value is UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def minutes_since(value: datetime) -> float:
    """Whole elapsed time in minutes (not `timedelta.seconds`, which drops days)."""
    return (utcnow() - as_utc(value)).total_seconds() / 60


# ---------------------------------------------------------------------------
# Runbook / postmortem knowledge base
# ---------------------------------------------------------------------------
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(30), default="runbook")
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    heading: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(JSON, nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (Index("ix_chunks_doc_ordinal", "document_id", "ordinal"),)


# ---------------------------------------------------------------------------
# Logs and incidents
# ---------------------------------------------------------------------------
class LogEvent(Base):
    __tablename__ = "log_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    service: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO", index=True)
    message: Mapped[str] = mapped_column(Text)
    stack_trace: Mapped[str] = mapped_column(Text, default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Always naive UTC - see app.ingestion.pipeline._parse_timestamp.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300))
    service: Mapped[str] = mapped_column(String(80), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="ERROR")
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    sample_message: Mapped[str] = mapped_column(Text, default="")
    sample_stack_trace: Mapped[str] = mapped_column(Text, default="")
    # Always naive UTC, derived from LogEvent.occurred_at - see above.
    first_seen: Mapped[datetime] = mapped_column(DateTime())
    last_seen: Mapped[datetime] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_incidents_fingerprint_service", "fingerprint", "service"),)


# ---------------------------------------------------------------------------
# Agent runs and traces
# ---------------------------------------------------------------------------
class AgentRun(Base):
    """One pass of the diagnosis/report/fix graph over a single incident."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    incident_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    report: Mapped[str] = mapped_column(Text, default="")
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    used_citations: Mapped[list] = mapped_column(JSON, default=list)
    proposed_fix: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    test_gate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_mode: Mapped[str] = mapped_column(String(20), default="demo")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRunEvent(Base):
    """Per-agent trace entry - what the UI timeline renders."""

    __tablename__ = "agent_run_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    agent: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="ok")
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Human-in-the-loop
# ---------------------------------------------------------------------------
class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    incident_id: Mapped[str] = mapped_column(String(32), default="")
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[str] = mapped_column(String(20), default="medium")
    rationale: Mapped[str] = mapped_column(Text, default="")
    flags: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    test_gate_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(String(40), default="incident-assistant")
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    execution_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Simulated environment the fix tools act on
# ---------------------------------------------------------------------------
class ServiceState(Base):
    __tablename__ = "service_state"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    previous_version: Mapped[str] = mapped_column(String(40), default="")
    replica_count: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    last_restarted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_deployed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# Captured OpenTelemetry spans (in-process, no external collector required)
# ---------------------------------------------------------------------------
class OtelSpan(Base):
    __tablename__ = "otel_spans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    span_id: Mapped[str] = mapped_column(String(16))
    parent_span_id: Mapped[str] = mapped_column(String(16), default="")
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), default="ok")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_otel_spans_trace", "trace_id"),)

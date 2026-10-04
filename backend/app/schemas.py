"""Request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- logs --------------------------------------------------------------
class LogRecord(BaseModel):
    timestamp: str | None = None
    service: str
    level: str = "INFO"
    message: str
    stack_trace: str = ""
    request_id: str = ""
    status_code: int | None = None
    latency_ms: int | None = None


class IngestLogsRequest(BaseModel):
    records: list[LogRecord]


class IngestSummaryOut(BaseModel):
    events_ingested: int
    incidents_opened: int
    incidents_updated: int
    incident_ids: list[str]


class LogEventOut(BaseModel):
    id: str
    service: str
    level: str
    message: str
    stack_trace: str
    request_id: str
    status_code: int | None
    latency_ms: int | None
    incident_id: str | None
    occurred_at: datetime


# --- incidents -----------------------------------------------------------
class IncidentOut(BaseModel):
    id: str
    title: str
    service: str
    severity: str
    status: str
    event_count: int
    sample_message: str
    first_seen: datetime
    last_seen: datetime


class Citation(BaseModel):
    index: int
    chunk_id: str
    document_id: str
    document_title: str
    heading: str
    snippet: str
    score: float
    dense_score: float
    lexical_score: float


class RunOut(BaseModel):
    run_id: str
    incident_id: str
    status: str
    diagnosis: str
    report: str
    checklist: list[str]
    citations: list[Citation] = Field(default_factory=list)
    used_citations: list[int] = Field(default_factory=list)
    proposed_fix: dict[str, Any] | None = None
    test_gate: dict[str, Any] | None = None
    verification: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    confidence: float
    requires_approval: bool
    approval_id: str | None = None
    llm_mode: str
    latency_ms: int
    token_usage: dict[str, Any] | None = None
    trace_id: str = ""


class TraceEntry(BaseModel):
    seq: int
    agent: str
    label: str
    status: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0


class IncidentDetail(IncidentOut):
    runs: list[RunOut] = Field(default_factory=list)


# --- approvals -------------------------------------------------------------
class ApprovalOut(BaseModel):
    id: str
    run_id: str
    incident_id: str
    tool_name: str
    arguments: dict[str, Any]
    risk: str
    rationale: str
    flags: list[str] = Field(default_factory=list)
    confidence: float
    test_gate_passed: bool
    status: str
    decided_by: str | None = None
    decision_note: str = ""
    execution_result: dict[str, Any] | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    decided_by: str = Field(default="oncall@example.com", max_length=160)
    note: str = Field(default="", max_length=1000)


# --- admin -----------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    llm_mode: str
    llm_model: str
    database: str
    embedding_provider: str
    documents: int
    chunks: int
    open_incidents: int
    otel_service_name: str


class ServiceStateOut(BaseModel):
    name: str
    version: str
    previous_version: str
    replica_count: int
    status: str


class DashboardResponse(BaseModel):
    incidents_total: int
    incidents_by_status: dict[str, int]
    runs_total: int
    approvals_by_status: dict[str, int]
    average_confidence: float
    average_latency_ms: float
    test_gate_pass_rate: float
    fixes_executed: int
    total_estimated_tokens: int
    flag_counts: dict[str, int]
    recent_runs: list[dict[str, Any]] = Field(default_factory=list)
    span_summary: list[dict[str, Any]] = Field(default_factory=list)

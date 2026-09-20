"""Shared state passed between agents in the LangGraph workflow.

`trace` uses an additive reducer so every node can append its own timeline
entry without clobbering earlier ones - that list is exactly what the UI
renders and what gets persisted to `agent_run_events`.
"""

from __future__ import annotations

import operator
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from sqlalchemy.orm import Session

from app.llm.client import LLMClient, UsageTracker


class AgentState(TypedDict, total=False):
    # --- inputs ---
    run_id: str
    incident_id: str
    incident_title: str
    incident_service: str
    incident_severity: str
    incident_event_count: int
    sample_message: str
    sample_stack_trace: str

    # --- retrieval ---
    retrieved: list[dict[str, Any]]
    context_block: str

    # --- diagnosis ---
    diagnosis: str
    used_citations: list[int]
    insufficient_evidence: bool

    # --- report ---
    report: str
    checklist: list[str]

    # --- fix ---
    candidate_tool: str
    proposed_fix: dict[str, Any] | None

    # --- test gate ---
    test_gate: dict[str, Any] | None

    # --- verification ---
    verification: dict[str, Any]
    confidence: float
    flags: list[str]
    requires_approval: bool
    approval_id: str
    status: str

    # --- bookkeeping ---
    trace: Annotated[list[dict[str, Any]], operator.add]


@dataclass
class RunContext:
    db: Session
    llm: LLMClient
    usage: UsageTracker = field(default_factory=UsageTracker)
    _seq: int = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq


AGENT_LABELS = {
    "retrieval": "Retrieval agent",
    "diagnosis": "Diagnosis agent",
    "report": "Report agent",
    "fix": "Fix agent",
    "test_gate": "Automated test gate",
    "verification": "Verification agent",
    "approval_gate": "Human approval gate",
}


def trace_event(
    ctx: RunContext,
    agent: str,
    summary: str,
    *,
    status: str = "ok",
    payload: dict | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    return {
        "seq": ctx.next_seq(),
        "agent": agent,
        "label": AGENT_LABELS.get(agent, agent.title()),
        "status": status,
        "summary": summary,
        "payload": payload or {},
        "duration_ms": int((time.perf_counter() - started) * 1000) if started else 0,
    }

"""Incident analysis orchestration: run the graph, persist the trace."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.agents.graph import COMPILED_GRAPH
from app.agents.state import RunContext
from app.db.models import AgentRun, AgentRunEvent, Incident
from app.llm.client import get_llm
from app.logging_config import get_logger
from app.telemetry import current_trace_id, span

log = get_logger(__name__)


def analyse_incident(db: Session, incident_id: str) -> dict:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"Incident {incident_id} not found")

    started = time.perf_counter()
    llm = get_llm()

    run = AgentRun(incident_id=incident.id, status="running", llm_mode=llm.mode)
    db.add(run)
    previous_status = incident.status
    incident.status = "investigating"
    db.commit()
    db.refresh(run)

    ctx = RunContext(db=db, llm=llm)
    config = {"configurable": {"ctx": ctx}, "recursion_limit": 20}
    initial = {
        "run_id": run.id,
        "incident_id": incident.id,
        "incident_title": incident.title,
        "incident_service": incident.service,
        "incident_severity": incident.severity,
        "incident_event_count": incident.event_count,
        "sample_message": incident.sample_message,
        "sample_stack_trace": incident.sample_stack_trace,
        "trace": [],
    }

    with span("incident.analyse", incident_id=incident.id, service=incident.service) as root_span:
        trace_id = current_trace_id()
        state: dict = dict(initial)
        try:
            for update in COMPILED_GRAPH.stream(initial, config=config, stream_mode="updates"):
                for _node_name, node_update in update.items():
                    if not isinstance(node_update, dict):
                        continue
                    events = node_update.get("trace") or []
                    for event in events:
                        db.add(
                            AgentRunEvent(
                                run_id=run.id,
                                seq=event["seq"],
                                agent=event["agent"],
                                status=event["status"],
                                summary=event["summary"],
                                payload=event["payload"],
                                duration_ms=event["duration_ms"],
                            )
                        )
                    db.commit()
                    state.update({k: v for k, v in node_update.items() if k != "trace"})
                    state["trace"] = state.get("trace", []) + events
        except Exception:  # pragma: no cover - defensive
            log.exception("Incident analysis failed")
            root_span.set_attribute("error", True)
            db.rollback()
            run.status = "failed"
            # Don't strand the incident in "investigating" - nothing is.
            incident.status = previous_status
            db.commit()
            raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    run.status = state.get("status", "diagnosed")
    run.diagnosis = state.get("diagnosis", "")
    run.report = state.get("report", "")
    run.checklist = state.get("checklist", [])
    run.citations = [
        {k: v for k, v in item.items() if k != "content"} for item in (state.get("retrieved") or [])
    ]
    run.used_citations = state.get("used_citations", [])
    run.proposed_fix = state.get("proposed_fix")
    run.test_gate = state.get("test_gate")
    run.verification = state.get("verification")
    run.flags = state.get("flags", [])
    run.confidence = float(state.get("confidence", 0.0))
    run.requires_approval = bool(state.get("requires_approval"))
    run.approval_id = state.get("approval_id")
    run.latency_ms = latency_ms
    run.token_usage = ctx.usage.as_dict()
    run.trace_id = trace_id

    incident.status = run.status
    db.commit()
    db.refresh(run)

    return _run_payload(run)


def _run_payload(run: AgentRun) -> dict:
    return {
        "run_id": run.id,
        "incident_id": run.incident_id,
        "status": run.status,
        "diagnosis": run.diagnosis,
        "report": run.report,
        "checklist": run.checklist,
        "citations": run.citations,
        "used_citations": run.used_citations,
        "proposed_fix": run.proposed_fix,
        "test_gate": run.test_gate,
        "verification": run.verification,
        "flags": run.flags or [],
        "confidence": run.confidence,
        "requires_approval": run.requires_approval,
        "approval_id": run.approval_id,
        "llm_mode": run.llm_mode,
        "latency_ms": run.latency_ms,
        "token_usage": run.token_usage,
        "trace_id": run.trace_id,
    }

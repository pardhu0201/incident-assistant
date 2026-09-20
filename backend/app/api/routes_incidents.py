"""Incident listing, detail, and triggering the diagnosis/fix pipeline."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runner import analyse_incident
from app.db.base import get_session
from app.db.models import AgentRun, AgentRunEvent, Incident
from app.schemas import IncidentDetail, IncidentOut, RunOut, TraceEntry

router = APIRouter(tags=["incidents"])


def _to_out(incident: Incident) -> IncidentOut:
    return IncidentOut(
        id=incident.id,
        title=incident.title,
        service=incident.service,
        severity=incident.severity,
        status=incident.status,
        event_count=incident.event_count,
        sample_message=incident.sample_message,
        first_seen=incident.first_seen,
        last_seen=incident.last_seen,
    )


@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(status: str | None = None, limit: int = 50, db: Session = Depends(get_session)):
    stmt = select(Incident).order_by(Incident.last_seen.desc()).limit(limit)
    if status and status != "all":
        stmt = stmt.where(Incident.status == status)
    return [_to_out(i) for i in db.execute(stmt).scalars()]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: str, db: Session = Depends(get_session)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    runs = list(
        db.execute(
            select(AgentRun)
            .where(AgentRun.incident_id == incident_id)
            .order_by(AgentRun.created_at)
        ).scalars()
    )
    run_outs = []
    for run in runs:
        run_outs.append(
            RunOut(
                run_id=run.id,
                incident_id=run.incident_id,
                status=run.status,
                diagnosis=run.diagnosis,
                report=run.report,
                checklist=run.checklist or [],
                citations=run.citations or [],
                used_citations=run.used_citations or [],
                proposed_fix=run.proposed_fix,
                test_gate=run.test_gate,
                verification=run.verification or {},
                flags=run.flags or [],
                confidence=run.confidence,
                requires_approval=run.requires_approval,
                approval_id=run.approval_id,
                llm_mode=run.llm_mode,
                latency_ms=run.latency_ms,
                token_usage=run.token_usage,
                trace_id=run.trace_id,
            )
        )

    detail = _to_out(incident).model_dump()
    detail["runs"] = [r.model_dump() for r in run_outs]
    return IncidentDetail(**detail)


@router.post("/incidents/{incident_id}/analyze", response_model=RunOut)
def analyze(incident_id: str, db: Session = Depends(get_session)):
    try:
        result = analyse_incident(db, incident_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RunOut(**result)


@router.get("/runs/{run_id}/trace", response_model=list[TraceEntry])
def run_trace(run_id: str, db: Session = Depends(get_session)):
    events = db.execute(
        select(AgentRunEvent).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.seq)
    ).scalars()
    return [
        TraceEntry(
            seq=e.seq,
            agent=e.agent,
            label=e.agent.replace("_", " ").title(),
            status=e.status,
            summary=e.summary,
            payload=e.payload or {},
            duration_ms=e.duration_ms,
        )
        for e in events
    ]

"""Log ingestion endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.models import LogEvent
from app.ingestion.pipeline import ingest_records, replay_sample_dataset
from app.schemas import IngestLogsRequest, IngestSummaryOut, LogEventOut

router = APIRouter(tags=["logs"])


@router.post("/logs/ingest", response_model=IngestSummaryOut)
def ingest(payload: IngestLogsRequest, db: Session = Depends(get_session)):
    records = [r.model_dump() for r in payload.records]
    summary = ingest_records(db, records)
    return IngestSummaryOut(
        events_ingested=summary.events_ingested,
        incidents_opened=summary.incidents_opened,
        incidents_updated=summary.incidents_updated,
        incident_ids=summary.incident_ids,
    )


@router.post("/logs/replay-sample", response_model=IngestSummaryOut)
def replay_sample(db: Session = Depends(get_session)):
    try:
        summary = replay_sample_dataset(db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return IngestSummaryOut(
        events_ingested=summary.events_ingested,
        incidents_opened=summary.incidents_opened,
        incidents_updated=summary.incidents_updated,
        incident_ids=summary.incident_ids,
    )


@router.get("/logs", response_model=list[LogEventOut])
def list_logs(
    service: str | None = None,
    level: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_session),
):
    stmt = select(LogEvent).order_by(LogEvent.occurred_at.desc()).limit(limit)
    if service:
        stmt = stmt.where(LogEvent.service == service)
    if level:
        stmt = stmt.where(LogEvent.level == level.upper())
    rows = db.execute(stmt).scalars()
    return [
        LogEventOut(
            id=r.id,
            service=r.service,
            level=r.level,
            message=r.message,
            stack_trace=r.stack_trace,
            request_id=r.request_id,
            status_code=r.status_code,
            latency_ms=r.latency_ms,
            incident_id=r.incident_id,
            occurred_at=r.occurred_at,
        )
        for r in rows
    ]

"""Fold error log events into incidents.

An incident is the unit the rest of the system reasons about - one row per
distinct problem, however many log lines it produced. A new error event joins
an existing incident when it shares that incident's fingerprint and service
and falls within `INCIDENT_WINDOW_SECONDS` of its last event; otherwise it
opens a new incident. If the matched incident was already resolved by a fix
and the event is newer than anything seen before, the incident is
**reopened** - a recurrence after a fix must be visible to on-call. This is deliberately simple (no ML
clustering) so it is fully explainable in an incident review - "why did these
47 log lines become one incident" always has a one-sentence answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AuditLog, Incident, LogEvent
from app.ingestion.fingerprint import compute_fingerprint

ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}
# Severity only ever escalates: a CRITICAL line after a FATAL one must not
# downgrade the incident.
SEVERITY_RANK = {"ERROR": 1, "CRITICAL": 2, "FATAL": 3}
# A fix was applied; a *newer* occurrence of the same error means it did not
# hold, so the incident is reopened rather than silently absorbing events.
FIXED_STATUSES = {"resolved"}
REOPENED_STATUS = "reopened"


def make_title(service: str, message: str) -> str:
    """A short, human-readable incident title from the first error's message."""
    cleaned = " ".join(message.split())
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
    return f"{service}: {cleaned}"


def find_open_incident(
    db: Session, *, service: str, fingerprint: str, occurred_at: datetime
) -> Incident | None:
    window_start = occurred_at - timedelta(seconds=settings.incident_window_seconds)
    window_end = occurred_at + timedelta(seconds=settings.incident_window_seconds)
    return (
        db.execute(
            select(Incident)
            .where(Incident.service == service)
            .where(Incident.fingerprint == fingerprint)
            .where(Incident.status != "closed")
            .where(Incident.last_seen >= window_start)
            .where(Incident.first_seen <= window_end)
            .order_by(Incident.last_seen.desc())
        )
        .scalars()
        .first()
    )


def assign_to_incident(db: Session, event: LogEvent) -> Incident | None:
    """Cluster one error-level event into an incident, creating one if needed.

    Returns the incident, or `None` if `event` is below error severity and
    therefore not incident material (still stored for the log explorer and
    for context around an incident, just not clustered on its own).
    """
    if event.level not in ERROR_LEVELS:
        return None

    fingerprint = compute_fingerprint(event.service, event.message, event.stack_trace)
    event.fingerprint = fingerprint

    incident = find_open_incident(
        db, service=event.service, fingerprint=fingerprint, occurred_at=event.occurred_at
    )
    if incident is None:
        incident = Incident(
            title=make_title(event.service, event.message),
            service=event.service,
            fingerprint=fingerprint,
            severity=event.level,
            status="open",
            event_count=0,
            sample_message=event.message,
            sample_stack_trace=event.stack_trace,
            first_seen=event.occurred_at,
            last_seen=event.occurred_at,
        )
        db.add(incident)
        db.flush()
    else:
        # Reopen only for an occurrence *after* everything seen so far: late,
        # out-of-order lines from before the fix must not reopen it.
        if incident.status in FIXED_STATUSES and event.occurred_at > incident.last_seen:
            previous = incident.status
            incident.status = REOPENED_STATUS
            db.add(
                AuditLog(
                    actor="clustering",
                    action="incident.reopened",
                    entity="incident",
                    entity_id=incident.id,
                    payload={
                        "previous_status": previous,
                        "event_id": event.id,
                        "occurred_at": event.occurred_at.isoformat(),
                    },
                )
            )
        if event.occurred_at < incident.first_seen:
            incident.first_seen = event.occurred_at
        if event.occurred_at > incident.last_seen:
            incident.last_seen = event.occurred_at
        if SEVERITY_RANK.get(event.level, 0) > SEVERITY_RANK.get(incident.severity, 0):
            incident.severity = event.level

    incident.event_count += 1
    event.incident_id = incident.id
    return incident

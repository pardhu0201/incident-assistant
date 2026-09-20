"""Log ingestion: parse -> store -> cluster into incidents.

Accepts either a batch of already-parsed records (the API's JSON path) or
raw JSONL text (the bundled sample dataset and file uploads), so the same
code path backs "a user pastes some logs," "replay the sample dataset," and
"a script POSTs a batch" - there is exactly one ingestion pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import SAMPLE_LOGS_DIR
from app.db.models import LogEvent
from app.ingestion.clustering import assign_to_incident
from app.logging_config import get_logger
from app.telemetry import span

log = get_logger(__name__)

VALID_LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"}


@dataclass
class IngestSummary:
    events_ingested: int
    incidents_opened: int
    incidents_updated: int
    incident_ids: list[str]


def _parse_timestamp(raw: str | None) -> datetime:
    """Always returns a naive UTC datetime.

    SQLite has no real timezone-aware storage: SQLAlchemy round-trips a
    `DateTime(timezone=True)` column as naive once it's read back from a
    query, even though a freshly-constructed, not-yet-queried row keeps
    whatever tzinfo it was given. Comparing the two (as incident clustering
    does) then raises `TypeError: can't compare offset-naive and
    offset-aware datetimes`. Normalising to naive UTC at the point of
    parsing - the only place a timezone actually enters the system - avoids
    the mismatch everywhere downstream.
    """
    if not raw:
        return datetime.now(UTC).replace(tzinfo=None)
    text = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC).replace(tzinfo=None)
    if dt.tzinfo:
        dt = dt.astimezone(UTC)
    return dt.replace(tzinfo=None)


def _normalise_level(raw: str | None) -> str:
    level = (raw or "INFO").strip().upper()
    if level == "WARNING":
        level = "WARN"
    return level if level in VALID_LEVELS else "INFO"


def parse_record(record: dict) -> LogEvent:
    return LogEvent(
        service=str(record.get("service", "unknown-service")),
        level=_normalise_level(record.get("level")),
        message=str(record.get("message", "")),
        stack_trace=str(record.get("stack_trace", "") or ""),
        request_id=str(record.get("request_id", "") or ""),
        status_code=record.get("status_code"),
        latency_ms=record.get("latency_ms"),
        occurred_at=_parse_timestamp(record.get("timestamp")),
    )


def ingest_records(db: Session, records: list[dict]) -> IngestSummary:
    with span("ingestion.batch", record_count=len(records)):
        touched: dict[str, str] = {}  # incident_id -> "opened" | "updated"
        for record in records:
            event = parse_record(record)
            db.add(event)
            db.flush()

            incident = assign_to_incident(db, event)
            if incident is not None and incident.id not in touched:
                # event_count only ever increments, so ==1 here means this event
                # was the first one ever assigned to this incident row - i.e. it
                # was just created, regardless of which ingestion batch we're in.
                touched[incident.id] = "opened" if incident.event_count == 1 else "updated"

        db.commit()

        opened = sum(1 for v in touched.values() if v == "opened")
        updated = sum(1 for v in touched.values() if v == "updated")
        summary = IngestSummary(
            events_ingested=len(records),
            incidents_opened=opened,
            incidents_updated=updated,
            incident_ids=list(touched.keys()),
        )
        log.info(
            "Ingested %d log events -> %d incidents opened, %d updated",
            summary.events_ingested,
            opened,
            updated,
        )
        return summary


def ingest_jsonl_text(db: Session, text: str) -> IngestSummary:
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("Skipping malformed JSONL line: %.80s", line)
    return ingest_records(db, records)


def load_sample_dataset(name: str = "checkout-api.jsonl") -> str:
    path = SAMPLE_LOGS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"No sample dataset named {name}")
    return path.read_text(encoding="utf-8")


def replay_sample_dataset(db: Session, name: str = "checkout-api.jsonl") -> IngestSummary:
    return ingest_jsonl_text(db, load_sample_dataset(name))

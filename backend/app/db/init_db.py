"""Schema creation and seed data.

Runs on every boot and is idempotent, so a free container comes up cold with
a working runbook corpus and simulated service fleet with no manual setup.
The sample log dataset is *not* auto-replayed on every boot (that would
duplicate incidents on every restart) - it's ingested once via
`/api/logs/replay-sample` or the frontend's "Load sample incidents" button.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.base import Base, engine, session_scope
from app.db.models import ServiceState
from app.logging_config import get_logger
from app.rag.ingest import ingest_seed_corpus

log = get_logger(__name__)

SEED_SERVICES = [
    {"name": "checkout-api", "version": "2.3.1", "previous_version": "2.3.0", "replica_count": 4},
    {
        "name": "payments-gateway",
        "version": "4.1.0",
        "previous_version": "4.0.5",
        "replica_count": 3,
    },
    {
        "name": "inventory-service",
        "version": "1.8.2",
        "previous_version": "1.8.2",
        "replica_count": 2,
    },
]


def create_schema() -> None:
    Base.metadata.create_all(bind=engine)
    log.info("Database schema ready (%s)", engine.url.render_as_string(hide_password=True))


def seed_services() -> int:
    created = 0
    with session_scope() as db:
        for record in SEED_SERVICES:
            existing = db.execute(
                select(ServiceState).where(ServiceState.name == record["name"])
            ).scalar_one_or_none()
            if existing is None:
                db.add(ServiceState(**record))
                created += 1
    if created:
        log.info("Seeded %d service states", created)
    return created


def seed_corpus() -> int:
    with session_scope() as db:
        results = ingest_seed_corpus(db)
    changed = sum(1 for r in results if r.status != "unchanged")
    log.info(
        "Runbook corpus ready: %d documents, %d chunks (%d changed)",
        len(results),
        sum(r.chunks for r in results),
        changed,
    )
    return len(results)


def initialise(seed: bool = True) -> None:
    create_schema()
    if seed:
        seed_services()
        seed_corpus()

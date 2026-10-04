"""Fingerprinting and incident clustering."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.ingestion.fingerprint import compute_fingerprint, normalise_message, top_frame
from app.ingestion.pipeline import ingest_records


def test_normalise_message_strips_numbers_before_unit_suffixes():
    # Regression: a bounded \b\d+\b regex silently fails to match a number
    # immediately followed by a unit letter ("30000ms") since digit->letter
    # is not a word boundary - this was a real bug that broke clustering.
    a = normalise_message("timeout waiting 30000ms for a connection")
    b = normalise_message("timeout waiting 28123ms for a connection")
    assert a == b
    assert "<n>" in a


def test_normalise_message_strips_uuids_and_quoted_values():
    text = normalise_message("request '550e8400-e29b-41d4-a716-446655440000' failed for 'user-42'")
    assert "550e8400" not in text
    assert "<uuid>" in text or "<val>" in text


def test_top_frame_is_first_nonempty_line():
    trace = "\n\nSomeError: boom\n  at foo.bar(baz.py:1)"
    assert top_frame(trace) == "someerror: boom"


def test_identical_error_shape_produces_same_fingerprint():
    f1 = compute_fingerprint(
        "checkout-api", "pool exhausted after 30234ms", "PoolTimeout: x\n  at a.b(c.py:1)"
    )
    f2 = compute_fingerprint(
        "checkout-api", "pool exhausted after 28991ms", "PoolTimeout: x\n  at a.b(c.py:1)"
    )
    assert f1 == f2


def test_different_services_never_share_a_fingerprint():
    f1 = compute_fingerprint("checkout-api", "pool exhausted after 30234ms", "PoolTimeout: x")
    f2 = compute_fingerprint("payments-gateway", "pool exhausted after 30234ms", "PoolTimeout: x")
    assert f1 != f2


def test_keyword_substring_does_not_leak_across_error_types():
    # "changed" contains "hang" as a literal substring - a naive `in` check
    # on the fix agent's keyword list mis-routed this to restart_service in
    # practice; fingerprinting must not conflate these either.
    f1 = compute_fingerprint("svc", "the config was changed unexpectedly")
    f2 = compute_fingerprint("svc", "the process appears to hang")
    assert f1 != f2


def test_ingest_records_clusters_matching_errors_into_one_incident(db):
    base = datetime(2026, 1, 1, 10, 0, 0)
    records = [
        {
            "timestamp": (base + timedelta(seconds=i * 30)).isoformat(),
            "service": "test-svc",
            "level": "ERROR",
            "message": f"pool exhausted after {30000 + i}ms",
            "stack_trace": "PoolTimeout: x\n  at a.b(c.py:1)",
        }
        for i in range(5)
    ]
    summary = ingest_records(db, records)
    assert summary.events_ingested == 5
    assert summary.incidents_opened == 1
    assert summary.incidents_updated == 0


def test_ingest_records_opens_separate_incidents_for_different_errors(db):
    base = datetime(2026, 1, 2, 10, 0, 0)
    records = [
        {
            "timestamp": base.isoformat(),
            "service": "test-svc-2",
            "level": "ERROR",
            "message": "pool exhausted",
        },
        {
            "timestamp": (base + timedelta(seconds=10)).isoformat(),
            "service": "test-svc-2",
            "level": "ERROR",
            "message": "upstream timeout calling payment processor",
        },
    ]
    summary = ingest_records(db, records)
    assert summary.incidents_opened == 2


def test_info_and_warn_events_are_not_clustered(db):
    base = datetime(2026, 1, 3, 10, 0, 0)
    records = [
        {
            "timestamp": base.isoformat(),
            "service": "test-svc-3",
            "level": "INFO",
            "message": "handled request",
        },
        {
            "timestamp": base.isoformat(),
            "service": "test-svc-3",
            "level": "WARN",
            "message": "slow response",
        },
    ]
    summary = ingest_records(db, records)
    assert summary.incidents_opened == 0
    assert summary.incidents_updated == 0


def test_a_recurrence_after_a_fix_reopens_the_incident(db):
    from app.db.models import AuditLog, Incident
    from app.ingestion.pipeline import ingest_records

    record = {"service": "reopen-svc", "level": "ERROR", "message": "Worker pool wedged"}
    first = ingest_records(db, [{**record, "timestamp": "2026-03-01T10:00:00Z"}])
    incident = db.get(Incident, first.incident_ids[0])
    incident.status = "resolved"  # a fix was applied
    db.commit()

    # A late line from *before* the fix must not reopen it...
    ingest_records(db, [{**record, "timestamp": "2026-03-01T09:59:00Z"}])
    db.refresh(incident)
    assert incident.status == "resolved"

    # ...but a newer occurrence means the fix did not hold.
    again = ingest_records(db, [{**record, "timestamp": "2026-03-01T10:05:00Z"}])
    assert again.incident_ids == [incident.id]
    db.refresh(incident)
    assert incident.status == "reopened"
    assert incident.event_count == 3
    audit = db.query(AuditLog).filter(AuditLog.entity_id == incident.id).all()
    assert [a.action for a in audit] == ["incident.reopened"]


def test_severity_only_escalates(db):
    from app.db.models import Incident
    from app.ingestion.pipeline import ingest_records

    base = {"service": "severity-svc", "message": "Kaboom", "timestamp": "2026-03-01T10:00:00Z"}
    summary = ingest_records(
        db, [{**base, "level": "ERROR"}, {**base, "level": "FATAL"}, {**base, "level": "CRITICAL"}]
    )
    assert db.get(Incident, summary.incident_ids[0]).severity == "FATAL"

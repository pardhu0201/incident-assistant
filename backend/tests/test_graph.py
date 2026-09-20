"""End-to-end behaviour of the diagnosis/report/fix LangGraph pipeline."""

from __future__ import annotations

from datetime import datetime

from app.agents.graph import graph_topology
from app.agents.runner import analyse_incident
from app.db.models import Incident, ServiceState


def _make_incident(db, *, service: str, message: str, stack_trace: str = "") -> Incident:
    now = datetime(2026, 1, 1, 12, 0, 0)
    incident = Incident(
        title=f"{service}: {message[:60]}",
        service=service,
        fingerprint="test-fp",
        severity="ERROR",
        status="open",
        event_count=5,
        sample_message=message,
        sample_stack_trace=stack_trace,
        first_seen=now,
        last_seen=now,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def test_pool_exhaustion_proposes_a_restart(db):
    if db.get(ServiceState, "graph-test-svc") is None:
        db.add(ServiceState(name="graph-test-svc"))
        db.commit()

    incident = _make_incident(
        db,
        service="graph-test-svc",
        message="Database connection pool exhausted after 29000ms waiting for a connection",
        stack_trace="psycopg.pool.PoolTimeout: connection pool exhausted, timeout waiting 30000ms",
    )
    result = analyse_incident(db, incident.id)

    assert result["status"] == "awaiting_approval"
    assert result["requires_approval"] is True
    assert result["citations"]
    assert result["proposed_fix"]["tool_name"] == "restart_service"
    assert result["test_gate"]["passed"] is True
    assert result["approval_id"]


def test_unroutable_error_is_never_silently_approved(db):
    # An error with no matching runbook may still spuriously trip the fix
    # agent's keyword heuristic (it runs before verification and can't yet
    # know the diagnosis is weak) - what actually matters is that the
    # verification agent's relevance floor catches the low-quality diagnosis
    # regardless, and the run is escalated to a human rather than queued for
    # approval as if it were solid.
    if db.get(ServiceState, "graph-test-svc-2") is None:
        db.add(ServiceState(name="graph-test-svc-2"))
        db.commit()

    incident = _make_incident(
        db,
        service="graph-test-svc-2",
        message="Printer on the third floor is out of toner and jammed again",
    )
    result = analyse_incident(db, incident.id)

    assert result["status"] != "awaiting_approval"
    assert result["requires_approval"] is False
    assert result["confidence"] < 0.5
    assert "error_not_covered_by_runbooks" in result["flags"]


def test_rollback_requires_a_known_target_version(db):
    # A service with no previous_version recorded cannot be safely rolled
    # back, even if the diagnosis text points at a recent deploy - the test
    # gate must catch this before a human ever sees an approval.
    db.add(ServiceState(name="graph-test-svc-3", version="1.0.0", previous_version=""))
    db.commit()

    incident = _make_incident(
        db,
        service="graph-test-svc-3",
        message="Upstream payment processor request timed out after 9000ms",
        stack_trace="httpx2.ReadTimeout: The read operation timed out\n  at PaymentProcessorClient.charge(client.py:1)",
    )
    result = analyse_incident(db, incident.id)

    if result["proposed_fix"] and result["proposed_fix"].get("tool_name") == "rollback_deployment":
        assert result["test_gate"]["passed"] is False
        assert result["status"] == "test_gate_failed"
        assert result["requires_approval"] is False


def test_analyse_persists_a_full_trace(db, client):
    if db.get(ServiceState, "graph-test-svc-4") is None:
        db.add(ServiceState(name="graph-test-svc-4"))
        db.commit()

    incident = _make_incident(
        db,
        service="graph-test-svc-4",
        message="Heap usage at 900MB / 1024MB - approaching OOM threshold",
    )
    result = analyse_incident(db, incident.id)

    trace = client.get(f"/api/runs/{result['run_id']}/trace").json()
    agents = [e["agent"] for e in trace]
    assert agents == [
        "retrieval",
        "diagnosis",
        "report",
        "fix",
        "test_gate",
        "verification",
        "approval_gate",
    ] or agents == ["retrieval", "diagnosis", "report", "fix", "test_gate", "verification"]
    assert [e["seq"] for e in trace] == sorted(e["seq"] for e in trace)


def test_graph_topology_is_described():
    topology = graph_topology()
    node_ids = {n["id"] for n in topology["nodes"]}
    assert {
        "retrieval",
        "diagnosis",
        "report",
        "fix",
        "test_gate",
        "verification",
        "approval_gate",
    } <= node_ids
    for edge in topology["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids

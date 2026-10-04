"""Remediation tool validation, preflight checks, and the automated test gate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.agents.fix_agent import _matches
from app.agents.state import RunContext
from app.agents.test_gate import run_test_suite
from app.db.models import ServiceState
from app.llm.client import get_llm
from app.tools.registry import TOOLS, get_tool
from app.tools.schemas import ScaleServiceArgs


def test_keyword_matching_rejects_embedded_substring():
    assert _matches("the config was changed unexpectedly", "hang") is False
    assert _matches("the process appears to hang", "hang") is True


def test_keyword_matching_allows_suffixed_forms():
    assert _matches("a recent deploy caused this", "deploy") is True
    assert _matches("the service was redeployed twice", "deploy") is False  # not at a word start
    assert _matches("this was a deployment issue", "deploy") is True


def test_scale_args_reject_absurd_replica_count():
    with pytest.raises(ValidationError):
        ScaleServiceArgs(service="checkout-api", replica_count=999)


def test_restart_preflight_blocks_recent_restart(db):
    svc = ServiceState(name="test-restart-svc", last_restarted_at=datetime.now(UTC))
    db.add(svc)
    db.commit()

    tool = get_tool("restart_service")
    args = tool.validate({"service": "test-restart-svc"})
    result = tool.preflight(db, args)
    assert not result.ok
    assert any("restarted" in b for b in result.blockers)


def test_restart_preflight_allows_when_no_recent_restart(db):
    svc = ServiceState(name="test-restart-svc-2")
    db.add(svc)
    db.commit()

    tool = get_tool("restart_service")
    args = tool.validate({"service": "test-restart-svc-2"})
    result = tool.preflight(db, args)
    assert result.ok


def test_rollback_preflight_blocks_noop(db):
    svc = ServiceState(name="test-rollback-svc", version="2.0.0")
    db.add(svc)
    db.commit()

    tool = get_tool("rollback_deployment")
    args = tool.validate({"service": "test-rollback-svc", "target_version": "2.0.0"})
    result = tool.preflight(db, args)
    assert not result.ok


def test_scale_preflight_warns_on_large_jump(db):
    svc = ServiceState(name="test-scale-svc", replica_count=2)
    db.add(svc)
    db.commit()

    tool = get_tool("scale_service")
    args = tool.validate({"service": "test-scale-svc", "replica_count": 20})
    result = tool.preflight(db, args)
    assert result.ok  # a warning, not a blocker
    assert result.warnings


def test_unknown_service_is_blocked_by_every_tool(db):
    for name, tool in TOOLS.items():
        args_data = {"service": "does-not-exist"}
        if name == "rollback_deployment":
            args_data["target_version"] = "1.0.0"
        if name == "scale_service":
            args_data["replica_count"] = 3
        args = tool.validate(args_data)
        result = tool.preflight(db, args)
        assert not result.ok, f"{name} should block an unknown service"


# --- automated test gate ---------------------------------------------------
def test_gate_passes_for_a_valid_restart(db):
    svc = ServiceState(name="gate-restart-svc")
    db.add(svc)
    db.commit()
    ctx = RunContext(db=db, llm=get_llm())
    result = run_test_suite(ctx, "restart_service", {"service": "gate-restart-svc"})
    assert result["passed"] is True
    assert all(c["passed"] for c in result["checks"])


def test_gate_fails_without_a_known_target_version(db):
    svc = ServiceState(name="gate-rollback-svc")
    db.add(svc)
    db.commit()
    ctx = RunContext(db=db, llm=get_llm())
    result = run_test_suite(
        ctx, "rollback_deployment", {"service": "gate-rollback-svc", "target_version": None}
    )
    assert result["passed"] is False
    failed = [c for c in result["checks"] if not c["passed"]]
    assert any(c["name"] == "check_target_version_known" for c in failed)


def test_gate_fails_on_implausible_scale_factor(db):
    svc = ServiceState(name="gate-scale-svc", replica_count=2)
    db.add(svc)
    db.commit()
    ctx = RunContext(db=db, llm=get_llm())
    result = run_test_suite(
        ctx, "scale_service", {"service": "gate-scale-svc", "replica_count": 40}
    )
    assert result["passed"] is False


def test_gate_reports_per_check_timing(db):
    svc = ServiceState(name="gate-timing-svc")
    db.add(svc)
    db.commit()
    ctx = RunContext(db=db, llm=get_llm())
    result = run_test_suite(ctx, "restart_service", {"service": "gate-timing-svc"})
    assert all("duration_ms" in c for c in result["checks"])
    assert result["total_ms"] >= 0


# --- timestamps read back from the database -----------------------------------
def _reload(name: str):
    """A ServiceState fetched in a *fresh* session, the way a later request sees it.

    SQLite returns DateTime(timezone=True) values naive once re-read; a test
    that checks in the same session that wrote the value only ever sees the
    aware in-memory copy and cannot catch that.
    """
    from app.db.base import SessionLocal

    session = SessionLocal()
    return session, session.get(ServiceState, name)


@pytest.mark.parametrize(("minutes_ago", "blocked"), [(1, True), (60, False), (60 * 49, False)])
def test_restart_cooldown_survives_a_database_round_trip(db, minutes_ago, blocked):
    from datetime import timedelta

    name = f"roundtrip-svc-{minutes_ago}"
    restarted = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    db.add(ServiceState(name=name, last_restarted_at=restarted))
    db.commit()

    session, _ = _reload(name)
    try:
        tool = get_tool("restart_service")
        # This used to raise TypeError (naive vs aware datetime).
        result = tool.preflight(session, tool.validate({"service": name}))
        assert result.ok is (not blocked)

        ctx = RunContext(db=session, llm=get_llm())
        gate = run_test_suite(ctx, "restart_service", {"service": name})
        cooldown = next(c for c in gate["checks"] if c["name"] == "check_no_restart_loop")
        assert cooldown["passed"] is (not blocked)
        assert "exception" not in cooldown["message"].lower()
        # Elapsed minutes include whole days (timedelta.seconds dropped them).
        assert f"{minutes_ago}m ago" in cooldown["message"]
    finally:
        session.close()


def test_scale_preflight_blocks_a_noop(db):
    db.add(ServiceState(name="test-scale-noop", replica_count=4))
    db.commit()
    tool = get_tool("scale_service")
    result = tool.preflight(db, tool.validate({"service": "test-scale-noop", "replica_count": 4}))
    assert not result.ok
    assert any("no-op" in b for b in result.blockers)


def test_stale_session_cannot_execute_an_approval_twice(db):
    """Two on-call engineers race on one fix: exactly one may execute it."""
    from app.db.base import SessionLocal
    from app.db.models import Approval
    from app.services.approvals import ApprovalError, decide

    db.add(ServiceState(name="race-svc", replica_count=2))
    approval = Approval(
        run_id="r",
        incident_id="",
        tool_name="scale_service",
        arguments={"service": "race-svc", "replica_count": 3},
        status="pending",
    )
    db.add(approval)
    db.commit()

    stale = SessionLocal()
    try:
        held = stale.get(Approval, approval.id)  # A reads it while still pending
        assert held.status == "pending"
        assert decide(db, approval.id, decision="approve")["status"] == "approved"  # B executes
        with pytest.raises(ApprovalError):  # A, holding a stale copy, must be refused
            decide(stale, approval.id, decision="approve")
    finally:
        stale.close()

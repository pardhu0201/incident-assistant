"""Automated test gate - runs before a fix can even be offered for approval.

This is a second, independent safety net on top of the fix agent's own
preflight checks (which validate the *proposed arguments*): the test gate
re-derives its checks from the *current live state* at execution time and
runs them as a small named test suite, the way a real deployment pipeline
runs a policy-as-code check before a human ever sees a "approve" button. A
fix that fails here is blocked outright - the approval gate is never reached,
regardless of how confident the diagnosis was.

Each check is a small, named, independently-readable function returning
`(passed, message)`, run and timed like a real test case - the graph
persists the full per-check report, not just a pass/fail bit, so a human
reviewing a blocked fix can see exactly which check failed and why.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState, RunContext, trace_event
from app.telemetry import span
from app.tools.ops_tools import MIN_RESTART_INTERVAL_MINUTES, get_service

TestCheck = Callable[[RunContext, dict], tuple[bool, str]]


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    duration_ms: int


# --- shared checks -----------------------------------------------------
def check_service_exists(ctx: RunContext, args: dict) -> tuple[bool, str]:
    service = get_service(ctx.db, args.get("service", ""))
    if service is None:
        return False, f"Service '{args.get('service')}' has no known state record."
    return True, f"Service '{args.get('service')}' found (status={service.status})."


# --- restart_service -----------------------------------------------------
def check_no_restart_loop(ctx: RunContext, args: dict) -> tuple[bool, str]:
    service = get_service(ctx.db, args.get("service", ""))
    if service is None or service.last_restarted_at is None:
        return True, "No prior restart recorded - safe to proceed."
    elapsed = datetime.now(UTC) - service.last_restarted_at
    if elapsed < timedelta(minutes=MIN_RESTART_INTERVAL_MINUTES):
        return (
            False,
            f"Last restart was {elapsed.seconds // 60}m ago - too soon, risk of a restart loop.",
        )
    return True, f"Last restart was {elapsed.seconds // 60}m ago - clear of the cooldown window."


def check_replica_capacity(ctx: RunContext, args: dict) -> tuple[bool, str]:
    service = get_service(ctx.db, args.get("service", ""))
    if service is None:
        return False, "Cannot verify capacity - unknown service."
    if service.replica_count <= 0:
        return False, "Service has 0 replicas - a restart will not restore capacity."
    return True, f"{service.replica_count} replica(s) available to absorb a rolling restart."


# --- rollback_deployment -----------------------------------------------
def check_target_version_known(ctx: RunContext, args: dict) -> tuple[bool, str]:
    target = args.get("target_version")
    if not target:
        return False, "No target_version specified - cannot roll back to an unknown version."
    return True, f"Target version {target} specified."


def check_target_differs_from_current(ctx: RunContext, args: dict) -> tuple[bool, str]:
    service = get_service(ctx.db, args.get("service", ""))
    if service is None:
        return False, "Cannot verify - unknown service."
    if args.get("target_version") == service.version:
        return (
            False,
            f"Service is already on version {service.version} - rollback would be a no-op.",
        )
    return True, f"Target {args.get('target_version')} differs from current {service.version}."


# --- scale_service -------------------------------------------------------
def check_replica_bounds(ctx: RunContext, args: dict) -> tuple[bool, str]:
    count = args.get("replica_count")
    if not isinstance(count, int) or count <= 0 or count > 50:
        return False, f"Requested replica_count {count} is out of the safe 1-50 bound."
    return True, f"Requested replica_count {count} is within bounds."


def check_scale_factor_sane(ctx: RunContext, args: dict) -> tuple[bool, str]:
    service = get_service(ctx.db, args.get("service", ""))
    count = args.get("replica_count")
    if service is None or not isinstance(count, int) or service.replica_count == 0:
        return True, "Insufficient state to compare - not blocking on this check alone."
    factor = count / service.replica_count
    if factor > 8:
        return (
            False,
            f"Scaling factor {factor:.1f}x in one step is implausibly large - likely a bad input.",
        )
    return True, f"Scaling factor {factor:.1f}x is within a plausible single-step range."


SUITES: dict[str, list[TestCheck]] = {
    "restart_service": [check_service_exists, check_no_restart_loop, check_replica_capacity],
    "rollback_deployment": [
        check_service_exists,
        check_target_version_known,
        check_target_differs_from_current,
    ],
    "scale_service": [check_service_exists, check_replica_bounds, check_scale_factor_sane],
}


def run_test_suite(ctx: RunContext, tool_name: str, arguments: dict) -> dict:
    checks = SUITES.get(tool_name, [])
    results: list[CheckResult] = []
    for check in checks:
        t0 = time.perf_counter()
        try:
            passed, message = check(ctx, arguments)
        except Exception as exc:  # pragma: no cover - a check must never crash the gate
            passed, message = False, f"Check raised an exception: {exc}"
        results.append(
            CheckResult(
                name=check.__name__,
                passed=passed,
                message=message,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        )

    all_passed = all(r.passed for r in results) if results else False
    return {
        "tool_name": tool_name,
        "passed": all_passed,
        "checks": [
            {"name": r.name, "passed": r.passed, "message": r.message, "duration_ms": r.duration_ms}
            for r in results
        ],
        "total_ms": sum(r.duration_ms for r in results),
    }


def test_gate_node(state: AgentState, config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    started = time.perf_counter()

    proposed = state.get("proposed_fix")
    if not proposed or not proposed.get("valid"):
        return {
            "test_gate": None,
            "trace": [
                trace_event(
                    ctx, "test_gate", "Skipped - no valid fix was proposed", started=started
                )
            ],
        }

    with span("agent.test_gate", tool=proposed["tool_name"], incident_id=state["incident_id"]):
        result = run_test_suite(ctx, proposed["tool_name"], proposed["arguments"])

    passed_count = sum(1 for c in result["checks"] if c["passed"])
    summary = f"{passed_count}/{len(result['checks'])} checks passed"
    return {
        "test_gate": result,
        "trace": [
            trace_event(
                ctx,
                "test_gate",
                summary if result["passed"] else f"{summary} - fix blocked before approval",
                status="ok" if result["passed"] else "error",
                payload=result,
                started=started,
            )
        ],
    }

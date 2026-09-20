"""Offline evaluation harness.

Replays the bundled sample log dataset, then runs the real diagnosis/fix
pipeline against each resulting incident and scores the numbers that matter
for an incident-response system:

* **Clustering correctness** - did the expected number of distinct incidents
  form at all (a regression here means the fingerprinting or clustering
  logic broke, silently merging or splitting incidents).
* **Retrieval accuracy**      - was the right runbook found for each incident.
* **Diagnosis quality**       - groundedness score, citation validity.
* **Fix accuracy**            - did the fix agent propose the tool the
  runbook actually recommends.
* **Safety gate enforcement** - did every proposed fix pass through the
  automated test gate, and was a genuinely out-of-scope error correctly
  scored low rather than confidently (mis)diagnosed.
* **Latency / cost**          - per-incident pipeline latency, and estimated
  spend (zero in demo mode - the eval never requires an API key).

Run it against either mode:

    python -m evals.run_eval              # demo mode (free, deterministic)
    ANTHROPIC_API_KEY=sk-... python -m evals.run_eval

Add ``--json report.json`` to write the full machine-readable report.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.runner import analyse_incident  # noqa: E402
from app.db.base import session_scope  # noqa: E402
from app.db.init_db import initialise  # noqa: E402
from app.db.models import Incident  # noqa: E402
from app.ingestion.pipeline import replay_sample_dataset  # noqa: E402
from app.llm.client import get_llm  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

GOLDEN_SET = Path(__file__).parent / "golden_set.json"
EXPECTED_INCIDENT_COUNT = 6


def _normalise(text: str) -> str:
    return " ".join(text.lower().replace("**", "").replace("*", "").split())


def _find_incident(db, case: dict) -> Incident | None:
    if case.get("custom_message"):
        incident = Incident(
            title=f"{case['service']}: {case['custom_message'][:60]}",
            service=case["service"],
            fingerprint="eval-synthetic",
            severity="ERROR",
            status="open",
            event_count=1,
            sample_message=case["custom_message"],
            sample_stack_trace="",
            first_seen=__import__("datetime").datetime(2026, 1, 1),
            last_seen=__import__("datetime").datetime(2026, 1, 1),
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)
        return incident

    for incident in db.query(Incident).filter(Incident.service == case["service"]).all():
        if case["message_contains"].lower() in incident.sample_message.lower():
            return incident
    return None


def evaluate_case(db, case: dict) -> dict:
    started = time.perf_counter()
    incident = _find_incident(db, case)
    if incident is None:
        return {"id": case["id"], "found": False}

    result = analyse_incident(db, incident.id)
    diagnosis = _normalise(result["diagnosis"])
    citations = result.get("citations") or []
    retrieved_titles = {c["document_title"] for c in citations}

    expected_doc = case.get("expected_document")
    doc_found = expected_doc in retrieved_titles if expected_doc else None

    expected_tool = case.get("expected_tool")
    actual_tool = (result.get("proposed_fix") or {}).get("tool_name")
    tool_correct = (actual_tool == expected_tool) if expected_tool else None

    contains = [_normalise(s) in diagnosis for s in case.get("must_contain", [])]

    verification = result.get("verification") or {}
    test_gate = result.get("test_gate") or {}

    return {
        "id": case["id"],
        "found": True,
        "doc_found": doc_found,
        "tool_correct": tool_correct,
        "actual_tool": actual_tool,
        "coverage": (sum(contains) / len(contains)) if contains else None,
        "groundedness_score": verification.get("groundedness_score", 0.0),
        "confidence": result["confidence"],
        "invalid_citations": verification.get("invalid_citations", []),
        "test_gate_ran": bool(test_gate),
        "test_gate_passed": test_gate.get("passed") if test_gate else None,
        "expect_test_gate_pass": case.get("expect_test_gate_pass"),
        "expect_low_confidence": case.get("expect_low_confidence", False),
        "requires_approval": result["requires_approval"],
        "status": result["status"],
        "llm_mode": result["llm_mode"],
        "latency_ms": result["latency_ms"],
        "estimated_cost_usd": 0.0,  # demo mode is free; populated below if Claude is used
        "token_usage": result.get("token_usage") or {},
        "total_ms": int((time.perf_counter() - started) * 1000),
    }


def _mean(values: list) -> float:
    numbers = [v for v in values if isinstance(v, int | float)]
    return round(sum(numbers) / len(numbers), 3) if numbers else 0.0


def _rate(values: list) -> float:
    flags = [v for v in values if isinstance(v, bool)]
    return round(sum(flags) / len(flags), 3) if flags else 0.0


def summarise(rows: list[dict], incident_count: int) -> dict:
    found_rows = [r for r in rows if r.get("found")]
    doc_rows = [r for r in found_rows if r.get("doc_found") is not None]
    tool_rows = [r for r in found_rows if r.get("tool_correct") is not None]
    gated_rows = [r for r in found_rows if r.get("expect_test_gate_pass") is not None]
    low_conf_rows = [r for r in found_rows if r.get("expect_low_confidence")]

    return {
        "cases": len(rows),
        "mode": get_llm().mode,
        "clustering": {
            "expected_incidents": EXPECTED_INCIDENT_COUNT,
            "actual_incidents": incident_count,
            "correct": incident_count == EXPECTED_INCIDENT_COUNT,
        },
        "retrieval": {"runbook_accuracy": _rate([r["doc_found"] for r in doc_rows])},
        "diagnosis": {
            "fact_coverage": _mean([r["coverage"] for r in found_rows]),
            "average_groundedness": _mean([r["groundedness_score"] for r in found_rows]),
            "invalid_citation_rate": _rate([bool(r["invalid_citations"]) for r in found_rows]),
        },
        "fix": {
            "tool_accuracy": _rate([r["tool_correct"] for r in tool_rows]),
            "test_gate_pass_rate_when_expected": _rate(
                [r["test_gate_passed"] == r["expect_test_gate_pass"] for r in gated_rows]
            ),
        },
        "safety": {
            "out_of_scope_correctly_low_confidence": _rate(
                [r["confidence"] < 0.5 for r in low_conf_rows]
            )
            if low_conf_rows
            else None,
            "out_of_scope_never_auto_approved": _rate(
                [not r["requires_approval"] for r in low_conf_rows]
            )
            if low_conf_rows
            else None,
        },
        "performance": {
            "mean_latency_ms": _mean([r["latency_ms"] for r in found_rows]),
            "total_estimated_cost_usd": round(
                sum(r.get("estimated_cost_usd", 0.0) for r in found_rows), 6
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the platform on the golden set")
    parser.add_argument("--json", type=Path, help="Write the full report to this path")
    args = parser.parse_args()

    configure_logging("WARNING")
    initialise(seed=True)

    payload = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    cases = payload["cases"]

    rows: list[dict] = []
    with session_scope() as db:
        summary = replay_sample_dataset(db)
        incident_count = len(
            {i.id for i in db.query(Incident).filter(Incident.fingerprint != "eval-synthetic")}
        )
        print(
            f"Replayed sample dataset: {summary.events_ingested} events, {incident_count} incidents\n"
        )

        for case in cases:
            row = evaluate_case(db, case)
            rows.append(row)
            if not row.get("found"):
                print(f"  [MISS] {case['id']} - incident not found")
                continue
            mark = (
                "T"
                if row.get("tool_correct")
                else ("-" if row.get("tool_correct") is None else "x")
            )
            doc_mark = (
                "D" if row.get("doc_found") else ("-" if row.get("doc_found") is None else "x")
            )
            print(
                f"  [{doc_mark}{mark}] ground={row['groundedness_score']:.2f} conf={row['confidence']:.2f} "
                f"{row['total_ms']:>5}ms  {row['id']}"
            )

    report = {"summary": summarise(rows, incident_count), "results": rows}
    print("\n" + json.dumps(report["summary"], indent=2))

    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nFull report written to {args.json}")

    summary = report["summary"]
    ok = (
        summary["clustering"]["correct"]
        and summary["retrieval"]["runbook_accuracy"] >= 0.9
        and summary["fix"]["tool_accuracy"] >= 0.9
        and summary["diagnosis"]["invalid_citation_rate"] == 0.0
        and (
            summary["safety"]["out_of_scope_never_auto_approved"] is None
            or summary["safety"]["out_of_scope_never_auto_approved"] == 1.0
        )
    )
    print("\nRESULT:", "PASS" if ok else "BELOW THRESHOLD")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

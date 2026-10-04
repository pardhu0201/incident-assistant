"""LangGraph wiring for the incident diagnosis/remediation pipeline.

    START -> retrieval -> diagnosis -> report -> fix -> test_gate -> verification
                                                                            |
                                accum accumulator                          |
                                                                            v
                                                            +---------------+---------------+
                                                            |               |                |
                                                       "diagnosed"     "approval"      "escalate" /
                                                            |               |         "test_gate_failed"
                                                            v               v                |
                                                           END      approval_gate            END
                                                                           |
                                                                          END

The graph is compiled once at import time and is stateless; per-request
services (DB session, LLM client, usage tracker) are injected through the
LangGraph config, so one compiled graph safely serves concurrent incidents.
"""

from __future__ import annotations

import time

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import update

from app.agents.diagnosis_agent import diagnosis_node
from app.agents.fix_agent import fix_node
from app.agents.report_agent import report_node
from app.agents.retrieval_agent import retrieval_node
from app.agents.state import AgentState, RunContext, trace_event
from app.agents.test_gate import test_gate_node
from app.agents.verification_agent import verification_node
from app.db.models import Approval
from app.logging_config import get_logger

log = get_logger(__name__)


def approval_gate_node(state: AgentState, config: RunnableConfig) -> dict:
    """Queue the tested fix for a human decision. Nothing is executed here."""
    ctx: RunContext = config["configurable"]["ctx"]
    started = time.perf_counter()

    fix = state.get("proposed_fix") or {}
    verification = state.get("verification") or {}
    test_gate = state.get("test_gate") or {}

    # One live proposal per incident. Re-analysing an incident produces a
    # fresh proposal computed against current state; any older pending one
    # was computed against state that may no longer hold (approving both
    # would apply a stale fix), so it is retired rather than left queued.
    superseded = ctx.db.execute(
        update(Approval)
        .where(Approval.incident_id == state["incident_id"], Approval.status == "pending")
        .values(status="superseded")
    ).rowcount

    approval = Approval(
        run_id=state["run_id"],
        incident_id=state["incident_id"],
        tool_name=fix.get("tool_name", "unknown"),
        arguments=fix.get("arguments", {}),
        risk=fix.get("risk", "medium"),
        rationale=fix.get("rationale", ""),
        flags=state.get("flags", []),
        confidence=state.get("confidence", 0.0),
        test_gate_passed=bool(test_gate.get("passed")),
        status="pending",
    )
    ctx.db.add(approval)
    ctx.db.commit()
    ctx.db.refresh(approval)

    return {
        "approval_id": approval.id,
        "status": "awaiting_approval",
        "trace": [
            trace_event(
                ctx,
                "approval_gate",
                f"Queued '{approval.tool_name}' for human approval (risk={approval.risk}, "
                f"tests passed={approval.test_gate_passed})",
                status="warning",
                payload={
                    "approval_id": approval.id,
                    "tool_name": approval.tool_name,
                    "risk": approval.risk,
                    "confidence": verification.get("confidence"),
                    "superseded_pending": superseded,
                },
                started=started,
            )
        ],
    }


def route_after_verification(state: AgentState) -> str:
    decision = (state.get("verification") or {}).get("decision", "diagnosed")
    return "approval_gate" if decision == "approval" else END


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("retrieval", retrieval_node)
    graph.add_node("diagnosis", diagnosis_node)
    graph.add_node("report", report_node)
    graph.add_node("fix", fix_node)
    graph.add_node("test_gate", test_gate_node)
    graph.add_node("verification", verification_node)
    graph.add_node("approval_gate", approval_gate_node)

    graph.add_edge(START, "retrieval")
    graph.add_edge("retrieval", "diagnosis")
    graph.add_edge("diagnosis", "report")
    graph.add_edge("report", "fix")
    graph.add_edge("fix", "test_gate")
    graph.add_edge("test_gate", "verification")
    graph.add_conditional_edges(
        "verification", route_after_verification, {"approval_gate": "approval_gate", END: END}
    )
    graph.add_edge("approval_gate", END)

    return graph.compile()


COMPILED_GRAPH = build_graph()


def graph_topology() -> dict:
    return {
        "nodes": [
            {
                "id": "retrieval",
                "label": "Retrieval agent",
                "role": "Hybrid search over runbooks/postmortems",
            },
            {
                "id": "diagnosis",
                "label": "Diagnosis agent",
                "role": "Grounded, cited root-cause analysis",
            },
            {"id": "report", "label": "Report agent", "role": "Drafts incident report + checklist"},
            {"id": "fix", "label": "Fix agent", "role": "Proposes a validated remediation action"},
            {
                "id": "test_gate",
                "label": "Automated test gate",
                "role": "Runs checks against live state before approval",
            },
            {
                "id": "verification",
                "label": "Verification agent",
                "role": "Groundedness + fix-safety checks",
            },
            {
                "id": "approval_gate",
                "label": "Human approval",
                "role": "Blocks execution until approved",
            },
        ],
        "edges": [
            {"source": "retrieval", "target": "diagnosis"},
            {"source": "diagnosis", "target": "report"},
            {"source": "report", "target": "fix"},
            {"source": "fix", "target": "test_gate"},
            {"source": "test_gate", "target": "verification"},
            {
                "source": "verification",
                "target": "approval_gate",
                "condition": "fix proposed, tests passed",
            },
        ],
    }

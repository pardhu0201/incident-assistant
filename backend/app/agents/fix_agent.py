"""Fix agent - proposes a validated remediation action, never executes one.

Output contract: a *proposal* containing the tool name, Pydantic-validated
arguments, and a preflight preview computed against live `ServiceState`.
Execution is physically impossible from this node - it is reachable only
from the approvals API after a human decision, and only after the automated
test gate (the next node) has passed.
"""

from __future__ import annotations

import json
import re
import time

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, ValidationError

from app.agents.prompts import FIX_SYSTEM
from app.agents.state import AgentState, RunContext, trace_event
from app.logging_config import get_logger
from app.telemetry import span
from app.tools.ops_tools import get_service
from app.tools.registry import TOOLS, ToolSpec, get_tool

log = get_logger(__name__)

# Deterministic keyword routing for demo mode / fallback - deliberately
# conservative: rollback requires a *known* last-good version, since guessing
# one would be unsafe, so it only fires when ServiceState actually has one.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "restart_service": (
        "connection pool",
        "pool exhaust",
        "stuck",
        "hang",
        "deadlock",
        "timeout waiting",
        "memory leak",
        "oom",
    ),
    "rollback_deployment": (
        "deploy",
        "release",
        "rollout",
        "new version",
        "regression",
        "changed in v",
    ),
    "scale_service": (
        "overload",
        "capacity",
        "traffic spike",
        "rate limit",
        "queue depth",
        "backlog",
        "too many requests",
    ),
}


class FixOutput(BaseModel):
    applicable: bool = Field(default=False)
    tool_name: str = Field(default="", description="One of the catalogue's tool names, or empty.")
    arguments_json: str = Field(default="{}")
    rationale: str = Field(default="")


def _matches(text: str, keyword: str) -> bool:
    """Word-start-boundary containment.

    A plain substring check would match "hang" inside "changed", or "oom"
    inside "room" - a real false positive this rule set hit in practice. A
    *trailing* boundary too would fix that but over-corrects the other way,
    rejecting "deploy" inside "deployed"/"deployment", which are exactly the
    forms a diagnosis is likely to use. Requiring only that the match starts
    at a word boundary rejects the embedded-substring case while still
    matching ordinary suffixed forms.
    """
    return re.search(rf"\b{re.escape(keyword)}", text) is not None


def _rule_based_fix(state: AgentState) -> FixOutput:
    text = f"{state.get('diagnosis', '')} {state['sample_message']} {state['sample_stack_trace']}".lower()
    service = state["incident_service"]

    for tool_name, keywords in _KEYWORDS.items():
        if not any(_matches(text, kw) for kw in keywords):
            continue

        if tool_name == "restart_service":
            args = {"service": service, "reason": f"Suspected transient fault in {service}"}
        elif tool_name == "scale_service":
            args = {"service": service, "replica_count": None, "reason": "Suspected capacity issue"}
        else:  # rollback_deployment - only if we actually know a last-good version
            args = {
                "service": service,
                "target_version": None,
                "reason": "Suspected regression from a recent deploy",
            }
        return FixOutput(
            applicable=True,
            tool_name=tool_name,
            arguments_json=json.dumps(args),
            rationale=f"Diagnosis text matched keyword pattern for {tool_name}.",
        )

    return FixOutput(
        applicable=False, rationale="No clear remediation pattern matched the diagnosis."
    )


def _fill_gaps(tool: ToolSpec, args: dict, ctx: RunContext) -> dict:
    """Resolve placeholders a keyword-matched rule couldn't know on its own."""
    service_state = get_service(ctx.db, args.get("service", ""))
    if tool.name == "scale_service" and args.get("replica_count") is None:
        current = service_state.replica_count if service_state else 3
        args["replica_count"] = max(1, current * 2)
    if tool.name == "rollback_deployment" and not args.get("target_version"):
        args["target_version"] = service_state.previous_version if service_state else None
    return args


def fix_node(state: AgentState, config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    started = time.perf_counter()

    with span("agent.fix", incident_id=state["incident_id"]):
        catalogue_text = "\n".join(
            f"- {name} (risk={spec.risk}): {spec.description}" for name, spec in TOOLS.items()
        )
        user = (
            f"Incident: {state['incident_title']}\n"
            f"Service: {state['incident_service']}\n"
            f"Diagnosis:\n{state.get('diagnosis', '(none)')}\n\n"
            f"Available remediation tools:\n{catalogue_text}"
        )
        result = ctx.llm.structured(
            agent="fix",
            system=FIX_SYSTEM,
            user=user,
            schema=FixOutput,
            fallback=lambda: _rule_based_fix(state),
            max_tokens=1500,
        )
    ctx.usage.add("fix", result)
    output: FixOutput = result.value  # type: ignore[assignment]

    if not output.applicable or not output.tool_name:
        return {
            "candidate_tool": "",
            "proposed_fix": None,
            "trace": [
                trace_event(
                    ctx,
                    "fix",
                    "No automated remediation proposed - recommend manual investigation",
                    payload={"mode": result.mode},
                    started=started,
                )
            ],
        }

    tool = get_tool(output.tool_name)
    if tool is None:
        return {
            "candidate_tool": "",
            "proposed_fix": None,
            "trace": [
                trace_event(
                    ctx,
                    "fix",
                    f"Model named unknown tool '{output.tool_name}' - ignored",
                    status="warning",
                    started=started,
                )
            ],
        }

    try:
        raw_args = json.loads(output.arguments_json)
    except json.JSONDecodeError:
        raw_args = {}
    raw_args = _fill_gaps(tool, raw_args, ctx)

    try:
        validated = tool.validate(raw_args)
    except (ValidationError, TypeError) as exc:
        return {
            "candidate_tool": "",
            "proposed_fix": {
                "tool_name": tool.name,
                "valid": False,
                "blockers": [f"Could not build a valid action: {exc}"],
                "warnings": [],
                "preview": {},
                "arguments": raw_args,
            },
            "trace": [
                trace_event(
                    ctx,
                    "fix",
                    f"Could not prepare '{tool.name}' - invalid arguments",
                    status="error",
                    payload={"error": str(exc)},
                    started=started,
                )
            ],
        }

    preflight = tool.preflight(ctx.db, validated)
    proposed = {
        "tool_name": tool.name,
        "description": tool.description,
        "risk": tool.risk,
        "requires_approval": tool.requires_approval,
        "arguments": json.loads(validated.model_dump_json()),
        "valid": preflight.ok,
        "blockers": preflight.blockers,
        "warnings": preflight.warnings,
        "preview": preflight.preview,
        "rationale": output.rationale,
    }

    status = "error" if preflight.blockers else ("warning" if preflight.warnings else "ok")
    return {
        "candidate_tool": tool.name,
        "proposed_fix": proposed,
        "trace": [
            trace_event(
                ctx,
                "fix",
                f"Proposed '{tool.name}'"
                if preflight.ok
                else f"Proposed '{tool.name}' but it is blocked",
                status=status,
                payload={
                    "tool": tool.name,
                    "arguments": proposed["arguments"],
                    "mode": result.mode,
                },
                started=started,
            )
        ],
    }

"""Report agent - drafts a short incident report and resolution checklist
from the diagnosis already produced. Adds no new factual claims."""

from __future__ import annotations

import time

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.agents.prompts import REPORT_SYSTEM
from app.agents.state import AgentState, RunContext, trace_event
from app.telemetry import span


class ReportOutput(BaseModel):
    report: str = Field(description="3-5 sentence incident summary.")
    checklist: list[str] = Field(default_factory=list, description="3-6 concrete action items.")


def _lead_finding(diagnosis: str) -> str:
    """The first substantive line of a diagnosis, for the report summary.

    The extractive diagnosis opens with a lead-in ("Likely relevant guidance
    from the runbooks:") and ends with an italic mode note; taking the first
    line verbatim put that dangling lead-in into every demo-mode report.
    """
    for raw in diagnosis.splitlines():
        line = raw.strip().removeprefix("- ").removeprefix("* ").strip()
        if not line or line.endswith(":") or line.startswith(("_", "#")):
            continue
        return f"Leading finding: {line}"
    return "Diagnosis pending."


def _deterministic_report(state: AgentState) -> ReportOutput:
    service = state["incident_service"]
    count = state["incident_event_count"]
    severity = state["incident_severity"]
    report = (
        f"**{state['incident_title']}**\n\n"
        f"{severity}-level errors were detected in `{service}` ({count} occurrence"
        f"{'s' if count != 1 else ''} so far). Sample error: "
        f'"{state["sample_message"][:200]}". '
        f"{_lead_finding(state.get('diagnosis', ''))}"
    )
    checklist = [
        f"Confirm current error rate for `{service}` in the dashboard.",
        "Review the diagnosis and cited runbook passages below.",
        "Check recent deploys and configuration changes to the service.",
        "If a fix is proposed, verify the automated test gate passed before approving.",
        "After remediation, monitor for 15 minutes before closing the incident.",
    ]
    return ReportOutput(report=report, checklist=checklist)


def report_node(state: AgentState, config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    started = time.perf_counter()

    with span("agent.report", incident_id=state["incident_id"]):
        user = (
            f"Incident: {state['incident_title']}\n"
            f"Service: {state['incident_service']}, severity: {state['incident_severity']}, "
            f"seen {state['incident_event_count']} times\n\n"
            f"Diagnosis already produced:\n{state.get('diagnosis', '(none)')}"
        )
        result = ctx.llm.structured(
            agent="report",
            system=REPORT_SYSTEM,
            user=user,
            schema=ReportOutput,
            fallback=lambda: _deterministic_report(state),
            max_tokens=2000,
        )
    ctx.usage.add("report", result)
    output: ReportOutput = result.value  # type: ignore[assignment]

    return {
        "report": output.report.strip(),
        "checklist": output.checklist,
        "trace": [
            trace_event(
                ctx,
                "report",
                f"Drafted report with a {len(output.checklist)}-item checklist",
                payload={"mode": result.mode},
                started=started,
            )
        ],
    }

"""Approval queue - the only code path that can execute a remediation action.

The agent graph can *propose* a fix and the test gate can *clear* it, but
only a human decision routed through here can *execute* it. Approving
re-validates the arguments and re-runs preflight against current state
(never trusts what was stored when the proposal was made) before calling the
tool's `execute()`.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Approval, AuditLog, Incident
from app.logging_config import get_logger
from app.tools.registry import get_tool

log = get_logger(__name__)


class ApprovalError(Exception):
    pass


def _claim(db: Session, approval_id: str) -> bool:
    """Atomically move a fix out of `pending` so it can only be decided once.

    Check-then-write on the ORM object would let two concurrent approvals both
    see `pending` and both execute the remediation. A conditional UPDATE is
    atomic on every backend: exactly one caller gets rowcount 1.
    """
    result = db.execute(
        update(Approval)
        .where(Approval.id == approval_id, Approval.status == "pending")
        .values(status="processing")
    )
    db.commit()
    return result.rowcount == 1


def list_approvals(db: Session, status: str | None = None, limit: int = 50) -> list[Approval]:
    stmt = select(Approval).order_by(Approval.created_at.desc()).limit(limit)
    if status and status != "all":
        stmt = stmt.where(Approval.status == status)
    return list(db.execute(stmt).scalars())


def get_approval(db: Session, approval_id: str) -> Approval | None:
    return db.get(Approval, approval_id)


def decide(
    db: Session,
    approval_id: str,
    *,
    decision: str,
    decided_by: str = "oncall@example.com",
    note: str = "",
) -> dict:
    if decision not in {"approve", "reject"}:
        raise ApprovalError("decision must be 'approve' or 'reject'")

    approval = db.get(Approval, approval_id)
    if approval is None:
        raise ApprovalError(f"Approval {approval_id} not found")
    if approval.status != "pending":
        raise ApprovalError(f"Approval {approval_id} is already {approval.status}")
    if not _claim(db, approval_id):
        db.refresh(approval)
        raise ApprovalError(f"Approval {approval_id} is already {approval.status}")
    db.refresh(approval)

    approval.decided_by = decided_by
    approval.decision_note = note

    from app.db.models import utcnow

    approval.decided_at = utcnow()

    if decision == "reject":
        approval.status = "rejected"
        message = f"Rejected by {decided_by}." + (f" Reason: {note}" if note else "")
        _finish(db, approval, "incident.rejected")
        return _payload(approval, message)

    tool = get_tool(approval.tool_name)
    if tool is None:
        approval.status = "failed"
        message = f"Cannot execute unknown tool '{approval.tool_name}'."
        _finish(db, approval, "incident.fix_failed")
        raise ApprovalError(message)

    try:
        validated = tool.validate(approval.arguments)
        preflight = tool.preflight(db, validated)
        if not preflight.ok:
            approval.status = "failed"
            message = "Execution blocked by policy:\n" + "\n".join(
                f"- {b}" for b in preflight.blockers
            )
            _finish(db, approval, "incident.fix_failed")
            return _payload(approval, message)

        result = tool.execute(db, validated, approval.id)
    except Exception as exc:
        log.exception("Tool execution failed for approval %s", approval_id)
        approval.status = "failed"
        message = f"The fix could not be applied: {exc}"
        _finish(db, approval, "incident.fix_failed")
        return _payload(approval, message)

    approval.status = "approved"
    approval.execution_result = result
    message = f"Applied. {result}"
    _finish(db, approval, "incident.fix_applied", resolve_incident=True)
    return _payload(approval, message)


def _finish(db: Session, approval: Approval, action: str, resolve_incident: bool = False) -> None:
    db.add(
        AuditLog(
            actor=approval.decided_by or "system",
            action=action,
            entity="approval",
            entity_id=approval.id,
            payload={
                "tool": approval.tool_name,
                "arguments": approval.arguments,
                "note": approval.decision_note,
            },
        )
    )
    if resolve_incident and approval.incident_id:
        incident = db.get(Incident, approval.incident_id)
        if incident is not None:
            incident.status = "resolved"
    db.commit()
    db.refresh(approval)


def _payload(approval: Approval, message: str) -> dict:
    return {
        "approval_id": approval.id,
        "status": approval.status,
        "tool_name": approval.tool_name,
        "message": message,
        "execution_result": approval.execution_result,
        "decided_by": approval.decided_by,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "incident_id": approval.incident_id,
    }

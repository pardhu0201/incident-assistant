"""Human-in-the-loop approval queue for proposed fixes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import require_admin
from app.db.base import get_session
from app.db.models import AuditLog
from app.schemas import ApprovalDecision, ApprovalOut
from app.services.approvals import ApprovalError, decide, get_approval, list_approvals

router = APIRouter(tags=["approvals"])


def _to_out(a) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        run_id=a.run_id,
        incident_id=a.incident_id,
        tool_name=a.tool_name,
        arguments=a.arguments,
        risk=a.risk,
        rationale=a.rationale,
        flags=a.flags or [],
        confidence=a.confidence,
        test_gate_passed=a.test_gate_passed,
        status=a.status,
        decided_by=a.decided_by,
        decision_note=a.decision_note,
        execution_result=a.execution_result,
        created_at=a.created_at,
        decided_at=a.decided_at,
    )


@router.get("/approvals", response_model=list[ApprovalOut])
def get_approvals(status: str = "all", limit: int = 50, db: Session = Depends(get_session)):
    return [_to_out(a) for a in list_approvals(db, status=status, limit=limit)]


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
def read_approval(approval_id: str, db: Session = Depends(get_session)):
    approval = get_approval(db, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return _to_out(approval)


@router.post("/approvals/{approval_id}/decision", dependencies=[Depends(require_admin)])
def submit_decision(
    approval_id: str, payload: ApprovalDecision, db: Session = Depends(get_session)
):
    try:
        return decide(
            db,
            approval_id,
            decision=payload.decision,
            decided_by=payload.decided_by,
            note=payload.note,
        )
    except ApprovalError as exc:
        status = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/audit")
def audit_trail(limit: int = 50, db: Session = Depends(get_session)):
    entries = db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    ).scalars()
    return [
        {
            "id": e.id,
            "actor": e.actor,
            "action": e.action,
            "entity": e.entity,
            "entity_id": e.entity_id,
            "payload": e.payload,
            "created_at": e.created_at,
        }
        for e in entries
    ]

"""Deterministic implementations behind the tool registry.

These stand in for a real orchestrator (Kubernetes, an ECS service, a deploy
tool) via a small `ServiceState` table. They are intentionally boring and
fully auditable - all the intelligence lives in the agents, all the
*authority* lives here behind the approval gate and the automated-test gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import AuditLog, ServiceState, minutes_since
from app.tools.schemas import RestartServiceArgs, RollbackDeploymentArgs, ScaleServiceArgs

MIN_RESTART_INTERVAL_MINUTES = 5


def get_service(db: Session, name: str) -> ServiceState | None:
    return db.get(ServiceState, name)


def _audit(db: Session, action: str, entity_id: str, payload: dict) -> None:
    db.add(
        AuditLog(
            actor="incident-assistant",
            action=action,
            entity="service_state",
            entity_id=entity_id,
            payload=payload,
        )
    )


# ---------------------------------------------------------------------------
# restart_service
# ---------------------------------------------------------------------------
def preflight_restart_service(db: Session, args: RestartServiceArgs):
    from app.tools.registry import PreflightResult

    warnings: list[str] = []
    blockers: list[str] = []

    service = get_service(db, args.service)
    if service is None:
        blockers.append(f"Unknown service '{args.service}' - no ServiceState record.")
        return PreflightResult(ok=False, preview={}, warnings=warnings, blockers=blockers)

    if service.last_restarted_at is not None:
        elapsed_minutes = minutes_since(service.last_restarted_at)
        if elapsed_minutes < MIN_RESTART_INTERVAL_MINUTES:
            blockers.append(
                f"'{args.service}' was already restarted {int(elapsed_minutes)}m ago - "
                f"restarting again within {MIN_RESTART_INTERVAL_MINUTES}m risks a restart loop."
            )

    if service.replica_count == 0:
        warnings.append("Service currently has 0 replicas; a restart alone will not add capacity.")

    preview = {
        "service": args.service,
        "current_status": service.status,
        "current_version": service.version,
        "replica_count": service.replica_count,
        "reason": args.reason,
    }
    return PreflightResult(ok=not blockers, preview=preview, warnings=warnings, blockers=blockers)


def execute_restart_service(db: Session, args: RestartServiceArgs, approval_id: str | None) -> dict:
    service = get_service(db, args.service)
    service.last_restarted_at = datetime.now(UTC)
    service.status = "healthy"
    db.flush()
    _audit(
        db, "service.restarted", args.service, {"reason": args.reason, "approval_id": approval_id}
    )
    db.commit()
    return {
        "action": "restart_service",
        "service": args.service,
        "status": service.status,
        "restarted_at": service.last_restarted_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# rollback_deployment
# ---------------------------------------------------------------------------
def preflight_rollback_deployment(db: Session, args: RollbackDeploymentArgs):
    from app.tools.registry import PreflightResult

    warnings: list[str] = []
    blockers: list[str] = []

    service = get_service(db, args.service)
    if service is None:
        blockers.append(f"Unknown service '{args.service}' - no ServiceState record.")
        return PreflightResult(ok=False, preview={}, warnings=warnings, blockers=blockers)

    if args.target_version == service.version:
        blockers.append(f"'{args.service}' is already on version {args.target_version}.")
    elif service.previous_version and args.target_version != service.previous_version:
        warnings.append(
            f"Target version {args.target_version} does not match the last known-good "
            f"version {service.previous_version} - verify it is actually safe before approving."
        )

    preview = {
        "service": args.service,
        "current_version": service.version,
        "target_version": args.target_version,
        "last_known_good": service.previous_version or "unknown",
        "reason": args.reason,
    }
    return PreflightResult(ok=not blockers, preview=preview, warnings=warnings, blockers=blockers)


def execute_rollback_deployment(
    db: Session, args: RollbackDeploymentArgs, approval_id: str | None
) -> dict:
    service = get_service(db, args.service)
    old_version = service.version
    service.previous_version = old_version
    service.version = args.target_version
    service.last_deployed_at = datetime.now(UTC)
    service.status = "healthy"
    db.flush()
    _audit(
        db,
        "service.rolled_back",
        args.service,
        {"from": old_version, "to": args.target_version, "approval_id": approval_id},
    )
    db.commit()
    return {
        "action": "rollback_deployment",
        "service": args.service,
        "from_version": old_version,
        "to_version": args.target_version,
        "deployed_at": service.last_deployed_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# scale_service
# ---------------------------------------------------------------------------
def preflight_scale_service(db: Session, args: ScaleServiceArgs):
    from app.tools.registry import PreflightResult

    warnings: list[str] = []
    blockers: list[str] = []

    service = get_service(db, args.service)
    if service is None:
        blockers.append(f"Unknown service '{args.service}' - no ServiceState record.")
        return PreflightResult(ok=False, preview={}, warnings=warnings, blockers=blockers)

    if args.replica_count == service.replica_count:
        # Typically a stale proposal: another fix already scaled the service
        # to this count. Executing it would change nothing yet report success.
        blockers.append(
            f"'{args.service}' already runs {service.replica_count} replicas - "
            "scaling to the same count is a no-op."
        )
    if args.replica_count < service.replica_count / 2 and service.replica_count > 2:
        warnings.append(
            f"Scaling from {service.replica_count} to {args.replica_count} more than halves "
            "capacity - confirm this won't cause overload elsewhere."
        )
    if args.replica_count > service.replica_count * 4:
        warnings.append(
            f"Scaling from {service.replica_count} to {args.replica_count} is a large jump - "
            "confirm downstream dependencies (database connections, rate limits) can absorb it."
        )

    preview = {
        "service": args.service,
        "current_replica_count": service.replica_count,
        "target_replica_count": args.replica_count,
        "reason": args.reason,
    }
    return PreflightResult(ok=not blockers, preview=preview, warnings=warnings, blockers=blockers)


def execute_scale_service(db: Session, args: ScaleServiceArgs, approval_id: str | None) -> dict:
    service = get_service(db, args.service)
    old_count = service.replica_count
    service.replica_count = args.replica_count
    service.status = "healthy"
    db.flush()
    _audit(
        db,
        "service.scaled",
        args.service,
        {"from": old_count, "to": args.replica_count, "approval_id": approval_id},
    )
    db.commit()
    return {
        "action": "scale_service",
        "service": args.service,
        "from_replicas": old_count,
        "to_replicas": args.replica_count,
    }

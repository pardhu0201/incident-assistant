"""Tool registry: the only place where the assistant can touch a live service.

Mirrors the pattern proven in the sibling multi-agent project: each tool
separates `validate` (Pydantic), `preflight` (deterministic checks against
live `ServiceState`), and `execute` (the actual mutation) - and `execute` is
reachable only from the approvals API after a human decision, never from the
agent graph itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.tools import ops_tools as impl
from app.tools.schemas import RestartServiceArgs, RollbackDeploymentArgs, ScaleServiceArgs


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    preview: dict
    warnings: list[str]
    blockers: list[str]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    risk: str  # low | medium | high
    requires_approval: bool
    preflight: Callable[[Session, BaseModel], PreflightResult]
    execute: Callable[[Session, BaseModel, str | None], dict]

    def json_schema(self) -> dict:
        return self.args_model.model_json_schema()

    def validate(self, arguments: dict) -> BaseModel:
        return self.args_model.model_validate(arguments)


TOOLS: dict[str, ToolSpec] = {
    "restart_service": ToolSpec(
        name="restart_service",
        description="Restart a service's process/pods. Use for transient errors, connection-pool exhaustion, or a stuck process.",
        args_model=RestartServiceArgs,
        risk="medium",
        requires_approval=True,
        preflight=lambda db, args: impl.preflight_restart_service(db, args),
        execute=lambda db, args, approval_id: impl.execute_restart_service(db, args, approval_id),
    ),
    "rollback_deployment": ToolSpec(
        name="rollback_deployment",
        description="Roll a service back to a previous version. Use when a recent deploy is the likely cause.",
        args_model=RollbackDeploymentArgs,
        risk="high",
        requires_approval=True,
        preflight=lambda db, args: impl.preflight_rollback_deployment(db, args),
        execute=lambda db, args, approval_id: impl.execute_rollback_deployment(
            db, args, approval_id
        ),
    ),
    "scale_service": ToolSpec(
        name="scale_service",
        description="Change a service's replica count. Use for capacity/overload-related errors.",
        args_model=ScaleServiceArgs,
        risk="medium",
        requires_approval=True,
        preflight=lambda db, args: impl.preflight_scale_service(db, args),
        execute=lambda db, args, approval_id: impl.execute_scale_service(db, args, approval_id),
    ),
}


def get_tool(name: str) -> ToolSpec | None:
    return TOOLS.get(name)


def tool_catalogue() -> list[dict]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "risk": spec.risk,
            "requires_approval": spec.requires_approval,
            "parameters": spec.json_schema(),
        }
        for spec in TOOLS.values()
    ]

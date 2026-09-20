"""Argument schemas for the remediation actions the fix agent can propose.

These double as the JSON Schemas handed to Claude, so a malformed action is
rejected by Pydantic *before* it ever reaches the test gate or an approval
queue.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RestartServiceArgs(BaseModel):
    service: str = Field(description="Name of the service to restart.")
    reason: str = Field(default="", description="Why a restart is expected to help.")


class RollbackDeploymentArgs(BaseModel):
    service: str = Field(description="Name of the service to roll back.")
    target_version: str = Field(description="Version to roll back to, e.g. '1.4.2'.")
    reason: str = Field(default="", description="Why this version is believed to be safe.")


class ScaleServiceArgs(BaseModel):
    service: str = Field(description="Name of the service to scale.")
    replica_count: int = Field(gt=0, le=50, description="Target replica count.")
    reason: str = Field(default="", description="Why this replica count is expected to help.")

    @field_validator("replica_count")
    @classmethod
    def _sane_bound(cls, v: int) -> int:
        if v > 50:
            raise ValueError("replica_count above 50 requires infrastructure sign-off, not a fix")
        return v

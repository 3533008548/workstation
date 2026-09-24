"""SkillManifest -- how a skill declares itself to the base.

The frontmatter fields (``name``/``description``/``version``) are deliberately
kept compatible with the community ``SKILL.md`` shape so that an existing
community skill can be dropped in with only the ``runtime``/``permissions``
block added. That was the single cheapest piece of ecosystem alignment
available: it costs nothing and buys the whole SKILL.md catalogue.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from .base import ContractModel
from .enums import PermissionResource, RuntimeKind
from .run import Budget

__all__ = ["Permission", "RuntimeSpec", "SkillManifest"]


class Permission(ContractModel):
    resource: PermissionResource
    scope: str = ""                 # e.g. "primary/vault/**", "api.deepseek.com"
    requires_confirm: bool = False
    reason: str = ""


class RuntimeSpec(ContractModel):
    kind: RuntimeKind
    entrypoint: str = Field(
        description="python module path | CLI command | http base url | adapter key"
    )
    transport_options: dict[str, Any] = Field(default_factory=dict)
    isolated: bool = Field(
        default=False,
        description="True forces a fresh process per run (PPT: pptxgenjs module-level state)",
    )
    startup_timeout_s: int = Field(default=30, ge=1)


class SkillManifest(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+")
    description: str = Field(min_length=1)
    when_to_use: str = Field(
        default="", description="Trigger guidance -- the router reads this, not the description"
    )

    inputs: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema (draft 2020-12) for TaskRequest.inputs"
    )
    outputs: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema for Run.outputs"
    )

    runtime: RuntimeSpec
    permissions: list[Permission] = Field(default_factory=list)
    default_budget: Budget = Field(default_factory=Budget)

    tags: list[str] = Field(default_factory=list)
    deprecated: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"version must be major.minor.patch, got {v!r}")
        return v

    def permits(self, resource: PermissionResource) -> bool:
        return any(p.resource is resource for p in self.permissions)

    def needs_confirmation(self, resource: PermissionResource) -> bool:
        return any(p.resource is resource and p.requires_confirm for p in self.permissions)

    def writes_primary(self) -> bool:
        return self.permits(PermissionResource.FS_PRIMARY) or self.permits(
            PermissionResource.VAULT_WRITE
        )

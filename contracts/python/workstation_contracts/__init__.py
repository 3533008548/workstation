"""workstation_contracts -- the stage-0 contract surface of the personal AI workbench.

Single source of truth is Python. JSON Schema is generated from these models
(``scripts/gen_schema.py``) and the TypeScript mirror in ``contracts/ts`` is
generated from that schema. Never hand-edit a downstream artefact.
"""

from __future__ import annotations

from .enums import (
    ArtifactKind,
    Confidence,
    EventType,
    FactKind,
    Origin,
    PermissionResource,
    Priority,
    RunStatus,
    RuntimeKind,
    SourceType,
    StepKind,
    StepStatus,
    TERMINAL_RUN_STATUSES,
)
from .fact import Fact, FactSet
from .ids import is_valid_id, new_id
from .run import (
    Artifact,
    Budget,
    Run,
    RunEvent,
    Step,
    TaskOptions,
    TaskRequest,
    Usage,
)
from .skill import Permission, RuntimeSpec, SkillManifest
from .source_ref import Locator, SourceRef, derive_source_id

CONTRACT_VERSION = "0.1.0"

__version__ = CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "Artifact",
    "ArtifactKind",
    "Budget",
    "Confidence",
    "EventType",
    "Fact",
    "FactKind",
    "FactSet",
    "Locator",
    "Origin",
    "Permission",
    "PermissionResource",
    "Priority",
    "Run",
    "RunEvent",
    "RunStatus",
    "RuntimeKind",
    "RuntimeSpec",
    "SkillManifest",
    "SourceRef",
    "SourceType",
    "Step",
    "StepKind",
    "StepStatus",
    "TaskOptions",
    "TaskRequest",
    "TERMINAL_RUN_STATUSES",
    "Usage",
    "derive_source_id",
    "is_valid_id",
    "new_id",
]

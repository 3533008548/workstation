"""Enumerations shared by every contract in this package.

Values are lowercase strings on the wire. Never rename an existing member --
add a new one and deprecate. Renaming is an L3 breaking change for every
adapter (see docs/contracts/03-data-layout.md § 变更影响分级).
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience only
        return str(self.value)


# ---------------------------------------------------------------- provenance


class SourceType(StrEnum):
    """Where a source physically lives."""

    PAPER = "paper"
    NOTE = "note"
    WEB = "web"
    DATASET = "dataset"
    ATTACHMENT = "attachment"
    DECK = "deck"
    GENERATED = "generated"


class Origin(StrEnum):
    """Backup policy class.

    ``primary`` cannot be rebuilt from anything else -- losing it is data loss.
    ``derived`` is a cache and may be deleted at any time.
    """

    PRIMARY = "primary"
    DERIVED = "derived"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class FactKind(StrEnum):
    CLAIM = "claim"
    METRIC = "metric"
    METHOD = "method"
    FINDING = "finding"
    DEFINITION = "definition"
    QUOTE = "quote"
    HYPOTHESIS = "hypothesis"


# --------------------------------------------------------------------- runs


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"      # blocked on external input / approval / rate limit
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"      # some steps produced output, run did not complete


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class StepKind(StrEnum):
    LLM = "llm"
    TOOL = "tool"
    RETRIEVE = "retrieve"
    RENDER = "render"
    VERIFY = "verify"
    WRITE = "write"


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    RUN_PROGRESS = "run.progress"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    STEP_STARTED = "step.started"
    STEP_FINISHED = "step.finished"
    ARTIFACT_PRODUCED = "artifact.produced"
    BUDGET_WARNING = "budget.warning"
    APPROVAL_REQUIRED = "approval.required"


class Priority(StrEnum):
    """Interactive beats batch. Mirrors research_agent's reserved-slot model."""

    INTERACTIVE = "interactive"
    NORMAL = "normal"
    BATCH = "batch"


# ------------------------------------------------------------------ skills


class RuntimeKind(StrEnum):
    INPROCESS = "inprocess"    # python module imported directly
    SUBPROCESS = "subprocess"  # one-shot child process (PPT: pptxgenjs state bleed)
    HTTP = "http"              # long-lived local service (knowledge desktop)
    BRIDGE = "bridge"          # adapter-owned transport, opaque to the base


class PermissionResource(StrEnum):
    LLM = "llm"
    NET = "net"
    FS_PRIMARY = "fs:primary"
    FS_DERIVED = "fs:derived"
    SUBPROCESS = "subprocess"
    VAULT_WRITE = "vault:write"


class ArtifactKind(StrEnum):
    DOCUMENT = "document"
    DECK = "deck"
    MARKDOWN = "markdown"
    IMAGE = "image"
    TABLE = "table"
    JSON = "json"
    REPORT = "report"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.PARTIAL}
)

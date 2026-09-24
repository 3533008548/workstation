"""Run / Step / Artifact / Event -- the execution contract.

A Run is the unit the workbench schedules, streams, budgets, resumes and
audits. It is deliberately stack-agnostic: a LangGraph invocation, a PPT
subprocess render and a knowledge-base HTTP call all produce the same Run
shape, so the UI, the budget governor and the audit trail only ever see one
thing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from .base import ContractModel, utcnow
from .enums import (
    TERMINAL_RUN_STATUSES,
    ArtifactKind,
    EventType,
    Origin,
    Priority,
    RunStatus,
    StepKind,
    StepStatus,
)
from .ids import is_valid_id, new_id

__all__ = [
    "Artifact",
    "Budget",
    "Run",
    "RunEvent",
    "Step",
    "TaskOptions",
    "TaskRequest",
    "Usage",
]


class Usage(ContractModel):
    """Accounting for one step or one whole run."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    cost_cny: float = 0.0
    wall_clock_s: float = 0.0
    retries: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            llm_calls=self.llm_calls + other.llm_calls,
            cost_cny=round(self.cost_cny + other.cost_cny, 6),
            wall_clock_s=round(self.wall_clock_s + other.wall_clock_s, 3),
            retries=self.retries + other.retries,
        )


class Budget(ContractModel):
    """Hard ceilings. Exceeding one is a run failure, never a silent overrun."""

    max_tokens: int | None = None
    max_cost_cny: float | None = None
    max_tool_calls: int | None = None
    max_wall_clock_s: float | None = None
    max_llm_calls: int | None = None

    def breach(self, usage: Usage) -> str | None:
        """Return the name of the first violated ceiling, else None."""
        checks: list[tuple[str, float | None, float | None]] = [
            ("max_tokens", self.max_tokens, float(usage.prompt_tokens + usage.completion_tokens)),
            ("max_cost_cny", self.max_cost_cny, usage.cost_cny),
            ("max_tool_calls", self.max_tool_calls, float(usage.tool_calls)),
            ("max_wall_clock_s", self.max_wall_clock_s, usage.wall_clock_s),
            ("max_llm_calls", self.max_llm_calls, float(usage.llm_calls)),
        ]
        for name, limit, actual in checks:
            if limit is not None and actual is not None and actual > limit:
                return name
        return None


class TaskOptions(ContractModel):
    priority: Priority = Priority.NORMAL
    timeout_s: int | None = Field(default=None, ge=1)
    budget: Budget = Field(default_factory=Budget)
    idempotency_key: str | None = Field(
        default=None, description="Re-issuing the same key returns the existing run"
    )
    dry_run: bool = False
    require_approval: bool = Field(
        default=False, description="Block before any fs:primary or vault:write side effect"
    )
    locale: str = "zh-CN"


class TaskRequest(ContractModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    skill: str = Field(description="Skill name, must match SkillManifest.name")
    inputs: dict[str, Any] = Field(default_factory=dict)
    options: TaskOptions = Field(default_factory=TaskOptions)
    parent_run_id: str | None = None
    requested_at: datetime = Field(default_factory=utcnow)
    context: str = Field(
        default="default",
        description=(
            "Scenario/domain partition. The workbench is a multi-scenario entry, "
            "NOT a merged memory store (red line #5): thesis vs interview must never "
            "contaminate each other. Known values: default | thesis | interview. "
            "Retrieval is scoped to this context unless cross_context is requested."
        ),
    )


class Step(ContractModel):
    step_id: str = Field(default_factory=lambda: new_id("step"))
    name: str
    kind: StepKind = StepKind.TOOL
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    usage: Usage = Field(default_factory=Usage)
    message: str | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        if self.started_at and self.ended_at:
            return round((self.ended_at - self.started_at).total_seconds(), 3)
        return 0.0

    def finish(self, status: StepStatus, *, message: str | None = None, error: str | None = None) -> None:
        self.status = status
        self.ended_at = utcnow()
        if message is not None:
            self.message = message
        if error is not None:
            self.error = error


class Artifact(ContractModel):
    artifact_id: str = Field(default_factory=lambda: new_id("art"))
    kind: ArtifactKind = ArtifactKind.DOCUMENT
    uri: str = Field(description="Path relative to WORKSTATION_HOME, or absolute URL")
    media_type: str = "application/octet-stream"
    origin: Origin = Origin.DERIVED
    producer: str = ""
    title: str = ""
    size_bytes: int | None = None
    source_ids: list[str] = Field(
        default_factory=list, description="SourceRef ids this artifact is derived from"
    )
    fact_ids: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class Run(ContractModel):
    """One execution of one skill."""

    run_id: str = Field(default_factory=lambda: new_id("run"))
    task_id: str | None = None
    skill: str
    skill_version: str = "0.0.0"
    context: str = "default"
    status: RunStatus = RunStatus.PENDING

    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    options: TaskOptions = Field(default_factory=TaskOptions)
    steps: list[Step] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)

    source_ids: list[str] = Field(
        default_factory=list, description="Every SourceRef touched, for citation back-fill"
    )

    parent_run_id: str | None = None
    checkpoint_ref: str | None = Field(
        default=None, description="langgraph thread id / subprocess workdir / opaque token"
    )
    idempotency_key: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @field_validator("parent_run_id")
    @classmethod
    def _check_parent(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_id(v, "run"):
            raise ValueError(f"parent_run_id must look like run_*, got {v!r}")
        return v

    @model_validator(mode="after")
    def _terminal_consistency(self) -> "Run":
        if self.status in TERMINAL_RUN_STATUSES and self.ended_at is None:
            object.__setattr__(self, "ended_at", utcnow())
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    @property
    def duration_s(self) -> float:
        if self.started_at and self.ended_at:
            return round((self.ended_at - self.started_at).total_seconds(), 3)
        return 0.0

    def recompute_usage(self) -> Usage:
        total = Usage()
        for step in self.steps:
            total = total.add(step.usage)
        self.usage = total
        return total

    def budget_breach(self) -> str | None:
        return self.options.budget.breach(self.usage)

    def add_step(self, name: str, kind: StepKind = StepKind.TOOL) -> Step:
        step = Step(name=name, kind=kind, status=StepStatus.RUNNING, started_at=utcnow())
        self.steps.append(step)
        return step

    def transition(self, status: RunStatus, *, error: str | None = None) -> None:
        if self.is_terminal and status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"run {self.run_id} is already terminal ({self.status})")
        self.status = status
        self.updated_at = utcnow()
        if status is RunStatus.RUNNING and self.started_at is None:
            self.started_at = self.updated_at
        if status in TERMINAL_RUN_STATUSES:
            self.ended_at = self.updated_at
        if error is not None:
            self.error = error

    @classmethod
    def from_request(cls, req: TaskRequest) -> "Run":
        return cls(
            task_id=req.task_id,
            skill=req.skill,
            inputs=req.inputs,
            options=req.options,
            parent_run_id=req.parent_run_id,
            idempotency_key=req.options.idempotency_key,
            context=req.context,
        )


class RunEvent(ContractModel):
    """SSE wire shape. The entire frontend contract lives here."""

    event_id: str = Field(default_factory=lambda: new_id("evt"))
    run_id: str
    seq: int = Field(ge=0)
    ts: datetime = Field(default_factory=utcnow)
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _check_run(cls, v: str) -> str:
        if not is_valid_id(v, "run"):
            raise ValueError(f"run_id must look like run_*, got {v!r}")
        return v

    def to_sse(self) -> str:
        """Serialize as one Server-Sent Events frame (data on a single line)."""
        import json

        body = self.model_dump_json()
        return f"event: {self.type.value}\nid: {self.seq}\ndata: {body}\n\n"

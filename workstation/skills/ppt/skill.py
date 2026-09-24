"""PPT 技能 —— 把 PPTAgent 接成底座的一个技能。

一条调用链，从底座的契约一路到 `.pptx`：

    工作台 Fact（带 source_ids）
        → 适配器逐字段映射成 BridgeFact
        → 页数预检（不划算就在这里停，不起子进程）
        → 写 request.json，起一次性 Node 子进程
        → 读 response.json（extra="forbid"，字段漂移在这里炸）
        → Run / Step / Artifact 回填

三处刻意的选择：

1. **不做静默兜底**。映射只按显式表走，confidence 里 `unknown` 归为
   ``low``（即"不可引用"），不猜、不补默认来源。三方多给字段也不会被透传。
2. **失败也是 Run**。子进程没起、超时、被门禁拒绝、校验不过，都会返回一个
   terminal 状态的 Run，而不是抛异常穿透调用方。审计链要求"每一次意图
   执行"都留下记录，包括失败的那些。
3. **成本记账是不完整的，而且明说**。PPT 子进程自己调模型，底座看不到它的
   token 与费用，只能拿到调用次数。`Run.outputs["cost_accounting"]` 直接
   标成 ``partial``，不假装账是平的（对应 REVIEW.md 已知缺陷 #4）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from workstation_contracts import (
    Artifact,
    ArtifactKind,
    Confidence,
    Fact,
    FactSet,
    Origin,
    Permission,
    PermissionResource,
    Run,
    RunStatus,
    RuntimeKind,
    RuntimeSpec,
    SkillManifest,
    StepKind,
    StepStatus,
    TaskRequest,
    Usage,
)
from workstation_contracts.base import ContractModel

from .contract import (
    BRIDGE_API_VERSION,
    BridgeBrief,
    BridgeFact,
    BridgeOutput,
    BridgePagePlan,
    BridgeRequest,
    BridgeResponse,
    BridgeTheme,
)
from .precheck import DeckFeasibility, assess_deck_feasibility
from .runner import Runner, RunnerError, RunnerResult

__all__ = ["PPT_SKILL_NAME", "PptSkill", "PptSkillConfig", "PptTaskInput"]

PPT_SKILL_NAME = "ppt-deck"

#: confidence 映射表。`unknown` 归为 low：不确定来源的事实不该被渲染进交付物。
_CONFIDENCE_MAP: dict[str, Literal["high", "medium", "low"]] = {
    Confidence.HIGH.value: "high",
    Confidence.MEDIUM.value: "medium",
    Confidence.LOW.value: "low",
    Confidence.UNKNOWN.value: "low",
}


class PptTaskInput(ContractModel):
    """`TaskRequest.inputs` 的形状。站在底座这一侧看，输入是契约 Fact。"""

    brief: BridgeBrief
    facts: list[Fact] = Field(default_factory=list)
    material: str | None = Field(
        default=None, description="文本素材原文；会被 PPTAgent 包进不可信边界"
    )
    mode: Literal["plan", "render"] = "plan"
    deck_spec: dict[str, Any] | None = Field(
        default=None, description="mode=render 时必填"
    )
    requested_pages: int | None = Field(default=None, gt=0)
    facts_per_page: float = Field(default=3.0, gt=0)
    max_pages: int | None = Field(default=None, gt=0)
    accept_padding: bool = Field(
        default=False, description="显式接受框架稿；不设就是拒绝超量页数"
    )
    theme: BridgeTheme | None = None


@dataclass(frozen=True)
class PptSkillConfig:
    workspace_home: Path
    ppt_agent_root: Path
    #: 命令前缀。默认走 npx tsx（开发态）；发布态应改为编译产物，例如
    #: ``command=("node",), cli_entry="dist/cli.js"``。
    command: tuple[str, ...] = ("npx", "tsx")
    cli_entry: str = "src/cli.ts"
    timeout_s: float = 600.0

    @property
    def runs_root(self) -> Path:
        return self.workspace_home / "derived" / "runs"

    @property
    def decks_root(self) -> Path:
        return self.workspace_home / "primary" / "decks"


def ppt_skill_manifest(config: PptSkillConfig) -> SkillManifest:
    return SkillManifest(
        name=PPT_SKILL_NAME,
        version="0.1.0",
        description="由已登记事实与 Brief 规划并渲染 .pptx（PPTAgent 桥接）",
        when_to_use=(
            "需要产出可编辑的 PowerPoint 交付物时；先用 FactSet 判断信息量是否"
            "支撑目标页数，再调用本技能"
        ),
        inputs={
            "type": "object",
            "required": ["brief"],
            "properties": {
                "brief": {"type": "object"},
                "facts": {"type": "array", "items": {"type": "object"}},
                "material": {"type": "string"},
                "mode": {"enum": ["plan", "render"]},
                "requested_pages": {"type": ["integer", "null"]},
                "facts_per_page": {"type": "number"},
                "max_pages": {"type": ["integer", "null"]},
                "accept_padding": {"type": "boolean"},
            },
        },
        outputs={
            "type": "object",
            "properties": {
                "pptx_path": {"type": "string"},
                "deck_path": {"type": "string"},
                "slide_count": {"type": "integer"},
                "used_fact_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        runtime=RuntimeSpec(
            kind=RuntimeKind.SUBPROCESS,
            entrypoint=" ".join([*config.command, config.cli_entry, "bridge"]),
            transport_options={
                "api_version": BRIDGE_API_VERSION,
                "cwd": str(config.ppt_agent_root),
                "request_file": "request.json",
                "response_file": "response.json",
            },
            # 硬约束：pptxgenjs 有模块级状态串扰，必须每次一个新进程。
            isolated=True,
            startup_timeout_s=40,
        ),
        permissions=[
            Permission(
                resource=PermissionResource.SUBPROCESS,
                scope="node",
                reason="渲染必须进程隔离（pptxgenjs 模块级状态）",
            ),
            Permission(
                resource=PermissionResource.FS_DERIVED,
                scope="derived/runs/**",
                reason="request/response/报告等中间态",
            ),
            Permission(
                resource=PermissionResource.FS_PRIMARY,
                scope="primary/decks/**",
                requires_confirm=True,
                reason="deck 是不可重建的交付物，写入 primary 需审批",
            ),
            Permission(
                resource=PermissionResource.LLM,
                scope="ppt-agent model routes",
                reason="PPTAgent 目前自持模型客户端，尚未走统一网关",
            ),
        ],
        tags=["ppt", "deck", "bridge", "isolated"],
        meta={
            "adapter": "workstation.skills.ppt",
            "upstream": "PPTAgent",
            "known_gap": "ppt-side llm tokens/cost are not visible to the base",
        },
    )


def _to_bridge_fact(fact: Fact) -> BridgeFact:
    """契约 Fact → 线上 BridgeFact。逐字段映射，不多不少。"""
    return BridgeFact(
        fact_id=fact.fact_id,
        text=fact.text,
        source_ids=list(fact.source_ids),
        confidence=_CONFIDENCE_MAP[Confidence(fact.confidence).value],
        kind=fact.kind.value if hasattr(fact.kind, "value") else str(fact.kind),
        qualifiers=dict(fact.qualifiers) or None,
        evidence=fact.evidence,
    )


class PptSkill:
    """PPT 技能执行器。一个实例可反复执行，但每次都起新进程。"""

    def __init__(self, config: PptSkillConfig, runner: Runner) -> None:
        self._config = config
        self._runner = runner
        self.manifest = ppt_skill_manifest(config)

    # ---------------------------------------------------------------- 执行

    def execute(self, request: TaskRequest) -> Run:
        run = Run.from_request(request)
        run.skill = self.manifest.name
        run.skill_version = self.manifest.version
        run.transition(RunStatus.RUNNING)

        # 审批门禁：写 primary 前必须有人点头。本阶段只做阻塞与可观测，
        # 审批流本身（队列 + UI）属于阶段 5。
        needs_approval = self.manifest.needs_confirmation(PermissionResource.FS_PRIMARY)
        if request.options.require_approval and needs_approval:
            gate = run.add_step("approval-gate", StepKind.WRITE)
            gate.finish(StepStatus.SUCCEEDED, message="waiting for fs:primary approval")
            run.outputs = {
                "pending_approval": PermissionResource.FS_PRIMARY.value,
                "scope": "primary/decks/**",
            }
            run.transition(RunStatus.WAITING)
            return run

        preflight = run.add_step("page-budget-preflight", StepKind.VERIFY)

        try:
            task = PptTaskInput.model_validate(request.inputs)
        except ValidationError as exc:
            preflight.finish(StepStatus.FAILED, error=str(exc))
            run.transition(RunStatus.FAILED, error=f"INVALID_TASK_INPUT: {exc}")
            return run

        fact_set = FactSet(facts=list(task.facts))
        feasibility = assess_deck_feasibility(
            fact_set,
            requested_pages=task.requested_pages,
            facts_per_page=task.facts_per_page,
            max_pages=task.max_pages,
            accept_padding=task.accept_padding,
        )

        if feasibility.is_blocking:
            preflight.finish(
                StepStatus.FAILED,
                message=feasibility.verdict.value,
                error=feasibility.describe(),
            )
            run.outputs = {
                "page_budget": _feasibility_payload(feasibility),
                "options": feasibility.options,
            }
            run.source_ids = sorted(
                {source_id for fact in fact_set.facts for source_id in fact.source_ids}
            )
            run.transition(
                RunStatus.FAILED,
                error=f"PAGE_BUDGET_{feasibility.verdict.value.upper()}: {feasibility.describe()}",
            )
            return run

        preflight.finish(
            StepStatus.SUCCEEDED,
            message=f"{feasibility.verdict.value} → {feasibility.effective_pages} pages",
        )

        workdir = self._config.runs_root / run.run_id
        deck_dir = self._config.decks_root / run.run_id
        bridge_request = self._build_request(task, run.run_id, workdir, deck_dir)

        if request.options.dry_run:
            dry = run.add_step("dry-run", StepKind.VERIFY)
            dry.finish(StepStatus.SUCCEEDED, message="request assembled; nothing executed")
            run.outputs = {
                "page_budget": _feasibility_payload(feasibility),
                "bridge_request": bridge_request.to_wire(),
                "executed": False,
            }
            run.transition(RunStatus.SUCCEEDED)
            return run

        workdir.mkdir(parents=True, exist_ok=True)
        request_path = workdir / "request.json"
        response_path = workdir / "response.json"
        request_path.write_text(
            json.dumps(bridge_request.to_wire(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        step = run.add_step("plan-and-render", StepKind.RENDER)
        argv = [*self._config.command, self._config.cli_entry, "bridge", str(request_path), str(response_path)]
        started = time.monotonic()
        try:
            result = self._runner.run(
                argv, cwd=str(self._config.ppt_agent_root), timeout_s=self._config.timeout_s
            )
        except RunnerError as exc:
            step.usage = Usage(wall_clock_s=round(time.monotonic() - started, 3), tool_calls=1)
            step.finish(StepStatus.FAILED, error=str(exc))
            run.recompute_usage()
            run.transition(RunStatus.FAILED, error=f"PPT_RUNNER_UNAVAILABLE: {exc}")
            return run

        elapsed = round(time.monotonic() - started, 3)
        response, parse_error = _read_response(response_path, result)
        if parse_error is not None:
            step.usage = Usage(wall_clock_s=elapsed, tool_calls=1)
            step.finish(StepStatus.FAILED, error=parse_error)
            run.recompute_usage()
            run.transition(RunStatus.FAILED, error=parse_error)
            return run

        assert response is not None  # parse_error 为 None 时必然有 response
        step.usage = Usage(
            wall_clock_s=elapsed,
            tool_calls=1,
            llm_calls=len(response.model_trace or []),
        )

        if not response.ok:
            step.finish(StepStatus.FAILED, error=response.describe_failure())
            run.recompute_usage()
            run.outputs = _success_payload(response, feasibility)
            run.source_ids = response.source_ids
            run.transition(RunStatus.FAILED, error=response.describe_failure())
            return run

        step.finish(StepStatus.SUCCEEDED, message=f"{response.slide_count} slides")
        run.artifacts.append(_deck_artifact(response, run.run_id))
        run.source_ids = response.source_ids
        run.outputs = _success_payload(response, feasibility)
        run.recompute_usage()

        breach = run.budget_breach()
        if breach is not None:
            run.transition(
                RunStatus.FAILED,
                error=f"BUDGET_EXCEEDED: {breach} (usage={run.usage.model_dump()})",
            )
            return run

        run.transition(RunStatus.SUCCEEDED)
        return run

    # -------------------------------------------------------------- 内部

    def _build_request(
        self,
        task: PptTaskInput,
        run_id: str,
        workdir: Path,
        deck_dir: Path,
    ) -> BridgeRequest:
        return BridgeRequest(
            mode=task.mode,
            brief=task.brief,
            facts=[_to_bridge_fact(fact) for fact in task.facts],
            material=task.material,
            page_plan=BridgePagePlan(
                requested=task.requested_pages,
                facts_per_page=task.facts_per_page,
                max_pages=task.max_pages,
                accept_padding=task.accept_padding,
            ),
            theme=task.theme,
            deck_spec=task.deck_spec,
            output=BridgeOutput(
                pptx_path=str(deck_dir / "deck.pptx"),
                deck_path=str(deck_dir / "deck.spec.json"),
                report_path=str(workdir / "render.report.json"),
            ),
            run_id=run_id,
        )


def _feasibility_payload(feasibility: DeckFeasibility) -> dict[str, Any]:
    return {
        "verdict": feasibility.verdict.value,
        "requested_pages": feasibility.requested_pages,
        "recommended_pages": feasibility.recommended_pages,
        "effective_pages": feasibility.effective_pages,
        "total_facts": feasibility.total_facts,
        "citable_facts": feasibility.citable_facts,
        "facts_per_page": feasibility.facts_per_page,
        "density": feasibility.density,
        "reasons": list(feasibility.reasons),
    }


def _success_payload(response: BridgeResponse, feasibility: DeckFeasibility) -> dict[str, Any]:
    return {
        "slide_count": response.slide_count,
        "page_ids": response.page_ids,
        "used_fact_ids": response.used_fact_ids,
        "pptx_path": response.artifacts.pptx_path if response.artifacts else None,
        "deck_path": response.artifacts.deck_path if response.artifacts else None,
        "page_budget": _feasibility_payload(feasibility),
        "injection_warnings": [
            warning.model_dump() for warning in response.injection_warnings or []
        ],
        "model_trace": [item.model_dump() for item in response.model_trace or []],
        "warnings": list(response.warnings or []),
        # 明说账不平：PPT 子进程自己调模型，token 与费用对本进程不可见。
        "cost_accounting": "partial",
        "unmetered": ["llm_prompt_tokens", "llm_completion_tokens", "llm_cost_cny"],
    }


def _deck_artifact(response: BridgeResponse, run_id: str) -> Artifact:
    artifacts = response.artifacts
    pptx_path = artifacts.pptx_path if artifacts else ""
    return Artifact(
        kind=ArtifactKind.DECK,
        uri=pptx_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        origin=Origin.DERIVED,
        producer=PPT_SKILL_NAME,
        title=Path(pptx_path).name,
        source_ids=response.source_ids,
        fact_ids=response.used_fact_ids,
        meta={
            "run_id": run_id,
            "slide_count": response.slide_count,
            "page_ids": response.page_ids or [],
            "deck_spec": artifacts.deck_path if artifacts else None,
        },
    )


def _read_response(
    response_path: Path, result: RunnerResult
) -> tuple[BridgeResponse | None, str | None]:
    """解析子进程产出的响应。任何偏差都必须变成可读的错误，而不是异常栈。"""
    if not response_path.exists():
        tail = (result.stderr or result.stdout or "").strip()[-800:]
        return None, (
            f"PPT_BRIDGE_NO_RESPONSE: exit_code={result.exit_code}, "
            f"response file {response_path.name} was not written. stderr tail: {tail!r}"
        )

    try:
        raw = json.loads(response_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"PPT_BRIDGE_BAD_RESPONSE: response is not valid JSON: {exc}"

    try:
        response = BridgeResponse.model_validate(raw)
    except ValidationError as exc:
        # 字段漂移会落到这里：PPTAgent 改了响应形状，红灯亮在工作台侧。
        return None, f"PPT_BRIDGE_CONTRACT_DRIFT: response does not match ppt-bridge/1: {exc}"

    # 形状上 verification 是可选的（失败响应没有），但"成功"必须自带校验结论：
    # 一个声称 ok 却不给出校验结果的响应，等于要求调用方盲信。
    if response.ok and (response.spec_verification is None or response.pptx_verification is None):
        return None, (
            "PPT_BRIDGE_CONTRACT_DRIFT: ok=true response is missing specVerification "
            "or pptxVerification; a success without verification is not acceptable"
        )

    return response, None

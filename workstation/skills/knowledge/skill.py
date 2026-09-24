"""知识库检索技能 —— 把「知识环」接成底座的一个只读技能。

一条调用链，从底座的契约一路到检索结果：

    查询 + Vault 路径
        → 适配器逐字段映射成 BridgeRequest
        → 检索预检（vault 不存在 / query 为空就在这里停，不起子进程）
        → 写 request.json，起一次性 Node 子进程（knowledge-bridge/1）
        → 读 response.json（extra="forbid"，字段漂移在这里炸）
        → 把 KB 的 SourceRef 映射成底座 SourceRef（derive_source_id 统一去重）
        → Run / Step / Artifact 回填

与 PPT 技能的三处对齐：

1. **失败也是 Run**。子进程没起、超时、校验不过、检索失败，都会返回一个
   terminal 状态的 Run，而不是抛异常穿透调用方——审计链要求"每一次意图
   执行"都留下记录，包括失败的那些。
2. **只读，所以权限面最小**。检索只读本地 Markdown + CPU 计算，不写 Vault、
   不调模型、不出网。因此 manifest 只声明 `subprocess` + `fs:derived`，
   没有 `fs:primary`（Vault 唯一写入通道仍归知识库桌面端）、没有 `llm`、
   没有 `net`。这正是阶段 3 相对 PPT 最干净的地方。
3. **成本记账是 none，不是 partial**。PPT 子进程自己调模型，账不平；知识库
   检索是纯本地计算，没有 token / 费用，直接标 `none`，不假装。
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
    Locator,
    Origin,
    Permission,
    PermissionResource,
    Run,
    RunStatus,
    RuntimeKind,
    RuntimeSpec,
    SkillManifest,
    SourceRef,
    SourceType,
    StepKind,
    StepStatus,
    TaskRequest,
    Usage,
    derive_source_id,
)
from workstation_contracts.base import ContractModel

from .contract import BRIDGE_API_VERSION, BridgeRequest, BridgeResponse, BridgeResult
from .precheck import SearchFeasibility, assess_search
from .runner import Runner, RunnerResult
from workstation.core.runtime.executor import SubprocessExecutor, SubprocessJob

__all__ = ["KNOWLEDGE_SKILL_NAME", "KnowledgeSkill", "KnowledgeSkillConfig", "KnowledgeTaskInput"]

KNOWLEDGE_SKILL_NAME = "knowledge-search"

#: KB 的 SourceType 概念 → 底座 SourceType。KB 没有 paper/dataset/deck，落到最近的语义。
_KB_SOURCE_TYPE_MAP: dict[str, SourceType] = {
    "note": SourceType.NOTE,
    "pdf": SourceType.ATTACHMENT,
    "image": SourceType.ATTACHMENT,
    "web": SourceType.WEB,
    "conversation": SourceType.GENERATED,
}


class KnowledgeTaskInput(ContractModel):
    """`TaskRequest.inputs` 的形状。站在底座这一侧看，输入是"查什么、在哪查"。"""

    vault: str = Field(min_length=1, description="Vault 绝对路径（Windows 原生格式）")
    query: str = Field(min_length=1)
    limit: int = Field(default=8, gt=0)
    scope: list[str] | None = Field(
        default=None, description="仅在这些文件夹内检索；null = 全库"
    )


@dataclass(frozen=True)
class KnowledgeSkillConfig:
    workspace_home: Path
    knowledge_root: Path
    #: 命令前缀。默认走 `npm --prefix <kb> run bridge --`（esbuild 已是其 devDep）；
    #: 发布态可改为预编译产物，例如 command=("node",), cli_entry="dist/kb-bridge.mjs"。
    command: tuple[str, ...] | None = None
    timeout_s: float = 120.0

    @property
    def runs_root(self) -> Path:
        return self.workspace_home / "derived" / "runs"


def knowledge_skill_manifest(config: KnowledgeSkillConfig) -> SkillManifest:
    default_command = (
        "npm",
        "--prefix",
        str(config.knowledge_root),
        "run",
        "bridge",
        "--",
    )
    return SkillManifest(
        name=KNOWLEDGE_SKILL_NAME,
        version="0.1.0",
        description="对知识库 Vault 做只读 Markdown 检索（knowledge-bridge/1 桥接）",
        when_to_use=(
            "需要检索本地知识库笔记、作为事实或素材来源时；返回带 SourceRef 的"
            "排名片段，供上层引用或沉淀"
        ),
        inputs={
            "type": "object",
            "required": ["vault", "query"],
            "properties": {
                "vault": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "scope": {"type": ["array", "null"], "items": {"type": "string"}},
            },
        },
        outputs={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "hit_count": {"type": "integer"},
                "indexed_files": {"type": "integer"},
                "chunk_count": {"type": "integer"},
                "hits": {"type": "array", "items": {"type": "object"}},
            },
        },
        runtime=RuntimeSpec(
            kind=RuntimeKind.SUBPROCESS,
            entrypoint=" ".join(default_command),
            transport_options={
                "api_version": BRIDGE_API_VERSION,
                "cwd": str(config.knowledge_root),
                "request_file": "request.json",
                "response_file": "response.json",
            },
            # TS 技能本就不能在 Python 底座内联，必须独立进程；检索又是只读，
            # 无模块级状态，isolated 只声明"始终是独立进程"。
            isolated=True,
            startup_timeout_s=30,
        ),
        permissions=[
            Permission(
                resource=PermissionResource.SUBPROCESS,
                scope="node",
                reason="检索在独立 Node 进程内运行（知识库 core/ 零 electron 依赖）",
            ),
            Permission(
                resource=PermissionResource.FS_DERIVED,
                scope="derived/runs/**",
                reason="request/response 等中间态",
            ),
        ],
        tags=["knowledge", "search", "bridge", "read-only"],
        meta={
            "adapter": "workstation.skills.knowledge",
            "upstream": "knowledge-loop-desktop",
            "known_gap": "read-only; Vault write-back (sink) not yet wired",
        },
    )


def _to_source_ref(result: BridgeResult) -> SourceRef:
    """KB 的 SourceRef → 底座 SourceRef。统一经由 derive_source_id 去重。"""
    src = result.chunk.source
    uri = src.path_or_url
    source_id = derive_source_id(uri, src.content_hash or None)
    source_type = _KB_SOURCE_TYPE_MAP.get(src.type or "note", SourceType.NOTE)
    title = Path(uri).stem
    locator = Locator(
        section=result.chunk.heading,
        line_start=result.chunk.start_line or None,
        line_end=result.chunk.end_line or None,
    )
    return SourceRef.mint(
        source_type=source_type,
        uri=uri,
        title=title,
        producer="knowledge.search",
        origin=Origin.PRIMARY,
        content_hash=src.content_hash or None,
        locator=locator,
        meta={"kb_locator": src.locator, "kb_parser_version": src.parser_version},
    )


class KnowledgeSkill:
    """知识库检索技能执行器。一个实例可反复执行，但每次都起新进程。"""

    def __init__(
        self,
        config: KnowledgeSkillConfig,
        runner: Runner,
        executor: SubprocessExecutor | None = None,
    ) -> None:
        self._config = config
        self._runner = runner
        self._executor = executor or SubprocessExecutor(runner)
        self.manifest = knowledge_skill_manifest(config)

    # ---------------------------------------------------------------- 执行

    def execute(self, request: TaskRequest) -> Run:
        run = Run.from_request(request)
        run.skill = self.manifest.name
        run.skill_version = self.manifest.version
        run.transition(RunStatus.RUNNING)

        preflight = run.add_step("search-preflight", StepKind.VERIFY)
        try:
            task = KnowledgeTaskInput.model_validate(request.inputs)
        except ValidationError as exc:
            preflight.finish(StepStatus.FAILED, error=str(exc))
            run.transition(RunStatus.FAILED, error=f"INVALID_TASK_INPUT: {exc}")
            return run

        feasibility = assess_search(task.vault, task.query)
        if feasibility.is_blocking:
            preflight.finish(
                StepStatus.FAILED,
                message=feasibility.verdict.value,
                error=feasibility.describe(),
            )
            run.transition(
                RunStatus.FAILED,
                error=f"SEARCH_{feasibility.verdict.value.upper()}: {feasibility.describe()}",
            )
            return run
        preflight.finish(StepStatus.SUCCEEDED, message=f"vault ok → query={task.query!r}")

        workdir = self._config.runs_root / run.run_id
        bridge_request = BridgeRequest(
            vault=task.vault,
            query=task.query,
            limit=task.limit,
            scope=task.scope,
        )

        if request.options.dry_run:
            dry = run.add_step("dry-run", StepKind.VERIFY)
            dry.finish(StepStatus.SUCCEEDED, message="request assembled; nothing executed")
            run.outputs = {
                "query": task.query,
                "limit": task.limit,
                "scope": task.scope,
                "bridge_request": bridge_request.to_wire(),
                "executed": False,
                "cost_accounting": "none",
            }
            run.transition(RunStatus.SUCCEEDED)
            return run

        workdir.mkdir(parents=True, exist_ok=True)
        # workdir 就是这个 Run 的恢复句柄：request.json 已完整描述这次检索，
        # 进程中途挂掉也能靠它原位重放。
        run.checkpoint_ref = str(workdir)
        request_path = workdir / "request.json"
        response_path = workdir / "response.json"
        request_path.write_text(
            json.dumps(bridge_request.to_wire(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        step = run.add_step("knowledge-search", StepKind.RETRIEVE)
        base_command = self._config.command or (
            "npm",
            "--prefix",
            str(self._config.knowledge_root),
            "run",
            "bridge",
            "--",
        )
        argv = [*base_command, str(request_path), str(response_path)]
        outcome = self._executor.execute(
            SubprocessJob(
                argv=tuple(argv),
                cwd=str(self._config.knowledge_root),
                timeout_s=self._config.timeout_s,
                checkpoint_ref=str(workdir),
            )
        )
        elapsed = outcome.duration_s

        # 124 超时 / 127 执行器不可用：都是"根本没跑起来"，不是业务失败。
        if outcome.exit_code in (124, 127):
            step.usage = Usage(wall_clock_s=elapsed, tool_calls=1)
            step.finish(StepStatus.FAILED, error=outcome.error)
            run.recompute_usage()
            run.transition(RunStatus.FAILED, error=f"KNOWLEDGE_RUNNER_UNAVAILABLE: {outcome.error}")
            return run

        result = RunnerResult(outcome.exit_code, outcome.stdout, outcome.stderr)
        response, parse_error = _read_response(response_path, result)
        if parse_error is not None:
            step.usage = Usage(wall_clock_s=elapsed, tool_calls=1)
            step.finish(StepStatus.FAILED, error=parse_error)
            run.recompute_usage()
            run.transition(RunStatus.FAILED, error=parse_error)
            return run

        assert response is not None  # parse_error 为 None 时必然有 response
        step.usage = Usage(wall_clock_s=elapsed, tool_calls=1)

        if not response.ok:
            step.finish(StepStatus.FAILED, error=response.describe_failure())
            run.recompute_usage()
            run.outputs = _success_payload(
                response, query=task.query, limit=task.limit
            )
            run.transition(RunStatus.FAILED, error=response.describe_failure())
            return run

        source_refs = [_to_source_ref(item) for item in response.results]
        hits = [_hit(item, ref) for item, ref in zip(response.results, source_refs)]
        source_ids = sorted({ref.source_id for ref in source_refs})

        step.finish(StepStatus.SUCCEEDED, message=f"{len(response.results)} hits / {response.indexed_files} files")
        run.artifacts.append(_search_artifact(response, run.run_id, response_path, source_ids, hits))
        run.source_ids = source_ids
        run.outputs = _success_payload(response, query=task.query, limit=task.limit, hits=hits)
        run.recompute_usage()
        run.transition(RunStatus.SUCCEEDED)
        return run

    # -------------------------------------------------------------- 内部


def _hit(result: BridgeResult, ref: SourceRef) -> dict[str, Any]:
    return {
        "source_id": ref.source_id,
        "title": ref.title,
        "source_type": ref.source_type.value,
        "uri": ref.uri,
        "score": result.score,
        "heading": result.chunk.heading,
        "locator": result.chunk.source.locator,
        "start_line": result.chunk.start_line,
        "end_line": result.chunk.end_line,
        "excerpt": result.excerpt,
    }


def _success_payload(
    response: BridgeResponse,
    *,
    query: str | None = None,
    limit: int | None = None,
    hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "query": query,
        "limit": limit,
        "indexed_files": response.indexed_files,
        "skipped_files": response.skipped_files,
        "chunk_count": response.chunk_count,
        "elapsed_ms": response.elapsed_ms,
        "hit_count": len(response.results),
        "hits": hits if hits is not None else [],
        # 纯本地只读检索，没有 token / 费用，账是平的。
        "cost_accounting": "none",
        "unmetered": [],
    }


def _search_artifact(
    response: BridgeResponse,
    run_id: str,
    response_path: Path,
    source_ids: list[str],
    hits: list[dict[str, Any]],
) -> Artifact:
    return Artifact(
        kind=ArtifactKind.JSON,
        uri=str(response_path),
        media_type="application/json",
        origin=Origin.DERIVED,
        producer=KNOWLEDGE_SKILL_NAME,
        title="knowledge-search-report",
        source_ids=source_ids,
        meta={
            "run_id": run_id,
            "indexed_files": response.indexed_files,
            "chunk_count": response.chunk_count,
            "hit_count": len(response.results),
            "hits": hits,
        },
    )


def _read_response(
    response_path: Path, result: RunnerResult
) -> tuple[BridgeResponse | None, str | None]:
    """解析子进程产出的响应。任何偏差都必须变成可读的错误，而不是异常栈。"""
    if not response_path.exists():
        tail = (result.stderr or result.stdout or "").strip()[-800:]
        return None, (
            f"KNOWLEDGE_BRIDGE_NO_RESPONSE: exit_code={result.exit_code}, "
            f"response file {response_path.name} was not written. stderr tail: {tail!r}"
        )

    try:
        raw = json.loads(response_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"KNOWLEDGE_BRIDGE_BAD_RESPONSE: response is not valid JSON: {exc}"

    try:
        response = BridgeResponse.model_validate(raw)
    except ValidationError as exc:
        # 字段漂移会落到这里：知识库改了响应形状，红灯亮在工作台侧。
        return None, f"KNOWLEDGE_BRIDGE_CONTRACT_DRIFT: response does not match knowledge-bridge/1: {exc}"

    if not response.ok and response.error_code is None and response.error is None:
        return None, "KNOWLEDGE_BRIDGE_CONTRACT_DRIFT: failed response missing error_code/error"

    return response, None

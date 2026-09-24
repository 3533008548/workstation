"""ResearchSkill —— 把科研助手的「深度研究」接成工作台的一个技能（毕设域）。

关键设计：**对端是异步的**。`POST /runs` 返回 202 + 句柄，研究在容器里跑。
所以工作台这个 Run 代表「提交」这一件事：

* `checkpoint_ref` = 对端 run 的 stream_url（跨进程恢复句柄，阶段 1b 的语义）；
* `outputs["agent_run_id"]` = 对端 run_id；
* `resume()`（实现阶段 1b 的 `Resumable`）轮询对端，把终态并回工作台 Run。

这样「提交」和「跑完」是两件事但同一个 Run，进程重启后仍能靠句柄接上。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workstation_contracts import (
    Budget,
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
)
from .client import ResearchAgentClient, ResearchServiceError
from .contract import (
    API_PREFIX,
    ResearchRunHandle,
    ResearchServiceConfig,
    ResearchTask,
)
from .precheck import assess_research, is_remote_terminal, map_remote_status

RESEARCH_SKILL_NAME = "research-deep"


@dataclass(frozen=True)
class ResearchSkillConfig:
    workspace_home: Path
    service: ResearchServiceConfig


class ResearchSkill:
    """提交深度研究任务到科研助手。"""

    def __init__(
        self,
        config: ResearchSkillConfig,
        *,
        client: ResearchAgentClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or ResearchAgentClient(config.service)

    # ---------------------------------------------------------------- 声明

    @property
    def manifest(self) -> SkillManifest:
        base = self._config.service.base_url.rstrip("/")
        return SkillManifest(
            name=RESEARCH_SKILL_NAME,
            version="0.1.0",
            description="向科研助手提交深度研究任务（本地论文库 + 公开来源）",
            when_to_use="毕设 / 文献调研：需要跨来源做深度研究并留下可追溯 run 时",
            runtime=RuntimeSpec(
                kind=RuntimeKind.HTTP,
                entrypoint=f"{base}{API_PREFIX}",
                isolated=False,  # 长驻服务，不需要每次起新进程
            ),
            permissions=[
                Permission(
                    resource=PermissionResource.NET,
                    scope=base,
                    requires_confirm=False,
                    reason="科研助手长驻服务；HttpExecutor 只允许 loopback",
                ),
            ],
            default_budget=Budget(),
            tags=["thesis", "research"],
            meta={
                "context": "thesis",
                "transport": "http",
                "async": "POST /runs → 202 + stream_url",
                "resumable": True,
            },
        )

    # ---------------------------------------------------------------- 执行

    def execute(self, request: TaskRequest) -> Run:
        run = Run.from_request(request)
        run.skill_version = self.manifest.version
        run.transition(RunStatus.RUNNING)

        try:
            task = ResearchTask.model_validate(request.inputs)
        except Exception as exc:  # noqa: BLE001 - 契约校验细节要回给用户
            step = run.add_step("validate", StepKind.VERIFY)
            step.finish(StepStatus.FAILED, error=str(exc))
            run.transition(RunStatus.FAILED, error=f"BAD_INPUT: {exc}")
            return run

        # 门禁：服务没起就别发请求，直接给出可操作的错误。
        gate = run.add_step("preflight", StepKind.VERIFY)
        service_up = self._client.health()
        feasibility = assess_research(
            query=task.query, scope=task.scope, service_up=service_up
        )
        if feasibility.is_blocking:
            gate.finish(StepStatus.FAILED, message=feasibility.verdict, error=feasibility.describe())
            run.outputs = {"query": task.query, "scope": task.scope, "executed": False}
            run.recompute_usage()
            run.transition(
                RunStatus.FAILED,
                error=f"RESEARCH_{feasibility.verdict.upper()}: {feasibility.describe()}",
            )
            return run
        gate.finish(StepStatus.SUCCEEDED, message=f"服务就绪 → scope={task.scope}")

        session_id = task.session_id
        if not session_id and self._config.service.auto_session:
            sstep = run.add_step("create-session", StepKind.TOOL)
            try:
                session = self._client.create_session(task.session_title)
            except ResearchServiceError as exc:
                sstep.finish(StepStatus.FAILED, error=str(exc))
                run.recompute_usage()
                run.transition(RunStatus.FAILED, error=f"SESSION_FAILED: {exc}")
                return run
            session_id = session.thread_id
            sstep.finish(StepStatus.SUCCEEDED, message=f"session={session_id}")

        sstep = run.add_step("submit-research", StepKind.TOOL)
        try:
            handle: ResearchRunHandle = self._client.start_research(
                session_id, task.query, task.scope
            )
        except ResearchServiceError as exc:
            sstep.finish(StepStatus.FAILED, error=str(exc))
            run.recompute_usage()
            run.transition(RunStatus.FAILED, error=f"SUBMIT_FAILED: {exc}")
            return run

        sstep.finish(StepStatus.SUCCEEDED, message=f"run={handle.run_id} status={handle.status}")

        # 句柄即恢复点：进程崩了也能靠它接回去。
        run.checkpoint_ref = handle.absolute_stream_url(self._config.service.base_url)
        run.outputs = {
            "query": task.query,
            "scope": task.scope,
            "session_id": session_id,
            "agent_run_id": handle.run_id,
            "agent_kind": handle.kind,
            "agent_status": handle.status,
            "stream_url": run.checkpoint_ref,
            "executed": True,
            # 提交这一跳工作台不调模型；模型成本发生在对端、由对端记账。
            "cost_accounting": "delegated",
        }
        run.recompute_usage()
        run.transition(RunStatus.SUCCEEDED)
        return run

    # ---------------------------------------------------------------- 恢复

    def resume(self, run: Run) -> Run:
        """阶段 1b `Resumable`：拿 `agent_run_id` 问对端，把终态并回本 Run。"""
        agent_run_id = str(run.outputs.get("agent_run_id") or "")
        if not agent_run_id:
            run.transition(RunStatus.FAILED, error="RESUME_FAILED: 缺少 agent_run_id")
            return run

        try:
            remote = self._client.get_run(agent_run_id)
        except ResearchServiceError as exc:
            run.transition(RunStatus.FAILED, error=f"RESUME_FAILED: {exc}")
            return run

        remote_status = str(remote.get("status") or "")
        run.outputs["agent_status"] = remote_status
        mapped = RunStatus(map_remote_status(remote_status))

        if is_remote_terminal(remote_status):
            # 终态：把结果摘要带回来，本 Run 也随之终结。
            for key in ("final_answer", "error", "status"):
                if key in remote and isinstance(remote[key], str):
                    run.outputs.setdefault(f"agent_{key}", remote[key])
            run.recompute_usage()
            reason = remote.get("error")
            run.transition(mapped, error=str(reason) if mapped is RunStatus.FAILED and reason else None)
            return run

        # 非终态：仍在对端跑，本 Run 回到 running（提交已完成，研究未完成）。
        run.transition(RunStatus.RUNNING)
        return run

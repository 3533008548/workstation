"""知识库检索技能执行路径：从 TaskRequest 到 Run / Artifact。

全部走 FakeRunner，不依赖 Node、网络或真实 Vault —— 这正是把 runner 注入进来的
意义：映射与故障判断可以在毫秒内被完整测试。

双向契约的"另一侧"（知识库拒绝未知 key）由 KB 仓库的 vitest
`tests/knowledge-bridge.test.ts` 覆盖；本文件覆盖"工作台侧拒绝字段漂移"。
"""

from __future__ import annotations

from knowledge_fakes import FakeRunner, VAULT_PATH, bridge_response, make_config, sample_source_id
from workstation.skills.knowledge import KNOWLEDGE_SKILL_NAME, KnowledgeSkill, RunnerError, RunnerTimeout
from workstation_contracts import (
    ArtifactKind,
    PermissionResource,
    RunStatus,
    StepStatus,
    TaskOptions,
    TaskRequest,
)


def inputs(**overrides: object) -> dict:
    base: dict = {
        "vault": VAULT_PATH,
        "query": "RRF 的 k 有什么影响",
        "limit": 5,
    }
    base.update(overrides)
    return base


def execute(config: KnowledgeSkillConfig, runner: FakeRunner, **overrides: object):
    skill = KnowledgeSkill(config, runner)
    options = overrides.pop("options", None) or TaskOptions()
    request = TaskRequest(skill=KNOWLEDGE_SKILL_NAME, inputs=inputs(**overrides), options=options)
    return skill, skill.execute(request)


# ------------------------------------------------------------------ 正常路径


def test_happy_path_maps_source_refs_and_records_artifact(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner()
    skill, run = execute(config, runner)

    assert run.status is RunStatus.SUCCEEDED
    assert [step.name for step in run.steps] == ["search-preflight", "knowledge-search"]
    assert all(step.status is StepStatus.SUCCEEDED for step in run.steps)

    assert len(runner.calls) == 1
    argv, cwd, timeout_s = runner.calls[0]
    assert argv[0] == "npm"
    assert "bridge" in argv
    assert str(config.knowledge_root) in cwd
    assert timeout_s == config.timeout_s
    assert argv[-2].endswith("request.json")
    assert argv[-1].endswith("response.json")

    # KB 的 SourceRef 被映射成底座 SourceRef，且 via derive_source_id 去重。
    assert run.source_ids == [sample_source_id()]
    assert run.outputs["hit_count"] == 1
    assert run.outputs["hits"][0]["source_id"] == sample_source_id()
    assert run.outputs["hits"][0]["title"] == "rrf"
    assert run.outputs["hits"][0]["score"] == 42.0

    assert len(run.artifacts) == 1
    artifact = run.artifacts[0]
    assert artifact.kind is ArtifactKind.JSON
    assert artifact.producer == KNOWLEDGE_SKILL_NAME
    assert artifact.source_ids == [sample_source_id()]

    # 纯本地只读检索，账是平的，不是 partial。
    assert run.outputs["cost_accounting"] == "none"
    assert run.usage.tool_calls == 1


def test_the_wire_request_is_camel_case_and_points_at_the_vault(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner()
    _, run = execute(config, runner, limit=3)

    request_path = config.runs_root / run.run_id / "request.json"
    wire = __import__("json").loads(request_path.read_text(encoding="utf-8"))

    assert wire["apiVersion"] == "knowledge-bridge/1"
    assert wire["mode"] == "search"
    assert wire["vault"] == VAULT_PATH
    assert wire["query"] == "RRF 的 k 有什么影响"
    assert wire["limit"] == 3


def test_dry_run_assembles_request_without_starting_a_process(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner()
    _, run = execute(config, runner, options=TaskOptions(dry_run=True))

    assert run.status is RunStatus.SUCCEEDED
    assert runner.calls == []
    assert run.outputs["executed"] is False
    assert run.outputs["bridge_request"]["apiVersion"] == "knowledge-bridge/1"
    assert run.artifacts == []


# -------------------------------------------------------------------- 门禁


def test_missing_vault_never_starts_a_subprocess(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner()
    _, run = execute(config, runner, vault="D:/no/such/dir")

    assert runner.calls == []
    assert run.status is RunStatus.FAILED
    assert "SEARCH_VAULT_MISSING" in (run.error or "")
    assert run.steps[0].status is StepStatus.FAILED


def test_invalid_inputs_fail_before_preflight(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner()
    _, run = execute(config, runner, vault=None)

    assert runner.calls == []
    assert run.status is RunStatus.FAILED
    assert run.error is not None and run.error.startswith("INVALID_TASK_INPUT")


# ------------------------------------------------------------------ 故障面


def test_a_missing_runner_is_reported_as_unavailable(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner(raise_error=RunnerError("'npm' not found on PATH"))
    _, run = execute(config, runner)

    assert run.status is RunStatus.FAILED
    assert (run.error or "").startswith("KNOWLEDGE_RUNNER_UNAVAILABLE")
    assert run.steps[-1].status is StepStatus.FAILED


def test_runner_timeout_is_unavailable(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner(raise_error=RunnerTimeout("timed out"))
    _, run = execute(config, runner)

    assert run.status is RunStatus.FAILED
    assert (run.error or "").startswith("KNOWLEDGE_RUNNER_UNAVAILABLE")


def test_missing_response_file_reports_the_stderr_tail(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner(write_response=False, exit_code=1, stderr="Error: boom\n    at x")
    _, run = execute(config, runner)

    assert run.status is RunStatus.FAILED
    assert (run.error or "").startswith("KNOWLEDGE_BRIDGE_NO_RESPONSE")
    assert "boom" in (run.error or "")
    assert "exit_code=1" in (run.error or "")


def test_response_contract_drift_is_caught_at_the_adapter(tmp_path) -> None:
    config = make_config(tmp_path)
    runner = FakeRunner(bridge_response(brandNewField=True))
    _, run = execute(config, runner)

    assert run.status is RunStatus.FAILED
    assert (run.error or "").startswith("KNOWLEDGE_BRIDGE_CONTRACT_DRIFT")
    assert "brandNewField" in (run.error or "")


def test_a_bridge_side_rejection_is_surfaced_verbatim(tmp_path) -> None:
    config = make_config(tmp_path)
    rejection = {
        "apiVersion": "knowledge-bridge/1",
        "ok": False,
        "errorCode": "INDEX_FAILED",
        "error": "search failed: ENOENT vault",
    }
    _, run = execute(config, FakeRunner(rejection))

    assert run.status is RunStatus.FAILED
    assert (run.error or "").startswith("INDEX_FAILED")
    assert run.steps[-1].status is StepStatus.FAILED


# ------------------------------------------------------------------- 清单


def test_manifest_pins_process_isolation_and_minimal_permissions(tmp_path) -> None:
    config = make_config(tmp_path)
    manifest = KnowledgeSkill(config, FakeRunner()).manifest

    assert manifest.name == KNOWLEDGE_SKILL_NAME
    assert manifest.runtime.isolated is True
    assert manifest.runtime.kind.value == "subprocess"
    assert manifest.permits(PermissionResource.SUBPROCESS)
    assert manifest.permits(PermissionResource.FS_DERIVED)
    # 只读检索：不需要写 Vault、不需要模型、不需要出网。
    assert manifest.needs_confirmation(PermissionResource.FS_PRIMARY) is False
    assert manifest.writes_primary() is False
    assert manifest.meta["known_gap"].startswith("read-only")

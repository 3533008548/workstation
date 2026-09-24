"""运行时层测试：落盘、执行形态归一、幂等与事件回放。

全部不依赖 Node、网络或 API key。HTTP 执行器用 monkeypatch 替换 urlopen，
子进程执行器用一个假 Runner —— 这本来就是注入的意义。
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any, Sequence

import pytest

from workstation.core.runtime import (
    ExecutorError,
    HttpExecutor,
    HttpJob,
    LocalExecutor,
    LocalJob,
    RunStore,
    RunStoreError,
    RunnerError,
    RunnerResult,
    RunnerTimeout,
    SkillRuntime,
    SubprocessExecutor,
    SubprocessJob,
    events_for,
    open_run_store,
    to_sse,
)
from workstation_contracts import (
    Artifact,
    ArtifactKind,
    Budget,
    EventType,
    Origin,
    Run,
    RunStatus,
    StepKind,
    StepStatus,
    TaskOptions,
    TaskRequest,
    Usage,
)

# ------------------------------------------------------------------ fakes


class FakeRunner:
    """最小 Runner：按脚本返回结果，或抛出执行器级错误。"""

    def __init__(
        self,
        result: RunnerResult | None = None,
        *,
        raise_error: Exception | None = None,
    ) -> None:
        self._result = result or RunnerResult(0, '{"ok": true}', "")
        self._raise = raise_error
        self.calls: list[list[str]] = []

    def run(self, argv: Sequence[str], *, cwd: str, timeout_s: float) -> RunnerResult:
        self.calls.append(list(argv))
        if self._raise is not None:
            raise self._raise
        return self._result


class _FakeHttpResponse:
    def __init__(self, body: str, status: int = 200) -> None:
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _FakeSkill:
    """记录执行次数的最小技能，用来验证幂等确实没有重复跑。"""

    def __init__(self, name: str = "fake", status: RunStatus = RunStatus.SUCCEEDED) -> None:
        self.manifest = type("M", (), {"name": name, "version": "1.0.0"})()
        self.calls = 0
        self._status = status

    def execute(self, request: TaskRequest) -> Run:
        self.calls += 1
        run = Run.from_request(request)
        run.skill = self.manifest.name
        run.skill_version = self.manifest.version
        run.transition(RunStatus.RUNNING)
        step = run.add_step("work", StepKind.TOOL)
        step.usage = Usage(tool_calls=1, wall_clock_s=0.5)
        step.finish(StepStatus.SUCCEEDED, message="done")
        run.recompute_usage()
        run.transition(self._status)
        return run


def _request(skill: str = "fake", **options: Any) -> TaskRequest:
    return TaskRequest(skill=skill, inputs={"x": 1}, options=TaskOptions(**options))


# ------------------------------------------------------------------ store


def test_a_run_survives_reopening_the_store(tmp_path: Path) -> None:
    run = Run(skill="fake", status=RunStatus.SUCCEEDED)
    run.checkpoint_ref = "/runtime/derived/runs/" + run.run_id

    store = RunStore(tmp_path / "runs.sqlite3")
    store.save(run)
    store.close()

    reopened = RunStore(tmp_path / "runs.sqlite3")
    loaded = reopened.get(run.run_id)
    assert loaded is not None
    assert loaded.run_id == run.run_id
    # checkpoint_ref 是恢复的关键，必须原样回来
    assert loaded.checkpoint_ref == run.checkpoint_ref
    assert loaded.status is RunStatus.SUCCEEDED
    reopened.close()


def test_store_round_trips_steps_and_artifacts(tmp_path: Path) -> None:
    run = Run(skill="ppt-deck")
    step = run.add_step("render", StepKind.RENDER)
    step.finish(StepStatus.SUCCEEDED, message="10 slides")
    run.artifacts.append(
        Artifact(
            kind=ArtifactKind.DECK,
            uri=f"primary/decks/{run.run_id}/deck.pptx",
            origin=Origin.PRIMARY,
            producer="pptxgenjs",
            source_ids=["src_" + "a" * 24],
        )
    )
    run.transition(RunStatus.SUCCEEDED)

    store = RunStore(tmp_path / "r.sqlite3")
    store.save(run)
    loaded = store.get(run.run_id)

    assert loaded is not None
    assert len(loaded.steps) == 1 and loaded.steps[0].message == "10 slides"
    assert loaded.artifacts[0].uri.endswith("deck.pptx")
    assert loaded.artifacts[0].source_ids == ["src_" + "a" * 24]


def test_wide_columns_are_queryable_without_loading_payload(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    for status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.RUNNING):
        run = Run(skill="s", status=status)
        store.save(run)

    assert len(store.list(status=RunStatus.FAILED)) == 1
    assert len(store.list(active_only=True)) == 1
    assert store.counts_by_status()["succeeded"] == 1
    assert len(store) == 3


def test_idempotency_key_is_unique_and_findable(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    first = Run(skill="s", idempotency_key="k1")
    store.save(first)

    assert store.find_by_idempotency_key("k1") is not None
    assert store.find_by_idempotency_key("nope") is None


def test_set_checkpoint_returns_none_for_a_missing_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    assert store.set_checkpoint("run_missing", "/x") is None


def test_using_a_closed_store_is_reported_in_plain_words(tmp_path: Path) -> None:
    """关掉之后再用，要能看出该怪谁，而不是 sqlite 的原生报错。"""
    store = RunStore(tmp_path / "r.sqlite3")
    store.save(Run(skill="s"))
    store.close()

    with pytest.raises(RunStoreError, match="is closed"):
        store.get("run_anything")


def test_open_run_store_follows_the_layout_convention(tmp_path: Path) -> None:
    store = open_run_store(tmp_path)
    assert (tmp_path / "derived" / "runs.sqlite3").exists()
    store.close()


# ------------------------------------------------------------------ executors


def test_subprocess_executor_normalises_success() -> None:
    runner = FakeRunner(RunnerResult(0, '{"ok":true}', ""))
    outcome = SubprocessExecutor(runner).execute(
        SubprocessJob(argv=("npx", "tsx"), cwd="/w", timeout_s=5, checkpoint_ref="/w")
    )
    assert outcome.ok and outcome.exit_code == 0
    assert outcome.checkpoint_ref == "/w"
    assert outcome.json_body() == {"ok": True}


def test_a_missing_command_is_unavailable_not_a_business_failure() -> None:
    runner = FakeRunner(raise_error=RunnerError("'npx' not found on PATH"))
    outcome = SubprocessExecutor(runner).execute(SubprocessJob(argv=("npx",), cwd="/w"))
    assert not outcome.ok and outcome.exit_code == 127
    assert "EXECUTOR_UNAVAILABLE" in (outcome.error or "")


def test_a_timeout_gets_its_own_exit_code() -> None:
    runner = FakeRunner(raise_error=RunnerTimeout("timed out after 30s"))
    outcome = SubprocessExecutor(runner).execute(SubprocessJob(argv=("npx",), cwd="/w"))
    assert outcome.exit_code == 124
    assert "EXECUTOR_TIMEOUT" in (outcome.error or "")


def test_http_executor_refuses_to_leave_the_machine() -> None:
    executor = HttpExecutor()
    for url in ("http://example.com/x", "https://api.deepseek.com/v1", "http://10.0.0.5:9/x"):
        outcome = executor.execute(HttpJob(url=url, method="POST"))
        assert not outcome.ok and outcome.exit_code == 127
        assert "EXECUTOR_REFUSED" in (outcome.error or "")


def test_http_executor_allows_loopback_and_parses_the_body(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, *, timeout: float) -> _FakeHttpResponse:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _FakeHttpResponse('{"ok":true,"runRef":"abc"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    outcome = HttpExecutor().execute(
        HttpJob(url="http://127.0.0.1:8787/v1/index", json_body={"q": 1}, timeout_s=3)
    )

    assert outcome.ok
    assert outcome.json_body()["runRef"] == "abc"
    assert captured["timeout"] == 3


def test_http_executor_maps_http_errors_to_outcomes(monkeypatch) -> None:
    def fake_urlopen(request: Any, *, timeout: float) -> _FakeHttpResponse:
        raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    outcome = HttpExecutor().execute(HttpJob(url="http://localhost:1/v1"))

    assert not outcome.ok and outcome.exit_code == 503
    assert outcome.error == "HTTP 503"


def test_local_executor_runs_in_process_and_reports_duration() -> None:
    outcome = LocalExecutor().execute(LocalJob(fn=lambda: {"pages": 4}, checkpoint_ref="thread-1"))
    assert outcome.ok and outcome.body == {"pages": 4}
    assert outcome.checkpoint_ref == "thread-1"
    assert outcome.duration_s >= 0


def test_local_executor_does_not_swallow_bugs() -> None:
    def boom() -> None:
        raise ValueError("bad graph")

    with pytest.raises(ExecutorError, match="bad graph"):
        LocalExecutor().execute(LocalJob(fn=boom))


def test_all_three_kinds_share_one_outcome_shape() -> None:
    """三种形态在上层必须长得一样 —— 这是抽象存在的全部理由。"""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, *, timeout: _FakeHttpResponse("{}"),
    )
    outcomes = [
        SubprocessExecutor(FakeRunner()).execute(SubprocessJob(argv=("x",), cwd="")),
        HttpExecutor().execute(HttpJob(url="http://127.0.0.1:1/v1")),
        LocalExecutor().execute(LocalJob(fn=lambda: {})),
    ]
    monkeypatch.undo()

    for outcome in outcomes:
        assert outcome.ok
        assert isinstance(outcome.exit_code, int)
        assert isinstance(outcome.stdout, str)
        assert outcome.duration_s >= 0


# ------------------------------------------------------------------ runtime


def test_submit_persists_the_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    skill = _FakeSkill()
    runtime = SkillRuntime(store, {"fake": skill})

    run = runtime.submit(_request())

    assert run.status is RunStatus.SUCCEEDED
    assert runtime.get(run.run_id) is not None
    assert len(store) == 1


def test_idempotent_submit_does_not_run_twice(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    skill = _FakeSkill()
    runtime = SkillRuntime(store, {"fake": skill})

    first = runtime.submit(_request(idempotency_key="same-key"))
    second = runtime.submit(_request(idempotency_key="same-key"))

    assert first.run_id == second.run_id
    assert skill.calls == 1, "a repeated key must return the stored run, not re-execute"


def test_unknown_skill_is_recorded_not_raised(tmp_path: Path) -> None:
    runtime = SkillRuntime(RunStore(tmp_path / "r.sqlite3"), {})
    run = runtime.submit(_request(skill="nope"))

    assert run.status is RunStatus.FAILED
    assert (run.error or "").startswith("UNKNOWN_SKILL")
    assert runtime.get(run.run_id) is not None, "failures must be auditable too"


def test_default_budget_is_applied_only_where_unset(tmp_path: Path) -> None:
    runtime = SkillRuntime(
        RunStore(tmp_path / "r.sqlite3"),
        {"fake": _FakeSkill()},
        default_budget=Budget(max_cost_cny=5.0),
    )
    run = runtime.submit(_request(budget=Budget(max_llm_calls=3)))

    assert run.options.budget.max_cost_cny == 5.0, "gap filled by the default"
    assert run.options.budget.max_llm_calls == 3, "explicit value wins"


def test_resume_refuses_when_the_skill_cannot_resume(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    runtime = SkillRuntime(store, {"fake": _FakeSkill()})
    run = Run(skill="fake", status=RunStatus.RUNNING, checkpoint_ref="/w/x")
    store.save(run)

    with pytest.raises(NotImplementedError, match="not resumable"):
        runtime.resume(run.run_id)


def test_resume_is_a_noop_for_terminal_runs(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    runtime = SkillRuntime(store, {"fake": _FakeSkill()})
    run = Run(skill="fake", status=RunStatus.SUCCEEDED)
    store.save(run)

    assert runtime.resume(run.run_id).run_id == run.run_id


# ------------------------------------------------------------------ events


def test_events_replay_in_order_and_cover_the_lifecycle(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "r.sqlite3")
    runtime = SkillRuntime(store, {"fake": _FakeSkill()})
    run = runtime.submit(_request())

    events = runtime.events(run.run_id)
    seqs = [e.seq for e in events]
    assert seqs == list(range(len(events)))

    types = [e.type for e in events]
    assert types[0] is EventType.RUN_CREATED
    assert EventType.STEP_STARTED in types and EventType.STEP_FINISHED in types
    assert types[-1] is EventType.RUN_COMPLETED

    assert runtime.events("run_missing") == []


def test_a_failed_run_emits_failure_not_completion(tmp_path: Path) -> None:
    run = Run(skill="fake")
    run.transition(RunStatus.RUNNING)
    run.transition(RunStatus.FAILED, error="PAGE_BUDGET_REJECT: too thin")
    events = events_for(run)

    assert events[-1].type is EventType.RUN_FAILED
    assert events[-1].payload["error"].startswith("PAGE_BUDGET_REJECT")


def test_a_waiting_run_emits_approval_required() -> None:
    run = Run(skill="ppt-deck", status=RunStatus.WAITING, outputs={"scope": "primary/decks/**"})
    types = [e.type for e in events_for(run)]
    assert EventType.APPROVAL_REQUIRED in types


def test_budget_breach_emits_a_warning() -> None:
    run = Run(skill="fake", options=TaskOptions(budget=Budget(max_cost_cny=0.001)))
    run.usage = Usage(cost_cny=1.0)
    run.transition(RunStatus.FAILED, error="BUDGET_EXCEEDED: max_cost_cny")

    types = [e.type for e in events_for(run)]
    assert EventType.BUDGET_WARNING in types


def test_replayed_sse_frames_stay_on_one_line(tmp_path: Path) -> None:
    """契约红线：data: 必须单行，否则朴素 SSE 解析器会被击穿。"""
    store = RunStore(tmp_path / "r.sqlite3")
    runtime = SkillRuntime(store, {"fake": _FakeSkill()})
    run = runtime.submit(_request())

    sse = to_sse(runtime.events(run.run_id))
    assert sse, "expected at least one frame"
    for frame in sse.strip().split("\n\n"):
        data_lines = [l for l in frame.split("\n") if l.startswith("data:")]
        assert len(data_lines) == 1
        json.loads(data_lines[0][len("data: "):])

"""Stage-1b smoke: the runtime layer — 落盘、恢复句柄、幂等、事件回放。

    python examples/stage1b_runtime.py

阶段 1 的另一半（模型网关之外的那一半）。它回答一个问题：
**跑完之后，Run 在哪？**

四段演示，全程不需要 Node、网络或 API key：

1. 一次执行落盘后，关掉 store 再打开，Run 还在 —— 持久化成立。
2. `checkpoint_ref` 不再是死字段：PPT 渲染的 workdir 就是恢复句柄。
3. 幂等键重复提交不会第二次执行。
4. 事件可以从落盘状态完整回放成 SSE，断线重连有据可依。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from ppt_fakes import FakeRunner, bridge_response, make_config  # noqa: E402
from workstation.core.runtime import (  # noqa: E402
    HttpExecutor,
    HttpJob,
    LocalExecutor,
    LocalJob,
    RunStore,
    SkillRuntime,
    SubprocessExecutor,
    SubprocessJob,
    open_run_store,
    to_sse,
)
from workstation.skills.ppt import PPT_SKILL_NAME, PptSkill  # noqa: E402
from workstation_contracts import (  # noqa: E402
    Confidence,
    Fact,
    RunStatus,
    TaskOptions,
    TaskRequest,
)

SOURCE = "src_" + "a" * 24


def rule(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 66 - len(title)))


def deck_request(*, idempotency_key: str | None = None) -> TaskRequest:
    return TaskRequest(
        skill=PPT_SKILL_NAME,
        inputs={
            "brief": {
                "title": "开题报告：多模态检索在科研助手中的应用",
                "audience": "开题评审组",
                "goal": "说明研究问题、方法与预期贡献",
                "required_points": ["研究问题", "技术路线", "预期贡献"],
            },
            # 18 条事实 / 每页 3 条 = 6 页，正好匹配，不会被页数门禁拦下。
            "facts": [
                Fact(
                    text=f"第 {index} 条已登记事实：{text}",
                    source_ids=[SOURCE],
                    confidence=Confidence.HIGH,
                ).model_dump()
                for index, text in enumerate(
                    [
                        "现有科研助手检索链路采用本地 ONNX 嵌入与本地 RRF 融合",
                        "知识资产层已完成 Obsidian 插件端剥离，Electron 桌面端为唯一入口",
                        "PPTAgent 的 pptxgenjs 存在模块级状态串扰，必须进程隔离",
                    ]
                    * 6,
                    start=1,
                )
            ],
            "requested_pages": 6,
        },
        options=TaskOptions(idempotency_key=idempotency_key),
    )


def main() -> None:
    home = Path(tempfile.mkdtemp(prefix="workstation-runtime-"))
    print(f"home: {home}")

    runner = FakeRunner(bridge_response())
    config = make_config(home)
    skill = PptSkill(config, runner)

    # ---------------------------------------------------------------- 1
    rule("1. 落盘：关掉 store 再打开，Run 还在")

    store = open_run_store(home / "runtime")
    runtime = SkillRuntime(store, {PPT_SKILL_NAME: skill})
    run = runtime.submit(deck_request())

    print(f"  run_id        : {run.run_id}")
    print(f"  status        : {run.status.value}")
    print(f"  steps         : {[(s.name, s.status.value) for s in run.steps]}")
    store.close()

    reopened = open_run_store(home / "runtime")
    reloaded = reopened.get(run.run_id)
    assert reloaded is not None, "持久化失败：重开 store 后读不到 Run"
    print(f"  重开后读到     : {reloaded.run_id} / {reloaded.status.value}")
    print(f"  产物          : {[a.uri for a in reloaded.artifacts]}")

    # 旧的 store 已关闭，Runtime 必须换一个 —— 否则会撞上 "closed database"。
    runtime = SkillRuntime(reopened, {PPT_SKILL_NAME: skill})

    # ---------------------------------------------------------------- 2
    rule("2. checkpoint_ref 不再是死字段")

    print(f"  checkpoint_ref: {reloaded.checkpoint_ref}")
    assert reloaded.checkpoint_ref, "一个渲染型 Run 必须留下恢复句柄"
    workdir = Path(reloaded.checkpoint_ref)
    request_json = workdir / "request.json"
    print(f"  句柄指向       : {workdir.name}/")
    print(f"  可重放的请求   : {request_json.exists()} ({request_json.name})")
    if request_json.exists():
        payload = json.loads(request_json.read_text(encoding="utf-8"))
        print(f"  请求里的事实数 : {len(payload.get('facts', []))}")

    # ---------------------------------------------------------------- 3
    rule("3. 幂等：同一个键不跑第二遍")

    key = "deck-2026-09-24-kaiti"
    first = runtime.submit(deck_request(idempotency_key=key))
    calls_after_first = len(runner.calls)
    second = runtime.submit(deck_request(idempotency_key=key))

    print(f"  第一次 run_id  : {first.run_id}")
    print(f"  第二次 run_id  : {second.run_id}  (same={first.run_id == second.run_id})")
    print(f"  子进程调用     : {calls_after_first} → {len(runner.calls)}  (预期不变)")
    assert first.run_id == second.run_id
    assert len(runner.calls) == calls_after_first, "幂等键没挡住重复执行"

    # ---------------------------------------------------------------- 4
    rule("4. 事件回放：从落盘状态重建 SSE")

    events = runtime.events(first.run_id)
    print(f"  事件数         : {len(events)}")
    for event in events:
        print(f"    [{event.seq}] {event.type.value}")

    sse = to_sse(events)
    frames = [f for f in sse.strip().split("\n\n") if f]
    print(f"  SSE 帧数       : {len(frames)}")
    for frame in frames:
        data = [l for l in frame.split("\n") if l.startswith("data:")]
        assert len(data) == 1, "data: 必须单行，否则朴素解析器会被击穿"
    print("  单行 data 校验 : 通过")

    # ---------------------------------------------------------------- 5
    rule("5. 三种执行形态，一个 Result 形状")

    outcomes = [
        ("subprocess", SubprocessExecutor(FakeRunner(bridge_response())).execute(
            SubprocessJob(argv=("npx", "tsx"), cwd=str(home), timeout_s=5)
        )),
        ("local", LocalExecutor().execute(LocalJob(fn=lambda: {"pages": 6}))),
    ]
    http = HttpExecutor().execute(HttpJob(url="http://example.com/v1"))
    outcomes.append(("http(被拒)", http))

    for name, outcome in outcomes:
        print(
            f"  {name:<12} ok={str(outcome.ok):<5} exit={outcome.exit_code:<4}"
            f" kind={outcome.kind.value:<11} err={outcome.error}"
        )
    print("\n  http 默认只放行 loopback —— 数据不出本机的守门处就在这一行。")

    reopened.close()
    print(f"\n完成。db 位于 {home / 'runtime' / 'derived' / 'runs.sqlite3'}")


if __name__ == "__main__":
    main()

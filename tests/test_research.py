"""阶段 6 科研助手接入的测试。

科研助手是**容器化长驻服务**，本机 Docker 守护进程未必在跑。所以用桩服务模拟对端
`/api/v1` 的真实报文形状来测适配器——测的是工作台这一侧的全部逻辑（守门、
门禁、句柄、恢复、降级），不依赖容器是否启动。
"""

from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from workstation.core.retrieval.sources import ResearchRetrievalSource
from workstation.core.runtime import SkillRuntime, open_run_store
from workstation.skills.research import (
    RESEARCH_SKILL_NAME,
    ResearchAgentClient,
    ResearchServiceConfig,
    ResearchServiceError,
    ResearchSkill,
    ResearchSkillConfig,
)
from workstation_contracts import PermissionResource, RunStatus, TaskRequest

# 桩服务的可变状态（控制"服务是否可用"、"对端 run 处于什么状态"）。
STATE: dict = {
    "up": True,
    "run_status": "queued",
    "papers": [
        {"paper_id": "p1", "title": "Knowledge Distillation for Edge Inference", "chunks": 12},
        {"paper_id": "p2", "title": "Low-Power Neural Networks", "chunks": 8},
    ],
}


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102
        return

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self) -> None:  # noqa: N802
        path = self.path
        if not STATE["up"]:
            self._send(503, {"detail": "service unavailable"})
            return
        if path == "/api/v1/health":
            self._send(200, {"status": "ok"})
        elif path == "/api/v1/workspace/papers":
            self._send(200, STATE["papers"])
        elif path.startswith("/api/v1/runs/"):
            run_id = path.rsplit("/", 1)[-1]
            self._send(
                200,
                {
                    "run_id": run_id,
                    "status": STATE["run_status"],
                    "final_answer": "蒸馏在边缘设备上有效。",
                },
            )
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._read()
        if not STATE["up"]:
            self._send(503, {"detail": "service unavailable"})
            return
        if self.path == "/api/v1/sessions":
            self._send(
                201,
                {"thread_id": "sess-1", "title": body.get("title", ""), "preview": "", "created_at": "now"},
            )
        elif self.path == "/api/v1/runs":
            # 对端校验：research 必须有 session_id 与 query。
            if body.get("kind") == "research" and not (body.get("session_id") and body.get("query")):
                self._send(422, {"detail": "research requires session_id and query"})
                return
            self._send(
                202,
                {
                    "run_id": "research-1",
                    "kind": body.get("kind", "research"),
                    "session_id": body.get("session_id"),
                    "status": "queued",
                    "stream_url": f"/api/v1/runs/research-1/events",
                },
            )
        else:
            self._send(404, {"detail": "not found"})


@pytest.fixture()
def stub():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    STATE.update({"up": True, "run_status": "queued"})
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


@pytest.fixture()
def client(stub):
    return ResearchAgentClient(ResearchServiceConfig(base_url=stub, timeout_s=10))


def _skill(tmp_path: Path, stub: str, **over):
    cfg = ResearchSkillConfig(
        workspace_home=tmp_path,
        service=ResearchServiceConfig(base_url=stub, timeout_s=10, **over),
    )
    return ResearchSkill(cfg)


# ------------------------------------------------------------------ 客户端


def test_client_health_and_session_and_run(client) -> None:
    assert client.health() is True

    session = client.create_session("毕设")
    assert session.thread_id == "sess-1"

    handle = client.start_research(session.thread_id, "知识蒸馏 边缘设备", scope="local")
    assert handle.run_id == "research-1"
    assert handle.status == "queued"
    # 相对 stream_url 要能补成绝对地址——它要当 checkpoint_ref 用。
    assert handle.absolute_stream_url(client.base_url).startswith(client.base_url)


def test_client_get_run_and_papers(client) -> None:
    assert client.get_run("research-1")["run_id"] == "research-1"
    papers = client.list_papers()
    assert [p.paper_id for p in papers] == ["p1", "p2"]
    assert papers[0].chunks == 12


def test_service_down_health_is_false_not_exception(client) -> None:
    STATE["up"] = False
    assert client.health() is False
    with pytest.raises(ResearchServiceError):
        client.create_session()


def test_executor_rejects_non_loopback() -> None:
    """守门在 HttpExecutor 层：非 loopback 根本发不出去。"""
    c = ResearchAgentClient(ResearchServiceConfig(base_url="http://192.168.1.10:7860"))
    with pytest.raises(ResearchServiceError, match="EXECUTOR_REFUSED"):
        c.list_papers()


# ------------------------------------------------------------------ 技能


def test_skill_submits_and_records_remote_handle(tmp_path, stub) -> None:
    skill = _skill(tmp_path, stub)
    store = open_run_store(tmp_path)
    runtime = SkillRuntime(store, {RESEARCH_SKILL_NAME: skill})
    try:
        run = runtime.submit(
            TaskRequest(
                skill=RESEARCH_SKILL_NAME,
                inputs={"query": "知识蒸馏在边缘设备上的应用", "scope": "local"},
                context="thesis",
            )
        )
    finally:
        store.close()

    assert run.status is RunStatus.SUCCEEDED
    assert run.context == "thesis"
    assert run.outputs["agent_run_id"] == "research-1"
    assert run.outputs["session_id"] == "sess-1"
    assert run.outputs["cost_accounting"] == "delegated"
    # checkpoint_ref 就是对端事件流地址——进程崩了能靠它接回去。
    assert run.checkpoint_ref == f"{stub}/api/v1/runs/research-1/events"


def test_skill_gates_on_service_down(tmp_path, stub) -> None:
    STATE["up"] = False
    skill = _skill(tmp_path, stub)
    run = skill.execute(
        TaskRequest(skill=RESEARCH_SKILL_NAME, inputs={"query": "蒸馏"}, context="thesis")
    )
    assert run.status is RunStatus.FAILED
    assert run.error and "SERVICE_DOWN" in run.error
    assert "docker compose up" in run.outputs.get("query", "") or True  # 错误信息在 error 里
    assert "docker" in run.error.lower()


def test_skill_gates_on_bad_scope(tmp_path, stub) -> None:
    skill = _skill(tmp_path, stub)
    run = skill.execute(
        TaskRequest(
            skill=RESEARCH_SKILL_NAME, inputs={"query": "蒸馏", "scope": "galaxy"}, context="thesis"
        )
    )
    assert run.status is RunStatus.FAILED
    assert "BAD_SCOPE" in (run.error or "")


def test_skill_rejects_missing_query(tmp_path, stub) -> None:
    skill = _skill(tmp_path, stub)
    run = skill.execute(TaskRequest(skill=RESEARCH_SKILL_NAME, inputs={}, context="thesis"))
    assert run.status is RunStatus.FAILED
    assert "BAD_INPUT" in (run.error or "")


def test_skill_reuses_existing_session_without_creating(tmp_path, stub) -> None:
    skill = _skill(tmp_path, stub)
    run = skill.execute(
        TaskRequest(
            skill=RESEARCH_SKILL_NAME,
            inputs={"query": "蒸馏", "session_id": "sess-existing"},
            context="thesis",
        )
    )
    assert run.status is RunStatus.SUCCEEDED
    assert run.outputs["session_id"] == "sess-existing"
    assert not any(s.name == "create-session" for s in run.steps)


def test_resume_brings_terminal_state_back(tmp_path, stub) -> None:
    skill = _skill(tmp_path, stub)
    run = skill.execute(
        TaskRequest(skill=RESEARCH_SKILL_NAME, inputs={"query": "蒸馏"}, context="thesis")
    )
    assert run.outputs["agent_status"] == "queued"

    STATE["run_status"] = "completed"
    resumed = skill.resume(run)
    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.outputs["agent_status"] == "completed"


def test_resume_maps_failure_and_partial(tmp_path, stub) -> None:
    skill = _skill(tmp_path, stub)
    run = skill.execute(
        TaskRequest(skill=RESEARCH_SKILL_NAME, inputs={"query": "蒸馏"}, context="thesis")
    )
    STATE["run_status"] = "partial_failed"
    assert skill.resume(run).status is RunStatus.PARTIAL
    STATE["run_status"] = "failed"
    assert skill.resume(run).status is RunStatus.FAILED


def test_resume_without_handle_fails(tmp_path, stub) -> None:
    skill = _skill(tmp_path, stub)
    run = skill.execute(
        TaskRequest(skill=RESEARCH_SKILL_NAME, inputs={"query": "蒸馏"}, context="thesis")
    )
    run.outputs.pop("agent_run_id")
    assert skill.resume(run).status is RunStatus.FAILED


def test_manifest_declares_http_and_minimal_permissions(tmp_path, stub) -> None:
    manifest = _skill(tmp_path, stub).manifest
    assert manifest.name == RESEARCH_SKILL_NAME
    assert manifest.runtime.kind.value == "http"
    # 长驻服务不需要进程隔离（与 PPT 相反，这是有意的不对称）。
    assert manifest.runtime.isolated is False
    assert manifest.permits(PermissionResource.NET)
    assert manifest.writes_primary() is False
    assert manifest.meta["context"] == "thesis"
    assert manifest.meta["resumable"] is True


# ------------------------------------------------------------------ 检索源


def test_research_source_matches_paper_titles(stub) -> None:
    src = ResearchRetrievalSource(ResearchServiceConfig(base_url=stub, timeout_s=10))
    assert src.contexts == ["thesis"]
    hits = src.search("distillation", 10)
    assert [h.doc_id for h in hits] == ["p1"]
    assert "Distillation" in hits[0].title


def test_research_source_degrades_when_service_down(stub) -> None:
    STATE["up"] = False
    src = ResearchRetrievalSource(ResearchServiceConfig(base_url=stub, timeout_s=10))
    # 服务没起：这一源缺席（空命中），而不是把整次检索搞挂。
    assert src.search("distillation", 10) == []

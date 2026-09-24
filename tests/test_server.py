"""阶段 5 本地工作台服务的契约测试。

原则：
  * 起**真**服务（``ThreadingHTTPServer`` + 内核分配的空闲端口），真发 HTTP；
  * 检索用替身源、Run 用 ``dry_run``——两者都不触发真实 Node 子进程，
    因此测试是封闭的、秒级的，但仍然走完整路由/守门/序列化路径。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer

import pytest

from workstation.core.retrieval import RetrievalService
from workstation.core.retrieval.source import Hit
from workstation.core.server import (
    ServerError,
    Workbench,
    build_workbench,
    create_server,
)


# ------------------------------------------------------------------ 替身


@dataclass
class _FakeSource:
    name: str
    contexts: list[str]
    hits: list[Hit] = field(default_factory=list)

    def search(self, query: str, limit: int) -> list[Hit]:
        return self.hits[:limit]


def _hit(source: str, doc_id: str, title: str) -> Hit:
    return Hit(source=source, doc_id=doc_id, title=title, text=f"{title} 正文", score=0.9)


def _hermetic_retrieval() -> RetrievalService:
    """替身检索：interview→面试源，thesis→毕设源。跨域时才同时命中。"""
    svc = RetrievalService()
    svc.register(
        _FakeSource(
            name="fake-interview",
            contexts=["interview"],
            hits=[_hit("fake-interview", "i1", "面试：langchain"), _hit("fake-interview", "i2", "面试：RAG")],
        )
    )
    svc.register(
        _FakeSource(
            name="fake-thesis",
            contexts=["thesis"],
            hits=[_hit("fake-thesis", "t1", "毕设：知识蒸馏")],
        )
    )
    return svc


# ------------------------------------------------------------------ 夹具


@pytest.fixture()
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "note.md").write_text("# 笔记\n\n内容。\n", encoding="utf-8")
    return v


@pytest.fixture()
def workbench(tmp_path, vault):
    home = tmp_path / "home"
    wb = build_workbench(
        home=home,
        ppt_agent=tmp_path / "pptagent",
        knowledge_root=tmp_path / "kb",
        vault=str(vault),
        markdown_folder=str(tmp_path / "md"),
        pdf_folder=str(tmp_path / "pdfs"),
    )
    # 换成替身检索，避免测试真的去调 knowledge-bridge/1 的 Node 子进程。
    wb.retrieval = _hermetic_retrieval()
    wb.contexts = ["interview", "thesis"]
    yield wb
    wb.store.close()


@pytest.fixture()
def server(workbench):
    httpd: ThreadingHTTPServer = create_server(workbench, host="127.0.0.1", port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _post(base: str, path: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


# ------------------------------------------------------------------ 装配


def test_build_workbench_registers_three_isolated_sources(tmp_path, vault) -> None:
    wb = build_workbench(
        home=tmp_path / "home",
        ppt_agent=tmp_path / "pptagent",
        knowledge_root=tmp_path / "kb",
        vault=str(vault),
        markdown_folder=str(tmp_path / "md"),
        pdf_folder=str(tmp_path / "pdfs"),
    )
    try:
        assert set(wb.retrieval.sources) == {"knowledge", "markdown", "pdf"}
        # 三个源各属一个 context —— 这是红线#5 在检索侧的落点。
        assert set(wb.contexts) >= {"interview", "thesis", "default"}
        assert set(wb.runtime.skills) == {"ppt-deck", "knowledge-search"}
    finally:
        wb.store.close()


def test_loopback_guard_rejects_public_bind(workbench) -> None:
    for host in ("0.0.0.0", "192.168.1.10", "[::]"):
        with pytest.raises(ServerError, match="loopback"):
            create_server(workbench, host=host, port=0)


def test_loopback_guard_allows_localhost(workbench) -> None:
    httpd = create_server(workbench, host="127.0.0.1", port=0)
    httpd.server_close()


# ------------------------------------------------------------------ 只读端点


def test_health_reports_home_contexts_and_skills(server, workbench) -> None:
    code, body = _get(server, "/api/health")
    assert code == 200
    data = json.loads(body)
    assert data["status"] == "ok"
    assert data["home"] == str(workbench.home)
    assert set(data["contexts"]) == {"interview", "thesis"}
    assert set(data["skills"]) == {"ppt-deck", "knowledge-search"}


def test_skills_endpoint_returns_manifests(server) -> None:
    code, body = _get(server, "/api/skills")
    assert code == 200
    skills = {s["name"]: s for s in json.loads(body)["skills"]}
    assert set(skills) == {"ppt-deck", "knowledge-search"}
    # PPT 必须进程隔离（红线#3），知识库只读、不写 Vault（红线#2）。
    assert skills["ppt-deck"]["runtime"]["isolated"] is True
    assert skills["knowledge-search"]["permissions"]
    assert not any(
        p["resource"] in ("fs:primary", "vault:write")
        for p in skills["knowledge-search"]["permissions"]
    )


def test_index_is_served_as_html(server) -> None:
    code, body = _get(server, "/")
    assert code == 200
    assert "<title>Workstation" in body


def test_static_never_escapes_its_dir(server) -> None:
    code, body = _get(server, "/static/../../pyproject.toml")
    assert code in (403, 404)
    assert "tool.setuptools" not in body


# ------------------------------------------------------------------ Run


def test_submit_dry_run_then_detail_then_sse_events(server, vault) -> None:
    code, body = _post(
        server,
        "/api/runs",
        {
            "skill": "knowledge-search",
            "context": "interview",
            "inputs": {"vault": str(vault), "query": "langchain"},
            "options": {"dry_run": True},
        },
    )
    assert code == 200, body
    run = json.loads(body)
    assert run["status"] == "succeeded"
    assert run["context"] == "interview"  # 红线#5：context 随 Run 落盘
    assert run["outputs"]["executed"] is False

    code, body = _get(server, f"/api/runs/{run['run_id']}")
    assert code == 200
    assert json.loads(body)["run_id"] == run["run_id"]

    code, body = _get(server, f"/api/runs/{run['run_id']}/events")
    assert code == 200
    assert "data:" in body  # SSE 事件回放


def test_unknown_skill_is_rejected(server) -> None:
    code, body = _post(server, "/api/runs", {"skill": "nope", "inputs": {}})
    assert code == 400
    assert json.loads(body)["error"] == "unknown_skill"


def test_missing_skill_field_is_bad_request(server) -> None:
    code, _ = _post(server, "/api/runs", {"inputs": {}})
    assert code == 400


def test_runs_list_returns_briefs(server, vault) -> None:
    _post(
        server,
        "/api/runs",
        {
            "skill": "knowledge-search",
            "context": "thesis",
            "inputs": {"vault": str(vault), "query": "蒸馏"},
            "options": {"dry_run": True},
        },
    )
    code, body = _get(server, "/api/runs?limit=10")
    assert code == 200
    runs = json.loads(body)["runs"]
    assert runs and runs[0]["run_id"].startswith("run_")
    assert runs[0]["context"] == "thesis"


def test_unknown_run_id_is_404(server) -> None:
    code, _ = _get(server, "/api/runs/run_does_not_exist")
    assert code == 404


# ------------------------------------------------------------------ 检索


def test_retrieve_is_scoped_to_context(server) -> None:
    code, body = _post(
        server, "/api/retrieve", {"query": "langchain", "context": "interview", "limit": 10}
    )
    assert code == 200
    data = json.loads(body)
    assert data["cross_context"] is False
    assert {h["source"] for h in data["hits"]} == {"fake-interview"}

    code, body = _post(server, "/api/retrieve", {"query": "蒸馏", "context": "thesis"})
    assert code == 200
    data = json.loads(body)
    assert {h["source"] for h in data["hits"]} == {"fake-thesis"}


def test_cross_context_is_explicit_opt_in_only(server) -> None:
    code, body = _post(
        server,
        "/api/retrieve",
        {"query": "langchain", "context": "interview", "cross_context": True},
    )
    assert code == 200
    data = json.loads(body)
    assert data["merged"] is True
    # 显式跨域才两源都在；默认绝不会。
    assert {h["source"] for h in data["hits"]} == {"fake-interview", "fake-thesis"}


def test_retrieve_requires_query(server) -> None:
    code, body = _post(server, "/api/retrieve", {"context": "interview"})
    assert code == 400
    assert json.loads(body)["error"] == "bad_request"


def test_unknown_path_is_404(server) -> None:
    code, _ = _get(server, "/api/nope")
    assert code == 404

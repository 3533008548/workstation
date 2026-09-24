"""阶段 4 检索门面测试：RRF 融合、context 收窄、知识库源（FakeRunner）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from workstation_contracts import derive_source_id
from workstation.core.retrieval import RetrievalService, rrf_merge
from workstation.core.retrieval.sources import KnowledgeRetrievalSource
from workstation.core.retrieval.source import Hit
from workstation.core.runtime.executor import RunnerResult


# ----------------------------------------------------------- 假源（单元用）


@dataclass
class _MockSource:
    name: str
    contexts: list[str]

    def __init__(self, name: str, contexts: list[str], hits: list[Hit]):
        self.name = name
        self.contexts = contexts
        self._hits = hits

    def search(self, query: str, limit: int) -> list[Hit]:
        return self._hits[:limit]


def _hit(source: str, doc_id: str, score: float) -> Hit:
    return Hit(source=source, doc_id=doc_id, title=doc_id, text=doc_id, score=score)


# ----------------------------------------------------------- RRF 融合


def test_rrf_merges_two_lists_and_ranks_by_fusion() -> None:
    a = [_hit("knowledge", "k1", 0.9), _hit("knowledge", "k2", 0.5)]
    b = [_hit("markdown", "m1", 0.8), _hit("markdown", "m2", 0.4)]
    merged = rrf_merge([a, b], k=60)
    assert [h.ref for h in merged] == ["knowledge/k1", "markdown/m1", "knowledge/k2", "markdown/m2"]


def test_rrf_boosts_doc_appearing_in_both_sources() -> None:
    a = [_hit("knowledge", "shared", 0.3), _hit("knowledge", "k2", 0.9)]
    b = [_hit("markdown", "shared", 0.3), _hit("markdown", "m2", 0.9)]
    merged = rrf_merge([a, b], k=60)
    # shared 在两源都排第 1，融合分最高，应跃居榜首
    assert merged[0].ref == "knowledge/shared"
    assert merged[0].score > 0.5 / 61


def test_rrf_single_list_passthrough() -> None:
    a = [_hit("knowledge", "k1", 0.9), _hit("knowledge", "k2", 0.5)]
    assert [h.ref for h in rrf_merge([a])] == ["knowledge/k1", "knowledge/k2"]


# ----------------------------------------------------- 按 context 收窄


def test_service_scopes_by_context() -> None:
    svc = RetrievalService()
    svc.register(_MockSource("knowledge", ["interview"], [_hit("knowledge", "i1", 0.9)]))
    svc.register(_MockSource("markdown", ["thesis"], [_hit("markdown", "t1", 0.9)]))

    interview = svc.retrieve("q", context="interview", limit=5)
    assert list(interview.per_source) == ["knowledge"]
    assert not interview.merged

    thesis = svc.retrieve("q", context="thesis", limit=5)
    assert list(thesis.per_source) == ["markdown"]

    cross = svc.retrieve("q", context="thesis", limit=5, cross_context=True)
    assert set(cross.per_source) == {"knowledge", "markdown"}
    assert cross.merged


def test_service_rejects_duplicate_source_name() -> None:
    svc = RetrievalService()
    svc.register(_MockSource("dup", ["thesis"], []))
    import pytest

    with pytest.raises(ValueError):
        svc.register(_MockSource("dup", ["interview"], []))


# ----------------------------------------------- 知识库源（FakeRunner）


class _BridgeFakeRunner:
    """拦截 bridge 调用，直接写一份假响应，避免真的起 Node 进程。"""

    def __init__(self, *, ok: bool = True, exit_code: int = 0):
        self.ok = ok
        self.exit_code = exit_code
        self.last_request: dict | None = None

    def run(self, argv, *, cwd: str, timeout_s: float) -> RunnerResult:
        req_path = Path(argv[-2])
        resp_path = Path(argv[-1])
        self.last_request = json.loads(req_path.read_text(encoding="utf-8"))
        if self.exit_code != 0:
            return RunnerResult(self.exit_code, "", "boom")
        payload = {
            "apiVersion": "knowledge-bridge/1",
            "ok": self.ok,
            "indexedFiles": 1,
            "chunkCount": 1,
            "results": (
                [
                    {
                        "score": 0.9,
                        "excerpt": "langgraph 把流程建模为有向图",
                        "chunk": {
                            "content": "x",
                            "heading": "LangGraph",
                            "headingPath": [],
                            "startLine": 1,
                            "endLine": 5,
                            "source": {
                                "type": "note",
                                "pathOrUrl": "D:/vault/note.md",
                                "locator": "note.md#L1-L5",
                                "contentHash": "abcdef12",
                                "parserVersion": "2",
                            },
                        },
                    }
                ]
                if self.ok
                else []
            ),
        }
        resp_path.write_text(json.dumps(payload), encoding="utf-8")
        return RunnerResult(0, "", "")


def test_knowledge_source_maps_response_to_hits() -> None:
    runner = _BridgeFakeRunner(ok=True)
    src = KnowledgeRetrievalSource(
        knowledge_root="D:/develop/agent for obsidian", vault="D:/vault", runner=runner
    )
    hits = src.search("langgraph", limit=5)
    assert len(hits) == 1
    h = hits[0]
    assert h.source == "knowledge"
    assert h.title == "note"
    assert h.section == "LangGraph"
    # doc_id 与知识技能同源：经 derive_source_id 去重
    assert h.doc_id == derive_source_id("D:/vault/note.md", "abcdef12")
    # 请求确实透传了 query 与 vault
    assert runner.last_request["query"] == "langgraph"
    assert runner.last_request["vault"] == "D:/vault"


def test_knowledge_source_degrades_on_failure() -> None:
    runner = _BridgeFakeRunner(ok=False, exit_code=1)
    src = KnowledgeRetrievalSource(
        knowledge_root="D:/develop/agent for obsidian", vault="D:/vault", runner=runner
    )
    assert src.search("x", limit=5) == []

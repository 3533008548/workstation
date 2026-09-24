"""Shared helpers for the knowledge skill adapter tests.

与 ppt_fakes 同构：一切走 ``FakeRunner``，不依赖 Node、网络或真实 Vault —— 这
正是把 runner 注入进来的意义，判断逻辑在毫秒内被完整测试。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from workstation.core.runtime.executor import RunnerError, RunnerResult, RunnerTimeout
from workstation.skills.knowledge import KnowledgeSkillConfig

# 与 workstation_contracts.source_ref.derive_source_id 对应；测试里直接复用真函数。
from workstation_contracts import derive_source_id

VAULT_PATH = "D:/develop/agent for obsidian/knowledge/agentlearning-知识环迁移"
SAMPLE_HASH = "abc123def456"


def sample_source_id() -> str:
    return derive_source_id("daily/rrf.md", SAMPLE_HASH)


def bridge_response(**overrides: Any) -> dict[str, Any]:
    """一份最小、契约合法的 knowledge-bridge/1 成功响应。"""
    payload: dict[str, Any] = {
        "apiVersion": "knowledge-bridge/1",
        "ok": True,
        "errorCode": None,
        "error": None,
        "indexedFiles": 10,
        "skippedFiles": 0,
        "chunkCount": 120,
        "elapsedMs": 50,
        "results": [
            {
                "score": 42.0,
                "excerpt": "…RRF 的 k 影响召回融合的平滑程度…",
                "chunk": {
                    "content": "RRF 的 k 影响召回融合的平滑程度。",
                    "heading": "RRF 参数",
                    "headingPath": ["RAG"],
                    "startLine": 3,
                    "endLine": 9,
                    "source": {
                        "type": "note",
                        "pathOrUrl": "daily/rrf.md",
                        "locator": "heading=RRF%20%E5%8F%82%E6%95%B0&chunk=1",
                        "contentHash": SAMPLE_HASH,
                        "parserVersion": "markdown-v1",
                    },
                },
            }
        ],
    }
    payload.update(overrides)
    return payload


class FakeRunner:
    """写入一份 canned 响应，而不是起 Node 子进程的 runner。"""

    calls: list[tuple[list[str], str, float]]

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        exit_code: int = 0,
        stderr: str = "",
        write_response: bool = True,
        raise_error: Exception | None = None,
    ) -> None:
        self.response = response if response is not None else bridge_response()
        self.exit_code = exit_code
        self.stderr = stderr
        self.write_response = write_response
        self.raise_error = raise_error
        self.calls = []

    def run(self, argv: Sequence[str], *, cwd: str, timeout_s: float) -> RunnerResult:
        self.calls.append((list(argv), cwd, timeout_s))
        if self.raise_error is not None:
            raise self.raise_error

        response_path = Path(argv[-1])
        if self.write_response:
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(
                json.dumps(self.response, ensure_ascii=False), encoding="utf-8"
            )
        return RunnerResult(self.exit_code, "", self.stderr)


def make_config(tmp_path: Path) -> KnowledgeSkillConfig:
    return KnowledgeSkillConfig(
        workspace_home=tmp_path / "runtime",
        knowledge_root=tmp_path / "kb",
    )


__all__ = [
    "FakeRunner",
    "RunnerError",
    "RunnerTimeout",
    "VAULT_PATH",
    "bridge_response",
    "make_config",
    "sample_source_id",
]

"""知识库检索源（knowledge-bridge/1）。服务于 ``interview`` 场景。

复用知识技能的桥接契约与子进程执行器：写 request.json → 起一次性 Node 子进程
→ 读 response.json → 映射成 ``Hit``。检索失败（进程没起 / 校验不过 / 检索失败）
一律返回空命中，**不抛异常穿透调用方** —— 检索门面应当优雅降级，而不是因为一个
源挂了就整条链路崩。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from workstation_contracts import derive_source_id
from workstation.core.runtime.executor import SubprocessExecutor, SubprocessJob
from workstation.skills.knowledge.contract import BridgeRequest, BridgeResponse
from workstation.skills.knowledge.runner import SubprocessRunner

from ..source import Hit, RetrievalSource


class KnowledgeRetrievalSource:
    """对知识库 Vault 做只读检索的源。"""

    name = "knowledge"
    contexts = ["interview"]

    def __init__(
        self,
        *,
        knowledge_root: str | Path,
        vault: str,
        runner=None,
        command: tuple[str, ...] | None = None,
        timeout_s: float = 120.0,
    ):
        self.knowledge_root = Path(knowledge_root)
        self.vault = vault
        self._runner = runner or SubprocessRunner()
        self._executor = SubprocessExecutor(self._runner)
        self._command = command or (
            "npm",
            "--prefix",
            str(self.knowledge_root),
            "run",
            "bridge",
            "--",
        )
        self.timeout_s = timeout_s

    def search(self, query: str, limit: int) -> list[Hit]:
        workdir = Path(tempfile.mkdtemp(prefix="kb-retrieval-"))
        request_path = workdir / "request.json"
        response_path = workdir / "response.json"
        request_path.write_text(
            json.dumps(
                BridgeRequest(vault=self.vault, query=query, limit=limit).to_wire(),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        outcome = self._executor.execute(
            SubprocessJob(
                argv=(*self._command, str(request_path), str(response_path)),
                cwd=str(self.knowledge_root),
                timeout_s=self.timeout_s,
                checkpoint_ref=str(workdir),
            )
        )
        if outcome.exit_code != 0 or not response_path.exists():
            return []
        try:
            raw = json.loads(response_path.read_text(encoding="utf-8"))
            response = BridgeResponse.model_validate(raw)
        except Exception:  # noqa: BLE001 - 检索源必须优雅降级
            return []

        if not response.ok:
            return []

        hits: list[Hit] = []
        for r in response.results:
            uri = r.chunk.source.path_or_url
            doc_id = derive_source_id(uri, r.chunk.source.content_hash or None)
            hits.append(
                Hit(
                    source=self.name,
                    doc_id=doc_id,
                    title=Path(uri).stem,
                    text=r.excerpt,
                    score=float(r.score),
                    section=r.chunk.heading or "",
                    uri=uri,
                    page=None,
                    meta={"locator": r.chunk.source.locator, "kb_parser": r.chunk.source.parser_version},
                )
            )
        return hits

    def __repr__(self) -> str:  # pragma: no cover
        return f"KnowledgeRetrievalSource(vault={self.vault!r}, contexts={self.contexts})"

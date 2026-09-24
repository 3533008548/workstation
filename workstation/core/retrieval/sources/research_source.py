"""科研助手论文库检索源（context = thesis，毕设域）。

从科研助手的 `/api/v1/workspace/papers` 拉本地论文库条目做词法召回。

两点要说清楚，免得日后误判能力边界：

1. **只有标题可检索。** 对端这个端点返回的是 `{paper_id, title, chunks,
   indexed_at, relation_count}` ——没有摘要、没有正文。所以这里做的是**标题级**
   词法召回，不是全文语义检索。真正的语义检索在对端的 Chroma 里。
2. **服务不可用 → 返回空命中，不抛。** 与 `KnowledgeRetrievalSource` 同策略：
   Docker 没起不该让一次跨源检索整个失败，只是这一源缺席。
"""

from __future__ import annotations

from ....skills.research import ResearchAgentClient, ResearchServiceConfig, ResearchServiceError
from ..source import Hit
from ._lexical import Chunk, lexical_rank


class ResearchRetrievalSource:
    """毕设域的论文库检索源。"""

    name = "research"
    contexts = ["thesis"]

    def __init__(
        self,
        service: ResearchServiceConfig | None = None,
        *,
        client: ResearchAgentClient | None = None,
    ) -> None:
        self._service = service or ResearchServiceConfig()
        self._client = client or ResearchAgentClient(self._service)

    def search(self, query: str, limit: int) -> list[Hit]:
        try:
            papers = self._client.list_papers()
        except ResearchServiceError:
            return []
        except Exception:  # noqa: BLE001 - 对端任何异常都只让这一源缺席
            return []

        chunks = [
            Chunk(
                source=self.name,
                doc_id=paper.paper_id,
                title=paper.title or paper.paper_id,
                text=" ".join(
                    part
                    for part in (
                        paper.title,
                        f"chunks={paper.chunks}" if paper.chunks else "",
                    )
                    if part
                ),
                uri=f"{self._service.base_url.rstrip('/')}/api/v1/workspace/papers",
                section="论文库",
            )
            for paper in papers
        ]
        return lexical_rank(chunks, query, limit)

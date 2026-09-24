"""检索门面 —— 源协议与命中结构。

阶段 4 的核心抽象。检索**按 context 收窄**（红线 #5）：每个源声明自己服务于
哪些场景（thesis / interview / default …），``RetrievalService`` 只查对应场景的
源；跨场景检索是显式 opt-in（``cross_context=True``），永不默认。

记忆/画像绝不在此合并 —— 这里只合并**检索结果排序**，不合并用户画像。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Hit:
    """一条检索命中。``doc_id`` 在同一源内须唯一，跨源可不同。"""

    source: str
    doc_id: str
    title: str
    text: str
    score: float = 0.0  # 源内相关性（RRF 只用排序，不依赖此值）
    page: int | None = None
    uri: str = ""
    section: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.source}/{self.doc_id}"


@runtime_checkable
class RetrievalSource(Protocol):
    """一个可被检索的内容源。"""

    name: str
    contexts: list[str]

    def search(self, query: str, limit: int) -> list[Hit]:
        """返回按相关性降序排列的命中（前 ``limit`` 条）。"""
        ...

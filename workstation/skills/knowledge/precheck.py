"""检索预检 —— 在起子进程之前先确认"这个检索值不值得跑"。

知识库桥接是只读检索，没有 PPT 那种页数门禁，预检只做两件最便宜的事：

1. vault 必须是一个存在的绝对目录路径（否则子进程会对空/错误目录做全量
   索引，浪费时间还返回误导性的 0 结果）；
2. query 不能为空。

与 PPT 的预检同构：权威在学校侧（知识库的 `parseRequest` 也会校验），这里
只负责早退。阶段 3 仅只读，所以没有审批/写入相关的门禁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

__all__ = ["SearchVerdict", "SearchFeasibility", "assess_search"]


class SearchVerdict(str, Enum):
    OK = "ok"
    VAULT_MISSING = "vault_missing"
    QUERY_EMPTY = "query_empty"


@dataclass(frozen=True)
class SearchFeasibility:
    verdict: SearchVerdict
    vault: str
    query: str
    reasons: list[str] = field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return self.verdict != SearchVerdict.OK

    def describe(self) -> str:
        head = f"{self.verdict.value}: vault={self.vault!r} query={self.query!r}"
        return "; ".join([head, *self.reasons])


def assess_search(vault: str, query: str) -> SearchFeasibility:
    """判断这次检索请求能不能跑。"""
    if not vault:
        return SearchFeasibility(
            SearchVerdict.VAULT_MISSING, vault, query, ["vault 不能为空"]
        )
    vault_path = Path(vault)
    if not vault_path.is_absolute():
        return SearchFeasibility(
            SearchVerdict.VAULT_MISSING, vault, query, ["vault 必须是绝对路径（勿传 /d/... 形式的 Git-Bash 路径）"]
        )
    if not vault_path.is_dir():
        return SearchFeasibility(
            SearchVerdict.VAULT_MISSING, vault, query, [f"vault 目录不存在：{vault}"]
        )
    if not query or not query.strip():
        return SearchFeasibility(
            SearchVerdict.QUERY_EMPTY, vault, query, ["query 不能为空"]
        )
    return SearchFeasibility(SearchVerdict.OK, vault, query, [])

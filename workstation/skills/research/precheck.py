"""提交前的门禁：别把注定失败的请求发给对端。

科研助手是外部服务，失败模式比本地技能多一层：服务根本没起（Docker 没跑）。
与其让它在 `POST /runs` 处炸出一个难懂的连接错误，不如提前说人话。
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import RESEARCH_SCOPES


@dataclass(frozen=True)
class ResearchFeasibility:
    ok: bool
    verdict: str          # ok | service_down | empty_query | bad_scope
    detail: str = ""

    @property
    def is_blocking(self) -> bool:
        return not self.ok

    def describe(self) -> str:
        return f"{self.verdict}: {self.detail}" if self.detail else self.verdict


def assess_research(*, query: str, scope: str, service_up: bool) -> ResearchFeasibility:
    if not (query or "").strip():
        return ResearchFeasibility(False, "empty_query", "query 不能为空")
    if scope not in RESEARCH_SCOPES:
        return ResearchFeasibility(
            False, "bad_scope", f"scope 必须是 {', '.join(RESEARCH_SCOPES)}，收到 {scope!r}"
        )
    if not service_up:
        return ResearchFeasibility(
            False,
            "service_down",
            "科研助手服务未就绪（Docker 未启动？先 docker compose up -d）",
        )
    return ResearchFeasibility(True, "ok", f"scope={scope}")


#: 对端 run 状态 → 工作台 RunStatus。 unknown 一律映射 running（保守：没跑完）。
_REMOTE_STATUS_MAP = {
    "queued": "running",
    "running": "running",
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "partial_failed": "partial",
}


def map_remote_status(remote: str) -> str:
    return _REMOTE_STATUS_MAP.get((remote or "").strip().lower(), "running")


def is_remote_terminal(remote: str) -> bool:
    return (remote or "").strip().lower() in {
        "completed",
        "failed",
        "cancelled",
        "partial_failed",
    }

"""科研助手 HTTP 客户端 —— 所有出网都经 `HttpExecutor` 的 loopback 守门。

工作台自己**不**用 requests：走底座的 `HttpExecutor`，非 loopback 的 base_url
会在执行器层被直接拒绝（``EXECUTOR_REFUSED``），而不是靠这里自觉。
"""

from __future__ import annotations

import json
from typing import Any

from workstation.core.runtime import ExecutorError, HttpExecutor, HttpJob
from .contract import (
    API_PREFIX,
    ResearchPaper,
    ResearchRunHandle,
    ResearchServiceConfig,
    ResearchSession,
)


class ResearchServiceError(RuntimeError):
    """对端不可达、拒绝或返回非预期。"""


class ResearchAgentClient:
    """科研助手 `/api/v1` 的最小封装——只包工作台用得到的那几个端点。"""

    def __init__(
        self,
        config: ResearchServiceConfig | None = None,
        *,
        executor: HttpExecutor | None = None,
    ) -> None:
        self._cfg = config or ResearchServiceConfig()
        self._base = self._cfg.base_url.rstrip("/")
        headers: dict[str, str] = {}
        if self._cfg.api_token:
            headers["X-API-Key"] = self._cfg.api_token
        self._exec = executor or HttpExecutor(default_headers=headers)

    @property
    def base_url(self) -> str:
        return self._base

    # ---------------------------------------------------------------- 底层

    def _url(self, path: str) -> str:
        return f"{self._base}{API_PREFIX}{path}"

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        job = HttpJob(
            url=self._url(path),
            method=method,
            json_body=body,
            timeout_s=self._cfg.timeout_s,
        )
        outcome = self._exec.execute(job)

        if outcome.error and outcome.error.startswith(("EXECUTOR_REFUSED", "EXECUTOR_UNAVAILABLE")):
            raise ResearchServiceError(f"{outcome.error}（{self._base}）")
        if not outcome.ok:
            detail = (outcome.stdout or "").strip()[:300]
            raise ResearchServiceError(f"{method} {path} → {outcome.error} {detail}".strip())

        raw = (outcome.stdout or "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResearchServiceError(f"{method} {path} 返回非 JSON：{exc}") from exc

    # ---------------------------------------------------------------- 端点

    def health(self) -> bool:
        """对端 `/health`。连不上返回 False —— 供 doctor 与门禁使用，不抛。"""
        try:
            self._call("GET", "/health")
        except (ResearchServiceError, ExecutorError):
            return False
        return True

    def create_session(self, title: str | None = None) -> ResearchSession:
        payload = self._call("POST", "/sessions", {"title": title} if title else {})
        if not isinstance(payload, dict):
            raise ResearchServiceError(f"POST /sessions 返回意外形状：{type(payload).__name__}")
        return ResearchSession.model_validate(payload)

    def start_research(
        self, session_id: str, query: str, scope: str = "both"
    ) -> ResearchRunHandle:
        """POST /runs（kind=research）→ 202，返回句柄。研究本身在对端异步跑。"""
        payload = self._call(
            "POST",
            "/runs",
            {"kind": "research", "session_id": session_id, "query": query, "scope": scope},
        )
        if not isinstance(payload, dict):
            raise ResearchServiceError(f"POST /runs 返回意外形状：{type(payload).__name__}")
        return ResearchRunHandle.model_validate(payload)

    def get_run(self, run_id: str) -> dict[str, Any]:
        payload = self._call("GET", f"/runs/{run_id}")
        return payload if isinstance(payload, dict) else {"run_id": run_id, "raw": payload}

    def list_papers(self) -> list[ResearchPaper]:
        payload = self._call("GET", "/workspace/papers")
        if not isinstance(payload, list):
            return []
        return [ResearchPaper.model_validate(p) for p in payload if isinstance(p, dict)]

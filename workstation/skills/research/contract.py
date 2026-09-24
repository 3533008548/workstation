"""科研助手适配器契约（工作台侧）。

这里的模型分两类，**区别对待**：

* 工作台自己发出的请求（`ResearchTask`）：`extra="forbid"`，拼错字段立刻报错。
* 对端返回的载荷（`ResearchSession` / `ResearchRunHandle` / `ResearchPaper`）：
  `extra="ignore"`。对端是独立演进的外部服务，它多返回一个字段不该让工作台崩；
  我们只取用得到的部分。这是防腐层的正常姿态，不是放松。
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from workstation_contracts.base import ContractModel

#: 对端 FastAPI 的路由前缀（`api/routes.py`: ``APIRouter(prefix="/api/v1")``）。
API_PREFIX = "/api/v1"

DEFAULT_BASE_URL = "http://127.0.0.1:7860"

#: 对端 `RunCreateRequest.scope` 的取值。
RESEARCH_SCOPES = ("both", "local", "public")


class ResearchServiceConfig(ContractModel):
    """怎么找到科研助手。"""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default=DEFAULT_BASE_URL, description="例如 http://127.0.0.1:7860")
    api_token: str = Field(default="", description="对端 API_AUTH_REQUIRED=1 时填；走 X-API-Key")
    timeout_s: int = Field(default=30, ge=1)
    #: 提交研究任务后是否顺带建会话（对端 research 需要 session_id）。
    auto_session: bool = True


class ResearchTask(ContractModel):
    """工作台发给科研助手的任务输入。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=12_000)
    scope: str = Field(default="both", description="both | local | public")
    session_id: str = Field(default="", description="复用已有会话；留空则新建")
    session_title: str = Field(default="工作台·毕设", max_length=80)


class ResearchSession(ContractModel):
    """对端 `SessionResponse` 的工作台视图。"""

    model_config = ConfigDict(extra="ignore")

    thread_id: str
    title: str = ""
    preview: str = ""
    created_at: str = ""


class ResearchRunHandle(ContractModel):
    """对端 `RunStartResponse` —— 即工作台 Run 的 `checkpoint_ref` 所指之物。"""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    kind: str = "research"
    session_id: str | None = None
    project_id: str | None = None
    status: str = ""
    stream_url: str = ""

    def absolute_stream_url(self, base_url: str) -> str:
        """/api/v1/... → http://127.0.0.1:7860/api/v1/..."""
        if self.stream_url.startswith("http"):
            return self.stream_url
        return f"{base_url.rstrip('/')}/{self.stream_url.lstrip('/')}"


class ResearchPaper(ContractModel):
    """对端 `PaperResponse`（本地论文库条目）。"""

    model_config = ConfigDict(extra="ignore")

    paper_id: str
    title: str = ""
    chunks: int = 0
    indexed_at: str = ""
    relation_count: int = 0

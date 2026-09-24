"""阶段 6 · 科研助手接入（毕设域）。

形态：科研助手是**容器化的长驻 HTTP 服务**（`127.0.0.1:7860`），与 PPT/知识库的
「一次性子进程」不同——这是有理由的不对称，不是例外：

| 项目 | 形态 | 为什么 |
|---|---|---|
| PPTAgent | 子进程 | `pptxgenjs` 模块级状态串扰，必须进程隔离 |
| 知识库 | 子进程 | 无 HTTP 服务；`core/` 纯 Node，一次性调用即可 |
| **科研助手** | **HTTP** | 自带编排/队列/向量库，本就是长驻服务；靠 `HttpExecutor` 的 loopback 守门 |

对端 `/runs` 是 **202 异步**：提交即返回句柄。这正好落在阶段 1b 预留的两个抽象上——
`checkpoint_ref` 存对端的 run 句柄，`Resumable.resume()` 轮询把状态并回工作台 Run。
"""

from .client import ResearchAgentClient, ResearchServiceError
from .contract import (
    API_PREFIX,
    ResearchPaper,
    ResearchRunHandle,
    ResearchServiceConfig,
    ResearchSession,
    ResearchTask,
)
from .skill import RESEARCH_SKILL_NAME, ResearchSkill, ResearchSkillConfig

__all__ = [
    "API_PREFIX",
    "RESEARCH_SKILL_NAME",
    "ResearchAgentClient",
    "ResearchPaper",
    "ResearchRunHandle",
    "ResearchServiceConfig",
    "ResearchServiceError",
    "ResearchSession",
    "ResearchSkill",
    "ResearchSkillConfig",
    "ResearchTask",
]

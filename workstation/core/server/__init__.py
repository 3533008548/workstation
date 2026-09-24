"""阶段 5 · 统一入口：只监听 loopback 的本地工作台服务。

为什么用标准库 ``http.server`` 而不是 FastAPI / uvicorn：

  * 单用户、本机、不分发 —— 用不上 ASGI 的并发模型；
  * 本项目至今只依赖 pydantic，引入整个网络栈会撑大"数据不出机器"的最小依赖面；
  * 守门只要一行：非 loopback 一律拒绝启动，不需要中间件栈来兜。

守门（红线）：
  * 只绑 ``127.0.0.1`` / ``localhost`` / ``::1``（红线：数据不出本机）；
  * 不新增持久化点 —— 所有状态仍经 ``RunStore``（红线 #4）；
  * 检索按 ``context`` 收窄，跨域须显式 ``cross_context``（红线 #5）。
"""

from .app import (
    LOOPBACK_HOSTS,
    ServerError,
    Workbench,
    build_workbench,
    create_server,
    serve,
)

__all__ = [
    "LOOPBACK_HOSTS",
    "ServerError",
    "Workbench",
    "build_workbench",
    "create_server",
    "serve",
]

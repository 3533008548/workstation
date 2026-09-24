"""knowledge-bridge/1 的 Python 镜像 —— 与知识库侧 `src/bridge-schema.ts` 同形。

两侧都能定义，且两侧都严格（TS 侧手写 strict 校验，这里用
``extra="forbid"``）。这是**双向契约测试**：

* 知识库给响应加一个字段 → 这里的 ``BridgeResponse`` 解析立刻抛错；
* 工作台给请求加一个字段 → 知识库的 `parseRequest` 立刻抛错。

字段名一律走 camelCase 别名，线上就是 camelCase，Python 内部仍是 snake_case。

来源 id 的格式 ``src_<24 hex>`` 仍由 `workstation_contracts.source_ref.derive_source_id`
统一生成；知识库侧的 `source.contentHash` 只是它解析器产出的 8 位哈希，用作
去重盐，不直接当作 sha256 落库。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from workstation_contracts.base import ContractModel

__all__ = [
    "BRIDGE_API_VERSION",
    "BridgeChunk",
    "BridgeRequest",
    "BridgeResponse",
    "BridgeResult",
    "BridgeSourceRef",
]

BRIDGE_API_VERSION = "knowledge-bridge/1"


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


class BridgeModel(ContractModel):
    """线上是 camelCase，本地是 snake_case；`extra="forbid"` 由基类继承。"""

    model_config = {"alias_generator": _to_camel, "populate_by_name": True}

    def to_wire(self) -> dict[str, Any]:
        """序列化成线上形状：camelCase，且丢掉 None（TS 侧对应 undefined）。"""
        return self.model_dump(by_alias=True, exclude_none=True)


# ------------------------------------------------------------------- 请求侧


class BridgeRequest(BridgeModel):
    api_version: Literal["knowledge-bridge/1"] = BRIDGE_API_VERSION
    mode: Literal["search"] = "search"
    vault: str = Field(min_length=1, description="Vault 绝对路径（Windows 原生格式，勿传 /d/...）")
    query: str = Field(min_length=1)
    limit: int = Field(default=8, gt=0)
    scope: list[str] | None = None


# ------------------------------------------------------------------- 响应侧


class BridgeSourceRef(BridgeModel):
    type: str
    path_or_url: str
    locator: str
    content_hash: str
    parser_version: str
    retrieved_at: str | None = None


class BridgeChunk(BridgeModel):
    content: str
    heading: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    start_line: int
    end_line: int
    source: BridgeSourceRef


class BridgeResult(BridgeModel):
    score: float
    excerpt: str
    chunk: BridgeChunk


class BridgeResponse(BridgeModel):
    """一次桥接调用的全部结果。失败也是响应（ok=false），不是异常。"""

    api_version: str
    ok: bool
    error_code: str | None = None
    error: str | None = None
    indexed_files: int = 0
    skipped_files: int = 0
    chunk_count: int = 0
    elapsed_ms: int = 0
    results: list[BridgeResult] = Field(default_factory=list)

    def describe_failure(self) -> str:
        return f"{self.error_code or 'BRIDGE_FAILED'}: {self.error or 'no detail'}"

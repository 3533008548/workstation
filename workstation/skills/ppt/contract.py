"""ppt-bridge/1 的 Python 镜像 —— 与 PPTAgent 侧 `src/bridge-schema.ts` 同形。

两侧都能定义，且两侧都严格（TS 用 zod 的 ``.strict()``，这里用
``extra="forbid"``）。这不是重复劳动，而是**双向的契约测试**：

* PPTAgent 给响应加一个字段 → 这里的 ``BridgeResponse`` 解析立刻抛错；
* 工作台给请求加一个字段 → PPTAgent 的 zod 立刻抛错。

因此"另一侧改了字段我这边不知道"这种漂移不可能悄悄发生。字段名一律走
camelCase 别名，线上就是 camelCase，Python 内部仍是 snake_case。

来源 id 的格式 ``src_<24 hex>`` 由两侧共同保证，形状定义在
``workstation_contracts.source_ref.derive_source_id``。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator

from workstation_contracts.base import ContractModel

__all__ = [
    "BRIDGE_API_VERSION",
    "SOURCE_ID_PATTERN",
    "BridgeAsset",
    "BridgeArtifacts",
    "BridgeBrief",
    "BridgeFact",
    "BridgeFactUsage",
    "BridgeInjectionWarning",
    "BridgeIssue",
    "BridgeModel",
    "BridgeModelTraceItem",
    "BridgeOutput",
    "BridgePagePlan",
    "BridgePagePlanReport",
    "BridgePptxVerification",
    "BridgeRequest",
    "BridgeResponse",
    "BridgeSpecVerification",
    "BridgeTheme",
    "PptxChecks",
]

BRIDGE_API_VERSION = "ppt-bridge/1"
SOURCE_ID_PATTERN = re.compile(r"^src_[0-9a-f]{24}$")


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


class BridgeFact(BridgeModel):
    """事实。`source_ids` 必填 ≥1 —— 没有来源的事实不是事实。"""

    fact_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"] = "medium"
    kind: str | None = None
    qualifiers: dict[str, Any] | None = None
    evidence: str | None = None

    @field_validator("source_ids")
    @classmethod
    def _check_ids(cls, value: list[str]) -> list[str]:
        for item in value:
            if not SOURCE_ID_PATTERN.match(item):
                raise ValueError(
                    f"source_ids must be content-addressed ids shaped like src_<24 hex>, got {item!r}"
                )
        return value


class BridgeBrief(BridgeModel):
    title: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    language: str = Field(default="中文", min_length=1)
    tone: str = Field(default="专业", min_length=1)
    required_points: list[str] = Field(default_factory=list)
    mandatory_elements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class BridgeTheme(BridgeModel):
    colors: list[str] = Field(min_length=1, max_length=4)
    body_font: str = Field(min_length=1)
    heading_font: str = Field(min_length=1)
    density: Literal["low", "medium", "high"]


class BridgeAsset(BridgeModel):
    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    kind: Literal["text", "image", "table"]
    description: str = ""


class BridgePagePlan(BridgeModel):
    """页数门禁的输入。`requested=None` 表示交给证据量决定。"""

    requested: int | None = Field(default=None, gt=0)
    facts_per_page: float = Field(default=3.0, gt=0)
    max_pages: int | None = Field(default=None, gt=0)
    accept_padding: bool = False


class BridgeOutput(BridgeModel):
    pptx_path: str = Field(min_length=1)
    deck_path: str | None = None
    report_path: str | None = None


class BridgeRequest(BridgeModel):
    api_version: Literal["ppt-bridge/1"] = BRIDGE_API_VERSION
    mode: Literal["plan", "render"] = "plan"
    brief: BridgeBrief
    facts: list[BridgeFact] = Field(default_factory=list)
    assets: list[BridgeAsset] = Field(default_factory=list)
    material: str | None = None
    page_plan: BridgePagePlan = Field(default_factory=BridgePagePlan)
    theme: BridgeTheme | None = None
    deck_spec: dict[str, Any] | None = None
    output: BridgeOutput
    run_id: str | None = None


# ------------------------------------------------------------------- 响应侧


class BridgeIssue(BridgeModel):
    severity: Literal["error", "warning"]
    code: str
    slide_id: str | None = None
    message: str


class BridgeFactUsage(BridgeModel):
    """fact → page 的反向映射，供底座把引用关系回填进事实账本。"""

    fact_id: str
    source_ids: list[str] = Field(default_factory=list)
    used_on_pages: list[str] = Field(default_factory=list)


class BridgePagePlanReport(BridgeModel):
    requested_pages: int | None = None
    effective_pages: int
    recommended_pages: int
    available_facts: int
    facts_per_page: float
    padding_accepted: bool


class BridgeInjectionWarning(BridgeModel):
    code: str
    block_id: str
    excerpt: str


class BridgeModelTraceItem(BridgeModel):
    task: str
    model: str


class PptxChecks(BridgeModel):
    """五项结构性检查。刻意逐项列名：PPTAgent 加一项检查，这里必须同步。"""

    slide_count: bool
    page_numbers: bool
    bounds: bool
    editable_tables: bool
    editable_charts: bool


class BridgePptxVerification(BridgeModel):
    ok: bool
    slide_count: int
    checks: PptxChecks
    stats: dict[str, float] = Field(default_factory=dict)
    issues: list[BridgeIssue] = Field(default_factory=list)


class BridgeSpecVerification(BridgeModel):
    ok: bool
    issues: list[BridgeIssue] = Field(default_factory=list)


class BridgeArtifacts(BridgeModel):
    pptx_path: str
    deck_path: str | None = None
    report_path: str | None = None


class BridgeResponse(BridgeModel):
    """一次桥接调用的全部结果。失败也是响应，不是异常。"""

    api_version: str
    ok: bool
    mode: Literal["plan", "render"] | None = None
    run_id: str | None = None
    error_code: str | None = None
    error: str | None = None
    artifacts: BridgeArtifacts | None = None
    slide_count: int | None = None
    page_ids: list[str] | None = None
    facts: list[BridgeFactUsage] | None = None
    page_plan: BridgePagePlanReport | None = None
    injection_warnings: list[BridgeInjectionWarning] | None = None
    model_trace: list[BridgeModelTraceItem] | None = None
    spec_verification: BridgeSpecVerification | None = None
    pptx_verification: BridgePptxVerification | None = None
    warnings: list[str] | None = None

    @property
    def used_fact_ids(self) -> list[str]:
        """真正被页面引用到的事实 id（不含仅登记未使用的）。"""
        return sorted(
            usage.fact_id for usage in self.facts or [] if usage.used_on_pages
        )

    @property
    def source_ids(self) -> list[str]:
        """本次产出涉及的全部来源 id，用于 Run.source_ids 回填。"""
        seen: dict[str, None] = {}
        for usage in self.facts or []:
            for source_id in usage.source_ids:
                seen.setdefault(source_id, None)
        return list(seen)

    def describe_failure(self) -> str:
        return f"{self.error_code or 'BRIDGE_FAILED'}: {self.error or 'no detail'}"

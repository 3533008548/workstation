"""ppt-bridge/1 线格式的 Python 侧契约。

这些测试的职责是"红灯亮在自己仓库里"：PPTAgent 改了响应形状、给事实加了
字段、或者来源 id 的算法变了，失败必须出现在这里，而不是某个晚上运行时
才炸。反过来，工作台往请求里塞了新字段，PPTAgent 的 zod 也会拒。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ppt_fakes import SOURCE_A, SOURCE_B, bridge_response
from workstation.skills.ppt.contract import (
    BRIDGE_API_VERSION,
    BridgeBrief,
    BridgeFact,
    BridgeOutput,
    BridgePagePlan,
    BridgeRequest,
    BridgeResponse,
)
from workstation_contracts import derive_source_id


def test_derived_source_ids_match_the_node_implementation() -> None:
    """与 PPTAgent `tests/isolation.test.ts` 里同一组向量对钉。"""
    assert derive_source_id("spec/demo.deck.json") == "src_69bbfb90cbab01e519deee03"
    assert derive_source_id("fixture://pptagent/demo-q3-review") == "src_37c90c905963bf47cd330660"
    assert derive_source_id("paper://doi/10.1000/xyz", "deadbeef") == "src_3458e82dc7bc29705926c1b0"


def test_requests_serialize_to_camel_case_and_drop_none() -> None:
    request = BridgeRequest(
        brief=BridgeBrief(title="季度复盘", audience="管理层", goal="确认行动"),
        facts=[
            BridgeFact(fact_id="f1", text="MAU 增长到 185 万", source_ids=[SOURCE_A]),
        ],
        page_plan=BridgePagePlan(requested=4, accept_padding=False),
        output=BridgeOutput(pptx_path="deck.pptx"),
    )

    wire = request.to_wire()

    assert wire["apiVersion"] == BRIDGE_API_VERSION
    assert wire["pagePlan"]["factsPerPage"] == 3.0
    assert wire["facts"][0]["factId"] == "f1"
    assert wire["facts"][0]["sourceIds"] == [SOURCE_A]
    # None 不下发：TS 侧对应 undefined。
    assert "material" not in wire
    assert "theme" not in wire
    assert "deckPath" not in wire["output"]


def test_a_request_can_be_parsed_back_from_its_own_wire_form() -> None:
    request = BridgeRequest(
        brief=BridgeBrief(title="t", audience="a", goal="g"),
        output=BridgeOutput(pptx_path="deck.pptx", report_path="report.json"),
        run_id="run_1",
    )

    restored = BridgeRequest.model_validate(request.to_wire())

    assert restored == request


def test_unknown_request_fields_are_rejected() -> None:
    payload = BridgeRequest(
        brief=BridgeBrief(title="t", audience="a", goal="g"),
        output=BridgeOutput(pptx_path="deck.pptx"),
    ).to_wire()
    payload["pageplan"] = {"requested": 30}  # 拼写错误

    with pytest.raises(ValidationError):
        BridgeRequest.model_validate(payload)


def test_a_fact_without_source_ids_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BridgeFact(fact_id="f1", text="无来源的断言", source_ids=[])


def test_source_ids_must_be_content_addressed() -> None:
    with pytest.raises(ValidationError):
        BridgeFact(fact_id="f1", text="t", source_ids=["fact-1"])


def test_response_rejects_a_field_pptagent_added_but_we_do_not_know() -> None:
    """这正是复现旧项目 'README 已过期' 那类问题的地方。"""
    payload = bridge_response(somethingNew={"nested": True})

    with pytest.raises(ValidationError) as excinfo:
        BridgeResponse.model_validate(payload)

    assert "somethingNew" in str(excinfo.value)


def test_response_rejects_a_missing_required_field() -> None:
    payload = bridge_response()
    del payload["apiVersion"]

    with pytest.raises(ValidationError):
        BridgeResponse.model_validate(payload)


def test_verification_blocks_are_optional_on_purpose() -> None:
    # 失败响应本来就没有 verification，所以形状上是可选的；
    # "成功响应必须带校验结果" 这条由适配器补，见 test_ppt_skill.py。
    payload = bridge_response()
    del payload["pptxVerification"]

    response = BridgeResponse.model_validate(payload)

    assert response.ok is True
    assert response.pptx_verification is None


def test_response_rejects_a_new_pptx_check_we_do_not_assert() -> None:
    payload = bridge_response()
    payload["pptxVerification"]["checks"]["fontFallback"] = True

    with pytest.raises(ValidationError):
        BridgeResponse.model_validate(payload)


def test_failure_responses_are_still_parseable() -> None:
    payload = {
        "apiVersion": BRIDGE_API_VERSION,
        "ok": False,
        "mode": "plan",
        "errorCode": "PAGE_BUDGET_EXCEEDS_EVIDENCE",
        "error": "Requested 30 pages but 4 citable facts only support about 1 pages.",
        "pagePlan": {
            "requestedPages": 30,
            "effectivePages": 1,
            "recommendedPages": 1,
            "availableFacts": 4,
            "factsPerPage": 3.0,
            "paddingAccepted": False,
        },
    }

    response = BridgeResponse.model_validate(payload)

    assert response.ok is False
    assert response.describe_failure().startswith("PAGE_BUDGET_EXCEEDS_EVIDENCE")
    assert response.page_plan is not None
    assert response.page_plan.recommended_pages == 1


def test_response_derives_source_ids_and_used_facts() -> None:
    response = BridgeResponse.model_validate(
        bridge_response(
            facts=[
                {"factId": "f1", "sourceIds": [SOURCE_A], "usedOnPages": ["page-02"]},
                {"factId": "f2", "sourceIds": [SOURCE_B], "usedOnPages": []},
                {"factId": "f3", "sourceIds": [SOURCE_A], "usedOnPages": ["page-03"]},
            ]
        )
    )

    assert response.used_fact_ids == ["f1", "f3"]
    # 去重且保序：SOURCE_A 出现两次，只回填一次。
    assert response.source_ids == [SOURCE_A, SOURCE_B]

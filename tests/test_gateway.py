"""Gateway behaviour: routing, fallback, circuit hopping, cost, dedupe."""

from __future__ import annotations

import json
import threading
from datetime import date

import pytest

from workstation.core.model_gateway.config import Priority, config_from_dict
from workstation.core.model_gateway.errors import (
    BudgetExceededError,
    CancelledError,
    ModelRoutingError,
)
from workstation.core.model_gateway.gateway import (
    CallPolicy,
    ModelGateway,
    ModelRequest,
    _parse_retry_after,
)
from workstation.core.model_gateway.pricing import PriceTable, cost_of
from workstation.core.model_gateway.transport import FakeTransport, HttpResponse, json_response
from workstation.core.model_gateway.usage import BudgetGovernor

from conftest import base_config, http_error, ok  # noqa: E402  (pytest puts tests/ on sys.path)


def _gw(fake: FakeTransport, **kw) -> ModelGateway:
    return ModelGateway(
        config_from_dict(base_config()),
        fake,
        governor=BudgetGovernor(today=date(2026, 9, 24), **kw.get("gov", {})),
        sleeper=lambda _s: None,
    )


def _req(task: str = "chat", user: str = "hello", **kw) -> ModelRequest:
    return ModelRequest(task=task, system="sys", user=user, **kw)


# -------------------------------------------------------------- happy path


def test_success_returns_parsed_json_and_usage(fake):
    fake.script["ds-chat"] = [ok("ds-chat", '{"answer": 42}', prompt_tokens=100, completion_tokens=50)]
    gw = _gw(fake)
    resp = gw.complete(_req())

    assert resp.data == {"answer": 42}
    assert resp.model == "ds-chat" and resp.provider == "deepseek"
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.llm_calls == 1
    assert not resp.used_fallback
    assert len(resp.attempts) == 1
    assert resp.attempts[0].outcome == "success"


def test_json_fence_and_prose_are_stripped(fake):
    fake.script["ds-chat"] = [ok("ds-chat", '```json\n{"a": 1}\n```')]
    assert _gw(fake).complete(_req()).data == {"a": 1}

    fake.script["ds-chat"] = [ok("ds-chat", 'Sure! {"a": 2} hope that helps')]
    assert _gw(fake).complete(_req()).data == {"a": 2}


def test_non_json_body_is_not_fatal_when_json_mode_off(fake):
    fake.script["ds-chat"] = [ok("ds-chat", "plain prose")]
    gw = _gw(fake)
    resp = gw.complete(_req(json_mode=False))
    assert resp.text == "plain prose" and resp.data is None


# ------------------------------------------------------------------ fallback


def test_falls_back_to_next_model_after_retryable_errors(fake):
    fake.script["ds-chat"] = [http_error(500), http_error(503)]
    fake.script["glm-chat"] = [ok("glm-chat")]
    gw = _gw(fake)
    resp = gw.complete(_req())

    assert resp.model == "glm-chat"
    assert resp.used_fallback, "fallback must be visible, never silent"
    assert [a.outcome for a in resp.attempts] == ["retryable_error", "retryable_error", "success"]
    assert fake.models_called == ["ds-chat", "ds-chat", "glm-chat"]


def test_permanent_error_skips_straight_to_next_model(fake):
    fake.script["ds-chat"] = [http_error(400, "bad request")]
    fake.script["glm-chat"] = [ok("glm-chat")]
    resp = _gw(fake).complete(_req())

    assert resp.model == "glm-chat"
    # A 4xx is a request bug -- retrying the identical payload cannot help.
    assert [a.outcome for a in resp.attempts] == ["permanent_error", "success"]
    assert fake.models_called == ["ds-chat", "glm-chat"]


def test_allow_fallback_false_disables_the_chain(fake):
    """The quality stages opt out: a cheaper fallback silently weakening a
    verification pass is exactly what research_agent refused to allow."""
    fake.script["ds-chat"] = [http_error(500), http_error(500)]
    gw = _gw(fake)
    with pytest.raises(ModelRoutingError):
        gw.complete(_req(task="verify"))
    assert fake.models_called == ["ds-chat", "ds-chat"], "must not reach glm"


def test_missing_api_key_is_permanent(fake, monkeypatch):
    monkeypatch.delenv("WORKSTATION_TEST_DS_KEY", raising=False)
    fake.script["glm-chat"] = [ok("glm-chat")]
    resp = _gw(fake).complete(_req())
    assert resp.model == "glm-chat"
    assert resp.attempts[0].outcome == "permanent_error"
    assert "API key" in (resp.attempts[0].error or "")


def test_all_routes_fail_raises_with_full_trail(fake):
    fake.script["ds-chat"] = [http_error(500), http_error(500)]
    fake.script["glm-chat"] = [http_error(500), http_error(500)]
    with pytest.raises(ModelRoutingError) as exc:
        _gw(fake).complete(_req())
    assert len(exc.value.attempts) == 4
    assert "ds-chat:retryable_error" in str(exc.value)


# ------------------------------------------------------------------- circuit


def test_open_circuit_hops_to_another_provider(fake):
    gw = _gw(fake)
    breaker = gw.circuits.for_provider("deepseek")
    for _ in range(3):
        breaker.allow_request(); breaker.record_failure()
    assert breaker.is_open

    fake.script["glm-chat"] = [ok("glm-chat")]
    resp = gw.complete(_req())
    assert resp.model == "glm-chat"
    assert resp.attempts[0].outcome == "circuit_open"
    assert fake.models_called == ["glm-chat"], "no request should reach deepseek"


def test_non_counting_stage_does_not_pollute_health_signal(fake):
    """verify/summary share the transport but must not move the circuit."""
    fake.script["ds-chat"] = [http_error(500), http_error(500)]
    gw = _gw(fake)
    breaker = gw.circuits.for_provider("deepseek")

    with pytest.raises(ModelRoutingError):
        gw.complete(_req(task="verify", policy=CallPolicy(counts_toward_circuit=False)))

    assert breaker.snapshot()["failures"] == 0, "non-counting probes must not accrue failures"
    assert not breaker.is_open


def test_counting_traffic_does_open_the_circuit(fake):
    fake.script["ds-chat"] = [http_error(500)] * 2
    fake.script["glm-chat"] = [http_error(500)] * 2
    raw = base_config()
    raw["admission"]["circuit_failure_threshold"] = 2  # matches attempts_per_model
    gw = ModelGateway(
        config_from_dict(raw), fake,
        governor=BudgetGovernor(today=date(2026, 9, 24)), sleeper=lambda _s: None,
    )
    with pytest.raises(ModelRoutingError):
        gw.complete(_req(task="chat"))  # chat: ds-chat -> glm-chat, both fail
    assert gw.circuits.for_provider("deepseek").is_open
    # glm took the same two failures, so it opens too -- providers fail independently.
    assert gw.circuits.for_provider("glm").is_open


# --------------------------------------------------------------------- dedupe


def test_identical_concurrent_requests_share_one_call(fake):
    gate = threading.Event()
    release = threading.Event()

    class Blocking(FakeTransport):
        def post(self, url, headers, payload, *, timeout):
            super().post(url, headers, payload, timeout=timeout)
            gate.set()
            release.wait(5)
            return json_response('{"ok": true}', payload["model"])

    transport = Blocking({"ds-chat": [ok("ds-chat")]})
    gw = ModelGateway(
        config_from_dict(base_config()), transport,
        governor=BudgetGovernor(today=date(2026, 9, 24)), sleeper=lambda _s: None,
    )

    outcomes = {}

    def worker(tag: str) -> None:
        outcomes[tag] = gw.complete(_req(user="same prompt"))

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); gate.wait(5)
    t2.start()
    release.set()
    t1.join(5); t2.join(5)

    assert transport.models_called == ["ds-chat"], "second caller must join, not re-issue"
    flags = {o.dedupe_shared for o in outcomes.values()}
    assert flags == {True, False}
    # Only the initiator is charged.
    assert gw.snapshot()["budget"]["daily"]["calls"] == 1


def test_different_prompts_are_not_deduped(fake):
    fake.script["ds-chat"] = [ok("ds-chat"), ok("ds-chat")]
    gw = _gw(fake)
    gw.complete(_req(user="a"))
    gw.complete(_req(user="b"))
    assert len(fake.calls) == 2


# --------------------------------------------------------------------- budget


def test_global_daily_ceiling_blocks_further_calls(fake):
    fake.script["ds-chat"] = [ok("ds-chat")] * 5
    gw = _gw(fake, gov={"daily_cny": 0.000001})
    gw.complete(_req())
    with pytest.raises(BudgetExceededError):
        gw.complete(_req())


def test_cost_is_computed_from_usage(fake):
    table = PriceTable({"ds-chat": (2.0, 8.0)})
    fake.script["ds-chat"] = [ok("ds-chat", prompt_tokens=1_000_000, completion_tokens=1_000_000)]
    gw = ModelGateway(
        config_from_dict(base_config()), fake,
        governor=BudgetGovernor(today=date(2026, 9, 24)), price_table=table,
        sleeper=lambda _s: None,
    )
    resp = gw.complete(_req())
    assert resp.cost.input_cny == 2.0
    assert resp.cost.output_cny == 8.0
    assert resp.usage.cost_cny == 10.0


def test_ledger_breaks_down_by_model_and_task(fake):
    fake.script["ds-chat"] = [ok("ds-chat", prompt_tokens=100, completion_tokens=50)]
    gw = _gw(fake)
    gw.complete(_req(task="chat"))
    snap = gw.snapshot()["budget"]
    assert snap["daily"]["by_task"]["chat"] > 0
    assert snap["daily"]["by_model"]["ds-chat"] > 0


def test_run_budget_allows_landing_exactly_on_the_ceiling():
    from workstation_contracts import Budget

    gov = BudgetGovernor(today=date(2026, 9, 24))
    budget = Budget(max_llm_calls=3, max_cost_cny=1.0)
    gov.check_run_budget(budget, spent_cny=1.0, calls=3)  # exactly at limit -> ok
    with pytest.raises(BudgetExceededError):
        gov.check_run_budget(budget, spent_cny=1.01, calls=3)
    with pytest.raises(BudgetExceededError):
        gov.check_run_budget(budget, spent_cny=0.5, calls=4)


# ------------------------------------------------------------------ misc


def test_cancellation_is_honoured(fake):
    event = threading.Event()
    event.set()
    gw = _gw(fake)
    with pytest.raises(CancelledError):
        gw.complete(_req(cancel_event=event))
    assert fake.calls == [], "must not dispatch a cancelled request"


def test_priority_is_recorded_and_defaults_to_research(fake):
    fake.script["ds-chat"] = [ok("ds-chat")]
    resp = _gw(fake).complete(_req(policy=CallPolicy(priority=Priority.INTERACTIVE)))
    assert resp.model == "ds-chat"


def test_payload_includes_response_format_when_json(fake):
    fake.script["ds-chat"] = [ok("ds-chat")]
    _gw(fake).complete(_req())
    assert fake.calls[0]["payload"]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5", 5.0),
        (" 0 ", 0.0),
        ("not-a-date", None),
        ("", None),
    ],
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


def test_malformed_json_body_is_retryable_then_gives_up(fake):
    fake.script["ds-chat"] = [
        HttpResponse(200, "not json at all"),
        HttpResponse(200, "still not json"),
    ]
    fake.script["glm-chat"] = [ok("glm-chat")]
    resp = _gw(fake).complete(_req())
    assert resp.model == "glm-chat"
    assert resp.attempts[0].outcome == "retryable_error"


def test_snapshot_exposes_every_subsystem(fake):
    fake.script["ds-chat"] = [ok("ds-chat")]
    gw = _gw(fake)
    gw.complete(_req())
    snap = gw.snapshot()
    assert {"total_calls", "admission", "circuits", "dedupe", "budget"} <= set(snap)
    assert snap["total_calls"] == 1

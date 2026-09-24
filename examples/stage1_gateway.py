"""Stage-1 smoke: the gateway under failure, budget pressure and fan-out.

    python examples/stage1_gateway.py

No API key and no network required -- every response is scripted.
"""

from __future__ import annotations

import sys
import threading
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workstation.core.model_gateway.config import config_from_dict  # noqa: E402
from workstation.core.model_gateway.errors import BudgetExceededError, ModelRoutingError
from workstation.core.model_gateway.gateway import CallPolicy, ModelGateway, ModelRequest
from workstation.core.model_gateway.pricing import PriceTable
from workstation.core.model_gateway.transport import FakeTransport, HttpResponse, json_response
from workstation.core.model_gateway.usage import BudgetGovernor
from workstation_contracts import Run, TaskRequest  # noqa: E402

CONFIG = {
    "providers": {
        "deepseek": {"base_url": "https://ds.test/v1", "api_key_env": "SMOKE_DS"},
        "glm": {"base_url": "https://glm.test/v1", "api_key_env": "SMOKE_GLM"},
    },
    "models": {"ds-chat": {"provider": "deepseek"}, "glm-chat": {"provider": "glm"}},
    "routes": {
        "classify": {"primary": "ds-chat", "fallbacks": ["glm-chat"]},
        "verify": {"primary": "ds-chat", "fallbacks": [], "allow_fallback": False},
    },
    "admission": {
        "max_concurrency": 4,
        "interactive_reserved_slots": 1,
        "circuit_failure_threshold": 3,
        "attempts_per_model": 2,
        "retry_base_delay_seconds": 0.01,
        "request_deadline_seconds": 30,
    },
}


def banner(text: str) -> None:
    print(f"\n=== {text} ===")


def main() -> int:
    import os

    os.environ.setdefault("SMOKE_DS", "k")
    os.environ.setdefault("SMOKE_GLM", "k")

    transport = FakeTransport()
    prices = PriceTable({"ds-chat": (2.0, 8.0), "glm-chat": (1.0, 4.0)})
    gw = ModelGateway(
        config_from_dict(CONFIG),
        transport,
        governor=BudgetGovernor(daily_cny=5.0, today=date(2026, 9, 24)),
        price_table=prices,
        sleeper=lambda _s: None,
    )

    # 1. happy path ------------------------------------------------------
    banner("1. normal call")
    transport.script["ds-chat"] = [json_response('{"intent": "search"}', "ds-chat",
                                                 prompt_tokens=800, completion_tokens=120)]
    r = gw.complete(ModelRequest(task="classify", system="classify intent", user="找近三年RAG综述"))
    print(f"model={r.model} used_fallback={r.used_fallback} "
          f"tokens={r.usage.prompt_tokens}+{r.usage.completion_tokens} cost=¥{r.usage.cost_cny:.5f}")

    # 2. provider outage -> visible fallback ------------------------------
    banner("2. deepseek 500s, fallback to glm (recorded, not silent)")
    transport.script["ds-chat"] = [HttpResponse(500, "upstream down")] * 2
    transport.script["glm-chat"] = [json_response('{"intent": "search"}', "glm-chat")]
    r = gw.complete(ModelRequest(task="classify", user="同上"))
    print(f"model={r.model} used_fallback={r.used_fallback}")
    for a in r.attempts:
        print(f"   - {a.model}/{a.provider} attempt{a.attempt}: {a.outcome} {a.error or ''}")

    # 3. quality stage refuses fallback -----------------------------------
    banner("3. verify route: allow_fallback=False")
    transport.script["ds-chat"] = [HttpResponse(500, "down")] * 2
    try:
        gw.complete(ModelRequest(task="verify", user="check this"))
    except ModelRoutingError as exc:
        print(f"refused to degrade: {exc}")

    # 4. circuit isolation ------------------------------------------------
    banner("4. non-counting stage must not pollute the health signal")
    before = gw.circuits.for_provider("deepseek").snapshot()["failures"]
    transport.script["ds-chat"] = [HttpResponse(500, "down")] * 2
    try:
        gw.complete(ModelRequest(task="verify", user="x",
                                 policy=CallPolicy(counts_toward_circuit=False)))
    except ModelRoutingError:
        pass
    after = gw.circuits.for_provider("deepseek").snapshot()["failures"]
    print(f"failures {before} -> {after} (must stay unchanged)")

    # 5. open circuit hops provider ---------------------------------------
    banner("5. circuit open -> requests skip the dead provider")
    breaker = gw.circuits.for_provider("deepseek")
    for _ in range(3):
        breaker.allow_request(); breaker.record_failure()
    transport.script["glm-chat"] = [json_response('{"ok": 1}', "glm-chat")]
    r = gw.complete(ModelRequest(task="classify", user="y"))
    print(f"served by {r.model}, first attempt outcome={r.attempts[0].outcome}")
    print(f"deepseek circuit: {breaker.snapshot()}")

    # 6. concurrent fan-out is deduped ------------------------------------
    banner("6. identical concurrent calls share one request")
    release = threading.Event()
    entered = threading.Event()

    class Slow(FakeTransport):
        def post(self, url, headers, payload, *, timeout):
            super().post(url, headers, payload, timeout=timeout)
            entered.set()
            release.wait(5)
            return json_response('{"ok": 1}', payload["model"])

    slow = Slow({"ds-chat": [json_response('{"ok": 1}', "ds-chat")]})
    gw2 = ModelGateway(config_from_dict(CONFIG), slow,
                       governor=BudgetGovernor(today=date(2026, 9, 24)), sleeper=lambda _s: None)
    out = {}

    def worker(tag):
        out[tag] = gw2.complete(ModelRequest(task="classify", user="same prompt"))

    t1 = threading.Thread(target=worker, args=("a",)); t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); entered.wait(5); t2.start(); release.set(); t1.join(5); t2.join(5)
    print(f"transport calls={len(slow.calls)} "
          f"shared_flags={sorted(o.dedupe_shared for o in out.values())}")

    # 7. global ceiling ---------------------------------------------------
    banner("7. global daily ceiling")
    gw3 = ModelGateway(config_from_dict(CONFIG), FakeTransport({"ds-chat": [json_response('{"a":1}', "ds-chat")]}),
                       governor=BudgetGovernor(daily_cny=0.000001, today=date(2026, 9, 24)),
                       sleeper=lambda _s: None)
    gw3.complete(ModelRequest(task="classify", user="first"))
    try:
        gw3.complete(ModelRequest(task="classify", user="second"))
    except BudgetExceededError as exc:
        print(f"blocked: {exc}")

    # 8. wiring into the Run contract -------------------------------------
    banner("8. gateway usage feeds the Run contract")
    run = Run.from_request(TaskRequest(skill="research.deep", inputs={"q": "RAG"},
                                       options={"budget": {"max_cost_cny": 0.01}}))
    step = run.add_step("classify", )
    gw4 = ModelGateway(config_from_dict(CONFIG),
                       FakeTransport({"ds-chat": [json_response('{"a":1}', "ds-chat",
                                                                prompt_tokens=5000,
                                                                completion_tokens=1000)]}),
                       governor=BudgetGovernor(today=date(2026, 9, 24)),
                       price_table=prices, sleeper=lambda _s: None)
    resp = gw4.complete(ModelRequest(task="classify", user="z"))
    step.usage = resp.usage
    run.recompute_usage()
    print(f"run cost=¥{run.usage.cost_cny:.5f} breach={run.budget_breach()}")

    banner("gateway snapshot")
    import json
    print(json.dumps(gw.snapshot(), ensure_ascii=False, indent=2)[:900])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

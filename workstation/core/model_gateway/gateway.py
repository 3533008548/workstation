"""The model gateway itself.

Synthesis of the three existing clients, keeping the strongest part of each:

============================================  ==========================================
from research_agent ``llm_client.py``         priority admission, reserved interactive
                                              slots, shared request budget, circuit
                                              breaker with ``counts_toward_circuit``,
                                              Retry-After aware backoff, cancellation
from PPTAgent ``llm.ts``                      ``ModelTask -> {primary, fallbacks}`` routing
                                              table, attempt trail, JSON fence stripping
from knowledge desktop ``*-client.ts``        in-flight deduplication, per-purpose
                                              token ceilings
new here                                      cost accounting + a global budget governor
============================================  ==========================================

One genuine conflict had to be resolved rather than merged:

**research_agent deliberately refuses model fallback** -- "发生故障时只会重试当前
配置的模型，失败后把可操作错误交给上层 UI，而不会在用户不知情的情况下改变回答
质量或成本". PPTAgent does the opposite and falls back silently across
providers.

Both are right about different risks: silent fallback hides quality and cost
changes; no fallback turns a transient provider blip into a user-visible
failure. The resolution is to keep fallback but make it **fully observable** --
every fallback is recorded in :class:`AttemptRecord`, attached to the Run step,
and can be disabled per-request via ``allow_fallback=False`` (which is what the
verify/quality stages use, since a cheaper fallback silently weakening a
verification pass is exactly the failure research_agent was guarding against).
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from workstation_contracts import Usage

from .admission import AdmissionController
from .circuit import CircuitBreakerRegistry
from .config import (
    DEFAULT_MODEL_PROVIDER,
    DEFAULT_ROUTES,
    GatewayConfig,
    Priority,
    ProviderSpec,
    RouteSpec,
    _guess_provider,
    config_from_dict,
)
from .dedupe import InFlightGate
from .errors import (
    BudgetExceededError,
    CancelledError,
    CircuitOpenError,
    ConfigError,
    GatewayError,
    ModelRoutingError,
    ResponseFormatError,
    TimeoutError,
)
from .pricing import CostBreakdown, PriceTable, cost_of
from .transport import HttpResponse, Transport, chat_payload
from .usage import BudgetGovernor

__all__ = [
    "AttemptRecord",
    "CallPolicy",
    "ModelGateway",
    "ModelRequest",
    "ModelResponse",
    "build_default_gateway",
]

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class AttemptRecord:
    """One attempt at one model. The audit trail for routing decisions."""

    model: str
    provider: str
    attempt: int
    outcome: str  # success | retryable_error | permanent_error | circuit_open
    latency_ms: int = 0
    error: str | None = None
    retry_after: str | None = None  # verbatim Retry-After header, if any

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "attempt": self.attempt,
            "outcome": self.outcome,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "retry_after": self.retry_after,
        }


@dataclass(frozen=True)
class CallPolicy:
    """Per-call overrides. Anything left None falls back to config."""

    priority: Priority = Priority.RESEARCH
    purpose: str = "chat"
    deadline_seconds: float | None = None
    max_retries: int | None = None
    allow_fallback: bool | None = None
    # Verification/summary stages share the transport but must not move the
    # health signal that governs interactive traffic.
    counts_toward_circuit: bool = True
    dedupe_key: str | None = None


@dataclass
class ModelRequest:
    task: str
    system: str = ""
    user: str = ""
    json_mode: bool | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    policy: CallPolicy = field(default_factory=CallPolicy)
    cancel_event: threading.Event | None = None

    def payload_key(self) -> str:
        """Stable key for dedupe. Same prompt + same task = same call."""
        import hashlib

        blob = f"{self.task}|{self.system}|{self.user}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


@dataclass
class ModelResponse:
    text: str
    model: str
    provider: str
    data: Any | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost: CostBreakdown | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    dedupe_shared: bool = False

    @property
    def used_fallback(self) -> bool:
        """True when the answer did not come from the route's primary model."""
        if not self.attempts:
            return False
        first = self.attempts[0].model
        return self.model != first

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "used_fallback": self.used_fallback,
            "attempts": [a.as_dict() for a in self.attempts],
            "usage": self.usage.model_dump(),
            "cost": self.cost.as_dict() if self.cost else None,
            "dedupe_shared": self.dedupe_shared,
        }


# ------------------------------------------------------------------- gateway


class ModelGateway:
    """Shared, budgeted, observable entry point for every model call."""

    def __init__(
        self,
        config: GatewayConfig,
        transport: Transport,
        *,
        governor: BudgetGovernor | None = None,
        price_table: PriceTable | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        config.validate()
        self.config = config
        self.transport = transport
        self.governor = governor or BudgetGovernor()
        self.prices = price_table or PriceTable()
        self.admission = AdmissionController(config.admission)
        self.circuits = CircuitBreakerRegistry(
            config.admission.circuit_failure_threshold,
            config.admission.circuit_recovery_seconds,
        )
        self.dedupe = InFlightGate()
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.RLock()
        self._total_calls = 0

    # ------------------------------------------------------------ public API

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute one model call under admission, budget and circuit control."""
        policy = request.policy
        route = self.config.route_for(request.task)
        deadline = policy.deadline_seconds or self.config.admission.request_deadline_seconds
        started = self._clock()

        if request.cancel_event is not None and request.cancel_event.is_set():
            raise CancelledError("request cancelled before dispatch")

        # Global ceiling first: refuse before spending anything.
        self.governor.check()

        dedupe_key = policy.dedupe_key or request.payload_key()

        def _run() -> ModelResponse:
            return self._dispatch(request, route, deadline, started)

        response, shared = self.dedupe.run(dedupe_key, _run)
        if shared:
            # The dedupe gate hands every waiter the SAME object. Mutating it
            # would make one caller's `dedupe_shared` overwrite another's, so
            # joiners get a copy: flagged as shared and billed zero (the
            # initiator already paid).
            return replace(response, dedupe_shared=True, usage=Usage())
        response.dedupe_shared = False
        self._after_call(request, response)
        return response

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        """Convenience: force JSON parsing and raise if the payload is not JSON."""
        request.json_mode = True if request.json_mode is None else request.json_mode
        return self.complete(request)

    # ------------------------------------------------------------- internals

    def _dispatch(
        self,
        request: ModelRequest,
        route: RouteSpec,
        deadline: float,
        started: float,
    ) -> ModelResponse:
        policy = request.policy
        allow_fallback = (
            route.allow_fallback if policy.allow_fallback is None else policy.allow_fallback
        )
        chain = route.chain if allow_fallback else (route.primary,)

        lease = self.admission.acquire(
            policy.priority,
            deadline_seconds=deadline,
            cancel_event=request.cancel_event,
        )
        try:
            attempts: list[AttemptRecord] = []
            for model_name in chain:
                model = self.config.model(model_name)
                provider = self.config.provider(model.provider)
                breaker = self.circuits.for_provider(provider.name)

                for attempt in range(1, self.config.admission.attempts_per_model + 1):
                    if request.cancel_event is not None and request.cancel_event.is_set():
                        raise CancelledError("request cancelled during dispatch")
                    elapsed = self._clock() - started
                    if elapsed >= deadline:
                        attempts.append(
                            AttemptRecord(model_name, provider.name, attempt, "retryable_error",
                                          error="deadline elapsed")
                        )
                        raise TimeoutError(
                            f"deadline {deadline:.1f}s elapsed before a successful call",
                            attempts=attempts,
                        )

                    if not breaker.allow_request():
                        remaining = breaker.remaining_seconds()
                        attempts.append(
                            AttemptRecord(model_name, provider.name, attempt, "circuit_open",
                                          error=f"circuit open, {remaining:.0f}s")
                        )
                        break  # try the next model in the chain

                    record = self._one_attempt(
                        request, route, model.name, provider, attempt, deadline - elapsed
                    )
                    attempts.append(record)

                    if record.outcome == "success":
                        # A response parsed from the wire; build it below.
                        return self._build_response(request, model.name, provider.name, attempts, record)
                    if record.outcome == "permanent_error":
                        break  # no point retrying this model
                    # retryable -> backoff then loop
                    delay = self._backoff(record, attempt)
                    if delay is None:
                        break
                    self._sleep(delay)

            raise ModelRoutingError(request.task, attempts)
        finally:
            self.admission.release(lease)

    def _one_attempt(
        self,
        request: ModelRequest,
        route: RouteSpec,
        model_name: str,
        provider: ProviderSpec,
        attempt: int,
        remaining: float,
    ) -> AttemptRecord:
        policy = request.policy
        breaker = self.circuits.for_provider(provider.name)
        began = self._clock()

        payload = chat_payload(
            model_name,
            request.system,
            request.user,
            temperature=(
                route.temperature if request.temperature is None else request.temperature
            ),
            max_tokens=(
                (route.max_tokens or self.config.model(model_name).max_output_tokens)
                if request.max_tokens is None
                else request.max_tokens
            ),
        )
        json_mode = route.json_mode if request.json_mode is None else request.json_mode
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        api_key = os.environ.get(provider.api_key_env, "")
        if not api_key:
            breaker.release_probe()
            return AttemptRecord(
                model_name, provider.name, attempt, "permanent_error",
                error=f"missing API key in env {provider.api_key_env}",
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **provider.extra_headers,
        }
        timeout = (
            min(provider.connect_timeout_seconds, remaining),
            min(provider.read_timeout_seconds, remaining),
        )

        try:
            response = self.transport.post(
                f"{provider.base_url.rstrip('/')}/chat/completions",
                headers,
                payload,
                timeout=timeout,
            )
        except CancelledError:
            breaker.release_probe()
            raise
        except Exception as exc:  # transport-level failure
            latency = int((self._clock() - began) * 1000)
            if policy.counts_toward_circuit:
                breaker.record_failure()
            else:
                breaker.release_probe()
            return AttemptRecord(
                model_name, provider.name, attempt, "retryable_error",
                latency_ms=latency, error=f"{type(exc).__name__}: {exc}",
            )

        latency = int((self._clock() - began) * 1000)

        if response.status_code == 429 or response.status_code >= 500:
            if policy.counts_toward_circuit:
                breaker.record_failure()
            else:
                breaker.release_probe()
            return AttemptRecord(
                model_name, provider.name, attempt, "retryable_error",
                latency_ms=latency,
                error=f"HTTP {response.status_code}: {response.body[:200]}",
                retry_after=response.headers.get("Retry-After"),
            )

        if not response.ok:
            # 4xx other than 429 is a request problem -- retrying cannot help.
            breaker.release_probe()
            return AttemptRecord(
                model_name, provider.name, attempt, "permanent_error",
                latency_ms=latency,
                error=f"HTTP {response.status_code}: {response.body[:200]}",
            )

        try:
            raw = json.loads(response.body)
            content = _message_content(raw)
            data = _extract_json(content) if json_mode else None
        except (ResponseFormatError, json.JSONDecodeError) as exc:
            # Malformed JSON is worth one retry: the model may comply next time.
            if policy.counts_toward_circuit:
                breaker.record_failure()
            else:
                breaker.release_probe()
            return AttemptRecord(
                model_name, provider.name, attempt, "retryable_error",
                latency_ms=latency, error=str(exc),
            )

        if policy.counts_toward_circuit:
            breaker.record_success()
        else:
            breaker.release_probe()

        record = AttemptRecord(model_name, provider.name, attempt, "success", latency_ms=latency)
        object.__setattr__(record, "_content", content)  # frozen dataclass escape hatch
        object.__setattr__(record, "_raw", raw)
        object.__setattr__(record, "_data", data)
        return record

    def _build_response(
        self,
        request: ModelRequest,
        model_name: str,
        provider_name: str,
        attempts: list[AttemptRecord],
        record: AttemptRecord,
    ) -> ModelResponse:
        raw: dict[str, Any] = getattr(record, "_raw", {})
        content: str = getattr(record, "_content", "")
        data = getattr(record, "_data", None)
        model = self.config.model(model_name)

        usage_raw = raw.get("usage") or {}
        prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
        completion_tokens = int(usage_raw.get("completion_tokens") or 0)
        cost = cost_of(model.name, provider_name, prompt_tokens, completion_tokens, self.prices)

        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            llm_calls=1,
            cost_cny=cost.total_cny,
        )
        return ModelResponse(
            text=content,
            model=model.name,
            provider=provider_name,
            data=data,
            attempts=list(attempts),
            usage=usage,
            cost=cost,
            raw=raw,
        )

    def _after_call(self, request: ModelRequest, response: ModelResponse) -> None:
        """Charge the call to the global ledger.

        Per-run ceilings are NOT enforced here on purpose: the gateway only sees
        one call at a time and would mistake "exactly at the limit" for "over
        the limit". The Run executor accumulates ``response.usage`` into
        ``Run.usage`` and calls ``Run.budget_breach()``, which already exists in
        the contract. Duplicating that check here would only add a second,
        subtly wrong copy.
        """
        with self._lock:
            self._total_calls += 1
        self.governor.record(response.cost, request.task)

    def _backoff(self, record: AttemptRecord, attempt: int) -> float | None:
        """Exponential backoff with jitter, honouring Retry-After when present."""
        spec = self.config.admission
        max_retries = spec.max_retries
        if attempt > max_retries:
            return None
        retry_after = record.retry_after
        if retry_after:
            parsed = _parse_retry_after(retry_after)
            if parsed is not None:
                return min(parsed, spec.retry_max_delay_seconds)
        delay = min(spec.retry_base_delay_seconds * (2 ** (attempt - 1)), spec.retry_max_delay_seconds)
        return delay + random.uniform(0, delay * 0.25)

    # ------------------------------------------------------------ telemetry

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "total_calls": self._total_calls,
                "admission": self.admission.snapshot(),
                "circuits": self.circuits.snapshot(),
                "dedupe": self.dedupe.snapshot(),
                "budget": self.governor.snapshot(),
            }


# ------------------------------------------------------------------ helpers


def _parse_retry_after(value: str) -> float | None:
    """Seconds from a Retry-After header, whether numeric or HTTP-date."""
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(tz=when.tzinfo) if when.tzinfo else _dt.datetime.now()
    return max(0.0, (when - now).total_seconds())


def _message_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ResponseFormatError("model response is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ResponseFormatError("model response has no choices")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise ResponseFormatError("model response has no message content")
    return content


def _extract_json(raw: str) -> Any:
    """Strip markdown fences, then parse. Tolerates prose around the object."""
    text = raw.strip()
    text = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ResponseFormatError("model response did not contain a JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ResponseFormatError(f"model response is not valid JSON: {exc}") from exc


def build_default_gateway(
    transport: Transport,
    *,
    raw_config: dict | None = None,
    governor: BudgetGovernor | None = None,
) -> ModelGateway:
    """Construct a gateway from the ``model:`` block, or sane defaults."""
    raw = raw_config or {}
    if not raw.get("providers") or not raw.get("models"):
        raw = {
            **raw,
            "providers": {
                "deepseek": {
                    "base_url": raw.get("base_url", "https://api.deepseek.com/v1"),
                    "api_key_env": "WORKSTATION_DEEPSEEK_API_KEY",
                },
                "glm": {
                    "base_url": raw.get("glm_base_url", "https://open.bigmodel.cn/api/paas/v4"),
                    "api_key_env": "WORKSTATION_GLM_API_KEY",
                },
            },
            "models": {
                name: {"provider": DEFAULT_MODEL_PROVIDER.get(name) or _guess_provider(name)}
                for name in _models_referenced_by(DEFAULT_ROUTES)
            },
        }
    return ModelGateway(config_from_dict(raw), transport, governor=governor)


def _models_referenced_by(routes: dict[str, RouteSpec]) -> list[str]:
    seen: dict[str, None] = {}
    for route in routes.values():
        for name in route.chain:
            seen.setdefault(name, None)
    return list(seen)

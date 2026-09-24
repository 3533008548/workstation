"""Gateway configuration: providers, models, routes, admission.

Configuration is declarative so that swapping a provider or re-pricing a model
never requires touching the gateway code. Loaded from
``config/workstation.yaml`` -> the ``model:`` block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from .errors import ConfigError

__all__ = [
    "ModelSpec",
    "Priority",
    "ProviderSpec",
    "RouteSpec",
    "AdmissionSpec",
    "GatewayConfig",
    "DEFAULT_ROUTES",
    "config_from_dict",
]


class Priority(IntEnum):
    """Admission classes sharing one gateway.

    Order matters: lower value = admitted first. ``RESERVED_SLOTS`` means a
    burst of BATCH work can never starve an INTERACTIVE request -- this is the
    single most important behaviour inherited from research_agent's client.
    """

    INTERACTIVE = 0
    RESEARCH = 1
    VERIFY = 2
    SUMMARY = 3
    BATCH = 4

    @property
    def is_background(self) -> bool:
        return self is not Priority.INTERACTIVE


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    context_window: int = 128_000
    max_output_tokens: int = 4_096
    capabilities: frozenset[str] = frozenset()
    enabled: bool = True
    # Pricing lives in pricing.py; these allow a per-model override.
    input_price_cny_per_mtok: float | None = None
    output_price_cny_per_mtok: float | None = None

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str
    kind: str = "openai-compatible"
    timeout_seconds: float = 90.0
    connect_timeout_seconds: float = 3.05
    read_timeout_seconds: float = 30.0
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteSpec:
    """Task -> primary model + ordered fallbacks.

    Shape adopted verbatim from PPTAgent's ``MODEL_ROUTES``, which was the
    clearest of the three implementations.
    """

    task: str
    primary: str
    fallbacks: tuple[str, ...] = ()
    allow_fallback: bool = True
    json_mode: bool = True
    temperature: float = 0.2
    max_tokens: int | None = None

    @property
    def chain(self) -> tuple[str, ...]:
        if not self.allow_fallback:
            return (self.primary,)
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True)
class AdmissionSpec:
    max_concurrency: int = 4
    interactive_reserved_slots: int = 1
    low_priority_max_concurrency: int = 1
    queue_size: int = 20
    attempts_per_model: int = 2
    retry_base_delay_seconds: float = 0.3
    retry_max_delay_seconds: float = 8.0
    circuit_failure_threshold: int = 3
    circuit_recovery_seconds: float = 120.0
    request_deadline_seconds: float = 45.0
    max_retries: int = 2


@dataclass(frozen=True)
class GatewayConfig:
    providers: dict[str, ProviderSpec]
    models: dict[str, ModelSpec]
    routes: dict[str, RouteSpec]
    admission: AdmissionSpec = AdmissionSpec()
    default_route: str = "chat"

    def route_for(self, task: str) -> RouteSpec:
        if task in self.routes:
            return self.routes[task]
        if self.default_route in self.routes:
            return self.routes[self.default_route]
        raise ConfigError(
            f"no route for task {task!r} and no default route {self.default_route!r}"
        )

    def model(self, name: str) -> ModelSpec:
        try:
            return self.models[name]
        except KeyError:
            raise ConfigError(f"unknown model {name!r}; known: {sorted(self.models)}") from None

    def provider(self, name: str) -> ProviderSpec:
        try:
            return self.providers[name]
        except KeyError:
            raise ConfigError(
                f"unknown provider {name!r}; known: {sorted(self.providers)}"
            ) from None

    def validate(self) -> None:
        """Fail fast on config mistakes rather than mid-run."""
        for task, route in self.routes.items():
            for model_name in route.chain:
                spec = self.model(model_name)
                if not spec.enabled:
                    raise ConfigError(f"route {task!r} uses disabled model {model_name!r}")
                self.provider(spec.provider)
        if self.admission.interactive_reserved_slots >= self.admission.max_concurrency:
            raise ConfigError(
                "interactive_reserved_slots must be < max_concurrency, "
                "otherwise batch traffic can starve interactive requests"
            )


# ------------------------------------------------------------------ defaults
#
# Model names below are the ones actually referenced in the three codebases,
# not invented ones:
#   research_agent  config.yaml ......... deepseek-flash (model + vision_model)
#   PPTAgent        src/llm.ts .......... deepseek-flash / deepseek-v4-flash /
#                                        deepseek-v4-pro / glm-5.3 / glm-5.3-flash
#   knowledge       src/main.ts ......... deepseek-v4-flash-vision-exp (vision)
#
# GLM is NOT dead globally: the knowledge desktop migrated off it, but PPTAgent
# still routes plan/compress to glm-5.3. So GLM stays a provider here -- it is
# the fallback/specialist lane, DeepSeek is the default lane.

DEFAULT_ROUTES: dict[str, RouteSpec] = {
    # Cheap, high-volume, latency-sensitive.
    "classify": RouteSpec(
        task="classify",
        primary="deepseek-flash",
        fallbacks=("glm-5.3-flash",),
        temperature=0.0,
        max_tokens=1_024,
    ),
    # Interactive prose. Fallback allowed because a waiting user beats a hard fail.
    "chat": RouteSpec(
        task="chat", primary="deepseek-flash", fallbacks=("glm-5.3-flash",), max_tokens=4_096
    ),
    # Long-context synthesis: PPT planning, research deep-dives.
    # Mirrors PPTAgent's MODEL_ROUTES, which puts glm-5.3 first for these.
    "plan": RouteSpec(
        task="plan", primary="glm-5.3", fallbacks=("deepseek-v4-pro",), max_tokens=8_192
    ),
    "compress": RouteSpec(
        task="compress", primary="glm-5.3", fallbacks=("deepseek-v4-pro",), max_tokens=8_192
    ),
    # Deterministic structured edits.
    "patch": RouteSpec(
        task="patch", primary="deepseek-flash", fallbacks=("deepseek-v4-flash",), temperature=0.0
    ),
    # Quality gate / verification: cheap and fast, must not pollute the health signal.
    "verify": RouteSpec(
        task="verify", primary="deepseek-flash", fallbacks=(), max_tokens=2_048, temperature=0.0
    ),
    # Vision. NOTE: this route sends local bytes to a cloud endpoint. It must be
    # gated by the egress policy (see docs/contracts/01 §5) -- the knowledge
    # desktop currently sends base64 images here with no such gate.
    "vision": RouteSpec(
        task="vision",
        primary="deepseek-v4-flash-vision-exp",
        fallbacks=(),
        allow_fallback=False,
        json_mode=False,
        max_tokens=2_048,
    ),
}

# Models referenced by DEFAULT_ROUTES, so a minimal config can be auto-built.
DEFAULT_MODEL_PROVIDER: dict[str, str] = {
    "deepseek-flash": "deepseek",
    "deepseek-v4-flash": "deepseek",
    "deepseek-v4-pro": "deepseek",
    "deepseek-v4-flash-vision-exp": "deepseek",
    "glm-5.3": "glm",
    "glm-5.3-flash": "glm",
}


def config_from_dict(raw: dict) -> GatewayConfig:
    """Build a GatewayConfig from the ``model:`` block of workstation.yaml."""
    providers: dict[str, ProviderSpec] = {}
    for name, spec in (raw.get("providers") or {}).items():
        providers[name] = ProviderSpec(
            name=name,
            base_url=spec["base_url"],
            api_key_env=spec.get("api_key_env", f"WORKSTATION_{name.upper()}_API_KEY"),
            kind=spec.get("kind", "openai-compatible"),
            timeout_seconds=float(spec.get("timeout_seconds", 90.0)),
            connect_timeout_seconds=float(spec.get("connect_timeout_seconds", 3.05)),
            read_timeout_seconds=float(spec.get("read_timeout_seconds", 30.0)),
            extra_headers=dict(spec.get("extra_headers") or {}),
        )

    models: dict[str, ModelSpec] = {}
    for name, spec in (raw.get("models") or {}).items():
        models[name] = ModelSpec(
            name=name,
            provider=spec.get("provider") or _guess_provider(name),
            context_window=int(spec.get("context_window", 128_000)),
            max_output_tokens=int(spec.get("max_output_tokens", 4_096)),
            capabilities=frozenset(spec.get("capabilities") or ()),
            enabled=bool(spec.get("enabled", True)),
            input_price_cny_per_mtok=spec.get("input_price_cny_per_mtok"),
            output_price_cny_per_mtok=spec.get("output_price_cny_per_mtok"),
        )

    routes: dict[str, RouteSpec] = {}
    for task, spec in (raw.get("routes") or {}).items():
        routes[task] = RouteSpec(
            task=task,
            primary=spec["primary"],
            fallbacks=tuple(spec.get("fallbacks") or ()),
            allow_fallback=bool(spec.get("allow_fallback", True)),
            json_mode=bool(spec.get("json_mode", True)),
            temperature=float(spec.get("temperature", 0.2)),
            max_tokens=spec.get("max_tokens"),
        )
    if not routes:
        routes = dict(DEFAULT_ROUTES)

    adm_raw = raw.get("admission") or {}
    admission = AdmissionSpec(**{k: v for k, v in adm_raw.items() if k in AdmissionSpec.__dataclass_fields__})

    cfg = GatewayConfig(
        providers=providers,
        models=models,
        routes=routes,
        admission=admission,
        default_route=str(raw.get("default_route", "chat")),
    )
    cfg.validate()
    return cfg


def _guess_provider(model_name: str) -> str:
    lowered = model_name.lower()
    if lowered.startswith("glm") or "glm" in lowered:
        return "glm"
    if "deepseek" in lowered:
        return "deepseek"
    return "openai"

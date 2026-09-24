"""Model gateway -- stage 1.

One shared entry point for every model call in the workbench, replacing the
three independent clients in research_agent, PPTAgent and the knowledge desktop.

    from workstation.core.model_gateway import build_default_gateway, ModelRequest, CallPolicy
    from workstation.core.model_gateway.transport import RequestsTransport

    gw = build_default_gateway(RequestsTransport())
    resp = gw.complete(ModelRequest(task="classify", system="...", user="..."))

See ``gateway.py`` for the provenance of each behaviour.
"""

from __future__ import annotations

from .admission import AdmissionController
from .circuit import CircuitBreaker, CircuitBreakerRegistry
from .config import (
    DEFAULT_ROUTES,
    AdmissionSpec,
    GatewayConfig,
    ModelSpec,
    Priority,
    ProviderSpec,
    RouteSpec,
    config_from_dict,
)
from .dedupe import InFlightGate
from .gateway import (
    AttemptRecord,
    CallPolicy,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    build_default_gateway,
)
from .pricing import CostBreakdown, PriceTable, cost_of
from .usage import BudgetGovernor, SpendLedger

__all__ = [
    "AdmissionController",
    "AdmissionSpec",
    "AttemptRecord",
    "BudgetGovernor",
    "CallPolicy",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CostBreakdown",
    "DEFAULT_ROUTES",
    "GatewayConfig",
    "InFlightGate",
    "ModelGateway",
    "ModelRequest",
    "ModelResponse",
    "ModelSpec",
    "PriceTable",
    "Priority",
    "ProviderSpec",
    "RouteSpec",
    "SpendLedger",
    "build_default_gateway",
    "config_from_dict",
    "cost_of",
]

"""Shared fixtures for gateway tests.

Every test runs against ``FakeTransport`` -- no network, no API key, no
``requests`` dependency, and no sleeping.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from workstation.core.model_gateway.config import AdmissionSpec, config_from_dict
from workstation.core.model_gateway.gateway import ModelGateway
from workstation.core.model_gateway.transport import FakeTransport, HttpResponse, json_response
from workstation.core.model_gateway.usage import BudgetGovernor

DS_KEY_ENV = "WORKSTATION_TEST_DS_KEY"
GLM_KEY_ENV = "WORKSTATION_TEST_GLM_KEY"


def base_config(**overrides) -> dict:
    raw = {
        "providers": {
            "deepseek": {
                "base_url": "https://ds.test/v1",
                "api_key_env": DS_KEY_ENV,
                "read_timeout_seconds": 5.0,
            },
            "glm": {
                "base_url": "https://glm.test/v1",
                "api_key_env": GLM_KEY_ENV,
                "read_timeout_seconds": 5.0,
            },
        },
        "models": {
            "ds-chat": {"provider": "deepseek", "max_output_tokens": 2048},
            "glm-chat": {"provider": "glm"},
            "glm-plus": {"provider": "glm"},
        },
        "routes": {
            "chat": {"primary": "ds-chat", "fallbacks": ["glm-chat"]},
            "plan": {"primary": "glm-plus", "fallbacks": ["ds-chat"]},
            "verify": {"primary": "ds-chat", "fallbacks": [], "allow_fallback": False},
        },
        "admission": {
            "max_concurrency": 4,
            "interactive_reserved_slots": 1,
            "low_priority_max_concurrency": 1,
            "queue_size": 8,
            "attempts_per_model": 2,
            "retry_base_delay_seconds": 0.001,
            "retry_max_delay_seconds": 0.002,
            "request_deadline_seconds": 30.0,
        },
    }
    raw.update(overrides)
    return raw


@pytest.fixture(autouse=True)
def api_keys(monkeypatch):
    monkeypatch.setenv(DS_KEY_ENV, "test-ds-key")
    monkeypatch.setenv(GLM_KEY_ENV, "test-glm-key")
    yield


@pytest.fixture
def fake() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def gateway(fake: FakeTransport) -> ModelGateway:
    cfg = config_from_dict(base_config())
    return ModelGateway(
        cfg,
        fake,
        governor=BudgetGovernor(today=date(2026, 9, 24)),
        sleeper=lambda _s: None,  # never actually sleep in tests
    )


def http_error(status: int, body: str = "boom", **headers) -> HttpResponse:
    return HttpResponse(status_code=status, body=body, headers=headers)


def ok(model: str, payload: str = '{"ok": true}', **kw) -> HttpResponse:
    return json_response(payload, model, **kw)

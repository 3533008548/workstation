"""Transport abstraction.

The gateway must be testable without network access and without an API key, so
the HTTP call is injected. ``RequestsTransport`` is the production
implementation and imports ``requests`` lazily -- the gateway package itself has
no hard HTTP dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = ["HttpResponse", "Transport", "RequestsTransport", "FakeTransport"]


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class Transport(Protocol):
    def post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: tuple[float, float],
    ) -> HttpResponse: ...


class RequestsTransport:
    """Production transport over ``requests``."""

    def post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: tuple[float, float],
    ) -> HttpResponse:
        import requests  # lazy: keeps the gateway importable without it

        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        return HttpResponse(
            status_code=response.status_code,
            body=response.text,
            headers=dict(response.headers),
        )


class FakeTransport:
    """Scripted transport for tests.

    ``script`` maps model name -> list of responses, consumed in order. A
    response may also be an exception instance, which is raised instead.
    """

    def __init__(self, script: dict[str, list[HttpResponse | Exception]] | None = None) -> None:
        self.script: dict[str, list[HttpResponse | Exception]] = script or {}
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: tuple[float, float],
    ) -> HttpResponse:
        model = payload.get("model", "")
        self.calls.append({"url": url, "model": model, "payload": payload})
        queue = self.script.setdefault(model, [])
        if not queue:
            raise AssertionError(f"FakeTransport: no scripted response for model {model!r}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def models_called(self) -> list[str]:
        return [c["model"] for c in self.calls]


def chat_payload(model: str, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
    """Build an OpenAI-compatible chat completion payload."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    payload.update(kwargs)
    return payload


def json_response(content: str, model: str, prompt_tokens: int = 100, completion_tokens: int = 50) -> HttpResponse:
    """Build a well-formed OpenAI-compatible response for tests/fixtures."""
    import json

    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    return HttpResponse(status_code=200, body=json.dumps(body, ensure_ascii=False))

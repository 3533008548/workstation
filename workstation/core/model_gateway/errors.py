"""Gateway error taxonomy.

Every failure the gateway raises is one of these, so callers (skills, the run
executor, the UI) can branch on cause instead of string-matching messages.
"""

from __future__ import annotations

from typing import Any


class GatewayError(RuntimeError):
    """Base class. Carries the attempt trail for observability."""

    def __init__(self, message: str, *, attempts: list[Any] | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []


class ConfigError(GatewayError):
    """Route/provider/key configuration is missing or inconsistent."""


class QueueFullError(GatewayError):
    """Admission queue is at capacity -- back off, do not retry immediately."""


class CircuitOpenError(GatewayError):
    """Provider circuit is open. ``retry_after`` is advisory."""

    def __init__(self, message: str, *, retry_after: float = 0.0, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class BudgetExceededError(GatewayError):
    """A ceiling (per-call, per-run, or global daily) was hit."""


class TimeoutError(GatewayError):  # noqa: A001 - deliberately shadows builtin in this namespace
    """Deadline elapsed during queueing or transport."""


class CancelledError(GatewayError):
    """Caller signalled cancellation."""


class ModelRoutingError(GatewayError):
    """Every model in the route failed. ``attempts`` holds the full trail."""

    def __init__(self, task: str, attempts: list[Any]) -> None:
        trail = ", ".join(f"{a.model}:{a.outcome}" for a in attempts) or "no attempts"
        super().__init__(f"all model routes failed for task {task!r} [{trail}]", attempts=attempts)
        self.task = task


class ResponseFormatError(GatewayError):
    """Transport succeeded but the payload could not be understood."""

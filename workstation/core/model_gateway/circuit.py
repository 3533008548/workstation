"""Per-provider circuit breaker.

Ported from research_agent's ``resilience.CircuitBreaker`` semantics, with the
detail that made that implementation worth copying preserved:

**A probe that does not count must not close a circuit opened by traffic that
does.** Optional quality stages (verification, summarisation) share the same
transport as interactive chat. If their successes reset the failure counter, a
provider that is failing every interactive request still looks healthy. Hence
``release_probe()`` -- it consumes the probe without recording a success.
"""

from __future__ import annotations

import threading
import time

__all__ = ["CircuitBreaker", "CircuitBreakerRegistry"]


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 120.0) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = int(failure_threshold)
        self.recovery_seconds = float(recovery_seconds)
        self._lock = threading.RLock()
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_out = False

    # ---------------------------------------------------------------- state

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._opened_at is not None and not self._recovered_locked()

    def remaining_seconds(self) -> float:
        """Seconds until the circuit closes again; 0 when already usable."""
        with self._lock:
            if self._opened_at is None:
                return 0.0
            elapsed = time.monotonic() - self._opened_at
            return max(0.0, self.recovery_seconds - elapsed)

    def _recovered_locked(self) -> bool:
        if self._opened_at is None:
            return True
        if time.monotonic() - self._opened_at >= self.recovery_seconds:
            self._opened_at = None
            self._failures = 0
            return True
        return False

    # ------------------------------------------------------------ admission

    def allow_request(self) -> bool:
        """Consume a slot. Caller MUST later call one of the record_* methods."""
        with self._lock:
            if self._recovered_locked():
                self._probe_out = True
                return True
            if self._opened_at is not None:
                return False
            self._probe_out = True
            return True

    def release_probe(self) -> None:
        """Abandon the in-flight probe without recording success or failure.

        Used when the request never actually reached the provider (queue full,
        cancelled, deadline before send) and when ``counts_toward_circuit`` is
        false.
        """
        with self._lock:
            self._probe_out = False

    # ------------------------------------------------------------- outcomes

    def record_success(self) -> None:
        with self._lock:
            self._probe_out = False
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._probe_out = False
            self._failures += 1
            if self._failures >= self.failure_threshold and self._opened_at is None:
                self._opened_at = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "open": self.is_open,
                "failures": self._failures,
                "remaining_seconds": round(self.remaining_seconds(), 2),
                "probe_out": self._probe_out,
            }


class CircuitBreakerRegistry:
    """One breaker per provider. Failures on DeepSeek must not open GLM."""

    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 120.0) -> None:
        self._threshold = failure_threshold
        self._recovery = recovery_seconds
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def for_provider(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(self._threshold, self._recovery)
            return self._breakers[name]

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {name: b.snapshot() for name, b in self._breakers.items()}

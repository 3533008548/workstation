"""In-flight request deduplication.

Ported from the knowledge desktop's ``InFlightRequestGate``. Its purpose there
was "keep repeated clicks from issuing the same external request twice"; the
same hazard exists here whenever a run fans out (retries, parallel sub-agents,
a user re-submitting while the first call is still in flight).

Semantics differ from a response cache in one important way: **only concurrent
requests are shared.** Once a call finishes the entry is dropped, so a later
identical call genuinely re-executes and gets fresh output.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

__all__ = ["InFlightGate"]

T = TypeVar("T")


class InFlightGate:
    def __init__(self) -> None:
        self._running: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.shared = 0

    def run(self, key: str, operation: Callable[[], T]) -> tuple[T, bool]:
        """Execute ``operation`` unless an identical key is already running.

        Returns ``(result, was_shared)``. ``was_shared`` is True when this
        caller joined an existing call instead of starting one -- useful for
        cost attribution, since a shared call must not be billed twice.
        """
        with self._lock:
            existing = self._running.get(key)
            if existing is not None:
                self.shared += 1
                placeholder, event = existing
                was_initiator = False
            else:
                placeholder: list[Any] = []
                event = threading.Event()
                self._running[key] = (placeholder, event)
                was_initiator = True

        if not was_initiator:
            event.wait()
            with self._lock:
                result, error = placeholder[0]
            if error is not None:
                raise error
            return result, True

        try:
            result = operation()
            with self._lock:
                placeholder.append((result, None))
            return result, False
        except BaseException as exc:  # noqa: BLE001 - must propagate to waiters
            with self._lock:
                placeholder.append((None, exc))
            raise
        finally:
            event.set()
            with self._lock:
                self._running.pop(key, None)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"running": len(self._running), "shared": self.shared}

"""Priority admission control: bounded concurrency with reserved interactive slots.

This is the behaviour that makes the gateway safe to share across all three
projects. Without it, a batch re-index in the knowledge layer eats every
concurrency slot and the interactive research session behind it stalls.

Rules (inherited from research_agent's client, generalised to 5 classes):

* ``max_concurrency`` hard ceiling on in-flight requests.
* ``interactive_reserved_slots`` slots that background traffic may never use,
  so an INTERACTIVE request is never blocked behind BATCH work.
* ``low_priority_max_concurrency`` tighter ceiling on the low classes, because
  a single deep-research fan-out should not occupy the whole pool.
* Queue is bounded; overflow raises ``QueueFullError`` instead of growing
  without limit.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .config import AdmissionSpec, Priority
from .errors import CancelledError, QueueFullError, TimeoutError

__all__ = ["AdmissionController", "Lease"]


@dataclass(frozen=True)
class Lease:
    """Handle for acquired capacity. Release exactly once."""

    slots: tuple[str, ...]

    def released_by(self, controller: "AdmissionController") -> None:
        controller.release(self)


class AdmissionController:
    def __init__(self, spec: AdmissionSpec) -> None:
        self.spec = spec
        max_c = max(1, spec.max_concurrency)
        reserved = min(max(0, spec.interactive_reserved_slots), max_c - 1)
        low_cap = min(max(1, spec.low_priority_max_concurrency), max_c - reserved)

        self._slots = threading.BoundedSemaphore(max_c)
        self._background_slots = threading.BoundedSemaphore(max_c - reserved)
        self._low_slots = threading.BoundedSemaphore(low_cap)

        self._lock = threading.RLock()
        self._waiting = 0
        self._in_flight = 0
        self._rejected = 0

    # ------------------------------------------------------------------ API

    def acquire(
        self,
        priority: Priority,
        *,
        deadline_seconds: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Lease:
        """Block until capacity is available, then return a Lease.

        Raises QueueFullError / TimeoutError / CancelledError. On success the
        caller MUST call ``release`` (or ``Lease.released_by``).
        """
        with self._lock:
            if self._waiting >= self.spec.queue_size:
                self._rejected += 1
                raise QueueFullError(
                    f"gateway queue full ({self.spec.queue_size} waiting); retry later"
                )
            self._waiting += 1

        deadline = None if deadline_seconds is None else time.monotonic() + deadline_seconds
        owned: list[str] = []
        try:
            for name, sem in self._semaphores_for(priority):
                if not self._acquire_one(sem, deadline, cancel_event, name):
                    raise TimeoutError(f"timeout waiting for {name} slot")
                owned.append(name)
            with self._lock:
                self._in_flight += 1
            return Lease(slots=tuple(owned))
        except BaseException:
            if owned:
                self._return(owned)
            raise
        finally:
            with self._lock:
                self._waiting -= 1

    def release(self, lease: Lease) -> None:
        self._return(list(lease.slots))
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    # ------------------------------------------------------------- internals

    def _semaphores_for(self, priority: Priority) -> list[tuple[str, threading.BoundedSemaphore]]:
        chain: list[tuple[str, threading.BoundedSemaphore]] = [("total", self._slots)]
        if priority.is_background:
            chain.append(("background", self._background_slots))
        if priority >= Priority.SUMMARY:
            chain.append(("low", self._low_slots))
        return chain

    def _acquire_one(
        self,
        sem: threading.BoundedSemaphore,
        deadline: float | None,
        cancel_event: threading.Event | None,
        name: str,
    ) -> bool:
        """Poll with a short wait so cancellation stays responsive."""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("request cancelled while queueing")
            if sem.acquire(blocking=False):
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            # 50 ms granularity: responsive enough for UI cancel, cheap enough
            # not to spin. A blocking acquire() would ignore cancel_event.
            if cancel_event is not None:
                cancelled = cancel_event.wait(0.05)
                if cancelled:
                    raise CancelledError("request cancelled while queueing")
            else:
                time.sleep(0.05)

    def _return(self, names: list[str]) -> None:
        lookup = {
            "total": self._slots,
            "background": self._background_slots,
            "low": self._low_slots,
        }
        for name in reversed(names):
            try:
                lookup[name].release()
            except ValueError:
                # Defensive: BoundedSemaphore.release() past the bound.
                pass

    # ------------------------------------------------------------ telemetry

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "waiting": self._waiting,
                "in_flight": self._in_flight,
                "rejected": self._rejected,
                "queue_size": self.spec.queue_size,
                "max_concurrency": self.spec.max_concurrency,
            }

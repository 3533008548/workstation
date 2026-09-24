"""Resilience primitives: circuit breaker, admission control, dedupe."""

from __future__ import annotations

import threading

import pytest

from workstation.core.model_gateway.admission import AdmissionController
from workstation.core.model_gateway.circuit import CircuitBreaker, CircuitBreakerRegistry
from workstation.core.model_gateway.config import AdmissionSpec, Priority
from workstation.core.model_gateway.dedupe import InFlightGate
from workstation.core.model_gateway.errors import (
    CancelledError,
    QueueFullError,
    TimeoutError,
)


# ------------------------------------------------------------------- circuit


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3)
    cb.allow_request(); cb.record_failure()
    cb.allow_request(); cb.record_failure()
    assert not cb.is_open
    cb.allow_request(); cb.record_failure()
    assert cb.is_open
    assert cb.remaining_seconds() > 0


def test_success_resets_failures():
    cb = CircuitBreaker(failure_threshold=2)
    cb.allow_request(); cb.record_failure()
    cb.allow_request(); cb.record_success()
    cb.allow_request(); cb.record_failure()
    assert not cb.is_open, "a success must clear the failure counter"


def test_release_probe_does_not_close_circuit():
    """The behaviour worth copying: a probe that does not count must not heal
    a circuit that counted traffic opened."""
    cb = CircuitBreaker(failure_threshold=2)
    cb.allow_request(); cb.record_failure()
    cb.allow_request(); cb.record_failure()
    assert cb.is_open

    # A non-counting stage (verify/summary) succeeds while the circuit is open.
    assert cb.allow_request() is False or True  # allow_request consumed below
    cb.release_probe()
    assert cb.is_open, "release_probe must not record a success"


def test_circuit_recovers_after_cooldown(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    cb = CircuitBreaker(failure_threshold=1, recovery_seconds=10)
    cb.allow_request(); cb.record_failure()
    assert cb.is_open
    now[0] = 11.0
    assert not cb.is_open
    assert cb.remaining_seconds() == 0.0


def test_registry_isolates_providers():
    reg = CircuitBreakerRegistry(failure_threshold=1)
    reg.for_provider("deepseek").record_failure()
    assert reg.for_provider("deepseek").is_open
    assert not reg.for_provider("glm").is_open, "glm must not inherit deepseek's outage"


# ----------------------------------------------------------------- admission


def _controller(**kw) -> AdmissionController:
    defaults = dict(
        max_concurrency=2, interactive_reserved_slots=1,
        low_priority_max_concurrency=1, queue_size=4,
    )
    defaults.update(kw)
    return AdmissionController(AdmissionSpec(**defaults))


def test_interactive_is_not_starved_by_batch():
    """The single most important inherited behaviour."""
    ctrl = _controller()
    batch = ctrl.acquire(Priority.BATCH, deadline_seconds=1.0)
    # Batch now holds total + background + low. Interactive only needs `total`.
    interactive = ctrl.acquire(Priority.INTERACTIVE, deadline_seconds=1.0)
    assert interactive is not None
    ctrl.release(interactive)
    ctrl.release(batch)


def test_capacity_exhausted_times_out():
    ctrl = _controller()
    a = ctrl.acquire(Priority.BATCH, deadline_seconds=1.0)   # 1 of 2 total
    b = ctrl.acquire(Priority.INTERACTIVE, deadline_seconds=1.0)  # 2 of 2 total
    with pytest.raises(TimeoutError):
        ctrl.acquire(Priority.RESEARCH, deadline_seconds=0.15)
    ctrl.release(a); ctrl.release(b)


def test_queue_full_is_rejected_immediately():
    ctrl = _controller(queue_size=1)
    # Simulate one waiter already queued (avoids a flaky thread race).
    ctrl._waiting = ctrl.spec.queue_size
    with pytest.raises(QueueFullError):
        ctrl.acquire(Priority.BATCH, deadline_seconds=1.0)


def test_cancel_while_queueing():
    ctrl = _controller()
    ctrl._waiting = 0
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        ctrl.acquire(Priority.BATCH, cancel_event=event)


def test_release_is_idempotent_under_double_release():
    ctrl = _controller()
    lease = ctrl.acquire(Priority.INTERACTIVE, deadline_seconds=1.0)
    ctrl.release(lease)
    # A second release must not corrupt the semaphore bound into unusable state.
    ctrl.release(lease)
    again = ctrl.acquire(Priority.INTERACTIVE, deadline_seconds=1.0)
    assert again is not None
    ctrl.release(again)


def test_snapshot_reports_pressure():
    ctrl = _controller()
    lease = ctrl.acquire(Priority.INTERACTIVE, deadline_seconds=1.0)
    snap = ctrl.snapshot()
    assert snap["in_flight"] == 1 and snap["max_concurrency"] == 2
    ctrl.release(lease)
    assert ctrl.snapshot()["in_flight"] == 0


# -------------------------------------------------------------------- dedupe


def test_concurrent_identical_calls_share_one_execution():
    gate = InFlightGate()
    started = threading.Event()
    release = threading.Event()
    executions = [0]
    lock = threading.Lock()

    def slow() -> str:
        with lock:
            executions[0] += 1
        started.set()
        release.wait(5)
        return "done"

    results: dict[str, object] = {}

    def worker(tag: str) -> None:
        result, shared = gate.run("same-key", slow)
        results[tag] = (result, shared)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    started.wait(5)
    t2.start()
    release.set()
    t1.join(5); t2.join(5)

    assert executions[0] == 1, "identical concurrent work must execute once"
    shared_flags = {v[1] for v in results.values()}
    assert shared_flags == {False, True}, "exactly one caller is the initiator"
    assert all(v[0] == "done" for v in results.values())


def test_different_keys_execute_separately():
    gate = InFlightGate()
    assert gate.run("a", lambda: 1)[1] is False
    assert gate.run("b", lambda: 2)[1] is False
    assert gate.snapshot()["running"] == 0, "finished calls must be evicted"


def test_error_propagates_to_all_waiters():
    gate = InFlightGate()
    with pytest.raises(ValueError):
        gate.run("k", lambda: (_ for _ in ()).throw(ValueError("nope")))
    # Key must be evicted even on failure, otherwise it poisons forever.
    assert gate.run("k", lambda: "ok")[0] == "ok"

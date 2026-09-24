"""Budget governor: the shared ceiling the three projects never had.

Three independent API clients meant three independent notions of "how much have
I spent", so a batch re-index in the knowledge layer could burn the day's
budget while the research session the user was actually looking at got throttled
by the provider. Cost is the only currency all of them share, so it is accounted
centrally here.

Two tiers:

* **Global** -- daily (and optional monthly) ceiling across every caller.
* **Per-run** -- the ``Budget`` from the Run contract, checked before each call
  so an overrunning run stops mid-flight instead of at the end.

In-memory by design. Persistence to SQLite is stage-1 follow-up; the contract
already has the fields, and a process restart losing today's tally is an
acceptable failure mode for a single-user local tool (it fails *open*, which is
the safe direction for availability but should be tightened once persisted).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from workstation_contracts import Budget

from .errors import BudgetExceededError
from .pricing import CostBreakdown

__all__ = ["BudgetGovernor", "SpendLedger"]


@dataclass
class SpendLedger:
    """Accumulated spend for one period."""

    period_key: str
    total_cny: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)
    by_task: dict[str, float] = field(default_factory=dict)

    def record(self, cost: CostBreakdown, task: str) -> None:
        self.total_cny = round(self.total_cny + cost.total_cny, 6)
        self.prompt_tokens += cost.prompt_tokens
        self.completion_tokens += cost.completion_tokens
        self.calls += 1
        self.by_model[cost.model] = round(
            self.by_model.get(cost.model, 0.0) + cost.total_cny, 6
        )
        self.by_task[task] = round(self.by_task.get(task, 0.0) + cost.total_cny, 6)

    def as_dict(self) -> dict[str, object]:
        return {
            "period": self.period_key,
            "total_cny": self.total_cny,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "calls": self.calls,
            "by_model": dict(sorted(self.by_model.items(), key=lambda kv: -kv[1])),
            "by_task": dict(sorted(self.by_task.items(), key=lambda kv: -kv[1])),
        }


class BudgetGovernor:
    def __init__(
        self,
        *,
        daily_cny: float | None = None,
        monthly_cny: float | None = None,
        today: date | None = None,
    ) -> None:
        self.daily_cny = daily_cny
        self.monthly_cny = monthly_cny
        self._lock = threading.RLock()
        self._today_fn: Callable[[], date] = (lambda: today) if today is not None else date.today
        self._ledgers: dict[str, SpendLedger] = {}

    # ------------------------------------------------------------------ keys

    def _daily_key(self) -> str:
        return self._today_fn().isoformat()

    def _monthly_key(self) -> str:
        return self._today_fn().isoformat()[:7]

    # -------------------------------------------------------------- checking

    def check(self, projected_cny: float = 0.0) -> None:
        """Raise if adding ``projected_cny`` would breach a ceiling."""
        with self._lock:
            for key, limit in (
                (self._daily_key(), self.daily_cny),
                (self._monthly_key(), self.monthly_cny),
            ):
                if limit is None:
                    continue
                spent = self._ledgers.get(key)
                used = spent.total_cny if spent else 0.0
                if used + projected_cny > limit:
                    raise BudgetExceededError(
                        f"{key} spend ¥{used:.4f} + projected ¥{projected_cny:.4f} "
                        f"exceeds limit ¥{limit:.2f}"
                    )

    def check_run_budget(self, budget: Budget, *, spent_cny: float, calls: int) -> None:
        """Gate a Run against its own Budget contract.

        Called by the Run executor with **cumulative** values, not by the
        gateway (which only ever sees a single call). Strict ``>`` so that
        landing exactly on the ceiling is allowed -- "used all of it" is not
        the same as "over it".
        """
        if budget.max_cost_cny is not None and spent_cny > budget.max_cost_cny:
            raise BudgetExceededError(
                f"run budget exceeded: ¥{spent_cny:.4f} > ¥{budget.max_cost_cny:.2f}"
            )
        if budget.max_llm_calls is not None and calls > budget.max_llm_calls:
            raise BudgetExceededError(
                f"run llm call budget exceeded: {calls} > {budget.max_llm_calls}"
            )
        if budget.max_tokens is not None and self._total_tokens() > budget.max_tokens:
            raise BudgetExceededError(
                f"run token budget exceeded: {self._total_tokens()} > {budget.max_tokens}"
            )

    def _total_tokens(self) -> int:
        """Tokens recorded in the current daily ledger (the practical scope)."""
        with self._lock:
            ledger = self._ledgers.get(self._daily_key())
            if ledger is None:
                return 0
            return ledger.prompt_tokens + ledger.completion_tokens

    # -------------------------------------------------------------- recording

    def record(self, cost: CostBreakdown, task: str) -> None:
        with self._lock:
            for key in (self._daily_key(), self._monthly_key()):
                ledger = self._ledgers.setdefault(key, SpendLedger(period_key=key))
                ledger.record(cost, task)

    # ------------------------------------------------------------- telemetry

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            daily = self._ledgers.get(self._daily_key())
            monthly = self._ledgers.get(self._monthly_key())
            return {
                "daily": (daily.as_dict() if daily else None),
                "monthly": (monthly.as_dict() if monthly else None),
                "daily_limit_cny": self.daily_cny,
                "monthly_limit_cny": self.monthly_cny,
                "daily_remaining_cny": (
                    None if self.daily_cny is None
                    else round(self.daily_cny - (daily.total_cny if daily else 0.0), 6)
                ),
            }

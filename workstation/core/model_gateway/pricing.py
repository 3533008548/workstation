"""Token -> cost conversion.

Why this module exists: **none of the three projects tracked cost at all.**
They each had separate API keys, separate rate limits and no shared ceiling,
so a batch job in one project silently slowed down or overspent the others.
Cost is the only unit all three can be compared in, so the gateway computes it
centrally and the budget governor (usage.py) enforces it.

Prices below are PLACEHOLDERS. Update them from your actual billing page --
a wrong price makes the budget governor enforce the wrong number, which is
worse than having no governor at all.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PriceTable", "CostBreakdown", "cost_of"]

DEFAULT_INPUT_CNY_PER_MTOK = 2.0
DEFAULT_OUTPUT_CNY_PER_MTOK = 8.0


@dataclass(frozen=True)
class CostBreakdown:
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    input_cny: float = 0.0
    output_cny: float = 0.0

    @property
    def total_cny(self) -> float:
        return round(self.input_cny + self.output_cny, 6)

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "input_cny": self.input_cny,
            "output_cny": self.output_cny,
            "total_cny": self.total_cny,
        }


class PriceTable:
    """Per-model prices in CNY per 1M tokens, with a fallback default."""

    def __init__(
        self,
        overrides: dict[str, tuple[float, float]] | None = None,
        *,
        default_input: float = DEFAULT_INPUT_CNY_PER_MTOK,
        default_output: float = DEFAULT_OUTPUT_CNY_PER_MTOK,
    ) -> None:
        self._overrides: dict[str, tuple[float, float]] = dict(overrides or {})
        self.default_input = default_input
        self.default_output = default_output

    def set(self, model: str, input_cny: float, output_cny: float) -> None:
        self._overrides[model] = (float(input_cny), float(output_cny))

    def price_for(self, model: str) -> tuple[float, float]:
        return self._overrides.get(model, (self.default_input, self.default_output))

    def known_models(self) -> list[str]:
        return sorted(self._overrides)


def cost_of(
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    table: PriceTable | None = None,
) -> CostBreakdown:
    table = table or PriceTable()
    in_price, out_price = table.price_for(model)
    return CostBreakdown(
        model=model,
        provider=provider,
        prompt_tokens=max(0, int(prompt_tokens)),
        completion_tokens=max(0, int(completion_tokens)),
        input_cny=round(max(0, prompt_tokens) / 1_000_000 * in_price, 6),
        output_cny=round(max(0, completion_tokens) / 1_000_000 * out_price, 6),
    )

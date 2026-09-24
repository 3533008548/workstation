"""Fact -- one atomic piece of evidence, always traceable to SourceRef(s).

Design rule that matters: **a Fact never carries free-text provenance.**
The PPT agent's legacy shape was ``Fact{id, text, source: string, confidence}``.
A free-text ``source`` cannot be verified, deduplicated, or blocked by policy,
which is exactly why the deck generator could hallucinate a citation. Here
``source_ids`` is required and non-empty.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .base import FrozenModel, utcnow
from .enums import Confidence, FactKind
from .ids import new_id

__all__ = ["Fact", "FactSet"]


class Fact(FrozenModel):
    fact_id: str = Field(default_factory=lambda: new_id("fact"))
    text: str = Field(min_length=1)
    kind: FactKind = FactKind.CLAIM

    source_ids: list[str] = Field(
        min_length=1,
        description="SourceRef.source_id values; REQUIRED -- a fact with no source is not a fact",
    )

    confidence: Confidence = Confidence.UNKNOWN
    qualifiers: dict[str, Any] = Field(
        default_factory=dict,
        description="Scope conditions: n=, year=, population=, unit=, caveat=",
    )
    evidence: str | None = Field(default=None, description="Verbatim snippet backing the text")
    created_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_ids")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for item in v:
            if not item:
                raise ValueError("source_ids must not contain empty strings")
            seen.setdefault(item, None)
        return list(seen)

    @property
    def is_citable(self) -> bool:
        """Deck policy gate: low-confidence or unsourced facts must not be rendered."""
        return self.confidence in (Confidence.HIGH, Confidence.MEDIUM)

    def with_source(self, source_id: str) -> "Fact":
        if source_id in self.source_ids:
            return self
        payload = self.model_dump()
        payload["source_ids"] = [*self.source_ids, source_id]
        return Fact(**payload)


class FactSet(FrozenModel):
    """An ordered bundle of facts, e.g. one study's evidence ledger."""

    set_id: str = Field(default_factory=lambda: new_id("fact"))
    label: str = ""
    facts: list[Fact] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def citable(self) -> list[Fact]:
        return [f for f in self.facts if f.is_citable]

    def density(self) -> float:
        """Facts per 1000 characters -- the deck planner's content-volume precheck.

        PPT defect #1 ("one sentence became 30 padded pages") was a missing
        precheck. With a FactSet the check becomes arithmetic instead of taste:
        a deck with N pages needs roughly N * 2..4 citable facts.
        """
        if not self.facts:
            return 0.0
        chars = sum(len(f.text) for f in self.facts) or 1
        return round(len(self.facts) / chars * 1000, 3)

    def recommends_page_count(self, facts_per_page: float = 3.0) -> int:
        if facts_per_page <= 0:
            raise ValueError("facts_per_page must be positive")
        n = len(self.citable)
        if n == 0:
            return 0
        return max(1, min(60, round(n / facts_per_page)))

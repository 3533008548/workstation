"""Shared base configuration for every contract model.

Two deliberate strictness choices:

``extra="forbid"``
    Adapters must map foreign payloads field-by-field instead of spraying
    unknown keys through. A PPT DeckSpec or a knowledge-base note that grows a
    new field therefore fails loudly in the adapter, not silently at runtime.

``frozen`` on value objects
    SourceRef / Fact are evidence. Once recorded they are referenced by id from
    decks, notes and runs; mutating one would corrupt every downstream citation.
    Correction is a new revision, never an edit in place.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class FrozenModel(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

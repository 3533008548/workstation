"""SourceRef -- the single citation primitive of the workbench.

Every skill, regardless of stack, must reduce its notion of "where did this
come from" to a SourceRef before the base will store, retrieve, rank or cite
it. This is the adapter seam described in the architecture notes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .base import FrozenModel, utcnow
from .enums import Origin, SourceType
__all__ = ["Locator", "SourceRef", "derive_source_id"]


class Locator(FrozenModel):
    """Position *inside* a source. All fields optional; fill what you know."""

    page: int | None = None
    section: str | None = None
    slide: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    timestamp_s: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("page", "slide")
    @classmethod
    def _positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("page/slide are 1-based")
        return v


def derive_source_id(uri: str, content_hash: str | None = None) -> str:
    """Content-addressed, deduplicating identifier for a source.

    Same URI + same bytes always yields the same ``source_id``, so re-indexing
    a paper or a note is idempotent and does not orphan existing citations.
    """
    digest = hashlib.sha256(f"{uri}||{content_hash or ''}".encode("utf-8")).hexdigest()
    return f"src_{digest[:24]}"


class SourceRef(FrozenModel):
    source_id: str = Field(description="Content-addressed id, see derive_source_id()")
    source_type: SourceType
    uri: str = Field(description="Relative path under WORKSTATION_HOME, or an absolute URL")
    title: str
    origin: Origin = Field(description="primary = must back up; derived = cache")
    producer: str = Field(
        description="Adapter that minted this ref, e.g. 'research.paper', 'knowledge.note'"
    )

    locator: Locator = Field(default_factory=Locator)
    content_hash: str | None = Field(default=None, description="sha256 of the bytes; drives invalidation")
    language: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("uri")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("uri is required -- a citation with no address is not a citation")
        return v

    @property
    def is_local(self) -> bool:
        return "://" not in self.uri

    def revise(self, **changes: Any) -> "SourceRef":
        """Return a new SourceRef with ``updated_at`` bumped (frozen model)."""
        payload = self.model_dump()
        payload.update(changes)
        if "updated_at" not in changes:
            payload["updated_at"] = utcnow()
        return SourceRef(**payload)

    @classmethod
    def mint(
        cls,
        *,
        source_type: SourceType,
        uri: str,
        title: str,
        producer: str,
        origin: Origin = Origin.PRIMARY,
        content_hash: str | None = None,
        **kwargs: Any,
    ) -> "SourceRef":
        return cls(
            source_id=derive_source_id(uri, content_hash),
            source_type=source_type,
            uri=uri,
            title=title,
            origin=origin,
            producer=producer,
            content_hash=content_hash,
            **kwargs,
        )

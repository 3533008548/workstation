"""Stable, sortable, collision-resistant identifiers.

Shape::

    <prefix>_<13-digit epoch ms>_<8 hex random>

* lexicographically sortable by creation time (matters for run/step streams)
* no external dependency (no ulid/uuid7 package needed)
* prefix makes the type obvious in logs and in SQLite dumps
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

_PREFIXES = frozenset({"src", "fact", "run", "step", "art", "evt", "skill", "task"})


def new_id(prefix: str, *, when: datetime | None = None) -> str:
    """Build a new prefixed identifier.

    >>> new_id("run").startswith("run_")
    True
    """
    if prefix not in _PREFIXES:
        raise ValueError(f"unknown id prefix {prefix!r}; expected one of {sorted(_PREFIXES)}")
    moment = when or datetime.now(timezone.utc)
    millis = int(moment.timestamp() * 1000)
    return f"{prefix}_{millis:013d}_{secrets.token_hex(4)}"


def id_prefix(value: str) -> str:
    """Return the prefix of an identifier, or ``""`` if it has none."""
    head, _, _ = value.partition("_")
    return head if head in _PREFIXES else ""


def is_valid_id(value: str, prefix: str | None = None) -> bool:
    parts = value.split("_")
    if len(parts) != 3:
        return False
    head, millis, entropy = parts
    if prefix is not None and head != prefix:
        return False
    return (
        head in _PREFIXES
        and len(millis) == 13
        and millis.isdigit()
        and len(entropy) == 8
        and all(c in "0123456789abcdef" for c in entropy)
    )


def now_ms() -> int:
    return int(time.time() * 1000)

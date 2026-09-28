"""Numeric OpenProject planning-release identifiers (separate from artifact SemVer)."""

from __future__ import annotations

import re

_RELEASE = re.compile(r"^R([1-9][0-9]*)(?:\s+[—–-]\s+.+)?$", re.IGNORECASE)


def release_number(name: str) -> int | None:
    match = _RELEASE.fullmatch(name.strip())
    return int(match.group(1)) if match else None


def release_sort_key(name: str) -> tuple[int, str]:
    number = release_number(name)
    if number is None:
        raise ValueError(f"Invalid R# planning release: {name!r}")
    return number, name.casefold()

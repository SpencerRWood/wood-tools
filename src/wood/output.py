"""Stable, bounded output and exit semantics for Wood Tools v2 commands."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal, cast

type Status = Literal[
    "success", "blocked", "invalid", "unavailable", "ambiguous", "not_applicable", "error"
]
type Mutation = Literal["read-only", "preview", "mutating"]

EXIT_CODES: dict[Status, int] = {
    "success": 0,
    "not_applicable": 0,
    "error": 1,
    "invalid": 2,
    "blocked": 3,
    "unavailable": 4,
    "ambiguous": 5,
}
MAX_ITEMS = 50
MAX_TEXT = 500
MAX_DEPTH = 6


def _bounded(value: object, depth: int = 0) -> object:
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if depth >= MAX_DEPTH:
        return "<depth limit>"
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))[:MAX_ITEMS]
        return {str(key): _bounded(item, depth + 1) for key, item in items}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_bounded(item, depth + 1) for item in value[:MAX_ITEMS]]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)[:MAX_TEXT]


def envelope(
    *,
    command: str,
    status: Status,
    mutation: Mutation = "read-only",
    summary: str,
    data: Mapping[str, object] | None = None,
    errors: Sequence[Mapping[str, str]] = (),
    warnings: Sequence[Mapping[str, str]] = (),
    next_actions: Sequence[str] = (),
    requires_approval: bool = False,
) -> dict[str, object]:
    """Build one deterministic v2 envelope; callers supply only safe data."""
    if requires_approval and status != "blocked":
        raise ValueError("Approval-required results must have blocked status")
    return {
        "schema_version": 2,
        "command": command,
        "status": status,
        "mutation": mutation,
        "requires_approval": requires_approval,
        "summary": _bounded(summary),
        "data": _bounded(data or {}),
        "warnings": _bounded(warnings),
        "errors": _bounded(errors),
        "next_actions": _bounded(next_actions),
    }


def render(payload: Mapping[str, object], *, as_json: bool) -> str:
    if as_json:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return str(payload["summary"])


def exit_code(payload: Mapping[str, object]) -> int:
    return EXIT_CODES[cast(Status, payload["status"])]

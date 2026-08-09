from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .models import CommandEnvelope, CommandStatus, EnvelopeMessage, MutationKind


def _normalize_messages(messages: Sequence[EnvelopeMessage] | None) -> list[EnvelopeMessage]:
    return list(messages or [])


def _normalize_next_actions(next_actions: Sequence[str] | None) -> list[str]:
    return [action for action in (next_actions or []) if action]


def build_envelope(
    *,
    command: str,
    status: CommandStatus,
    mutation: MutationKind,
    requires_approval: bool,
    summary: str,
    data: dict[str, Any] | None = None,
    warnings: Sequence[EnvelopeMessage] | None = None,
    errors: Sequence[EnvelopeMessage] | None = None,
    next_actions: Sequence[str] | None = None,
) -> CommandEnvelope:
    return {
        "command": command,
        "status": status,
        "mutation": mutation,
        "requires_approval": requires_approval,
        "summary": summary,
        "data": dict(data or {}),
        "warnings": _normalize_messages(warnings),
        "errors": _normalize_messages(errors),
        "next_actions": _normalize_next_actions(next_actions),
    }


def success_output(
    *,
    command: str,
    mutation: MutationKind,
    summary: str,
    data: dict[str, Any] | None = None,
    warnings: Sequence[EnvelopeMessage] | None = None,
    next_actions: Sequence[str] | None = None,
) -> CommandEnvelope:
    return build_envelope(
        command=command,
        status="success",
        mutation=mutation,
        requires_approval=False,
        summary=summary,
        data=data,
        warnings=warnings,
        next_actions=next_actions,
    )


def warning_output(
    *,
    command: str,
    mutation: MutationKind,
    summary: str,
    data: dict[str, Any] | None = None,
    warnings: Sequence[EnvelopeMessage] | None = None,
    errors: Sequence[EnvelopeMessage] | None = None,
    next_actions: Sequence[str] | None = None,
) -> CommandEnvelope:
    return build_envelope(
        command=command,
        status="warning",
        mutation=mutation,
        requires_approval=False,
        summary=summary,
        data=data,
        warnings=warnings,
        errors=errors,
        next_actions=next_actions,
    )


def error_output(
    *,
    command: str,
    mutation: MutationKind,
    summary: str,
    data: dict[str, Any] | None = None,
    errors: Sequence[EnvelopeMessage] | None = None,
    next_actions: Sequence[str] | None = None,
) -> CommandEnvelope:
    return build_envelope(
        command=command,
        status="error",
        mutation=mutation,
        requires_approval=False,
        summary=summary,
        data=data,
        errors=errors,
        next_actions=next_actions,
    )


def blocked_output(
    *,
    command: str,
    summary: str,
    data: dict[str, Any] | None = None,
    warnings: Sequence[EnvelopeMessage] | None = None,
    next_actions: Sequence[str] | None = None,
) -> CommandEnvelope:
    return build_envelope(
        command=command,
        status="blocked",
        mutation="mutating",
        requires_approval=True,
        summary=summary,
        data=data,
        warnings=warnings,
        next_actions=next_actions,
    )

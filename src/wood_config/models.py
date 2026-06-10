from __future__ import annotations

from typing import Any, Literal, TypeAlias, TypedDict

CommandStatus: TypeAlias = Literal["success", "warning", "error", "blocked"]
MutationKind: TypeAlias = Literal["read-only", "mutating"]


class OutputMessage(TypedDict, total=False):
    code: str
    field: str
    message: str
    remediation: str
    profile: str


EnvelopeMessage: TypeAlias = str | OutputMessage


class CommandEnvelope(TypedDict):
    command: str
    status: CommandStatus
    mutation: MutationKind
    requires_approval: bool
    summary: str
    data: dict[str, Any]
    warnings: list[EnvelopeMessage]
    errors: list[EnvelopeMessage]
    next_actions: list[str]

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib import request


class OpenProjectError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OpenProjectSettings:
    base_url: str
    token: str = field(repr=False)
    token_provider: str
    user_agent: str


class Transport(Protocol):
    def __call__(
        self,
        req: request.Request,
        *,
        timeout: int,
    ) -> Any: ...


__all__ = ["OpenProjectError", "OpenProjectSettings", "Transport"]

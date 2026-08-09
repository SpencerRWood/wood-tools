from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ResourceError(ValueError):
    """Raised when a resource manifest or installation is invalid."""


@dataclass(frozen=True)
class ResourceManifest:
    kind: str
    name: str
    version: str
    digest: str
    compatibility: dict[str, Any]
    helper_contract: dict[str, Any] | None
    raw: dict[str, Any]

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised for invalid config state or invalid user input."""


@dataclass(frozen=True)
class ConfigPaths:
    file_path: Path

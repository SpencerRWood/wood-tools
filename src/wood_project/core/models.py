from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ProjectError(ValueError):
    """Raised for invalid project metadata or invalid user input."""


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    project_file: Path
    wood_home: Path
    wood_config_file: Path
    wood_home_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class LinkedRepository:
    name: str
    path: Path
    role: str | None = None

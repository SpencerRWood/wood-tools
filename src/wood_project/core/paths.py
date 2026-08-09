from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .models import ProjectError, ProjectPaths

PROJECT_FILE_NAME = "project.json"
WOOD_HOME_ENV = "WOOD_HOME"
WOOD_HOME_DIR_NAME = ".wood"
WOOD_CONFIG_FILE_NAME = "config.toml"
WOOD_HOME_DIRECTORY_NAMES = (
    "packs/templates",
    "packs/references",
    "packs/agents",
    "tools",
    "scripts",
    "cache",
    "state",
)
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def resolve_project_root(project_root: Path | None = None) -> Path:
    root = (project_root or Path.cwd()).expanduser().resolve()
    if not root.exists():
        raise ProjectError(f"Project root does not exist: {root}")
    if not root.is_dir():
        raise ProjectError(f"Project root must be a directory: {root}")
    return root


def resolve_wood_home(wood_home: Path | None = None) -> Path:
    raw_home = wood_home
    if raw_home is None:
        env_value = os.environ.get(WOOD_HOME_ENV)
        if env_value is not None and not env_value.strip():
            raise ProjectError("WOOD_HOME must be a non-empty path when set.")
        raw_home = Path(env_value) if env_value else Path.home() / WOOD_HOME_DIR_NAME
    return raw_home.expanduser().resolve()


def _validate_wood_home_location(project_root: Path, wood_home: Path) -> None:
    if wood_home == project_root / WOOD_HOME_DIR_NAME:
        raise ProjectError(
            "Wood home must not be the project-local .wood directory. Set WOOD_HOME to a "
            "user-global path or use the default ~/.wood."
        )


def build_paths(
    *,
    project_root: Path | None = None,
    wood_home: Path | None = None,
    project_slug: str | None = None,
) -> ProjectPaths:
    resolved_root = resolve_project_root(project_root)
    resolved_wood_home = resolve_wood_home(wood_home)
    _validate_wood_home_location(resolved_root, resolved_wood_home)
    project_file = resolved_root / PROJECT_FILE_NAME
    return ProjectPaths(
        project_root=resolved_root,
        project_file=project_file,
        wood_home=resolved_wood_home,
        wood_config_file=resolved_wood_home / WOOD_CONFIG_FILE_NAME,
        wood_home_dirs=tuple(resolved_wood_home / name for name in WOOD_HOME_DIRECTORY_NAMES),
    )


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _check_directory_access(field: str, path: Path, *, require_write: bool) -> dict[str, Any]:
    readable = os.access(path, os.R_OK | os.X_OK)
    writable = os.access(path, os.W_OK | os.X_OK)
    if not readable:
        raise ProjectError(f"{field} is not readable: {path}")
    if require_write and not writable:
        raise ProjectError(f"{field} is not writable: {path}")
    return {
        "path": str(path),
        "readable": readable,
        "writable": writable,
    }


def _check_file_access(field: str, path: Path, *, require_write: bool) -> dict[str, Any]:
    readable = os.access(path, os.R_OK)
    writable = os.access(path, os.W_OK)
    if not readable:
        raise ProjectError(f"{field} is not readable: {path}")
    if require_write and not writable:
        raise ProjectError(f"{field} is not writable: {path}")
    return {
        "path": str(path),
        "readable": readable,
        "writable": writable,
    }


def _check_mutation_parent(field: str, path: Path) -> None:
    parent = path if path.exists() else _nearest_existing_parent(path)
    if parent is None:
        raise ProjectError(
            f"{field} is unavailable: {path} (no existing parent found; the expected mount may "
            "not be available)"
        )
    if not parent.is_dir():
        raise ProjectError(f"{field} parent must be a directory: {parent}")
    _check_directory_access(field, parent, require_write=True)

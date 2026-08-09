from __future__ import annotations

from pathlib import Path
from typing import Any

from .documents import validate_project_document
from .models import LinkedRepository, ProjectError
from .paths import (
    WOOD_HOME_DIRECTORY_NAMES,
    _check_directory_access,
    _check_file_access,
    _nearest_existing_parent,
)


def _validate_existing_directory(field: str, path: Path) -> None:
    if not path.exists():
        nearest_parent = _nearest_existing_parent(path)
        if nearest_parent is not None and nearest_parent != path.parent:
            raise ProjectError(
                f"{field} is unavailable: {path} (nearest existing parent: {nearest_parent}; "
                "the expected mount may not be available)"
            )
        raise ProjectError(f"{field} does not exist: {path}")
    if not path.is_dir():
        raise ProjectError(f"{field} must be a directory: {path}")


def _validate_existing_file(field: str, path: Path) -> None:
    if not path.exists():
        nearest_parent = _nearest_existing_parent(path)
        if nearest_parent is not None and nearest_parent != path.parent:
            raise ProjectError(
                f"{field} is unavailable: {path} (nearest existing parent: {nearest_parent}; "
                "the expected Wood home may not be available)"
            )
        raise ProjectError(f"{field} does not exist: {path}")
    if not path.is_file():
        raise ProjectError(f"{field} must be a file: {path}")


def _ensure_unique_paths(entries: list[tuple[str, Path]]) -> None:
    seen: dict[Path, str] = {}
    for field, path in entries:
        resolved = path.resolve()
        other = seen.get(resolved)
        if other is not None:
            raise ProjectError(f"{field} conflicts with {other}: both resolve to {resolved}")
        seen[resolved] = field


def _load_linked_repositories(document: dict[str, Any]) -> list[LinkedRepository]:
    raw = document.get("linked_repositories")
    if raw is None:
        return []

    repositories: list[LinkedRepository] = []
    if isinstance(raw, dict):
        items = raw.items()
        for name, value in items:
            if not isinstance(name, str) or not name.strip():
                raise ProjectError("linked_repositories keys must be non-empty strings.")
            if isinstance(value, str):
                path_value = value
                role = None
            elif isinstance(value, dict):
                path_value = value.get("path")
                role = value.get("role")
            else:
                path_value = None
                role = None
            if not isinstance(path_value, str) or not path_value.strip():
                raise ProjectError(
                    f"linked_repositories['{name}'] must define a non-empty absolute path."
                )
            if role is not None and (not isinstance(role, str) or not role.strip()):
                raise ProjectError(
                    f"linked_repositories['{name}'] role must be a non-empty string when set."
                )
            path = Path(path_value)
            if not path.is_absolute():
                raise ProjectError(f"linked_repositories['{name}'] path must be an absolute path.")
            repositories.append(
                LinkedRepository(name=name, path=path, role=role.strip() if role else None)
            )
        return repositories

    if isinstance(raw, list):
        seen_names: set[str] = set()
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ProjectError("linked_repositories entries must be objects.")
            name = entry.get("name")
            path_value = entry.get("path")
            role = entry.get("role")
            if not isinstance(name, str) or not name.strip():
                raise ProjectError(f"linked_repositories[{index}] must define a non-empty name.")
            if name in seen_names:
                raise ProjectError(f"linked_repositories contains duplicate name '{name}'.")
            seen_names.add(name)
            if not isinstance(path_value, str) or not path_value.strip():
                raise ProjectError(
                    f"linked_repositories[{index}] must define a non-empty absolute path."
                )
            if role is not None and (not isinstance(role, str) or not role.strip()):
                raise ProjectError(
                    f"linked_repositories[{index}] role must be a non-empty string when set."
                )
            path = Path(path_value)
            if not path.is_absolute():
                raise ProjectError(f"linked_repositories[{index}] path must be an absolute path.")
            repositories.append(
                LinkedRepository(name=name, path=path, role=role.strip() if role else None)
            )
        return repositories

    raise ProjectError("linked_repositories must be an object or list of objects.")


def validate_project_state(
    document: dict[str, Any],
) -> dict[str, Any]:
    validate_project_document(document)

    project_root = Path(document["project_root"])
    wood_home = Path(document["wood_home"])
    wood_config_file = Path(document["wood_config_file"])
    wood_home_dirs = tuple(wood_home / name for name in WOOD_HOME_DIRECTORY_NAMES)
    linked_repositories = _load_linked_repositories(document)

    _validate_existing_directory("project_root", project_root)
    _validate_existing_directory("wood_home", wood_home)
    _validate_existing_file("wood_config_file", wood_config_file)
    for directory in wood_home_dirs:
        field = f"wood_home_dirs.{directory.relative_to(wood_home)}"
        _validate_existing_directory(field, directory)

    mount_checks = {
        "project_root": _check_directory_access("project_root", project_root, require_write=False),
        "wood_home": _check_directory_access("wood_home", wood_home, require_write=False),
        "wood_config_file": _check_file_access(
            "wood_config_file", wood_config_file, require_write=False
        ),
        "wood_home_dirs": {
            str(directory.relative_to(wood_home)): _check_directory_access(
                f"wood_home_dirs.{directory.relative_to(wood_home)}",
                directory,
                require_write=False,
            )
            for directory in wood_home_dirs
        },
    }

    _ensure_unique_paths(
        [
            ("project_root", project_root),
            ("wood_home", wood_home),
            ("wood_config_file", wood_config_file),
            *[
                (f"wood_home_dirs.{directory.relative_to(wood_home)}", directory)
                for directory in wood_home_dirs
            ],
        ]
    )

    linked_payload: list[dict[str, str]] = []
    linked_entries: list[tuple[str, Path]] = []
    for repository in linked_repositories:
        _validate_existing_directory(
            f"linked_repositories['{repository.name}']",
            repository.path,
        )
        access = _check_directory_access(
            f"linked_repositories['{repository.name}']",
            repository.path,
            require_write=False,
        )
        linked_entries.append((f"linked_repositories['{repository.name}']", repository.path))
        entry = {"name": repository.name, "path": str(repository.path)}
        if repository.role is not None:
            entry["role"] = repository.role
        entry["access"] = {
            "readable": access["readable"],
            "writable": access["writable"],
        }
        linked_payload.append(entry)

    _ensure_unique_paths(
        [
            ("project_root", project_root),
            ("wood_home", wood_home),
            ("wood_config_file", wood_config_file),
            *[
                (f"wood_home_dirs.{directory.relative_to(wood_home)}", directory)
                for directory in wood_home_dirs
            ],
            *linked_entries,
        ]
    )

    return {
        "valid": True,
        "project": document,
        "checked_paths": {
            "project_root": str(project_root),
            "wood_home": str(wood_home),
            "wood_config_file": str(wood_config_file),
            "wood_home_dirs": [str(directory) for directory in wood_home_dirs],
        },
        "mount_checks": mount_checks,
        "linked_repositories": linked_payload,
    }

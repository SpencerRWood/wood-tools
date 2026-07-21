from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

PROJECT_SCHEMA_VERSION = 1
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
OBSOLETE_ARTIFACT_MESSAGE = (
    "artifact_root, artifact_dir, metadata_dir, and artifact-specific settings are obsolete. "
    "Use the user-global Wood home via WOOD_HOME or the default ~/.wood."
)
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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


def slugify_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ProjectError("Project slug cannot be empty.")
    if not SLUG_PATTERN.fullmatch(normalized):
        raise ProjectError("Project slug must use lowercase letters, numbers, and hyphens.")
    return normalized


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


def generate_project_id() -> str:
    return str(uuid4())


def create_project_document(
    *,
    project_id: str | None,
    project_slug: str | None,
    project_root: Path | None,
    wood_home: Path | None,
) -> dict[str, Any]:
    paths = build_paths(
        project_root=project_root,
        wood_home=wood_home,
        project_slug=project_slug,
    )
    slug = project_slug or slugify_project_name(paths.project_root.name)
    document = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project_id or generate_project_id(),
        "project_slug": slug,
        "project_root": str(paths.project_root),
        "wood_home": str(paths.wood_home),
        "wood_config_file": str(paths.wood_config_file),
    }
    validate_project_document(document)
    return document


def validate_project_document(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise ProjectError("Project document root must be an object.")

    if document.get("schema_version") != PROJECT_SCHEMA_VERSION:
        raise ProjectError(f"schema_version must be {PROJECT_SCHEMA_VERSION}.")

    project_id = document.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ProjectError("project_id must be a non-empty string.")

    project_slug = document.get("project_slug")
    if not isinstance(project_slug, str) or not SLUG_PATTERN.fullmatch(project_slug):
        raise ProjectError("project_slug must use lowercase letters, numbers, and hyphens only.")

    obsolete_fields = [
        field
        for field in ("artifact_root", "artifact_dir", "metadata_dir", "artifact_aliases")
        if field in document
    ]
    if obsolete_fields:
        raise ProjectError(
            f"{', '.join(obsolete_fields)} are obsolete. {OBSOLETE_ARTIFACT_MESSAGE}"
        )

    for field in ("project_root", "wood_home", "wood_config_file"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProjectError(f"{field} must be a non-empty string.")
        if not Path(value).is_absolute():
            raise ProjectError(f"{field} must be an absolute path.")

    project_root = Path(document["project_root"])
    wood_home = Path(document["wood_home"])
    wood_config_file = Path(document["wood_config_file"])

    _validate_wood_home_location(project_root, wood_home)
    if wood_config_file != wood_home / WOOD_CONFIG_FILE_NAME:
        raise ProjectError("wood_config_file must be '<wood_home>/config.toml'.")


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


def save_project_document(project_file: Path, document: dict[str, Any]) -> None:
    validate_project_document(document)
    wood_home = Path(document["wood_home"])
    wood_config_file = Path(document["wood_config_file"])
    wood_home_dirs = tuple(wood_home / name for name in WOOD_HOME_DIRECTORY_NAMES)

    _check_mutation_parent("project_root", project_file.parent)
    _check_mutation_parent("wood_home", wood_home)

    wood_home.mkdir(parents=True, exist_ok=True)
    if wood_config_file.exists() and not wood_config_file.is_file():
        raise ProjectError(f"wood_config_file must be a file: {wood_config_file}")
    for directory in wood_home_dirs:
        if directory.exists() and not directory.is_dir():
            field = f"wood_home_dirs.{directory.relative_to(wood_home)}"
            raise ProjectError(f"{field} must be a directory: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
    if not wood_config_file.exists():
        wood_config_file.write_text("version = 1\n", encoding="utf-8")

    with NamedTemporaryFile("w", encoding="utf-8", dir=project_file.parent, delete=False) as tmp:
        json.dump(document, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(project_file)


def load_project_document(project_file: Path) -> dict[str, Any]:
    if not project_file.exists():
        raise ProjectError(f"Project file not found: {project_file}")
    try:
        document = json.loads(project_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in project file: {exc}") from exc
    validate_project_document(document)
    return document


def init_project(
    *,
    project_root: Path | None,
    wood_home: Path | None,
    project_id: str | None,
    project_slug: str | None,
    apply: bool,
) -> dict[str, Any]:
    document = create_project_document(
        project_id=project_id,
        project_slug=project_slug,
        project_root=project_root,
        wood_home=wood_home,
    )
    project_file = Path(document["project_root"]) / PROJECT_FILE_NAME

    changed = False
    if project_file.exists():
        existing = load_project_document(project_file)
        return {"changed": False, "path": str(project_file), "project": existing}

    if apply:
        save_project_document(project_file, document)
        changed = True

    return {"changed": changed, "path": str(project_file), "project": document}


def show_project(
    project_file: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    return load_project_document(file_path)


def validate_project(
    project_file: Path | None = None, project_root: Path | None = None
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    document = load_project_document(file_path)
    payload = validate_project_state(document)
    payload["path"] = str(file_path)
    return payload


def _resolve_repository_path(repo_path: Path) -> Path:
    resolved = repo_path.expanduser().resolve()
    if not resolved.exists():
        raise ProjectError(f"Repository path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ProjectError(f"Repository path must be a directory: {resolved}")
    return resolved


def _canonicalize_linked_repositories(
    repositories: list[LinkedRepository],
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for repository in repositories:
        entry = {"name": repository.name, "path": str(repository.path)}
        if repository.role is not None:
            entry["role"] = repository.role
        payload.append(entry)
    return payload


def link_repository(
    *,
    repo_path: Path,
    repo_name: str | None,
    repo_role: str | None,
    project_file: Path | None = None,
    project_root: Path | None = None,
    apply: bool,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    document = load_project_document(file_path)

    resolved_repo_path = _resolve_repository_path(repo_path)
    resolved_name = (repo_name or resolved_repo_path.name).strip()
    if not resolved_name:
        raise ProjectError("Repository name cannot be empty.")

    resolved_role = repo_role.strip() if repo_role is not None else None
    if repo_role is not None and not resolved_role:
        raise ProjectError("Repository role cannot be empty when provided.")

    linked_repositories = _load_linked_repositories(document)
    linked_repositories.append(
        LinkedRepository(name=resolved_name, path=resolved_repo_path, role=resolved_role)
    )

    updated_document = dict(document)
    updated_document["linked_repositories"] = _canonicalize_linked_repositories(linked_repositories)
    validate_project_state(updated_document)

    changed = False
    if apply:
        save_project_document(file_path, updated_document)
        changed = True

    repository_payload = {
        "name": resolved_name,
        "path": str(resolved_repo_path),
    }
    if resolved_role is not None:
        repository_payload["role"] = resolved_role

    return {
        "changed": changed,
        "path": str(file_path),
        "project": updated_document,
        "repository": repository_payload,
    }

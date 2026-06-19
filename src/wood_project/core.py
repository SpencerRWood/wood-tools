from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

PROJECT_SCHEMA_VERSION = 1
PROJECT_FILE_NAME = "project.json"
METADATA_DIR_NAME = ".wood"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ProjectError(ValueError):
    """Raised for invalid project metadata or invalid user input."""


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    metadata_dir: Path
    project_file: Path
    artifact_root: Path
    artifact_dir: Path


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


def build_paths(
    *,
    project_root: Path | None = None,
    artifact_root: Path | None = None,
    project_slug: str | None = None,
) -> ProjectPaths:
    resolved_root = resolve_project_root(project_root)
    resolved_slug = project_slug or slugify_project_name(resolved_root.name)
    resolved_artifact_root = (
        artifact_root.expanduser().resolve()
        if artifact_root is not None
        else resolved_root / METADATA_DIR_NAME / "artifacts"
    )
    metadata_dir = resolved_root / METADATA_DIR_NAME
    project_file = resolved_root / PROJECT_FILE_NAME
    artifact_dir = resolved_artifact_root / resolved_slug
    return ProjectPaths(
        project_root=resolved_root,
        metadata_dir=metadata_dir,
        project_file=project_file,
        artifact_root=resolved_artifact_root,
        artifact_dir=artifact_dir,
    )


def generate_project_id() -> str:
    return str(uuid4())


def create_project_document(
    *,
    project_id: str | None,
    project_slug: str | None,
    project_root: Path | None,
    artifact_root: Path | None,
) -> dict[str, Any]:
    paths = build_paths(
        project_root=project_root,
        artifact_root=artifact_root,
        project_slug=project_slug,
    )
    slug = project_slug or slugify_project_name(paths.project_root.name)
    document = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project_id or generate_project_id(),
        "project_slug": slug,
        "project_root": str(paths.project_root),
        "artifact_root": str(paths.artifact_root),
        "artifact_dir": str(paths.artifact_dir),
        "metadata_dir": str(paths.metadata_dir),
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

    for field in ("project_root", "artifact_root", "artifact_dir", "metadata_dir"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProjectError(f"{field} must be a non-empty string.")
        if not Path(value).is_absolute():
            raise ProjectError(f"{field} must be an absolute path.")

    project_root = Path(document["project_root"])
    artifact_root = Path(document["artifact_root"])
    artifact_dir = Path(document["artifact_dir"])
    metadata_dir = Path(document["metadata_dir"])

    if metadata_dir != project_root / METADATA_DIR_NAME:
        raise ProjectError("metadata_dir must be '<project_root>/.wood'.")
    if artifact_dir.parent != artifact_root:
        raise ProjectError("artifact_dir must be inside artifact_root.")
    if artifact_dir.name != project_slug:
        raise ProjectError("artifact_dir must end with project_slug.")


def _validate_existing_directory(field: str, path: Path) -> None:
    if not path.exists():
        raise ProjectError(f"{field} does not exist: {path}")
    if not path.is_dir():
        raise ProjectError(f"{field} must be a directory: {path}")


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
    metadata_dir = Path(document["metadata_dir"])
    artifact_root = Path(document["artifact_root"])
    artifact_dir = Path(document["artifact_dir"])
    linked_repositories = _load_linked_repositories(document)

    _validate_existing_directory("project_root", project_root)
    _validate_existing_directory("metadata_dir", metadata_dir)
    _validate_existing_directory("artifact_root", artifact_root)
    _validate_existing_directory("artifact_dir", artifact_dir)

    if metadata_dir.name != METADATA_DIR_NAME:
        raise ProjectError("metadata_dir must use the '.wood' directory name.")
    if metadata_dir.parent != project_root:
        raise ProjectError("metadata_dir must be a direct child of project_root.")

    _ensure_unique_paths(
        [
            ("project_root", project_root),
            ("metadata_dir", metadata_dir),
            ("artifact_root", artifact_root),
            ("artifact_dir", artifact_dir),
        ]
    )

    linked_payload: list[dict[str, str]] = []
    linked_entries: list[tuple[str, Path]] = []
    for repository in linked_repositories:
        _validate_existing_directory(
            f"linked_repositories['{repository.name}']",
            repository.path,
        )
        linked_entries.append((f"linked_repositories['{repository.name}']", repository.path))
        entry = {"name": repository.name, "path": str(repository.path)}
        if repository.role is not None:
            entry["role"] = repository.role
        linked_payload.append(entry)

    _ensure_unique_paths(
        [
            ("project_root", project_root),
            ("metadata_dir", metadata_dir),
            ("artifact_root", artifact_root),
            ("artifact_dir", artifact_dir),
            *linked_entries,
        ]
    )

    return {
        "valid": True,
        "project": document,
        "checked_paths": {
            "project_root": str(project_root),
            "metadata_dir": str(metadata_dir),
            "artifact_root": str(artifact_root),
            "artifact_dir": str(artifact_dir),
        },
        "linked_repositories": linked_payload,
    }


def save_project_document(project_file: Path, document: dict[str, Any]) -> None:
    validate_project_document(document)
    metadata_dir = Path(document["metadata_dir"])
    artifact_root = Path(document["artifact_root"])
    artifact_dir = Path(document["artifact_dir"])

    metadata_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

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
    artifact_root: Path | None,
    project_id: str | None,
    project_slug: str | None,
    apply: bool,
) -> dict[str, Any]:
    document = create_project_document(
        project_id=project_id,
        project_slug=project_slug,
        project_root=project_root,
        artifact_root=artifact_root,
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

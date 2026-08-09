from __future__ import annotations

from pathlib import Path
from typing import Any

from .documents import create_project_document, load_project_document, save_project_document
from .models import LinkedRepository, ProjectError
from .paths import PROJECT_FILE_NAME, resolve_project_root
from .validation import _load_linked_repositories, validate_project_state


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

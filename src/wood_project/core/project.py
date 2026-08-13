from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from wood_config.core import (
    ConfigError,
    ConfigPaths,
    load_config,
    save_config,
)

from .documents import create_project_document, load_project_document, save_project_document
from .models import LinkedRepository, ProjectError
from .paths import PROJECT_FILE_NAME, resolve_project_root
from .validation import _load_linked_repositories, validate_project_state

DEFAULT_OPENPROJECT_REGISTRY_PATH = "~/.config/wood-tools/config.json"


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


def _optional_positive_int(value: str | int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    raise ProjectError(f"{field} must be a positive integer.")


def _required_string(value: str | None, *, field: str) -> str:
    if value is None or not value.strip():
        raise ProjectError(f"{field} is required.")
    return value.strip()


def _config_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _active_profile_values(document: dict[str, Any]) -> dict[str, Any]:
    active_profile = str(document["active_profile"])
    profile = document["profiles"].setdefault(active_profile, {})
    if not isinstance(profile, dict):
        raise ProjectError(f"Config profile {active_profile!r} must be an object.")
    return profile


def _openproject_config(profile: dict[str, Any]) -> dict[str, Any]:
    integrations = profile.setdefault("integrations", {})
    if not isinstance(integrations, dict):
        raise ProjectError("integrations must be an object.")
    openproject = integrations.setdefault("openproject", {})
    if not isinstance(openproject, dict):
        raise ProjectError("integrations.openproject must be an object.")
    return openproject


def _selected_openproject_defaults(
    *,
    registry_document: dict[str, Any],
    repo_path: Path,
) -> dict[str, Any]:
    profile = _active_profile_values(registry_document)
    openproject = _openproject_config(profile)
    projects = openproject.get("projects")
    if not isinstance(projects, dict):
        return {}

    matches: list[tuple[int, dict[str, Any]]] = []
    for raw_path, metadata in projects.items():
        if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(metadata, dict):
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve(strict=False)
        if candidate == repo_path or candidate in repo_path.parents:
            matches.append((len(candidate.parts), metadata))
    if not matches:
        return {}
    return max(matches, key=lambda match: match[0])[1]


def link_openproject(
    *,
    project_root: Path | None,
    registry_path: str | None,
    url: str | None,
    initiative_id: str | int | None,
    token_ref: str | None,
    user_agent: str | None,
    apply: bool,
) -> dict[str, Any]:
    repo_path = resolve_project_root(project_root)
    registry_ref = (
        registry_path.strip()
        if registry_path and registry_path.strip()
        else (DEFAULT_OPENPROJECT_REGISTRY_PATH)
    )
    registry_file = _config_path(registry_ref)
    local_config_paths = ConfigPaths(file_path=repo_path / ".wood" / "config" / "config.json")
    registry_config_paths = ConfigPaths(file_path=registry_file)

    try:
        local_document = load_config(local_config_paths)
        registry_document = load_config(registry_config_paths)
    except (ConfigError, OSError) as exc:
        raise ProjectError(f"Unable to load Wood config: {exc}") from exc

    selected_defaults = _selected_openproject_defaults(
        registry_document=registry_document,
        repo_path=repo_path,
    )
    resolved_url = _required_string(
        url or selected_defaults.get("url") or os.environ.get("OPENPROJECT_URL"),
        field="OpenProject URL (--url or existing registry url)",
    )
    resolved_token_ref = _required_string(
        token_ref
        or selected_defaults.get("token_ref")
        or os.environ.get("OPENPROJECT_API_TOKEN_REF"),
        field="OpenProject token reference (--token-ref or existing registry token_ref)",
    )
    resolved_initiative_id = _optional_positive_int(
        initiative_id
        or selected_defaults.get("initiative_id")
        or os.environ.get("OPENPROJECT_INITIATIVE_ID"),
        field="OpenProject initiative ID (--initiative or existing registry initiative_id)",
    )
    if resolved_initiative_id is None:
        raise ProjectError("OpenProject initiative ID (--initiative) is required.")

    local_profile = _active_profile_values(local_document)
    local_openproject = _openproject_config(local_profile)
    previous_registry_ref = local_openproject.get("registry_path")
    local_openproject.clear()
    local_openproject["registry_path"] = registry_ref

    registry_profile = _active_profile_values(registry_document)
    registry_openproject = _openproject_config(registry_profile)
    projects = registry_openproject.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise ProjectError("integrations.openproject.projects must be an object.")

    project_key = str(repo_path)
    metadata: dict[str, Any] = {
        "url": resolved_url,
        "initiative_id": resolved_initiative_id,
        "token_ref": resolved_token_ref,
    }
    if user_agent is not None and user_agent.strip():
        metadata["user_agent"] = user_agent.strip()
    elif isinstance(selected_defaults.get("user_agent"), str) and selected_defaults["user_agent"]:
        metadata["user_agent"] = selected_defaults["user_agent"]

    previous_metadata = projects.get(project_key)
    projects[project_key] = metadata

    local_changed = previous_registry_ref != registry_ref
    registry_changed = previous_metadata != metadata
    if apply:
        try:
            save_config(local_config_paths, local_document)
            save_config(registry_config_paths, registry_document)
        except (ConfigError, OSError) as exc:
            raise ProjectError(f"Unable to save Wood config: {exc}") from exc

    return {
        "changed": apply and (local_changed or registry_changed),
        "apply": apply,
        "project_root": str(repo_path),
        "local_config_path": str(local_config_paths.file_path),
        "registry_path": registry_ref,
        "registry_config_path": str(registry_file),
        "local_changed": local_changed if apply else False,
        "registry_changed": registry_changed if apply else False,
        "openproject": {
            "project_path": project_key,
            **metadata,
        },
    }

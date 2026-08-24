from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from wood_config.core import ConfigError, ConfigPaths, load_config, save_config

from .models import ProjectError
from .project import DEFAULT_OPENPROJECT_REGISTRY_PATH


def _registry_config_path(raw_path: str | None) -> Path:
    value = raw_path.strip() if raw_path is not None else DEFAULT_OPENPROJECT_REGISTRY_PATH
    if not value:
        raise ProjectError("Registry config path must be non-empty.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _load_registry_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectError(f"Unable to read registry manifest: {exc}") from exc

    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(raw)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(raw)
        else:
            raise ProjectError("Registry manifest must use .json, .yaml, or .yml.")
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in registry manifest: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProjectError(f"Invalid YAML in registry manifest: {exc}") from exc

    if not isinstance(payload, dict):
        raise ProjectError("Registry manifest root must be an object.")
    return payload


def _manifest_projects(payload: dict[str, Any]) -> dict[str, Any]:
    openproject = payload.get("openproject")
    if isinstance(openproject, dict) and "projects" in openproject:
        projects = openproject["projects"]
    else:
        projects = payload.get("projects")

    if not isinstance(projects, dict) or not projects:
        raise ProjectError("Registry manifest must define a non-empty projects object.")
    return projects


def _validate_project_metadata(project_path: str, metadata: Any) -> dict[str, Any]:
    if not isinstance(project_path, str) or not project_path.strip():
        raise ProjectError("Registry project paths must be non-empty strings.")
    if not isinstance(metadata, dict):
        raise ProjectError(f"Registry project {project_path!r} must be an object.")

    url = metadata.get("url")
    token_ref = metadata.get("token_ref")
    if not isinstance(url, str) or not url.strip():
        raise ProjectError(f"Registry project {project_path!r} must include a non-empty url.")
    if not isinstance(token_ref, str) or not token_ref.strip():
        raise ProjectError(f"Registry project {project_path!r} must include a non-empty token_ref.")

    cleaned: dict[str, Any] = {
        "url": url.strip(),
        "token_ref": token_ref.strip(),
    }

    for field in ("initiative_id", "project_id"):
        value = metadata.get(field)
        if value is None:
            continue
        if field == "initiative_id":
            if isinstance(value, int) and value > 0:
                cleaned[field] = value
            elif isinstance(value, str) and value.isdecimal() and int(value) > 0:
                cleaned[field] = int(value)
            else:
                raise ProjectError(
                    f"Registry project {project_path!r} initiative_id must be a positive integer."
                )
        elif isinstance(value, str) and value.strip():
            cleaned[field] = value.strip()
        else:
            raise ProjectError(
                f"Registry project {project_path!r} project_id must be a non-empty string."
            )

    user_agent = metadata.get("user_agent")
    if user_agent is not None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ProjectError(
                f"Registry project {project_path!r} user_agent must be a non-empty string."
            )
        cleaned["user_agent"] = user_agent.strip()

    return cleaned


def _active_profile_values(document: dict[str, Any]) -> dict[str, Any]:
    active_profile = str(document["active_profile"])
    profile = document["profiles"].setdefault(active_profile, {})
    if not isinstance(profile, dict):
        raise ProjectError(f"Config profile {active_profile!r} must be an object.")
    return profile


def import_openproject_registry(
    *,
    manifest_path: Path,
    registry_path: str | None,
    apply: bool,
) -> dict[str, Any]:
    manifest_file = manifest_path.expanduser()
    payload = _load_registry_manifest(manifest_file)
    raw_projects = _manifest_projects(payload)
    imported_projects = {
        project_path.strip(): _validate_project_metadata(project_path, metadata)
        for project_path, metadata in raw_projects.items()
    }

    registry_file = _registry_config_path(registry_path)
    try:
        document = load_config(ConfigPaths(file_path=registry_file))
    except (ConfigError, OSError) as exc:
        raise ProjectError(f"Unable to load registry config: {exc}") from exc

    profile = _active_profile_values(document)
    integrations = profile.setdefault("integrations", {})
    if not isinstance(integrations, dict):
        raise ProjectError("integrations must be an object.")
    openproject = integrations.setdefault("openproject", {})
    if not isinstance(openproject, dict):
        raise ProjectError("integrations.openproject must be an object.")
    projects = openproject.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise ProjectError("integrations.openproject.projects must be an object.")

    imported = []
    for project_path, metadata in imported_projects.items():
        previous = projects.get(project_path)
        state = "added" if previous is None else "unchanged" if previous == metadata else "updated"
        projects[project_path] = metadata
        imported.append({"path": project_path, "state": state, **metadata})

    states = [item["state"] for item in imported]
    changed = any(state != "unchanged" for state in states)
    if apply:
        try:
            save_config(ConfigPaths(file_path=registry_file), document)
        except (ConfigError, OSError) as exc:
            raise ProjectError(f"Unable to save registry config: {exc}") from exc

    return {
        "changed": apply and changed,
        "apply": apply,
        "manifest_path": str(manifest_file),
        "registry_config_path": str(registry_file),
        "selected_count": len(imported),
        "added_count": states.count("added"),
        "updated_count": states.count("updated"),
        "unchanged_count": states.count("unchanged"),
        "imported": imported,
    }

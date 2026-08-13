from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from wood_config.core import ConfigError, build_paths, load_config
from wood_secrets.core import SecretResolver
from wood_secrets.core.providers import SecretProviderError

from .models import OpenProjectError, OpenProjectSettings


def _default_user_agent() -> str:
    try:
        return f"wood-tools/{version('wood-tools')}"
    except PackageNotFoundError:
        return "wood-tools/0.0.0"


def _active_profile(document: dict[str, Any], profile: str | None) -> tuple[str, dict[str, Any]]:
    selected = profile or str(document["active_profile"])
    profiles = document.get("profiles", {})
    values = profiles.get(selected)
    if not isinstance(values, dict):
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            f"Config profile {selected!r} is not available.",
        )
    return selected, values


def _optional_positive_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    raise OpenProjectError(
        "OPENPROJECT_CONFIG_UNAVAILABLE",
        f"{field} must be a positive integer when provided.",
    )


def _path_matches(project_path: Path, current_path: Path) -> bool:
    return project_path == current_path or project_path in current_path.parents


def _resolved_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


def _selected_project_metadata(openproject: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    projects = openproject.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            "Missing required OpenProject configuration: integrations.openproject.projects.",
        )

    current_path = Path.cwd().resolve(strict=False)
    matches: list[tuple[int, str, dict[str, Any]]] = []
    for raw_path, metadata in projects.items():
        if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(metadata, dict):
            continue
        project_path = _resolved_project_path(raw_path)
        if _path_matches(project_path, current_path):
            matches.append((len(project_path.parts), raw_path, metadata))

    if not matches:
        known = ", ".join(str(path) for path in sorted(projects)) or "none configured"
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            f"No OpenProject project metadata matches {current_path}. Known paths: {known}.",
        )

    _, selected_path, metadata = sorted(matches, reverse=True)[0]
    return selected_path, metadata


def _openproject_from_registry(
    openproject: dict[str, Any],
    *,
    profile: str | None,
) -> dict[str, Any]:
    projects = openproject.get("projects")
    if isinstance(projects, dict) and projects:
        return openproject

    registry_path = openproject.get("registry_path")
    if not isinstance(registry_path, str) or not registry_path.strip():
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            (
                "Missing required OpenProject configuration: "
                "integrations.openproject.registry_path or integrations.openproject.projects."
            ),
        )

    registry_file = Path(registry_path).expanduser()
    if not registry_file.is_absolute():
        registry_file = Path.cwd() / registry_file
    try:
        registry_document = load_config(build_paths(registry_file))
        _, registry_values = _active_profile(registry_document, profile)
    except (ConfigError, OSError, KeyError, TypeError, ValueError) as exc:
        raise OpenProjectError("OPENPROJECT_CONFIG_UNAVAILABLE", str(exc)) from exc

    integrations = registry_values.get("integrations")
    registry_openproject = (
        integrations.get("openproject") if isinstance(integrations, dict) else None
    )
    registry_openproject = (
        registry_openproject if isinstance(registry_openproject, dict) else {}
    )
    if not isinstance(registry_openproject.get("projects"), dict):
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            (
                "Global OpenProject registry must define "
                "integrations.openproject.projects."
            ),
        )
    return registry_openproject


def load_settings(
    *,
    config_path: Path | None = None,
    profile: str | None = None,
    resolver: SecretResolver | None = None,
) -> OpenProjectSettings:
    try:
        document = load_config(build_paths(config_path))
        _, values = _active_profile(document, profile)
    except (ConfigError, OSError, KeyError, TypeError, ValueError) as exc:
        raise OpenProjectError("OPENPROJECT_CONFIG_UNAVAILABLE", str(exc)) from exc

    integrations = values.get("integrations")
    openproject = integrations.get("openproject") if isinstance(integrations, dict) else None
    openproject = openproject if isinstance(openproject, dict) else {}
    openproject = _openproject_from_registry(openproject, profile=profile)
    selected_project_path, metadata = _selected_project_metadata(openproject)

    base_url = metadata.get("url")
    raw_project_id = metadata.get("project_id")
    project_id = (
        raw_project_id.strip()
        if isinstance(raw_project_id, str) and raw_project_id.strip()
        else None
    )
    initiative_id = _optional_positive_int(
        metadata.get("initiative_id"),
        field=f"integrations.openproject.projects.{selected_project_path}.initiative_id",
    )
    token_ref = metadata.get("token_ref")
    user_agent = metadata.get("user_agent") or _default_user_agent()
    missing = [
        field
        for field, value in (
            (f"integrations.openproject.projects.{selected_project_path}.url", base_url),
            (f"integrations.openproject.projects.{selected_project_path}.token_ref", token_ref),
        )
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            "Missing required OpenProject configuration: " + ", ".join(missing) + ".",
        )

    secret_resolver = resolver or SecretResolver()
    try:
        token = secret_resolver.resolve(str(token_ref).strip())
    except SecretProviderError as exc:
        raise OpenProjectError("OPENPROJECT_ACCESS_UNAVAILABLE", str(exc)) from exc

    return OpenProjectSettings(
        base_url=base_url.strip(),
        token=token.value,
        token_provider=token.provider,
        user_agent=str(user_agent).strip() or _default_user_agent(),
        project_id=project_id,
        initiative_id=initiative_id,
    )


__all__ = ["load_settings"]

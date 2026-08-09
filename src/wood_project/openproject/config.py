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

    base_url = openproject.get("url")
    project_id = openproject.get("project_id")
    token_ref = openproject.get("token_ref")
    user_agent = openproject.get("user_agent") or _default_user_agent()
    missing = [
        field
        for field, value in (
            ("integrations.openproject.url", base_url),
            ("integrations.openproject.project_id", project_id),
            ("integrations.openproject.token_ref", token_ref),
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
        project_id=project_id.strip(),
        token=token.value,
        token_provider=token.provider,
        user_agent=str(user_agent).strip() or _default_user_agent(),
    )


__all__ = ["load_settings"]

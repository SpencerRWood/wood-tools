from __future__ import annotations

import os
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .credentials import resolve_environment
from .models import OpenProjectError, OpenProjectSettings


def _default_user_agent() -> str:
    try:
        return f"wood-tools/{version('wood-tools')}"
    except PackageNotFoundError:
        return "wood-tools/0.0.0"


def load_settings(environ: Mapping[str, str] | None = None) -> OpenProjectSettings:
    values = resolve_environment(Path.cwd(), os.environ) if environ is None else environ
    missing = [
        name for name in ("OPENPROJECT_URL", "OPENPROJECT_API_TOKEN") if not values.get(name)
    ]
    if missing:
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            "Missing injected OpenProject variables: " + ", ".join(missing) + ".",
        )
    return OpenProjectSettings(
        base_url=values["OPENPROJECT_URL"].strip(),
        token=values["OPENPROJECT_API_TOKEN"],
        token_provider=(
            "infisical"
            if environ is None and not os.environ.get("OPENPROJECT_API_TOKEN")
            else "injected-environment"
        ),
        user_agent=_default_user_agent(),
    )


__all__ = ["load_settings"]

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

DEFAULT_PROFILE = "default"
KEY_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]*$")


class ConfigError(ValueError):
    """Raised for invalid config state or invalid user input."""


@dataclass(frozen=True)
class ConfigPaths:
    file_path: Path


def default_config_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home).expanduser() if xdg_config_home else Path.home() / ".config"
    return base / "wood-tools" / "config.json"


def build_paths(path: Path | None = None) -> ConfigPaths:
    return ConfigPaths(file_path=path or default_config_path())


def _default_document() -> dict[str, Any]:
    return {
        "version": 1,
        "active_profile": DEFAULT_PROFILE,
        "profiles": {DEFAULT_PROFILE: {}},
    }


def load_config(paths: ConfigPaths) -> dict[str, Any]:
    if not paths.file_path.exists():
        return _default_document()
    try:
        raw = json.loads(paths.file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config file: {exc}") from exc
    validate_document(raw)
    return raw


def validate_document(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise ConfigError("Config root must be an object")

    if document.get("version") != 1:
        raise ConfigError("Config version must be 1")

    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ConfigError("profiles must be a non-empty object")

    for profile_name, profile_values in profiles.items():
        if not isinstance(profile_name, str) or not profile_name:
            raise ConfigError("Profile names must be non-empty strings")
        if not isinstance(profile_values, dict):
            raise ConfigError("Each profile must contain an object of key/value entries")

    active_profile = document.get("active_profile")
    if not isinstance(active_profile, str) or active_profile not in profiles:
        raise ConfigError("active_profile must reference an existing profile")


def save_config(paths: ConfigPaths, document: dict[str, Any]) -> None:
    validate_document(document)
    paths.file_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=paths.file_path.parent, delete=False) as tmp:
        json.dump(document, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(paths.file_path)
    os.chmod(paths.file_path, 0o600)


def init_config(paths: ConfigPaths, *, apply: bool) -> dict[str, Any]:
    if paths.file_path.exists():
        config = load_config(paths)
        return {"changed": False, "config": config, "path": str(paths.file_path)}

    config = _default_document()
    if apply:
        save_config(paths, config)
    return {"changed": apply, "config": config, "path": str(paths.file_path)}


def get_active_profile(document: dict[str, Any]) -> str:
    validate_document(document)
    return str(document["active_profile"])


def set_active_profile(document: dict[str, Any], profile: str) -> dict[str, Any]:
    if not profile:
        raise ConfigError("Profile name cannot be empty")
    profiles = document["profiles"]
    if profile not in profiles:
        profiles[profile] = {}
    document["active_profile"] = profile
    return document


def _validate_key_name(key: str) -> None:
    if not KEY_PATTERN.fullmatch(key):
        raise ConfigError("Key must match pattern: [a-zA-Z][a-zA-Z0-9_.-]*")


def _resolve_profile(document: dict[str, Any], profile: str | None) -> tuple[str, dict[str, Any]]:
    active_profile = get_active_profile(document)
    selected = profile or active_profile
    profiles = document["profiles"]
    if selected not in profiles:
        raise ConfigError(f"Profile '{selected}' does not exist")
    return selected, profiles[selected]


def show_config(document: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    selected, values = _resolve_profile(document, profile)
    return {
        "active_profile": get_active_profile(document),
        "selected_profile": selected,
        "values": values,
    }


def get_value(document: dict[str, Any], key: str, profile: str | None = None) -> Any:
    _validate_key_name(key)
    _, values = _resolve_profile(document, profile)
    if key not in values:
        raise ConfigError(f"Key '{key}' is not set")
    return values[key]


def set_value(
    document: dict[str, Any],
    key: str,
    value: Any,
    *,
    profile: str | None = None,
    activate_profile: bool = False,
) -> dict[str, Any]:
    _validate_key_name(key)

    if profile:
        if profile not in document["profiles"]:
            document["profiles"][profile] = {}
        selected_profile = profile
    else:
        selected_profile = get_active_profile(document)

    document["profiles"][selected_profile][key] = value
    if activate_profile:
        document["active_profile"] = selected_profile
    return document

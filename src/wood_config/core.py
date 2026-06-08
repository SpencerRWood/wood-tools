from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

DEFAULT_PROFILE = "default"
KEY_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
SENSITIVE_PARTS = ("token", "secret", "password")
REQUIRED_PATH_KEYS = (
    "project_root",
    "artifact_root",
    "scheduler_root",
    "template_search_paths",
)
REFERENCE_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://.+$")


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
    default_values: dict[str, Any] = {
        "paths": {
            "project_root": "./projects",
            "artifact_root": "./artifacts",
            "project_aliases": {},
            "artifact_aliases": {},
            "scheduler_root": "./scheduler",
            "template_search_paths": ["./templates"],
        },
        "integrations": {
            "openproject": {
                "url": None,
                "project_id": None,
                "token_ref": None,
                "user_agent": "wood-tools/0.1",
            },
            "ntfy": {
                "url": None,
                "token_ref": None,
            },
            "vaultwarden": {
                "url": None,
                "config_ref": None,
                "session_file": None,
            },
        },
        "diagnostics": {
            "agent_readiness": {
                "enabled": True,
            }
        },
        "output": {
            "json_envelope": {
                "enabled": True,
            }
        },
    }
    return {
        "version": 1,
        "active_profile": DEFAULT_PROFILE,
        "profiles": {DEFAULT_PROFILE: default_values},
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
        profiles[profile] = deepcopy(profiles[DEFAULT_PROFILE])
    document["active_profile"] = profile
    return document


def _validate_key_name(key: str) -> None:
    if not KEY_PATTERN.fullmatch(key):
        raise ConfigError("Key must match pattern: [a-zA-Z][a-zA-Z0-9_.-]*")


def _split_path(key: str) -> list[str]:
    parts = key.split(".")
    if any(not part for part in parts):
        raise ConfigError("Key path segments cannot be empty")
    return parts


def _lookup(values: dict[str, Any], key: str) -> Any:
    current: Any = values
    for part in _split_path(key):
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"Key '{key}' is not set")
        current = current[part]
    return current


def _set_nested(values: dict[str, Any], key: str, value: Any) -> None:
    current: dict[str, Any] = values
    parts = _split_path(key)
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            current[part] = {}
            existing = current[part]
        if not isinstance(existing, dict):
            raise ConfigError(f"Cannot set nested key under non-object path segment '{part}'")
        current = existing
    current[parts[-1]] = value


def _validate_secret_reference_key_and_value(key: str, value: Any) -> None:
    final_segment = _split_path(key)[-1]
    if any(
        part in final_segment.lower() for part in SENSITIVE_PARTS
    ) and not final_segment.endswith("_ref"):
        raise ConfigError(
            "Sensitive values must be stored as references only. Use keys ending in '_ref'."
        )
    if final_segment.endswith("_ref") and value is not None and not isinstance(value, str):
        raise ConfigError("Reference values must be string values or null")


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
    return _lookup(values, key)


def set_value(
    document: dict[str, Any],
    key: str,
    value: Any,
    *,
    profile: str | None = None,
    activate_profile: bool = False,
) -> dict[str, Any]:
    _validate_key_name(key)
    _validate_secret_reference_key_and_value(key, value)

    if profile:
        if profile not in document["profiles"]:
            document["profiles"][profile] = deepcopy(document["profiles"][DEFAULT_PROFILE])
        selected_profile = profile
    else:
        selected_profile = get_active_profile(document)

    _set_nested(document["profiles"][selected_profile], key, value)
    if activate_profile:
        document["active_profile"] = selected_profile
    return document


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _validate_path_aliases(aliases: Any, *, field_name: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not isinstance(aliases, dict):
        findings.append(
            {
                "code": "malformed_aliases",
                "field": f"paths.{field_name}",
                "message": f"'{field_name}' must be an object mapping alias names to paths.",
                "remediation": f'Set paths.{field_name} to an object like {{"demo":"./path"}}.',
            }
        )
        return findings

    for alias, target in aliases.items():
        if not isinstance(alias, str) or alias.strip() == "":
            findings.append(
                {
                    "code": "invalid_alias_name",
                    "field": f"paths.{field_name}",
                    "message": "Alias names must be non-empty strings.",
                    "remediation": "Rename empty/non-string aliases to a non-empty string key.",
                }
            )
        if not _is_non_empty_string(target):
            findings.append(
                {
                    "code": "invalid_alias_target",
                    "field": f"paths.{field_name}.{alias}",
                    "message": "Alias targets must be non-empty string paths.",
                    "remediation": f"Set paths.{field_name}.{alias} to a non-empty path string.",
                }
            )
    return findings


def _validate_reference(value: Any, *, field: str, required: bool) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if value is None and required:
        findings.append(
            {
                "code": "missing_reference",
                "field": field,
                "message": "Required integration reference is missing.",
                "remediation": f"Set {field} to a secret reference such as 'env://YOUR_ENV_VAR'.",
            }
        )
        return findings
    if value is None:
        return findings
    if not isinstance(value, str) or not REFERENCE_PATTERN.fullmatch(value):
        findings.append(
            {
                "code": "malformed_reference",
                "field": field,
                "message": "Integration reference must be a URI-like reference (scheme://value).",
                "remediation": f"Set {field} to a valid reference, for example 'env://YOUR_ENV_VAR'.",
            }
        )
    return findings


def _validate_optional_string(value: Any, *, field: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if _is_non_empty_string(value):
        return []
    return [
        {
            "code": "invalid_string",
            "field": field,
            "message": "Setting must be a non-empty string when provided.",
            "remediation": f"Set {field} to a non-empty string or null.",
        }
    ]


def _validate_enabled_toggle(value: Any, *, field: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return [
            {
                "code": "invalid_object",
                "field": field,
                "message": "Setting must be an object with an 'enabled' boolean.",
                "remediation": f'Set {field} like {{"enabled": true}}.',
            }
        ]
    enabled = value.get("enabled")
    if isinstance(enabled, bool):
        return []
    return [
        {
            "code": "invalid_enabled",
            "field": f"{field}.enabled",
            "message": "Setting must include an 'enabled' boolean.",
            "remediation": f"Set {field}.enabled to true or false.",
        }
    ]


def validate_profile(profile: dict[str, Any], *, profile_name: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    paths = profile.get("paths")
    if not isinstance(paths, dict):
        return [
            {
                "code": "missing_paths",
                "field": "paths",
                "message": "Profile is missing 'paths' settings.",
                "remediation": (
                    "Initialize config with 'wood-config init --apply' and "
                    "re-apply profile overrides."
                ),
                "profile": profile_name,
            }
        ]

    for key in REQUIRED_PATH_KEYS:
        value = paths.get(key)
        if key == "template_search_paths":
            if (
                not isinstance(value, list)
                or not value
                or any(not _is_non_empty_string(item) for item in value)
            ):
                findings.append(
                    {
                        "code": "invalid_required_path",
                        "field": f"paths.{key}",
                        "message": (
                            "Required path setting must be a non-empty list of non-empty strings."
                        ),
                        "remediation": "Set paths.template_search_paths like ['./templates'].",
                    }
                )
            continue

        if not _is_non_empty_string(value):
            findings.append(
                {
                    "code": "invalid_required_path",
                    "field": f"paths.{key}",
                    "message": "Required path setting must be a non-empty string.",
                    "remediation": f"Set paths.{key} to a non-empty path value.",
                }
            )

    findings.extend(
        _validate_path_aliases(paths.get("project_aliases"), field_name="project_aliases")
    )
    findings.extend(
        _validate_path_aliases(paths.get("artifact_aliases"), field_name="artifact_aliases")
    )

    integrations = profile.get("integrations")
    if not isinstance(integrations, dict):
        findings.append(
            {
                "code": "missing_integrations",
                "field": "integrations",
                "message": "Profile is missing 'integrations' settings.",
                "remediation": (
                    "Initialize config defaults and add integration settings under integrations.*."
                ),
            }
        )
    else:
        openproject = integrations.get("openproject")
        ntfy = integrations.get("ntfy")
        vaultwarden = integrations.get("vaultwarden")

        openproject = openproject if isinstance(openproject, dict) else {}
        ntfy = ntfy if isinstance(ntfy, dict) else {}
        vaultwarden = vaultwarden if isinstance(vaultwarden, dict) else {}

        findings.extend(
            _validate_reference(
                openproject.get("token_ref"),
                field="integrations.openproject.token_ref",
                required=True,
            )
        )
        findings.extend(
            _validate_reference(
                ntfy.get("token_ref"),
                field="integrations.ntfy.token_ref",
                required=True,
            )
        )
        findings.extend(
            _validate_reference(
                vaultwarden.get("config_ref"),
                field="integrations.vaultwarden.config_ref",
                required=True,
            )
        )
        findings.extend(
            _validate_optional_string(
                openproject.get("user_agent"),
                field="integrations.openproject.user_agent",
            )
        )
        findings.extend(
            _validate_optional_string(
                vaultwarden.get("session_file"),
                field="integrations.vaultwarden.session_file",
            )
        )

    diagnostics = profile.get("diagnostics")
    if diagnostics is not None:
        if not isinstance(diagnostics, dict):
            findings.append(
                {
                    "code": "invalid_object",
                    "field": "diagnostics",
                    "message": "Profile diagnostics settings must be an object.",
                    "remediation": "Set diagnostics to an object with diagnostics.* settings.",
                }
            )
            diagnostics = {}
        findings.extend(
            _validate_enabled_toggle(
                diagnostics.get("agent_readiness"),
                field="diagnostics.agent_readiness",
            )
        )

    output = profile.get("output")
    if output is not None:
        if not isinstance(output, dict):
            findings.append(
                {
                    "code": "invalid_object",
                    "field": "output",
                    "message": "Profile output settings must be an object.",
                    "remediation": "Set output to an object with output.* settings.",
                }
            )
            output = {}
        findings.extend(
            _validate_enabled_toggle(
                output.get("json_envelope"),
                field="output.json_envelope",
            )
        )

    for finding in findings:
        finding["profile"] = profile_name

    return findings


def validate_config(document: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    selected, values = _resolve_profile(document, profile)
    errors = validate_profile(values, profile_name=selected)
    return {
        "command": "validate",
        "profile": selected,
        "valid": not errors,
        "errors": errors,
    }


def doctor_config(document: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    validation = validate_config(document, profile=profile)
    return {
        "command": "doctor",
        "profile": validation["profile"],
        "status": "ok" if validation["valid"] else "issues-found",
        "issues": validation["errors"],
        "summary": {
            "issue_count": len(validation["errors"]),
            "contains_secrets": False,
        },
    }

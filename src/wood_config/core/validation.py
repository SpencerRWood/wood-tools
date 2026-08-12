from __future__ import annotations

import re
from typing import Any

from .aliases import (
    REQUIRED_PATH_KEYS,
    SUPPORTED_PATH_KEYS,
    _is_non_empty_string,
    _validate_path_aliases,
    resolve_path_aliases,
)
from .document import _resolve_profile

REFERENCE_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://.+$")


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


def _validate_required_string(value: Any, *, field: str) -> list[dict[str, str]]:
    if _is_non_empty_string(value):
        return []
    return [
        {
            "code": "missing_required_string",
            "field": field,
            "message": "Setting must be a non-empty string.",
            "remediation": f"Set {field} to a non-empty string value.",
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


def _validate_materialized_secrets(value: Any, *, field: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if value is None:
        return findings
    if not isinstance(value, dict):
        return [
            {
                "code": "invalid_object",
                "field": field,
                "message": "Materialized secrets must be an object keyed by secret name.",
                "remediation": (
                    f'Set {field} like '
                    '{"name": {"ref": "env://TOKEN", "target": "file"}}.'
                ),
            }
        ]

    for name, definition in value.items():
        entry_field = f"{field}.{name}"
        if not _is_non_empty_string(name):
            findings.append(
                {
                    "code": "invalid_materialized_secret_name",
                    "field": field,
                    "message": "Materialized secret names must be non-empty strings.",
                    "remediation": "Rename empty materialized secret keys.",
                }
            )
            continue
        if not isinstance(definition, dict):
            findings.append(
                {
                    "code": "invalid_materialized_secret_definition",
                    "field": entry_field,
                    "message": "Materialized secret definitions must be objects.",
                    "remediation": (
                        f'Set {entry_field} like '
                        '{"ref": "env://TOKEN", "target": "file"}.'
                    ),
                }
            )
            continue
        findings.extend(
            _validate_reference(definition.get("ref"), field=f"{entry_field}.ref", required=True)
        )
        findings.extend(
            _validate_required_string(definition.get("target"), field=f"{entry_field}.target")
        )
    return findings


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

    for unsupported_key in sorted(set(paths) - SUPPORTED_PATH_KEYS):
        findings.append(
            {
                "code": "unsupported_path_setting",
                "field": f"paths.{unsupported_key}",
                "message": f"Unsupported path setting: paths.{unsupported_key}",
                "remediation": f"Remove paths.{unsupported_key} from the profile.",
            }
        )

    findings.extend(
        _validate_optional_string(paths.get("secrets_root"), field="paths.secrets_root")
    )
    findings.extend(
        _validate_path_aliases(paths.get("project_aliases"), field_name="project_aliases")
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
        vaultwarden_cli = vaultwarden.get("cli")
        vaultwarden_cli = vaultwarden_cli if isinstance(vaultwarden_cli, dict) else {}

        findings.extend(
            _validate_reference(
                openproject.get("token_ref"),
                field="integrations.openproject.token_ref",
                required=True,
            )
        )
        findings.extend(
            _validate_required_string(
                openproject.get("url"),
                field="integrations.openproject.url",
            )
        )
        findings.extend(
            _validate_required_string(
                openproject.get("project_id"),
                field="integrations.openproject.project_id",
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
        findings.extend(
            _validate_optional_string(
                vaultwarden_cli.get("executable"),
                field="integrations.vaultwarden.cli.executable",
            )
        )
        findings.extend(
            _validate_materialized_secrets(
                vaultwarden.get("materialized_secrets"),
                field="integrations.vaultwarden.materialized_secrets",
            )
        )

    wood_agents = profile.get("wood_agents")
    if wood_agents is not None:
        if not isinstance(wood_agents, dict):
            findings.append(
                {
                    "code": "invalid_object",
                    "field": "wood_agents",
                    "message": "Profile wood_agents settings must be an object.",
                    "remediation": (
                        "Set wood_agents to an object with wood_agents.* reference settings."
                    ),
                }
            )
            wood_agents = {}
        findings.extend(
            _validate_optional_string(
                wood_agents.get("boundary_ref"),
                field="wood_agents.boundary_ref",
            )
        )
        findings.extend(
            _validate_optional_string(
                wood_agents.get("adapters_ref"),
                field="wood_agents.adapters_ref",
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
    alias_resolution = resolve_path_aliases(values)
    return {
        "command": "validate",
        "profile": selected,
        "valid": not errors,
        "errors": errors,
        "alias_resolution": alias_resolution,
    }

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


def _validate_optional_positive_int(value: Any, *, field: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, int) and value > 0:
        return []
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return []
    return [
        {
            "code": "invalid_positive_integer",
            "field": field,
            "message": "Setting must be a positive integer when provided.",
            "remediation": f"Set {field} to a positive integer or null.",
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
                        '{"name": {"ref": "vaultwarden://service/principal/credential#FIELD"}}.'
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
                        '{"ref": "vaultwarden://service/principal/credential#FIELD"}.'
                    ),
                }
            )
            continue
        findings.extend(
            _validate_reference(definition.get("ref"), field=f"{entry_field}.ref", required=True)
        )
        findings.extend(
            _validate_optional_string(definition.get("target"), field=f"{entry_field}.target")
        )
    return findings


def _validate_openproject_metadata(value: Any, *, field: str) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return [
            {
                "code": "invalid_object",
                "field": field,
                "message": "OpenProject project metadata must be an object.",
                "remediation": (
                    f'Set {field} like '
                    '{"url": "https://openproject.example.test", '
                    '"token_ref": "env://OPENPROJECT_TOKEN", "initiative_id": 208}.'
                ),
            }
        ]

    findings: list[dict[str, str]] = []
    findings.extend(
        _validate_reference(
            value.get("token_ref"),
            field=f"{field}.token_ref",
            required=True,
        )
    )
    findings.extend(
        _validate_required_string(
            value.get("url"),
            field=f"{field}.url",
        )
    )
    findings.extend(
        _validate_optional_string(
            value.get("project_id"),
            field=f"{field}.project_id",
        )
    )
    findings.extend(
        _validate_optional_positive_int(
            value.get("initiative_id"),
            field=f"{field}.initiative_id",
        )
    )
    findings.extend(
        _validate_optional_string(
            value.get("user_agent"),
            field=f"{field}.user_agent",
        )
    )
    return findings


def _validate_openproject_projects(value: Any, *, field: str) -> list[dict[str, str]]:
    if not isinstance(value, dict) or not value:
        return [
            {
                "code": "invalid_openproject_projects",
                "field": field,
                "message": "OpenProject projects must be a non-empty object keyed by project path.",
                "remediation": (
                    f'Set {field} like '
                    '{".": {"url": "https://openproject.example.test", '
                    '"token_ref": "env://OPENPROJECT_TOKEN", "initiative_id": 208}}.'
                ),
            }
        ]

    findings: list[dict[str, str]] = []
    for project_path, metadata in value.items():
        if not _is_non_empty_string(project_path):
            findings.append(
                {
                    "code": "invalid_openproject_project_path",
                    "field": field,
                    "message": "OpenProject project path keys must be non-empty strings.",
                    "remediation": "Use project path keys such as '.' or an absolute project path.",
                }
            )
            continue
        findings.extend(
            _validate_openproject_metadata(
                metadata,
                field=f'{field}["{project_path}"]',
            )
        )
    return findings


def _validate_openproject(value: Any, *, field: str) -> list[dict[str, str]]:
    value = value if isinstance(value, dict) else {}
    registry_path = value.get("registry_path")
    projects = value.get("projects")

    findings: list[dict[str, str]] = []
    if registry_path is not None:
        findings.extend(_validate_optional_string(registry_path, field=f"{field}.registry_path"))
    if projects is not None:
        findings.extend(_validate_openproject_projects(projects, field=f"{field}.projects"))
    if registry_path is None and projects is None:
        findings.append(
            {
                "code": "missing_openproject_registry",
                "field": field,
                "message": "OpenProject config must define registry_path or projects.",
                "remediation": (
                    f'Set {field}.registry_path to a global config path or '
                    f"set {field}.projects to a path-keyed metadata registry."
                ),
            }
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
            _validate_openproject(
                openproject,
                field="integrations.openproject",
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

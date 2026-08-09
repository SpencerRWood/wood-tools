from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import ConfigError

REQUIRED_PATH_KEYS = ("project_root", "scheduler_root", "template_search_paths")
ALIAS_PATH_FIELDS = ("project_aliases",)
SUPPORTED_PATH_KEYS = frozenset((*REQUIRED_PATH_KEYS, *ALIAS_PATH_FIELDS))


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _select_alias_target(path_value: str, targets: list[str]) -> tuple[str, str, bool]:
    for candidate in targets:
        if Path(candidate).expanduser().exists():
            return candidate, "target", True
    if Path(path_value).expanduser().exists():
        return path_value, "path", True
    if targets:
        return targets[0], "target-fallback", False
    return path_value, "path-fallback", False


def _resolve_alias_map(aliases: Any, *, field_name: str) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    if not isinstance(aliases, dict):
        return resolved

    for alias, target in aliases.items():
        if not isinstance(alias, str) or alias.strip() == "":
            continue
        if not isinstance(target, dict):
            continue

        path_value = target.get("path")
        targets = target.get("targets")
        if not _is_non_empty_string(path_value):
            continue
        if not isinstance(targets, list) or any(not _is_non_empty_string(item) for item in targets):
            continue

        normalized_targets = [str(item) for item in targets]
        resolved_path, resolution_source, available = _select_alias_target(
            str(path_value), normalized_targets
        )
        resolved[alias] = {
            "path": str(path_value),
            "targets": normalized_targets,
            "resolved_path": resolved_path,
            "resolution_source": resolution_source,
            "available": available,
            "field": f"paths.{field_name}.{alias}",
        }

    return resolved


def resolve_path_aliases(profile: dict[str, Any]) -> dict[str, Any]:
    paths = profile.get("paths")
    if not isinstance(paths, dict):
        return {field_name: {} for field_name in ALIAS_PATH_FIELDS}

    return {
        field_name: _resolve_alias_map(paths.get(field_name), field_name=field_name)
        for field_name in ALIAS_PATH_FIELDS
    }


def _validate_alias_entry(
    alias: Any,
    target: Any,
    *,
    field_name: str,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    field_root = f"paths.{field_name}"

    if not isinstance(alias, str) or alias.strip() == "":
        findings.append(
            {
                "code": "invalid_alias_name",
                "field": field_root,
                "message": "Alias names must be non-empty strings.",
                "remediation": "Rename empty/non-string aliases to a non-empty string key.",
            }
        )
        return findings

    field = f"{field_root}.{alias}"
    if not isinstance(target, dict):
        findings.append(
            {
                "code": "invalid_alias_target",
                "field": field,
                "message": "Alias entries must be objects with path and targets fields.",
                "remediation": (
                    f"Set {field} to an object like "
                    '{"path":"//nas/demo","targets":["/Volumes/demo","/mnt/demo"]}.'
                ),
            }
        )
        return findings

    path_value = target.get("path")
    if not _is_non_empty_string(path_value):
        findings.append(
            {
                "code": "invalid_alias_path",
                "field": f"{field}.path",
                "message": "Alias path must be a non-empty string.",
                "remediation": f"Set {field}.path to the canonical shared path string.",
            }
        )

    targets = target.get("targets")
    if not isinstance(targets, list) or not targets:
        findings.append(
            {
                "code": "invalid_alias_targets",
                "field": f"{field}.targets",
                "message": "Alias targets must be a non-empty list of candidate local paths.",
                "remediation": f'Set {field}.targets to a list like ["/Volumes/demo","/mnt/demo"].',
            }
        )
        return findings

    for index, candidate in enumerate(targets):
        if _is_non_empty_string(candidate):
            continue
        findings.append(
            {
                "code": "invalid_alias_target_path",
                "field": f"{field}.targets.{index}",
                "message": "Each alias target must be a non-empty string path.",
                "remediation": f"Replace {field}.targets.{index} with a non-empty path string.",
            }
        )

    return findings


def _validate_path_aliases(aliases: Any, *, field_name: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not isinstance(aliases, dict):
        findings.append(
            {
                "code": "malformed_aliases",
                "field": f"paths.{field_name}",
                "message": f"'{field_name}' must be an object mapping alias names to paths.",
                "remediation": (
                    f"Set paths.{field_name} to an object like "
                    '{"demo":{"path":"//nas/demo","targets":["/mnt/demo"]}}.'
                ),
            }
        )
        return findings

    for alias, target in aliases.items():
        findings.extend(_validate_alias_entry(alias, target, field_name=field_name))
    return findings


def _validate_alias_mutation(profile_values: dict[str, Any], *, key: str) -> None:
    paths = profile_values.get("paths")
    if not isinstance(paths, dict):
        return

    findings: list[dict[str, str]]
    if key == "paths.project_aliases":
        findings = _validate_path_aliases(
            paths.get("project_aliases"),
            field_name="project_aliases",
        )
    elif key.startswith("paths.project_aliases."):
        alias_name = key.removeprefix("paths.project_aliases.").split(".", 1)[0]
        aliases = paths.get("project_aliases", {})
        findings = _validate_alias_entry(
            alias_name,
            aliases.get(alias_name) if isinstance(aliases, dict) else None,
            field_name="project_aliases",
        )
    elif key.startswith("paths.") and key.split(".", 2)[1] not in SUPPORTED_PATH_KEYS:
        raise ConfigError(f"Unsupported path setting: paths.{key.split('.', 2)[1]}")
    else:
        return

    if findings:
        raise ConfigError(findings[0]["message"])

from __future__ import annotations

from typing import Any

from .validation import validate_config

DOCTOR_CHECK_FIELDS: dict[str, tuple[str, ...]] = {
    "vaultwarden": ("integrations.vaultwarden.",),
    "openproject": ("integrations.openproject.",),
    "ntfy": ("integrations.ntfy.",),
    "scheduler": ("paths.scheduler_root",),
    "agent-readiness": ("diagnostics.agent_readiness.", "wood_agents."),
}
DEFAULT_DOCTOR_CHECKS = tuple(DOCTOR_CHECK_FIELDS)


def _filter_findings_for_check(
    findings: list[dict[str, str]],
    *,
    check_name: str,
) -> list[dict[str, str]]:
    prefixes = DOCTOR_CHECK_FIELDS[check_name]
    return [
        finding
        for finding in findings
        if any(
            finding["field"] == prefix or finding["field"].startswith(prefix) for prefix in prefixes
        )
    ]


def doctor_config(
    document: dict[str, Any],
    profile: str | None = None,
    *,
    checks: list[str] | None = None,
) -> dict[str, Any]:
    validation = validate_config(document, profile=profile)
    selected_checks = list(checks or DEFAULT_DOCTOR_CHECKS)
    issues: list[dict[str, str]] = []
    check_results: list[dict[str, Any]] = []
    alias_resolution = validation["alias_resolution"]

    for check_name in selected_checks:
        check_issues = _filter_findings_for_check(validation["errors"], check_name=check_name)
        issues.extend(check_issues)
        check_results.append(
            {
                "name": check_name,
                "status": "ok" if not check_issues else "issues-found",
                "issues": check_issues,
                "summary": {
                    "issue_count": len(check_issues),
                    "contains_secrets": False,
                },
            }
        )

    alias_diagnostics: list[dict[str, Any]] = []
    for field_name, aliases in alias_resolution.items():
        for alias_name, details in aliases.items():
            alias_details = {"name": alias_name, "kind": field_name, **details}
            alias_diagnostics.append(alias_details)
            if details["available"]:
                continue
            issues.append(
                {
                    "code": "unresolved_alias_target",
                    "field": details["field"],
                    "message": (
                        f"Alias '{alias_name}' did not match an existing local target; "
                        f"using {details['resolution_source']}."
                    ),
                    "remediation": (
                        f"Update {details['field']}.targets so one path exists on this machine."
                    ),
                    "profile": validation["profile"],
                }
            )

    return {
        "command": "doctor",
        "profile": validation["profile"],
        "status": "ok" if not issues else "issues-found",
        "checks": check_results,
        "issues": issues,
        "alias_resolution": alias_resolution,
        "alias_diagnostics": alias_diagnostics,
        "summary": {
            "selected_checks": selected_checks,
            "issue_count": len(issues),
            "contains_secrets": False,
        },
    }

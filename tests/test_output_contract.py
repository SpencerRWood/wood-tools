from __future__ import annotations

import json

from resources.cli.output import blocked_output, error_output, success_output, warning_output


def test_success_output_contract_is_stable() -> None:
    payload = success_output(
        command="show",
        mutation="read-only",
        summary="Loaded config.",
        data={"profile": "default"},
        next_actions=["Run wood-config validate --json."],
    )

    assert payload == {
        "command": "show",
        "status": "success",
        "mutation": "read-only",
        "requires_approval": False,
        "summary": "Loaded config.",
        "data": {"profile": "default"},
        "warnings": [],
        "errors": [],
        "next_actions": ["Run wood-config validate --json."],
    }
    assert json.loads(json.dumps(payload)) == payload


def test_blocked_output_marks_approval_gated_mutation() -> None:
    payload = blocked_output(
        command="set",
        summary="Config update is waiting for approval.",
        data={"key": "paths.project_root", "changed": False},
        next_actions=["Re-run with --apply to persist the config change."],
    )

    assert payload["status"] == "blocked"
    assert payload["mutation"] == "mutating"
    assert payload["requires_approval"] is True
    assert payload["errors"] == []
    assert payload["next_actions"] == ["Re-run with --apply to persist the config change."]


def test_warning_and_error_outputs_preserve_messages() -> None:
    warning = warning_output(
        command="validate",
        mutation="read-only",
        summary="Validation found issues.",
        data={"profile": "default", "valid": False},
        errors=[
            {
                "code": "missing_reference",
                "field": "integrations.openproject.token_ref",
                "message": "Required integration reference is missing.",
                "remediation": "Set integrations.openproject.token_ref to env://OPENPROJECT_TOKEN.",
            }
        ],
        next_actions=["Set integrations.openproject.token_ref to env://OPENPROJECT_TOKEN."],
    )
    failure = error_output(
        command="get",
        mutation="read-only",
        summary="Key is not set",
        errors=[{"message": "Key 'missing.key' is not set"}],
    )

    assert warning["status"] == "warning"
    assert warning["errors"][0]["field"] == "integrations.openproject.token_ref"
    assert failure["status"] == "error"
    assert failure["errors"] == [{"message": "Key 'missing.key' is not set"}]

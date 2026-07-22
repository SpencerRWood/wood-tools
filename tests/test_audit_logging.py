from __future__ import annotations

import json
from pathlib import Path

import pytest

from wood_config.audit import (
    MAX_BYTES_ENV,
    MAX_FILES_ENV,
    MODE_ENV,
    PATH_ENV,
    build_audit_event,
    write_audit_event,
)
from wood_config.output import blocked_output, error_output, success_output


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_success_event_writes_metadata_only_to_global_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wood_home = tmp_path / "wood-home"
    monkeypatch.setenv("WOOD_HOME", str(wood_home))
    monkeypatch.delenv(MODE_ENV, raising=False)
    monkeypatch.delenv(PATH_ENV, raising=False)

    envelope = success_output(
        command="set",
        mutation="mutating",
        summary="Updated config key integrations.openproject.token_ref.",
        data={
            "path": str(tmp_path / "config.json"),
            "key": "integrations.openproject.token_ref",
            "value": "env://OPENPROJECT_TOKEN",
        },
    )

    command_args = [
        "--config-path",
        str(tmp_path / "config.json"),
        "set",
        "integrations.openproject.token_ref",
        "env://OPENPROJECT_TOKEN",
        "--json",
    ]

    write_audit_event(envelope, cli_name="wood-config", command_args=command_args)

    log_path = wood_home / "state" / "logs" / "audit.jsonl"
    raw_line = log_path.read_text(encoding="utf-8").splitlines()[0]
    assert raw_line.startswith('{"timestamp": "')
    assert raw_line.index('"cli": "wood-config"') < raw_line.index('"command": "set"')
    assert raw_line.index('"command": "set"') < raw_line.index('"full_command": ')
    assert raw_line.index('"full_command": ') < raw_line.index('"summary": ')
    assert '"invocation": ' not in raw_line
    assert raw_line.index('"summary": ') < raw_line.index('"outcome": ')
    assert raw_line.rindex('"actor": ') > raw_line.index('"reason": ')
    assert raw_line.rindex('"schema_version": 1') > raw_line.rindex('"actor": ')

    events = _read_jsonl(log_path)
    assert len(events) == 1
    event = events[0]
    assert event["schema_version"] == 1
    assert event["cli"] == "wood-config"
    assert "invocation" not in event
    assert event["command"] == "set"
    assert event["full_command"].startswith("wood-config --config-path")
    assert event["summary"] == "Update a Wood Tools configuration value"
    assert event["reason"] == "Updated config key integrations.openproject.token_ref."
    assert event["outcome"] == "success"
    assert event["mutation"] is True
    assert event["mutation_status"] == "mutating"
    assert event["target"] == {"type": "config", "path": str(tmp_path / "config.json")}
    assert "timestamp" in event
    assert "actor" in event
    assert "data" not in event
    assert "env://OPENPROJECT_TOKEN" not in json.dumps(event)


def test_blocked_event_records_approval_gate_without_sensitive_payload() -> None:
    envelope = blocked_output(
        command="init",
        summary="Config initialization requires approval to write /tmp/config.json.",
        data={"path": "/tmp/config.json", "value": "password=super-secret", "changed": False},
        next_actions=["Re-run with --apply to create the config file."],
    )

    event = build_audit_event(envelope, cli_name="wood-config")

    assert event["outcome"] == "blocked"
    assert event["summary"] == "Initialize Wood Tools configuration"
    assert event["reason"] == "Config initialization requires approval to write /tmp/config.json."
    assert event["requires_approval"] is True
    assert event["mutation"] is True
    assert event["target"] == {"type": "config", "path": "/tmp/config.json"}
    assert event["next_actions_count"] == 1
    assert "super-secret" not in json.dumps(event)


def test_full_command_records_cli_args_and_redacts_sensitive_config_value() -> None:
    envelope = success_output(
        command="set",
        mutation="mutating",
        summary="Updated config key integrations.openproject.token_ref.",
    )

    event = build_audit_event(
        envelope,
        cli_name="wood-config",
        command_args=[
            "--config-path",
            "/tmp/config.json",
            "set",
            "integrations.openproject.token_ref",
            "env://OPENPROJECT_TOKEN",
            "--json",
        ],
    )

    assert event["full_command"] == (
        "wood-config --config-path /tmp/config.json set "
        "integrations.openproject.token_ref '<redacted>' --json"
    )
    assert "env://OPENPROJECT_TOKEN" not in json.dumps(event)


def test_full_command_redacts_sensitive_flags_and_assignments() -> None:
    envelope = error_output(
        command="exec",
        mutation="read-only",
        summary="Inline exec bindings must use NAME=reference syntax.",
    )

    event = build_audit_event(
        envelope,
        cli_name="wood-secrets",
        command_args=[
            "exec",
            "TOKEN=plain-secret",
            "--",
            "curl",
            "--password",
            "hunter2",
            "api_key=abcd",
        ],
    )
    rendered = json.dumps(event)

    assert "plain-secret" not in rendered
    assert "hunter2" not in rendered
    assert "abcd" not in rendered
    assert event["full_command"] == (
        "wood-secrets exec 'TOKEN=<redacted>' -- curl --password '<redacted>' 'api_key=<redacted>'"
    )


def test_project_payload_sets_project_target_type() -> None:
    envelope = success_output(
        command="init",
        mutation="mutating",
        summary="Project initialized.",
        data={"project": {"project_id": "proj-123"}, "path": "/tmp/project.json"},
    )

    event = build_audit_event(envelope, cli_name="wood-project")

    assert event["cli"] == "wood-project"
    assert "invocation" not in event
    assert event["summary"] == "Initialize project metadata"
    assert event["target"] == {"type": "project", "path": "/tmp/project.json"}


def test_failure_event_retains_redacted_error_evidence() -> None:
    envelope = error_output(
        command="resolve",
        mutation="read-only",
        summary="Provider failed with token=abc123.",
        errors=[
            {
                "code": "provider_failed",
                "message": "Could not resolve password=hunter2.",
                "remediation": "Run unlock with api_key=abcd.",
            }
        ],
    )

    event = build_audit_event(envelope, cli_name="wood-secrets")
    rendered = json.dumps(event)

    assert event["outcome"] == "error"
    assert event["summary"] == "Resolve a secret reference without exposing the value"
    assert event["reason"] == "Provider failed with token=<redacted>."
    assert event["mutation"] is False
    assert event["errors"] == [
        {
            "code": "provider_failed",
            "message": "Could not resolve password=<redacted>.",
            "remediation": "Run unlock with api_key=<redacted>.",
        }
    ]
    assert "abc123" not in rendered
    assert "hunter2" not in rendered
    assert "abcd" not in rendered


def test_disabled_file_logging_creates_no_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(MODE_ENV, "disabled")
    monkeypatch.setenv(PATH_ENV, str(log_path))

    write_audit_event(success_output(command="show", mutation="read-only", summary="Loaded."))

    assert not log_path.exists()


def test_console_only_logging_writes_to_stderr_without_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv(MODE_ENV, "console")
    monkeypatch.setenv(PATH_ENV, str(log_path))

    write_audit_event(success_output(command="show", mutation="read-only", summary="Loaded."))

    assert json.loads(capsys.readouterr().err)["command"] == "show"
    assert not log_path.exists()


def test_audit_write_failures_do_not_break_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("already here\n", encoding="utf-8")
    monkeypatch.setenv(MODE_ENV, "file")
    monkeypatch.setenv(PATH_ENV, str(parent_file / "audit.jsonl"))

    write_audit_event(success_output(command="show", mutation="read-only", summary="Loaded."))


def test_audit_log_rotation_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "audit.jsonl"
    log_path.write_text("x" * 20, encoding="utf-8")
    log_path.with_name("audit.jsonl.1").write_text("older\n", encoding="utf-8")
    monkeypatch.setenv(MODE_ENV, "file")
    monkeypatch.setenv(PATH_ENV, str(log_path))
    monkeypatch.setenv(MAX_BYTES_ENV, "5")
    monkeypatch.setenv(MAX_FILES_ENV, "2")

    write_audit_event(success_output(command="validate", mutation="read-only", summary="Valid."))

    assert _read_jsonl(log_path)[0]["command"] == "validate"
    assert log_path.with_name("audit.jsonl.1").read_text(encoding="utf-8") == "x" * 20
    assert log_path.with_name("audit.jsonl.2").read_text(encoding="utf-8") == "older\n"

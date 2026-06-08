from __future__ import annotations

import json

import pytest

from wood_config.cli import main


def test_init_set_get_show_success_path(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"

    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    assert config_path.exists()

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "paths.project_aliases",
                '{"demo":"./projects/demo"}',
                "--profile",
                "dev",
                "--activate-profile",
                "--apply",
            ]
        )
        == 0
    )

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "get",
                "paths.project_aliases.demo",
                "--profile",
                "dev",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "value: ./projects/demo" in out

    assert main(["--config-path", str(config_path), "show", "--profile", "dev"]) == 0
    out = capsys.readouterr().out
    assert "selected_profile: dev" in out
    assert "project_root': './projects'" in out
    assert "user_agent': 'wood-tools/0.1'" in out

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "integrations.vaultwarden.session_file",
                '"~/.config/wood-tools/vaultwarden-session.json"',
                "--profile",
                "dev",
                "--apply",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "get",
                "integrations.vaultwarden.session_file",
                "--profile",
                "dev",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "value: ~/.config/wood-tools/vaultwarden-session.json" in out


def test_get_missing_key_returns_error(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    code = main(["--config-path", str(config_path), "get", "missing.key"])

    assert code == 2
    err = capsys.readouterr().err
    assert "is not set" in err


def test_set_invalid_key_returns_error(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    code = main(["--config-path", str(config_path), "set", "1bad", "value", "--apply"])

    assert code == 2
    err = capsys.readouterr().err
    assert "Key must match pattern" in err


def test_set_secret_value_without_reference_key_returns_error(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    code = main(
        [
            "--config-path",
            str(config_path),
            "set",
            "integrations.openproject.token",
            "abc123",
            "--apply",
        ]
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "must be stored as references only" in err


def test_json_output_for_supported_commands(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"

    assert main(["--config-path", str(config_path), "init", "--apply", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is True
    assert payload["config"]["profiles"]["default"]["paths"]["project_root"] == "./projects"
    assert (
        payload["config"]["profiles"]["default"]["integrations"]["openproject"]["token_ref"] is None
    )
    assert (
        payload["config"]["profiles"]["default"]["integrations"]["openproject"]["user_agent"]
        == "wood-tools/0.1"
    )
    assert (
        payload["config"]["profiles"]["default"]["diagnostics"]["agent_readiness"]["enabled"]
        is True
    )
    assert payload["config"]["profiles"]["default"]["output"]["json_envelope"]["enabled"] is True

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "integrations.openproject.token_ref",
                '"env://OPENPROJECT_TOKEN"',
                "--apply",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == "env://OPENPROJECT_TOKEN"

    assert main(["--config-path", str(config_path), "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["values"]["output"]["json_envelope"]["enabled"] is True

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "get",
                "integrations.openproject.user_agent",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["key"] == "integrations.openproject.user_agent"
    assert payload["value"] == "wood-tools/0.1"


def test_validate_success_path(tmp_path: pytest.TempPathFactory) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "integrations.openproject.token_ref",
                '"env://OPENPROJECT_TOKEN"',
                "--apply",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "integrations.ntfy.token_ref",
                '"env://NTFY"',
                "--apply",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "integrations.vaultwarden.config_ref",
                '"env://VAULTWARDEN_CONFIG"',
                "--apply",
            ]
        )
        == 0
    )

    assert main(["--config-path", str(config_path), "validate"]) == 0
    assert main(["--config-path", str(config_path), "doctor"]) == 0


def test_validate_invalid_config_reports_actionable_errors(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    capsys.readouterr()

    code = main(["--config-path", str(config_path), "validate"])
    assert code == 2
    out = capsys.readouterr().out
    assert "status: invalid" in out
    assert "integrations.openproject.token_ref" in out
    assert "remediation:" in out
    assert "YOUR_ENV_VAR" in out

    assert main(["--config-path", str(config_path), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "issues:" in out
    assert "token_ref" in out
    assert "env://" in out


def test_json_output_for_validate_and_doctor(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    capsys.readouterr()

    code = main(["--config-path", str(config_path), "validate", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["valid"] is False
    assert payload["errors"]
    assert payload["errors"][0]["remediation"]
    assert payload["errors"][0]["field"].startswith("integrations.")

    assert main(["--config-path", str(config_path), "doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "doctor"
    assert payload["status"] == "issues-found"
    assert payload["summary"]["contains_secrets"] is False
    assert payload["summary"]["issue_count"] > 0


def test_validate_reports_invalid_follow_up_fields(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "integrations.openproject.user_agent",
                '""',
                "--apply",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "diagnostics.agent_readiness",
                '{"enabled":"yes"}',
                "--apply",
            ]
        )
        == 0
    )
    capsys.readouterr()

    code = main(["--config-path", str(config_path), "validate", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    fields = {issue["field"] for issue in payload["errors"]}
    assert "integrations.openproject.user_agent" in fields
    assert "diagnostics.agent_readiness.enabled" in fields

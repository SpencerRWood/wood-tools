from __future__ import annotations

import json

import pytest

from wood_config.cli import main


def test_init_set_get_show_success_path(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    project_target = tmp_path / "mounts" / "demo-project"
    project_target.mkdir(parents=True)

    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    assert config_path.exists()

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "paths.project_aliases",
                json.dumps(
                    {
                        "demo": {
                            "path": "//nas/projects/demo",
                            "targets": ["/missing/demo-project", str(project_target)],
                        }
                    }
                ),
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
    assert "resolved_value" in out
    assert str(project_target) in out

    assert main(["--config-path", str(config_path), "show", "--profile", "dev"]) == 0
    out = capsys.readouterr().out
    assert "selected_profile: dev" in out
    assert "project_root': './projects'" in out
    assert "alias_resolution" in out
    assert str(project_target) in out
    assert "user_agent': 'wood-tools/0.3.0'" in out
    assert "'executable': 'bw'" in out

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

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "wood_agents.boundary_ref",
                '"docs://wood-agents/boundary"',
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
                "wood_agents.boundary_ref",
                "--profile",
                "dev",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "value: docs://wood-agents/boundary" in out


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


def test_set_invalid_alias_target_returns_error(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    code = main(
        [
            "--config-path",
            str(config_path),
            "set",
            "paths.project_aliases.demo",
            '{"path":"//nas/projects/demo","targets":["", "/mnt/demo"]}',
            "--apply",
        ]
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "Each alias target must be a non-empty string path." in err


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
    assert payload["command"] == "init"
    assert payload["status"] == "success"
    assert payload["mutation"] == "mutating"
    assert payload["requires_approval"] is False
    assert payload["data"]["changed"] is True
    assert payload["data"]["config"]["profiles"]["default"]["paths"]["project_root"] == "./projects"
    assert (
        payload["data"]["config"]["profiles"]["default"]["integrations"]["openproject"]["token_ref"]
        is None
    )
    assert (
        payload["data"]["config"]["profiles"]["default"]["integrations"]["openproject"][
            "user_agent"
        ]
        == "wood-tools/0.3.0"
    )
    assert (
        payload["data"]["config"]["profiles"]["default"]["integrations"]["vaultwarden"]["cli"][
            "executable"
        ]
        == "bw"
    )
    assert (
        payload["data"]["config"]["profiles"]["default"]["diagnostics"]["agent_readiness"][
            "enabled"
        ]
        is True
    )
    assert (
        payload["data"]["config"]["profiles"]["default"]["output"]["json_envelope"]["enabled"]
        is True
    )
    assert payload["data"]["config"]["profiles"]["default"]["wood_agents"]["boundary_ref"] is None
    assert payload["data"]["config"]["profiles"]["default"]["paths"]["project_aliases"] == {}
    assert "artifact_root" not in payload["data"]["config"]["profiles"]["default"]["paths"]
    assert "artifact_aliases" not in payload["data"]["config"]["profiles"]["default"]["paths"]

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
    assert payload["command"] == "set"
    assert payload["status"] == "success"
    assert payload["data"]["value"] == "env://OPENPROJECT_TOKEN"

    assert main(["--config-path", str(config_path), "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "show"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["values"]["output"]["json_envelope"]["enabled"] is True
    assert payload["data"]["values"]["integrations"]["vaultwarden"]["cli"]["executable"] == "bw"

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
    assert payload["command"] == "get"
    assert payload["data"]["key"] == "integrations.openproject.user_agent"
    assert payload["data"]["value"] == "wood-tools/0.3.0"

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "wood_agents.adapters_ref",
                '"pkg://wood-agents/adapters"',
                "--apply",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "set"
    assert payload["data"]["key"] == "wood_agents.adapters_ref"
    assert payload["data"]["value"] == "pkg://wood-agents/adapters"


def test_unsupported_path_settings_are_rejected(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    code = main(
        [
            "--config-path",
            str(config_path),
            "set",
            "paths.artifact_aliases.demo",
            '{"path":"//nas/artifacts/demo","targets":["/mnt/demo"]}',
            "--apply",
        ]
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "Unsupported path setting: paths.artifact_aliases" in err

    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["profiles"]["default"]["paths"]["artifact_root"] = "./artifacts"
    document["profiles"]["default"]["paths"]["artifact_aliases"] = {}
    config_path.write_text(json.dumps(document), encoding="utf-8")

    code = main(["--config-path", str(config_path), "validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    fields = {issue["field"] for issue in payload["errors"]}
    assert "paths.artifact_root" in fields
    assert "paths.artifact_aliases" in fields


def test_string_alias_schema_is_rejected(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    code = main(
        [
            "--config-path",
            str(config_path),
            "set",
            "paths.project_aliases.demo",
            '"./projects/demo"',
            "--apply",
        ]
    )

    assert code == 2
    assert "Alias entries must be objects with path and targets fields." in capsys.readouterr().err


def test_validate_success_path(tmp_path: pytest.TempPathFactory) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "integrations.openproject.url",
                '"https://openproject.example.test"',
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
                "integrations.openproject.project_id",
                '"wood"',
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
                "wood_agents.boundary_ref",
                '"docs://wood-agents/boundary"',
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
                "wood_agents.adapters_ref",
                '"pkg://wood-agents/adapters"',
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
    assert "integrations.openproject.url" in out
    assert "integrations.openproject.project_id" in out
    assert "remediation:" in out
    assert "YOUR_ENV_VAR" in out

    assert main(["--config-path", str(config_path), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "issues:" in out
    assert "token_ref" in out
    assert "env://" in out


def test_validate_reports_invalid_alias_targets(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    capsys.readouterr()
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["profiles"]["default"]["paths"]["project_aliases"] = {
        "demo": {
            "path": "//nas/projects/demo",
            "targets": ["/mnt/demo"],
        },
        "bad": {
            "path": "//nas/projects/bad",
            "targets": [],
        },
    }
    config_path.write_text(json.dumps(document), encoding="utf-8")

    code = main(["--config-path", str(config_path), "validate", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    fields = {issue["field"] for issue in payload["errors"]}
    assert "paths.project_aliases.bad.targets" in fields
    assert (
        payload["data"]["alias_resolution"]["project_aliases"]["demo"]["resolved_path"]
        == "/mnt/demo"
    )


def test_json_output_for_validate_and_doctor(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    project_target = tmp_path / "mounts" / "demo-project"
    project_target.mkdir(parents=True)
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "paths.project_aliases.demo",
                json.dumps(
                    {
                        "path": "//nas/projects/demo",
                        "targets": [str(project_target), "/missing/demo-project"],
                    }
                ),
                "--apply",
            ]
        )
        == 0
    )
    capsys.readouterr()

    code = main(["--config-path", str(config_path), "validate", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "warning"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["valid"] is False
    assert payload["errors"]
    assert payload["errors"][0]["remediation"]
    assert payload["errors"][0]["field"].startswith("integrations.")
    assert payload["next_actions"]
    assert payload["data"]["alias_resolution"]["project_aliases"]["demo"]["resolved_path"] == str(
        project_target
    )

    assert main(["--config-path", str(config_path), "doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "doctor"
    assert payload["status"] == "warning"
    assert payload["data"]["status"] == "issues-found"
    assert payload["data"]["summary"]["selected_checks"] == [
        "vaultwarden",
        "openproject",
        "ntfy",
        "scheduler",
        "agent-readiness",
    ]
    assert [check["name"] for check in payload["data"]["checks"]] == payload["data"]["summary"][
        "selected_checks"
    ]
    assert payload["data"]["summary"]["contains_secrets"] is False
    assert payload["data"]["summary"]["issue_count"] > 0
    assert payload["data"]["alias_diagnostics"][0]["resolved_path"] == str(project_target)
    assert payload["warnings"]


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
                "integrations.vaultwarden.cli.executable",
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
                "wood_agents.boundary_ref",
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
    assert "integrations.openproject.url" in fields
    assert "integrations.openproject.project_id" in fields
    assert "integrations.vaultwarden.cli.executable" in fields
    assert "wood_agents.boundary_ref" in fields
    assert "diagnostics.agent_readiness.enabled" in fields


def test_doctor_check_filters_to_requested_area(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "doctor",
                "--check",
                "scheduler",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["data"]["summary"]["selected_checks"] == ["scheduler"]
    assert payload["data"]["summary"]["issue_count"] == 0
    assert payload["data"]["status"] == "ok"
    assert len(payload["data"]["checks"]) == 1
    assert payload["data"]["checks"][0]["name"] == "scheduler"
    assert payload["data"]["checks"][0]["summary"]["issue_count"] == 0

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "doctor",
                "--check",
                "openproject",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    assert payload["data"]["summary"]["selected_checks"] == ["openproject"]
    assert payload["data"]["status"] == "issues-found"
    assert len(payload["data"]["checks"]) == 1
    assert payload["data"]["checks"][0]["name"] == "openproject"
    assert payload["data"]["checks"][0]["summary"]["issue_count"] == len(payload["warnings"])
    assert payload["warnings"]
    assert all(
        issue["field"].startswith("integrations.openproject.") for issue in payload["warnings"]
    )


def test_doctor_success_path_with_selected_checks_and_redacted_json(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    assert main(["--config-path", str(config_path), "init", "--apply"]) == 0

    success_commands = [
        ["set", "integrations.openproject.url", '"https://openproject.example.test"', "--apply"],
        ["set", "integrations.openproject.project_id", '"wood"', "--apply"],
        ["set", "integrations.openproject.token_ref", '"env://OPENPROJECT_TOKEN"', "--apply"],
        ["set", "integrations.ntfy.token_ref", '"env://NTFY_TOKEN"', "--apply"],
        ["set", "integrations.vaultwarden.config_ref", '"env://VAULTWARDEN_CONFIG"', "--apply"],
        ["set", "wood_agents.boundary_ref", '"docs://wood-agents/boundary"', "--apply"],
        ["set", "wood_agents.adapters_ref", '"pkg://wood-agents/adapters"', "--apply"],
    ]
    for command in success_commands:
        assert main(["--config-path", str(config_path), *command]) == 0
        capsys.readouterr()

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "doctor",
                "--check",
                "vaultwarden",
                "--check",
                "openproject",
                "--check",
                "ntfy",
                "--check",
                "scheduler",
                "--check",
                "agent-readiness",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["summary"]["issue_count"] == 0
    assert payload["data"]["summary"]["contains_secrets"] is False
    assert payload["warnings"] == []
    assert [check["status"] for check in payload["data"]["checks"]] == [
        "ok",
        "ok",
        "ok",
        "ok",
        "ok",
    ]
    serialized = json.dumps(payload)
    assert "OPENPROJECT_TOKEN" not in serialized
    assert "NTFY_TOKEN" not in serialized
    assert "VAULTWARDEN_CONFIG" not in serialized

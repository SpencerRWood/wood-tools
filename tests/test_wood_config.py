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
    assert payload["selected_profile"] == "default"

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "get",
                "integrations.openproject.token_ref",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == "env://OPENPROJECT_TOKEN"

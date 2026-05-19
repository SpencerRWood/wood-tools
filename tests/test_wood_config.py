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
                "project.name",
                "demo",
                "--profile",
                "dev",
                "--activate-profile",
                "--apply",
            ]
        )
        == 0
    )

    assert main(["--config-path", str(config_path), "get", "project.name", "--profile", "dev"]) == 0
    out = capsys.readouterr().out
    assert "value: demo" in out

    assert main(["--config-path", str(config_path), "show", "--profile", "dev"]) == 0
    out = capsys.readouterr().out
    assert "selected_profile: dev" in out
    assert "project.name=demo" in out


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


def test_json_output_for_supported_commands(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"

    assert main(["--config-path", str(config_path), "init", "--apply", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is True

    assert (
        main(
            [
                "--config-path",
                str(config_path),
                "set",
                "retries",
                "3",
                "--apply",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == 3

    assert main(["--config-path", str(config_path), "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_profile"] == "default"

    assert main(["--config-path", str(config_path), "get", "retries", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == 3

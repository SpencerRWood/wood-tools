from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from wood.cli import main
from wood.diagnostics import doctor
from wood_project.openproject import OpenProjectError, credentials, load_settings


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in (*credentials.REQUIRED_NAMES, "INFISICAL_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".infisical.json").write_text('{"workspaceId":"test-project"}')
    monkeypatch.setattr(credentials.shutil, "which", lambda name: "/bin/infisical")
    return tmp_path


def test_fetches_only_missing_values_without_persisting_credentials(
    context: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENPROJECT_URL", "https://example.test")
    seen = []

    def fetch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        assert kwargs["cwd"] == context
        assert kwargs["timeout"] == 15
        assert kwargs["capture_output"] is True
        assert "--path=/openproject" in command
        assert "--env=dev" in command
        return subprocess.CompletedProcess(command, 0, "private-token\n", "private-log")

    monkeypatch.setattr(credentials.subprocess, "run", fetch)
    settings = load_settings()
    assert settings.token == "private-token"
    assert settings.token_provider == "infisical"
    assert len(seen) == 1
    assert seen[0][3] == "OPENPROJECT_API_TOKEN"
    assert "private-token" not in repr(settings)
    assert "OPENPROJECT_API_TOKEN" not in os.environ
    assert not (context / ".env").exists()


def test_global_context_works_from_another_repository(
    context: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = context / "shared"
    shared.mkdir()
    (shared / ".infisical.json").write_text('{"workspaceId":"shared-project"}')
    config = context / "config" / "wood"
    config.mkdir(parents=True)
    (config / "infisical.toml").write_text(
        f'project_config_dir = "{shared}"\nenvironment = "prod"\nsecret_path = "/planning"\n'
    )
    values = {"OPENPROJECT_URL": "https://example.test", "OPENPROJECT_API_TOKEN": "private"}

    def fetch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["cwd"] == shared
        assert "--env=prod" in command
        assert "--path=/planning" in command
        return subprocess.CompletedProcess(command, 0, values[command[3]], "")

    monkeypatch.setattr(credentials.subprocess, "run", fetch)
    for _ in range(2):
        settings = load_settings()
        assert settings.base_url == values["OPENPROJECT_URL"]
        assert settings.token == values["OPENPROJECT_API_TOKEN"]
        assert "OPENPROJECT_API_TOKEN" not in os.environ


def test_injected_values_skip_infisical_and_invalid_config(
    context: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = context / "config" / "wood"
    config.mkdir(parents=True)
    (config / "infisical.toml").write_text("invalid toml")
    monkeypatch.setenv("OPENPROJECT_URL", "https://example.test")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "injected")
    monkeypatch.setattr(credentials.shutil, "which", lambda name: None)
    assert load_settings().token_provider == "injected-environment"


@pytest.mark.parametrize(
    "contents",
    [
        "invalid toml",
        'project_config_dir = "relative"\nenvironment = "dev"\nsecret_path = "/openproject"',
        'project_config_dir = "/tmp"\nenvironment = 1\nsecret_path = "/openproject"',
        'project_config_dir = "/tmp"\nenvironment = ""\nsecret_path = "/openproject"',
        'project_config_dir = "/tmp"\nenvironment = "dev"\nsecret_path = "relative"',
        'token = "private-token"',
    ],
)
def test_invalid_global_configuration_fails_without_fallback(context: Path, contents: str) -> None:
    config = context / "config" / "wood"
    config.mkdir(parents=True)
    (config / "infisical.toml").write_text(contents)
    with pytest.raises(OpenProjectError) as exc:
        load_settings()
    assert exc.value.code == "INFISICAL_CONFIG_INVALID"
    assert "private-token" not in str(exc.value)


@pytest.mark.parametrize("output", ["", "  \n", "value\nother-value", "value\rother-value"])
def test_malformed_values_are_rejected(
    context: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, output, "private"),
    )
    with pytest.raises(OpenProjectError, match="Authenticate Infisical") as exc:
        load_settings()
    assert exc.value.code == "INFISICAL_CREDENTIALS_UNAVAILABLE"
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("failure", ["exit", "timeout", "os", "unicode"])
def test_fetch_failures_never_expose_output_or_exception_values(
    context: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("WOOD_AUDIT_LOG_PATH", str(context / "audit.jsonl"))

    def fetch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 15, output="private-token")
        if failure == "os":
            raise OSError("private-token")
        if failure == "unicode":
            raise UnicodeError("private-token")
        return subprocess.CompletedProcess(command, 1, "private-token", "private-token")

    monkeypatch.setattr(credentials.subprocess, "run", fetch)
    assert main(["story", "next", "--json"]) == 4
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["errors"][0]["code"] == "INFISICAL_CREDENTIALS_UNAVAILABLE"
    assert payload["next_actions"] == [credentials.SETUP_ACTION]
    assert "private-token" not in output.out + output.err
    assert "private-token" not in (context / "audit.jsonl").read_text()


def test_missing_cli_and_context_report_distinct_errors(
    context: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda name: None)
    with pytest.raises(OpenProjectError) as exc:
        load_settings()
    assert exc.value.code == "INFISICAL_CLI_UNAVAILABLE"
    monkeypatch.setattr(credentials.shutil, "which", lambda name: "/bin/infisical")
    (context / ".infisical.json").unlink()
    with pytest.raises(OpenProjectError) as exc:
        load_settings()
    assert exc.value.code == "INFISICAL_CONTEXT_UNAVAILABLE"


def test_explicit_project_selection(context: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (context / ".infisical.json").unlink()
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "selected-project")
    monkeypatch.setenv("OPENPROJECT_URL", "https://example.test")

    def fetch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "--projectId=selected-project" in command
        return subprocess.CompletedProcess(command, 0, "private-token", "")

    monkeypatch.setattr(credentials.subprocess, "run", fetch)
    assert load_settings().token == "private-token"


def test_local_commands_do_not_require_infisical(
    context: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda name: None)
    assert main(["contract", "--json"]) == 0
    capsys.readouterr()
    assert main(["secret", "requirements", "--json"]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        main(["story", "--help"])
    assert exc.value.code == 0


def test_doctor_reports_acquisition_failure_without_values(
    context: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(credentials.shutil, "which", lambda name: None)
    payload = doctor(context, {})
    assert payload["status"] == "unavailable"
    assert payload["errors"][0]["code"] == "INFISICAL_CLI_UNAVAILABLE"
    assert payload["next_actions"][0] == credentials.SETUP_ACTION

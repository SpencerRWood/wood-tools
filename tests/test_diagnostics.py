from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from urllib.error import URLError

import pytest

from resources.cli.audit import build_audit_event
from wood import diagnostics, runtime
from wood.cli import main


def test_secret_commands_report_presence_without_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "a-value-that-must-never-appear"
    env = {"OPENPROJECT_URL": "https://example.test", "OPENPROJECT_API_TOKEN": secret}
    for action in ("check", "requirements"):
        payload = diagnostics.secret_command(action, root=tmp_path, environ=env, names=[])
        assert payload["status"] == "success"
        assert secret not in json.dumps(payload)
    assert main(["secret", "check", "--name", "NOT_PRESENT", "--json"]) == 4
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["missing_names"] == ["NOT_PRESENT"]


def test_infisical_status_discards_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".infisical.json").write_text('{"workspaceId":"project"}', encoding="utf-8")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/bin/infisical")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, b"sensitive-output", b""),
    )
    payload = diagnostics.secret_command(
        "status",
        root=tmp_path,
        environ={
            "OPENPROJECT_URL": "https://example.test",
            "OPENPROJECT_API_TOKEN": "secret",
            "INFISICAL_ENVIRONMENT": "private-environment-value",
            "INFISICAL_SECRET_PATH": "/private-path-value",
        },
        names=[],
    )
    assert payload["status"] == "success"
    assert "sensitive-output" not in json.dumps(payload)
    assert "secret" not in json.dumps(payload["data"])
    assert "private-environment-value" not in json.dumps(payload)
    assert "private-path-value" not in json.dumps(payload)


def test_doctor_distinguishes_not_applicable_repo_and_unavailable_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(*args: Any, **kwargs: Any) -> Any:
        raise URLError("offline")

    monkeypatch.setattr(diagnostics.request, "urlopen", offline)
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/bin/infisical")
    env = {
        "OPENPROJECT_URL": "https://example.test",
        "OPENPROJECT_API_TOKEN": "hidden-token",
        "INFISICAL_TOKEN": "hidden-auth",
        "INFISICAL_PROJECT_ID": "project",
    }
    payload = diagnostics.doctor(tmp_path, env)
    checks = payload["data"]["checks"]
    assert checks["repository"]["state"] == "not_applicable"
    assert checks["openproject_connectivity"]["state"] == "unavailable"
    assert "hidden-token" not in json.dumps(payload)
    assert "hidden-auth" not in json.dumps(payload)


def test_doctor_checks_repository_and_openproject_without_exposing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, b" M README.md\n", b""),
    )

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self, size: int) -> bytes:
            assert size == 1
            return b"{"

    monkeypatch.setattr(diagnostics.request, "urlopen", lambda *args, **kwargs: Response())
    env = {"OPENPROJECT_URL": "https://example.test", "OPENPROJECT_API_TOKEN": "hidden-token"}
    assert diagnostics.repository_readiness(tmp_path) == {
        "state": "ready",
        "clean": False,
        "next_action": None,
    }
    result = diagnostics.openproject_connectivity(env)
    assert result == {"state": "ready", "next_action": None}
    assert "hidden-token" not in json.dumps(result)
    assert (
        diagnostics.openproject_connectivity({**env, "OPENPROJECT_URL": "http://example.test"})[
            "state"
        ]
        == "unavailable"
    )


def test_audit_event_omits_secret_data() -> None:
    payload = diagnostics.secret_command(
        "check", root=Path.cwd(), environ={"OPENPROJECT_API_TOKEN": "hidden"}, names=[]
    )
    event = build_audit_event(payload, cli_name="wood")
    assert event["target"]["type"] == "secret-readiness"
    assert "hidden" not in json.dumps(event)

from pathlib import Path

import pytest

from wood_project.implementation import openproject


def test_implementation_settings_work_without_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENPROJECT_URL", "https://example.test")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "injected-token")

    settings = openproject.parse_env_file(tmp_path / ".env.resolved")
    openproject.require_env(settings, ["OPENPROJECT_URL", "OPENPROJECT_API_TOKEN"])

    assert settings == {}
    assert openproject.env_value(settings, "OPENPROJECT_API_TOKEN") == "injected-token"


def test_injected_token_takes_precedence_over_resolved_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env.resolved"
    env_file.write_text("OPENPROJECT_API_TOKEN=old-file-token\n", encoding="utf-8")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "injected-token")

    assert (
        openproject.env_value(openproject.parse_env_file(env_file), "OPENPROJECT_API_TOKEN")
        == "injected-token"
    )

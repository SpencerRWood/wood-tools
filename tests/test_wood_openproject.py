from __future__ import annotations

import json
from typing import Any
from urllib import parse

import pytest

from wood_project import cli as project_cli
from wood_project.commands import openproject as openproject_commands
from wood_project.commands import story as story_commands
from wood_project.openproject import (
    OpenProjectClient,
    OpenProjectError,
    OpenProjectSettings,
    load_settings,
)
from wood_secrets.core import SecretResolver
from wood_secrets.core.providers import SecretProviderError


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_load_settings_resolves_configured_token_ref_without_leaking_value(
    tmp_path: pytest.TempPathFactory,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_profile": "default",
                "profiles": {
                    "default": {
                        "integrations": {
                            "openproject": {
                                "url": "https://openproject.example.test",
                                "project_id": "wood",
                                "token_ref": "env://OPENPROJECT_TOKEN",
                                "user_agent": "wood-tools-test/1",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    resolver = SecretResolver(environ={"OPENPROJECT_TOKEN": "super-secret-token"})

    settings = load_settings(config_path=config_path, resolver=resolver)

    assert settings.base_url == "https://openproject.example.test"
    assert settings.project_id == "wood"
    assert settings.token == "super-secret-token"
    assert settings.user_agent == "wood-tools-test/1"
    assert "super-secret-token" not in repr(settings)


def test_load_settings_reports_provider_failure(tmp_path: pytest.TempPathFactory) -> None:
    class FailingResolver:
        def resolve(self, reference: str) -> object:
            raise SecretProviderError(f"provider failed for {reference}")

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_profile": "default",
                "profiles": {
                    "default": {
                        "integrations": {
                            "openproject": {
                                "url": "https://openproject.example.test",
                                "project_id": "wood",
                                "token_ref": "env://OPENPROJECT_TOKEN",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OpenProjectError) as excinfo:
        load_settings(config_path=config_path, resolver=FailingResolver())  # type: ignore[arg-type]

    assert excinfo.value.code == "OPENPROJECT_ACCESS_UNAVAILABLE"
    assert "provider failed" in excinfo.value.message


def test_client_uses_read_only_get_requests_and_summarizes_work_package() -> None:
    seen: list[tuple[str, str, str]] = []

    def transport(req: Any, *, timeout: int) -> FakeResponse:
        seen.append((req.get_method(), req.full_url, req.headers["User-agent"]))
        assert timeout == 30
        assert req.headers["Accept"] == "application/hal+json"
        assert req.headers["Authorization"].startswith("Basic ")
        return FakeResponse(
            {
                "id": 292,
                "subject": "Implement read-only OpenProject CLI and inspection",
                "description": {
                    "format": "markdown",
                    "raw": "Story packet\n\n- Inspect work packages",
                    "html": "<p>Story packet</p>",
                },
                "_links": {
                    "type": {"title": "Story"},
                    "status": {"title": "In progress"},
                    "project": {"href": "/api/v3/projects/1", "title": "Wood Tools"},
                    "parent": {"href": "/api/v3/work_packages/290", "title": "Foundation"},
                    "version": {"href": "/api/v3/versions/7", "title": "R2"},
                },
            }
        )

    settings = OpenProjectSettings(
        base_url="https://openproject.example.test/",
        project_id="wood",
        token="super-secret-token",
        token_provider="env",
        user_agent="wood-tools-test/1",
    )
    client = OpenProjectClient(settings, transport=transport)

    payload = client.work_package(292)

    assert seen == [
        (
            "GET",
            "https://openproject.example.test/api/v3/work_packages/292",
            "wood-tools-test/1",
        )
    ]
    assert payload["id"] == 292
    assert payload["status"] == "In progress"
    assert payload["description"] == {
        "format": "markdown",
        "raw": "Story packet\n\n- Inspect work packages",
    }
    assert payload["project"] == {"id": 1, "title": "Wood Tools"}
    assert payload["parent"] == {"id": 290, "title": "Foundation"}
    assert "super-secret-token" not in json.dumps(payload)


def test_client_request_json_supports_mutations() -> None:
    seen: dict[str, Any] = {}

    def transport(req: Any, *, timeout: int) -> FakeResponse:
        seen.update(
            method=req.get_method(),
            body=json.loads(req.data.decode("utf-8")),
            content_type=req.headers["Content-type"],
            timeout=timeout,
        )
        return FakeResponse({"id": 301, "lockVersion": 5})

    client = OpenProjectClient(
        OpenProjectSettings(
            base_url="https://openproject.example.test",
            project_id="wood",
            token="super-secret-token",
            token_provider="env",
            user_agent="wood-tools-test/1",
        ),
        transport=transport,
    )

    payload = client.request_json(
        "PATCH",
        "/api/v3/work_packages/301",
        body={"lockVersion": 4},
    )

    assert seen == {
        "method": "PATCH",
        "body": {"lockVersion": 4},
        "content_type": "application/json",
        "timeout": 30,
    }
    assert payload == {"id": 301, "lockVersion": 5}


def test_client_story_context_fetches_relation_fixture() -> None:
    seen_paths: list[str] = []

    def transport(req: Any, *, timeout: int) -> FakeResponse:
        parsed = parse.urlparse(req.full_url)
        seen_paths.append(parsed.path)
        if parsed.path.endswith("/work_packages/292"):
            return FakeResponse(
                {
                    "id": 292,
                    "subject": "Story",
                    "description": {"format": "markdown", "raw": "Goal\n\n- Acceptance"},
                    "_links": {},
                }
            )
        assert parsed.path.endswith("/relations")
        return FakeResponse(
            {
                "_embedded": {
                    "elements": [
                        {
                            "id": 5,
                            "type": "follows",
                            "_links": {
                                "from": {
                                    "href": "/api/v3/work_packages/291",
                                    "title": "Predecessor",
                                },
                                "to": {"href": "/api/v3/work_packages/292", "title": "Story"},
                            },
                        }
                    ]
                }
            }
        )

    client = OpenProjectClient(
        OpenProjectSettings(
            base_url="https://openproject.example.test",
            project_id="wood",
            token="super-secret-token",
            token_provider="env",
            user_agent="wood-tools-test/1",
        ),
        transport=transport,
    )

    payload = client.story_context(292)

    assert seen_paths == ["/api/v3/work_packages/292", "/api/v3/relations"]
    assert payload["work_package"]["description"] == {
        "format": "markdown",
        "raw": "Goal\n\n- Acceptance",
    }
    assert payload["relations"] == [
        {
            "id": 5,
            "type": "follows",
            "from": {"id": 291, "title": "Predecessor"},
            "to": {"id": 292, "title": "Story"},
        }
    ]


def test_wood_project_openproject_json_uses_standard_envelope_and_no_secret_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeClient:
        def __init__(self, settings: object) -> None:
            self.settings = settings

        def story_context(self, work_package_id: int) -> dict[str, Any]:
            assert work_package_id == 292
            return {
                "work_package": {
                    "id": 292,
                    "subject": "Read only",
                    "status": "In progress",
                },
                "relations": [],
            }

    monkeypatch.setattr(
        story_commands,
        "load_settings",
        lambda **kwargs: OpenProjectSettings(
            base_url="https://openproject.example.test",
            project_id="wood",
            token="super-secret-token",
            token_provider="env",
            user_agent="wood-tools-test/1",
        ),
    )
    monkeypatch.setattr(story_commands, "OpenProjectClient", FakeClient)

    code = project_cli.main(["story", "show", "292", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "story-show"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["work_package"]["id"] == 292
    assert "super-secret-token" not in json.dumps(payload)


def test_wood_project_openproject_provider_failure_returns_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_settings(**kwargs: object) -> object:
        raise OpenProjectError("OPENPROJECT_ACCESS_UNAVAILABLE", "provider locked")

    monkeypatch.setattr(openproject_commands, "load_settings", fail_settings)

    code = project_cli.main(["user", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "user"
    assert payload["status"] == "error"
    assert payload["mutation"] == "read-only"
    assert payload["errors"] == [
        {"code": "OPENPROJECT_ACCESS_UNAVAILABLE", "message": "provider locked"}
    ]

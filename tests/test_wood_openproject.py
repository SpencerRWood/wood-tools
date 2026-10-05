from __future__ import annotations

import json
from typing import Any
from urllib import parse

import pytest

from wood_project.openproject import (
    OpenProjectClient,
    OpenProjectError,
    OpenProjectSettings,
    load_settings,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_load_settings_uses_injected_environment_without_leaking_value() -> None:
    settings = load_settings(
        {
            "OPENPROJECT_URL": "https://openproject.example.test",
            "OPENPROJECT_PROJECT_ID": "wood",
            "OPENPROJECT_API_TOKEN": "super-secret-token",
        }
    )

    assert settings.base_url == "https://openproject.example.test"
    assert not hasattr(settings, "project_id")
    assert settings.token == "super-secret-token"
    assert settings.token_provider == "injected-environment"
    assert "super-secret-token" not in repr(settings)


def test_load_settings_reports_missing_injected_variables() -> None:
    with pytest.raises(OpenProjectError) as excinfo:
        load_settings({"OPENPROJECT_URL": "https://openproject.example.test"})

    assert excinfo.value.code == "OPENPROJECT_CONFIG_UNAVAILABLE"
    assert "OPENPROJECT_API_TOKEN" in excinfo.value.message


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


def test_story_context_pages_all_relations(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenProjectClient(
        OpenProjectSettings(
            base_url="https://openproject.example.test",
            token="secret",
            token_provider="test",
            user_agent="test/1",
        )
    )
    offsets: list[str] = []

    def fake_get(path: str, *, query: dict[str, str] | None = None) -> dict[str, Any]:
        if path.endswith("/work_packages/292"):
            return {"id": 292, "_links": {}}
        assert query is not None
        offsets.append(query["offset"])
        return {
            "total": 2,
            "_embedded": {
                "elements": [{"id": int(query["offset"]), "type": "relates", "_links": {}}]
            },
        }

    monkeypatch.setattr(client, "get_json", fake_get)
    result = client.story_context(292)
    assert offsets == ["1", "2"]
    assert [relation["id"] for relation in result["relations"]] == [1, 2]

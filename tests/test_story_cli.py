from __future__ import annotations

import json
from typing import Any

import pytest

from wood_project.cli import main as project_main
from wood_project.commands import story as story_commands
from wood_project.openproject import OpenProjectClient, OpenProjectSettings
from wood_project.story import branches


@pytest.fixture(autouse=True)
def disable_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")


def settings(
    *,
    project_id: str | None = "3",
    initiative_id: int | None = None,
) -> OpenProjectSettings:
    return OpenProjectSettings(
        base_url="https://openproject.example.test",
        token="secret-token",
        token_provider="test",
        user_agent="wood-tools-test/1",
        project_id=project_id,
        initiative_id=initiative_id,
    )


def test_project_story_set_status_is_preview_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert project_main(["story", "set-status", "301", "In progress", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "story-set-status"
    assert payload["status"] == "blocked"
    assert payload["requires_approval"] is True
    assert payload["data"]["dry_run"] is True
    assert payload["data"]["mutation"]["target_status"] == "In progress"


def test_project_story_set_status_apply_uses_openproject_lock_version(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(
        self: OpenProjectClient,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requests.append((method, path, body))
        if method == "GET" and path == "/api/v3/work_packages/301":
            return {
                "id": 301,
                "subject": "Story",
                "lockVersion": 4,
                "_links": {"status": {"title": "New"}},
            }
        if method == "GET" and path == "/api/v3/statuses":
            return {
                "_embedded": {
                    "elements": [
                        {"name": "In progress", "_links": {"self": {"href": "/api/v3/statuses/7"}}}
                    ]
                }
            }
        if method == "PATCH" and path == "/api/v3/work_packages/301":
            assert body == {
                "lockVersion": 4,
                "_links": {"status": {"href": "/api/v3/statuses/7"}},
            }
            return {
                "id": 301,
                "subject": "Story",
                "_links": {"status": {"title": "In progress"}},
            }
        raise AssertionError(path)

    monkeypatch.setattr(story_commands, "load_settings", lambda **kwargs: settings(project_id=None))
    monkeypatch.setattr(OpenProjectClient, "request_json", fake_request)

    assert project_main(["story", "set-status", "301", "In progress", "--apply", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["data"]["dry_run"] is False
    assert payload["data"]["mutation"]["from_status"] == "New"
    assert payload["data"]["mutation"]["to_status"] == "In progress"
    assert requests[-1][0] == "PATCH"


def test_project_story_create_branch_is_preview_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        branches,
        "repo_state",
        lambda: {
            "path": "/repo",
            "current_branch": "main",
            "working_tree_clean": True,
            "untracked_files": [],
        },
    )
    monkeypatch.setattr(branches, "branch_exists", lambda branch: False)

    assert (
        project_main(
            [
                "story",
                "create-branch",
                "301",
                "--title",
                "Productize the Story Loop as first-class CLI commands",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["data"]["branch"] == {
        "name": "feature/op-301-productize-the-story-loop-as-first-class-cli-commands",
        "would_create": True,
        "would_checkout": True,
    }


def test_project_story_next_selects_in_progress_story(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_request(
        self: OpenProjectClient,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert method == "GET"
        if path == "/api/v3/work_packages/208":
            return {
                "id": 208,
                "subject": "Root",
                "_links": {"project": {"href": "/api/v3/projects/3"}},
            }
        if path == "/api/v3/statuses":
            return {"_embedded": {"elements": [{"name": "Closed", "isClosed": True}]}}
        if path == "/api/v3/projects/3/versions":
            return {"_embedded": {"elements": [{"name": "V0.3", "status": "open"}]}}
        if path == "/api/v3/work_packages":
            return {
                "total": 1,
                "_embedded": {
                    "elements": [
                        {
                            "id": 301,
                            "subject": "Productize Story Loop",
                            "description": {
                                "raw": (
                                    "Codex Implementation Packet\n\nGoal\nShip the workflow.\n\n"
                                    "Acceptance Criteria\nFirst criterion"
                                )
                            },
                            "_links": {
                                "type": {"title": "Story"},
                                "status": {"title": "In progress"},
                                "version": {"title": "V0.3"},
                                "parent": {"href": "/api/v3/work_packages/297"},
                            },
                        }
                    ]
                },
            }
        if path == "/api/v3/relations":
            return {"total": 0, "_embedded": {"elements": []}}
        raise AssertionError(path)

    monkeypatch.setattr(story_commands, "load_settings", lambda **kwargs: settings())
    monkeypatch.setattr(OpenProjectClient, "request_json", fake_request)

    assert project_main(["story", "next", "208", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["data"]["story"]["id"] == 301
    assert payload["data"]["story"]["branch"] == "feature/op-301-productize-story-loop"
    assert payload["data"]["summary"]["goal"] == "Ship the workflow."
    assert payload["data"]["summary"]["acceptance_criteria"] == ["First criterion"]


def test_project_story_next_uses_configured_initiative_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen_roots: list[int] = []

    def fake_discover_next_story(**kwargs: Any) -> dict[str, Any]:
        seen_roots.append(kwargs["root_work_package_id"])
        return {
            "ok": True,
            "read_only": True,
            "root": {"id": kwargs["root_work_package_id"]},
            "story": {"id": 301, "subject": "Story"},
            "summary": {},
        }

    monkeypatch.setattr(
        story_commands,
        "load_settings",
        lambda **kwargs: settings(initiative_id=208),
    )
    monkeypatch.setattr(story_commands, "discover_next_story", fake_discover_next_story)

    assert project_main(["story", "next", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["data"]["root"] == {"id": 208}
    assert seen_roots == [208]

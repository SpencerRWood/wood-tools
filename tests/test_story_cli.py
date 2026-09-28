from __future__ import annotations

import json
from typing import Any

import pytest

from wood_project.cli import main as project_main
from wood_project.commands import story as story_commands
from wood_project.openproject import OpenProjectClient, OpenProjectSettings
from wood_project.planning_release import release_sort_key
from wood_project.story import branches, discovery


@pytest.fixture(autouse=True)
def disable_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")


def settings() -> OpenProjectSettings:
    return OpenProjectSettings(
        base_url="https://openproject.example.test",
        project_id="3",
        token="secret-token",
        token_provider="test",
        user_agent="wood-tools-test/1",
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

    monkeypatch.setattr(story_commands, "load_settings", lambda **kwargs: settings())
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
            return {"_embedded": {"elements": [{"name": "R3", "status": "open"}]}}
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
                                "version": {"title": "R3"},
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


def test_numeric_planning_release_order() -> None:
    assert sorted(["R10 — Later", "R9", "R2", "R1"], key=release_sort_key) == [
        "R1",
        "R2",
        "R9",
        "R10 — Later",
    ]


def test_blocks_relation_is_a_predecessor(monkeypatch) -> None:
    relations = [
        {
            "id": 7,
            "type": "blocks",
            "_links": {
                "from": {"href": "/api/v3/work_packages/1"},
                "to": {"href": "/api/v3/work_packages/2"},
            },
        }
    ]
    monkeypatch.setattr(discovery, "fetch_collection", lambda *_args, **_kwargs: relations)
    assert discovery.fetch_predecessor_map(OpenProjectClient(settings()), {1, 2}, 100) == {2: {1}}


def test_next_story_keeps_earliest_active_release_and_dependencies(monkeypatch) -> None:
    def story(story_id: int, version: str, status: str = "New") -> dict[str, Any]:
        return {
            "id": story_id,
            "subject": f"Story {story_id}",
            "_links": {
                "type": {"title": "Story"},
                "status": {"title": status},
                "version": {"title": version},
            },
        }

    stories = [
        story(1, "R9", "In progress"),
        story(2, "R9"),
        story(3, "R10 — Later", "In progress"),
    ]
    documents = {
        "/api/v3/work_packages/208": {
            "id": 208,
            "_links": {"project": {"href": "/api/v3/projects/3"}},
        },
        "/api/v3/statuses": {"_embedded": {"elements": [{"name": "Closed", "isClosed": True}]}},
        "/api/v3/projects/3/versions": {
            "_embedded": {
                "elements": [
                    {"name": "R10 — Later", "status": "open"},
                    {"name": "R9", "status": "open"},
                    {"name": "R2", "status": "closed"},
                ]
            }
        },
    }
    monkeypatch.setattr(discovery, "api_get_json", lambda _client, path: documents[path])
    monkeypatch.setattr(discovery, "fetch_descendants", lambda *_args: stories)
    monkeypatch.setattr(discovery, "fetch_predecessor_map", lambda *_args: {1: {2}})
    client = OpenProjectClient(settings())
    result = discovery.discover_next_story(
        client=client,
        root_work_package_id=208,
        target_status="New",
        story_type="Story",
        page_size=100,
    )
    assert result["story"]["id"] == 2
    stories[1]["_links"]["status"]["title"] = "Closed"
    result = discovery.discover_next_story(
        client=client,
        root_work_package_id=208,
        target_status="New",
        story_type="Story",
        page_size=100,
    )
    assert result["story"]["id"] == 1
    stories[0]["_links"]["status"]["title"] = "Closed"
    result = discovery.discover_next_story(
        client=client,
        root_work_package_id=208,
        target_status="New",
        story_type="Story",
        page_size=100,
    )
    assert result["story"]["id"] == 3
    stories[2]["_links"]["status"]["title"] = "Closed"
    with pytest.raises(discovery.StoryWorkflowError, match="No eligible Story"):
        discovery.discover_next_story(
            client=client,
            root_work_package_id=208,
            target_status="New",
            story_type="Story",
            page_size=100,
        )

from __future__ import annotations

import json
from typing import Any

import pytest

from wood.cli import main
from wood_project.openproject import OpenProjectClient, OpenProjectSettings
from wood_project.planning_release import release_sort_key
from wood_project.story import discovery, workflow


@pytest.fixture(autouse=True)
def disable_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")


def settings() -> OpenProjectSettings:
    return OpenProjectSettings(
        base_url="https://openproject.example.test",
        token="secret-token",
        token_provider="test",
        user_agent="wood-tools-test/1",
    )


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


def _story(story_id: int = 301, status: str = "New", version: str = "R1") -> dict[str, Any]:
    return {
        "id": story_id,
        "subject": "Ship Story workflow",
        "lockVersion": 4,
        "description": {
            "raw": (
                "Codex Implementation Packet\n\nOpenProject\n"
                "Primary Repository: wood-tools\n\nGoal\nShip workflow.\n\n"
                "Acceptance Criteria\nWorks."
            )
        },
        "_links": {
            "type": {"title": "Story"},
            "status": {"title": status},
            "version": {"title": version, "href": "/api/v3/versions/20"},
            "project": {"href": "/api/v3/projects/3"},
        },
    }


def _fake_api(monkeypatch: pytest.MonkeyPatch, story: dict[str, Any]) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def request(self: OpenProjectClient, method: str, path: str, *, query=None, body=None):
        calls.append((method, path))
        if path == "/api/v3/work_packages/3":
            return {"id": 3, "_links": {"type": {"title": "Task"}}}
        if path == "/api/v3/versions/20":
            return {"id": 20, "name": "R1", "status": "open"}
        if path == f"/api/v3/work_packages/{story['id']}":
            if method == "PATCH":
                result = dict(story)
                result["_links"] = {**story["_links"], "status": {"title": "In progress"}}
                return result
            return story
        if path == "/api/v3/statuses":
            return {
                "_embedded": {
                    "elements": [
                        {"name": "In progress", "_links": {"self": {"href": "/api/v3/statuses/7"}}},
                        {
                            "name": "Closed",
                            "isClosed": True,
                            "_links": {"self": {"href": "/api/v3/statuses/8"}},
                        },
                    ]
                }
            }
        if path == "/api/v3/relations":
            return {"total": 0, "_embedded": {"elements": []}}
        if path == "/api/v3/work_packages":
            return {"total": 1, "_embedded": {"elements": [story]}}
        if path == "/api/v3/projects/3":
            return {"id": 3, "name": "Wood Platform"}
        raise AssertionError((method, path))

    monkeypatch.setattr(OpenProjectClient, "request_json", request)
    monkeypatch.setattr("wood.story.load_settings", lambda: settings())
    return calls


def test_public_story_get_returns_packet_and_relations(monkeypatch, capsys) -> None:
    story = _story()
    _fake_api(monkeypatch, story)
    assert main(["story", "get", "301", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["implementation"]["goal"] == "Ship workflow."
    assert result["data"]["implementation"]["repository"] == "wood-tools"
    assert result["data"]["relations"] == []
    assert "secret-token" not in json.dumps(result)


def test_public_story_status_preview_validates_remote_state(monkeypatch, capsys) -> None:
    calls = _fake_api(monkeypatch, _story())
    assert main(["story", "set-status", "301", "In progress", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mutation"] == "preview"
    assert result["data"]["dry_run"] is True
    assert ("PATCH", "/api/v3/work_packages/301") not in calls
    assert main(["story", "set-status", "301", "Closed", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["errors"][0]["code"] == "LIFECYCLE_COMMAND_REQUIRED"


def test_public_story_status_apply_uses_lock_version(monkeypatch, capsys) -> None:
    story = _story()
    calls = _fake_api(monkeypatch, story)
    assert main(["story", "set-status", "301", "In progress", "--apply", "--json"]) == 0
    assert ("PATCH", "/api/v3/work_packages/301") in calls
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "In progress"


def test_public_story_list_is_filtered_and_bounded(monkeypatch, capsys) -> None:
    story = _story()
    _fake_api(monkeypatch, story)
    assert main(["story", "list", "3", "--status", "New", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["total"] == 1
    assert result["data"]["stories"][0]["id"] == 301


def test_start_requires_closed_predecessors(monkeypatch) -> None:
    story = _story()
    monkeypatch.setattr(
        workflow,
        "api_get_json",
        lambda _client, path: (
            {"id": 20, "status": "open"}
            if path.endswith("versions/20")
            else story
            if path.endswith("301")
            else _story(300, "Rejected")
        ),
    )
    monkeypatch.setattr(discovery, "fetch_predecessor_map", lambda *_args: {301: {300}})
    with pytest.raises(workflow.StoryWorkflowError, match="not Closed"):
        workflow.start_story(OpenProjectClient(settings()), 301, apply=False)


def test_complete_requires_validation_evidence(monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_story", lambda *_args: _story(status="In progress"))
    _fake_api(monkeypatch, _story(status="In progress"))
    with pytest.raises(workflow.StoryWorkflowError, match="validation evidence"):
        workflow.complete_story(OpenProjectClient(settings()), 301, evidence={}, apply=False)


def test_complete_rejects_failed_ci_and_previews_passed_evidence(monkeypatch) -> None:
    story = _story(status="In progress")
    monkeypatch.setattr(workflow, "_story", lambda *_args: story)
    monkeypatch.setattr(
        workflow,
        "api_get_json",
        lambda _client, path: (
            {
                "_embedded": {
                    "elements": [
                        {
                            "name": "Closed",
                            "isClosed": True,
                            "_links": {"self": {"href": "/api/v3/statuses/8"}},
                        }
                    ]
                }
            }
            if path == "/api/v3/statuses"
            else story
        ),
    )
    evidence = {
        "repository_checks": [{"name": "pytest", "status": "passed"}],
        "ci": {"url": "https://github.com/example/repo/actions/runs/1", "status": "failed"},
    }
    client = OpenProjectClient(settings())
    with pytest.raises(workflow.StoryWorkflowError, match="passed CI"):
        workflow.complete_story(client, 301, evidence=evidence, apply=False)
    evidence["ci"]["status"] = "passed"
    result = workflow.complete_story(client, 301, evidence=evidence, apply=False)
    assert result["status"]["to_status"] == "Closed"
    assert result["status"]["dry_run"] is True


def test_create_story_uses_epic_and_requirement_packet(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []

    def get(_client, path):
        if path.endswith("/208"):
            return {
                "_links": {
                    "type": {"title": "Initiative"},
                    "project": {"href": "/api/v3/projects/3"},
                }
            }
        if path.endswith("/392"):
            return {
                "_links": {
                    "type": {"title": "Epic"},
                    "parent": {"href": "/api/v3/work_packages/208"},
                }
            }
        if path.endswith("/20"):
            return {"name": "R1", "status": "open"}
        return {
            "_embedded": {
                "elements": [{"name": "Story", "_links": {"self": {"href": "/api/v3/types/1"}}}]
            }
        }

    monkeypatch.setattr(workflow, "api_get_json", get)
    monkeypatch.setattr(discovery, "fetch_collection", lambda *_args, **_kwargs: [{"id": 20}])
    monkeypatch.setattr(
        workflow,
        "api_request_json",
        lambda _method, _client, _path, *, body: requests.append(body) or _story(),
    )
    data = workflow.create_story(
        OpenProjectClient(settings()),
        project_id=3,
        initiative_id=208,
        epic_id=392,
        subject="New Story",
        goal="Ship",
        requirements="FR-007",
        acceptance=["Works"],
        repository="wood-tools",
        version_id=20,
        apply=True,
    )
    assert data["work_package"]["id"] == 301
    assert requests[0]["_links"]["parent"]["href"] == "/api/v3/work_packages/392"
    assert "Requirement IDs\nFR-007" in requests[0]["description"]["raw"]


def test_next_excludes_rejected_blocked_and_unversioned(monkeypatch) -> None:
    stories = [_story(1, "Rejected"), _story(2, "Blocked"), _story(3, "New", ""), _story(4)]
    monkeypatch.setattr(
        discovery,
        "api_get_json",
        lambda _client, path: {
            "/api/v3/work_packages/208": {
                "id": 208,
                "_links": {"project": {"href": "/api/v3/projects/3"}},
            },
            "/api/v3/statuses": {"_embedded": {"elements": [{"name": "Closed", "isClosed": True}]}},
            "/api/v3/projects/3/versions": {
                "_embedded": {"elements": [{"name": "R1", "status": "open"}]}
            },
        }[path],
    )
    monkeypatch.setattr(discovery, "fetch_descendants", lambda *_args: stories)
    monkeypatch.setattr(discovery, "fetch_predecessor_map", lambda *_args: {})
    result = discovery.discover_next_story(
        client=OpenProjectClient(settings()),
        root_work_package_id=208,
        target_status="New",
        story_type="Story",
        page_size=100,
    )
    assert result["story"]["id"] == 4


def test_start_prepares_standard_branch_after_readiness(monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(workflow, "_story", lambda *_args: _story())
    monkeypatch.setattr(workflow, "_ready", lambda *_args: None)
    monkeypatch.setattr(workflow, "repo_state", lambda: {"path": "/tmp/wood-tools"})
    monkeypatch.setattr(
        workflow, "_status", lambda *_args, **kwargs: {"dry_run": not kwargs["apply"]}
    )
    monkeypatch.setattr(
        workflow,
        "create_branch",
        lambda **kwargs: (
            calls.append(kwargs["apply"]) or {"branch": "feature/op-301-ship-story-workflow"}
        ),
    )
    result = workflow.start_story(OpenProjectClient(settings()), 301, apply=True)
    assert calls == [False, True]
    assert result["branch"]["branch"] == "feature/op-301-ship-story-workflow"


def test_block_records_reason_after_status_change(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(workflow, "_story", lambda *_args: _story())
    monkeypatch.setattr(
        workflow,
        "api_get_json",
        lambda *_args: {"_embedded": {"elements": [{"name": "On hold"}]}},
    )
    monkeypatch.setattr(workflow, "_status", lambda *_args, **_kwargs: {"status": "On hold"})
    monkeypatch.setattr(
        workflow,
        "api_request_json",
        lambda method, _client, _path, *, body: calls.append((method, body)) or {},
    )
    result = workflow.block_story(OpenProjectClient(settings()), 301, "Waiting for API", apply=True)
    assert result["status"]["status"] == "On hold"
    assert calls == [("POST", {"comment": {"raw": "Blocked: Waiting for API"}})]


def test_ci_verification_rejects_unfinished_run(monkeypatch) -> None:
    class Result:
        returncode = 0
        stdout = '{"status":"in_progress","conclusion":null}'

    monkeypatch.setattr(workflow.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(workflow.StoryWorkflowError, match="has not passed"):
        workflow._verify_ci_run("https://github.com/example/repo/actions/runs/123", "repo")
    with pytest.raises(workflow.StoryWorkflowError, match="GitHub Actions run"):
        workflow._verify_ci_run("https://example.com/build/123", "repo")


def test_rejected_predecessor_does_not_unlock_story(monkeypatch) -> None:
    rejected = _story(1, "Rejected")
    candidate = _story(2)
    monkeypatch.setattr(
        discovery,
        "api_get_json",
        lambda _client, path: {
            "/api/v3/work_packages/208": {
                "id": 208,
                "_links": {"project": {"href": "/api/v3/projects/3"}},
            },
            "/api/v3/statuses": {
                "_embedded": {
                    "elements": [
                        {"name": "Closed", "isClosed": True},
                        {"name": "Rejected", "isClosed": True},
                    ]
                }
            },
            "/api/v3/projects/3/versions": {
                "_embedded": {"elements": [{"name": "R1", "status": "open"}]}
            },
        }[path],
    )
    monkeypatch.setattr(discovery, "fetch_descendants", lambda *_args: [rejected, candidate])
    monkeypatch.setattr(discovery, "fetch_predecessor_map", lambda *_args: {2: {1}})
    with pytest.raises(discovery.StoryWorkflowError, match="No eligible next story"):
        discovery.discover_next_story(
            client=OpenProjectClient(settings()),
            root_work_package_id=208,
            target_status="New",
            story_type="Story",
            page_size=100,
        )

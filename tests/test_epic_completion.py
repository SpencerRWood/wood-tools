from __future__ import annotations

import json
from typing import Any

import pytest
from test_story_cli import _story, settings

from wood.cli import main
from wood_project.openproject import OpenProjectClient, OpenProjectError
from wood_project.story import workflow
from wood_project.story.models import StoryWorkflowError


def package(package_id: int, kind: str, status: str) -> dict[str, Any]:
    return {
        "id": package_id,
        "lockVersion": 4,
        "_links": {"type": {"title": kind}, "status": {"title": status}},
    }


@pytest.fixture
def remote(monkeypatch):
    story = _story(status="In progress")
    story["description"] = {"raw": "Goal\nComplete Story."}
    story["_links"]["parent"] = {"href": "/api/v3/work_packages/392"}
    epic = package(392, "Epic", "New")
    state: dict[str, Any] = {
        "story": story,
        "epic": epic,
        "siblings": [],
        "calls": [],
        "fail_epic": False,
        "reopen_story": False,
        "empty_second_page": False,
        "completion_status": "Closed",
    }

    def request(self, method, path, *, query=None, body=None):
        state["calls"].append((method, path, query, body))
        if path == "/api/v3/statuses":
            return {
                "_embedded": {
                    "elements": [
                        {
                            "name": name,
                            "isClosed": closed,
                            "_links": {"self": {"href": f"/api/v3/statuses/{index}"}},
                        }
                        for index, (name, closed) in enumerate(
                            [
                                ("New", False),
                                ("In progress", False),
                                ("Closed", True),
                                ("Rejected", True),
                                ("Done", True),
                            ]
                        )
                    ]
                }
            }
        if path == "/api/v3/work_packages":
            filters = json.loads(query["filters"])
            assert filters == [
                {"ancestor": {"operator": "=", "values": ["392"]}},
                {"status": {"operator": "*", "values": []}},
            ]
            children = [state["story"], *state["siblings"]]
            size = int(query["pageSize"])
            offset = (int(query["offset"]) - 1) * size
            if int(query["offset"]) > 1 and state["empty_second_page"]:
                return {"total": len(children), "_embedded": {"elements": []}}
            return {
                "total": len(children),
                "_embedded": {"elements": children[offset : offset + size]},
            }
        if path in {"/api/v3/work_packages/301", "/api/v3/work_packages/392"}:
            key = "story" if path.endswith("301") else "epic"
            item = state[key]
            if method == "PATCH":
                if key == "epic" and state["fail_epic"]:
                    raise OpenProjectError("CONFLICT", "lock version changed")
                assert body["lockVersion"] == item["lockVersion"]
                item["_links"]["status"] = {"title": state["completion_status"]}
                item["lockVersion"] += 1
                if key == "story" and state["reopen_story"]:
                    item["_links"]["status"] = {"title": "In progress"}
                    return package(301, "Story", "Closed")
            return item
        raise AssertionError((method, path))

    monkeypatch.setattr(OpenProjectClient, "request_json", request)
    monkeypatch.setattr("wood.story.load_settings", settings)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    monkeypatch.delenv("OPENPROJECT_STORY_CLOSED_STATUS", raising=False)
    return state


def complete(remote, *, apply=True):
    return workflow.complete_story(
        OpenProjectClient(settings()),
        301,
        evidence={"validation_summary": "Checks passed"},
        apply=apply,
    )


def patches(remote):
    return [call[1] for call in remote["calls"] if call[0] == "PATCH"]


def test_final_story_completes_epic_and_repeat_is_idempotent(remote):
    remote["siblings"] = [package(302, "Story", "Closed")]
    result = complete(remote)
    assert result["epic"] == {"id": 392, "status": "Closed", "automatically_completed": True}
    assert patches(remote) == ["/api/v3/work_packages/301", "/api/v3/work_packages/392"]
    assert result["status"]["status"] == "Closed"
    repeated = complete(remote)
    assert repeated["status"]["already_complete"] is True
    assert repeated["epic"]["reason"] == "already_complete"
    assert len(patches(remote)) == 2
    calls = [(method, path) for method, path, *_ in remote["calls"]]
    assert calls.index(("PATCH", "/api/v3/work_packages/301")) < calls.index(
        ("GET", "/api/v3/work_packages")
    )


@pytest.mark.parametrize("status", ["New", "In progress", "On hold", "Blocked", "Unknown"])
def test_incomplete_sibling_keeps_epic_open(remote, status):
    remote["siblings"] = [package(302, "Story", status)]
    result = complete(remote)
    assert result["epic"]["reason"] == "incomplete_stories"
    assert result["epic"]["incomplete_story_ids"] == [302]
    assert remote["epic"]["_links"]["status"]["title"] == "New"
    assert patches(remote) == ["/api/v3/work_packages/301"]


def test_rejected_siblings_do_not_block(remote):
    remote["siblings"] = [package(302, "Story", "Rejected"), package(303, "Task", "New")]
    assert complete(remote)["epic"]["automatically_completed"] is True


@pytest.mark.parametrize("status", ["Closed", "Done"])
def test_already_completed_epic_is_not_rewritten(remote, status):
    remote["epic"]["_links"]["status"]["title"] = status
    assert complete(remote)["epic"]["reason"] == "already_complete"
    assert patches(remote) == ["/api/v3/work_packages/301"]


def test_all_child_pages_are_checked(remote):
    remote["siblings"] = [package(index, "Story", "Closed") for index in range(500, 600)]
    remote["siblings"].append(package(600, "Story", "In progress"))
    result = complete(remote)
    assert result["epic"]["incomplete_story_ids"] == [600]
    assert patches(remote) == ["/api/v3/work_packages/301"]


def test_incomplete_collection_does_not_complete_epic(remote):
    remote["siblings"] = [package(index, "Story", "Closed") for index in range(500, 601)]
    remote["empty_second_page"] = True
    with pytest.raises(StoryWorkflowError, match="INCOMPLETE_COLLECTION"):
        complete(remote)
    assert patches(remote) == ["/api/v3/work_packages/301"]


@pytest.mark.parametrize("parent_type", [None, "Initiative"])
def test_absent_or_non_epic_parent(remote, parent_type):
    if parent_type is None:
        del remote["story"]["_links"]["parent"]
    else:
        remote["epic"]["_links"]["type"]["title"] = parent_type
    assert complete(remote)["epic"] is None
    assert patches(remote) == ["/api/v3/work_packages/301"]


def test_preview_performs_no_writes_or_epic_prediction(remote):
    result = complete(remote, apply=False)
    assert result["status"]["dry_run"] is True
    assert result["epic"] is None
    assert patches(remote) == []


def test_epic_update_failure_can_be_retried_after_story_closed(remote):
    remote["fail_epic"] = True
    with pytest.raises(StoryWorkflowError, match="Story WP-301 is complete") as error:
        complete(remote)
    assert error.value.code == "EPIC_COMPLETION_FAILED"
    remote["fail_epic"] = False
    assert complete(remote)["epic"]["automatically_completed"] is True
    assert patches(remote).count("/api/v3/work_packages/301") == 1


def test_remote_story_reopen_blocks_epic_completion(remote):
    remote["reopen_story"] = True
    result = complete(remote)
    assert result["epic"]["incomplete_story_ids"] == [301]
    assert patches(remote) == ["/api/v3/work_packages/301"]


def test_configured_done_status(remote, monkeypatch):
    monkeypatch.setenv("OPENPROJECT_STORY_CLOSED_STATUS", "Done")
    remote["completion_status"] = "Done"
    result = complete(remote)
    assert result["epic"]["status"] == "Done"
    assert result["epic"]["automatically_completed"] is True


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_reports_automatic_epic_completion(remote, tmp_path, capsys, as_json):
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"validation_summary": "Checks passed"}))
    args = ["story", "complete", "301", "--evidence", str(evidence), "--apply"]
    assert main(args + (["--json"] if as_json else [])) == 0
    output = capsys.readouterr().out
    assert "Parent Epic WP-392 automatically completed (Closed)." in output
    if as_json:
        assert json.loads(output)["data"]["epic"]["automatically_completed"] is True

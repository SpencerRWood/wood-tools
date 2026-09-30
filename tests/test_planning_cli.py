from __future__ import annotations

import json

import pytest
from test_story_cli import settings

from wood.cli import main
from wood_project.openproject import OpenProjectClient, OpenProjectError


def package(identifier, name, kind="Epic", status="New"):
    return {
        "id": identifier,
        "subject": name,
        "_links": {
            "type": {"title": kind},
            "status": {"title": status},
            "project": {"href": "/api/v3/projects/3", "title": "Wood Platform"},
        },
    }


@pytest.fixture
def remote(monkeypatch):
    state = {
        "epics": [package(389, "Foundation")],
        "stories": [package(411, "Story", "Story", "Closed")],
        "versions": [
            {
                "id": 20,
                "name": "R1",
                "status": "open",
                "startDate": "2026-01-01",
                "_links": {
                    "definingProject": {"href": "/api/v3/projects/3", "title": "Wood Platform"}
                },
            }
        ],
        "calls": [],
        "fail": False,
        "empty": False,
        "repeat": False,
        "bad_total": False,
    }

    def request(self, method, path, *, query=None, body=None):
        assert method == "GET" and body is None
        state["calls"].append((path, query))
        if state["fail"]:
            raise OpenProjectError("NETWORK_ERROR", "API unavailable")
        if path == "/api/v3/work_packages":
            filters = json.loads(query["filters"])
            assert {"status": {"operator": "*", "values": []}} in filters
            children = any("ancestor" in item for item in filters)
            items = state["stories"] if children else state["epics"]
        elif path == "/api/v3/projects/3/versions":
            items = state["versions"]
        elif path == "/api/v3/statuses":
            items = [
                {"id": i, "name": name, "isClosed": closed}
                for i, (name, closed) in enumerate(
                    [("Closed", True), ("Done", True), ("Rejected", True), ("New", False)]
                )
            ]
        else:
            raise AssertionError(path)
        start = (int(query["offset"]) - 1) * int(query["pageSize"])
        if state["repeat"]:
            start = 0
        page = [] if state["empty"] and start else items[start : start + int(query["pageSize"])]
        result = {"total": len(items), "_embedded": {"elements": page}}
        if state["bad_total"]:
            result.pop("total")
        return result

    monkeypatch.setattr(OpenProjectClient, "request_json", request)
    monkeypatch.setattr("wood.planning.load_settings", settings)
    monkeypatch.setattr("wood.planning.story_reference", lambda _: ("208", 3))
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    return state


def invoke(capsys, *args):
    code = main([*args, "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["mutation"] == "read-only"
    return code, result


@pytest.mark.parametrize(
    "kind,ref", [("epic", "389"), ("epic", "Foundation"), ("release", "20"), ("release", "R1")]
)
def test_id_and_name_lookup(remote, capsys, kind, ref):
    code, result = invoke(capsys, kind, "get", ref)
    assert code == 0
    assert result["data"][kind]["id"] == (389 if kind == "epic" else 20)
    if kind == "release":
        assert result["data"][kind]["start_date"] == "2026-01-01"
        assert result["data"][kind]["project"]["id"] == 3


@pytest.mark.parametrize(
    "status,ready",
    [
        ("New", False),
        ("In progress", False),
        ("Unknown", False),
        ("Rejected", True),
        ("Done", True),
    ],
)
def test_live_epic_readiness(remote, capsys, status, ready):
    remote["stories"].append(package(412, "Sibling", "Story", status))
    code, result = invoke(capsys, "epic", "get", "389")
    assert code == 0
    assert result["data"]["completion_ready"] is ready
    assert result["data"]["incomplete_story_count"] == (0 if ready else 1)


def test_completed_epic_is_read_only_and_still_inspects_live_children(remote, capsys):
    remote["epics"][0]["_links"]["status"]["title"] = "Closed"
    remote["stories"][0]["_links"]["status"]["title"] = "New"
    _, result = invoke(capsys, "epic", "get", "Foundation")
    assert result["data"]["already_complete"]
    assert not result["data"]["completion_ready"]


def test_empty_epic_is_not_ready(remote, capsys):
    remote["stories"] = []
    _, result = invoke(capsys, "epic", "get", "389")
    assert not result["data"]["completion_ready"]


def test_late_child_page_blocks_completion_and_output_pages(remote, capsys):
    remote["stories"] = [package(i, "Child", "Story", "Closed") for i in range(1, 102)]
    remote["stories"][-1]["_links"]["status"]["title"] = "New"
    _, result = invoke(capsys, "epic", "get", "389", "--offset", "50")
    data = result["data"]
    assert not data["completion_ready"]
    assert data["incomplete_story_ids"] == [101]
    assert data["stories"]["total"] == 101
    assert len(data["stories"]["items"]) == 50
    assert data["stories"]["next_offset"] == 100


@pytest.mark.parametrize("kind", ["epic", "release"])
def test_lists_paginate_filter_and_disambiguate(remote, capsys, kind):
    remote["epics"] = [package(i, "Same") for i in range(1, 102)]
    remote["versions"] = [{"id": i, "name": "Same", "status": "open"} for i in range(1, 102)]
    code, result = invoke(capsys, kind, "list", "--project", "3", "--offset", "100")
    assert code == 0
    assert result["data"][kind + "s"]["items"][0]["id"] == 101
    code, result = invoke(capsys, kind, "get", "Same")
    assert code != 0 and result["status"] == "ambiguous"
    assert len(result["data"]["candidates"]) == 50
    _, result = invoke(capsys, kind, "list", "--status", "Missing")
    assert result["data"][kind + "s"]["total"] == 0


@pytest.mark.parametrize("kind,ref", [("epic", "0"), ("epic", "-1"), ("release", "Missing")])
def test_invalid_lookup(remote, capsys, kind, ref):
    code, result = invoke(capsys, kind, "get", ref)
    assert code != 0 and result["status"] == "invalid"


def test_non_epic_is_rejected(remote, capsys):
    remote["epics"] = [package(389, "Foundation", "Story")]
    code, _ = invoke(capsys, "epic", "get", "389")
    assert code != 0


@pytest.mark.parametrize("kind", ["epic", "release"])
def test_api_failure_and_incomplete_collection(remote, capsys, kind):
    remote["fail"] = True
    code, result = invoke(capsys, kind, "list")
    assert code == 4 and result["status"] == "unavailable"
    remote["fail"] = False
    remote["empty"] = True
    remote["epics"] = [package(i, "Epic") for i in range(1, 102)]
    remote["versions"] = [{"id": i, "name": "R1"} for i in range(1, 102)]
    code, result = invoke(capsys, kind, "list")
    assert code == 4 and result["errors"][0]["code"] == "INCOMPLETE_COLLECTION"


@pytest.mark.parametrize(
    "args",
    [
        ("epic", "list", "--offset", "-1"),
        ("release", "list", "--project", "0"),
        ("release", "get", "20", "--status", "open"),
        ("release", "get", "20", "--offset", "1"),
    ],
)
def test_invalid_options(remote, capsys, args):
    code, result = invoke(capsys, *args)
    assert code == 2 and result["status"] == "invalid"


def test_project_mapping_required(remote, monkeypatch, capsys):
    monkeypatch.setattr("wood.planning.story_reference", lambda _: ("208", None))
    code, _ = invoke(capsys, "epic", "list")
    assert code == 2


def test_contract_advertises_planning_commands(capsys):
    _, result = invoke(capsys, "contract")
    assert {"epic list", "epic get", "release list", "release get"} <= set(
        result["data"]["capabilities"]
    )


def test_repeated_child_page_fails_without_readiness(remote, capsys):
    remote["stories"] = [package(i, "Child", "Story", "Closed") for i in range(1, 102)]
    remote["repeat"] = True
    code, result = invoke(capsys, "epic", "get", "389")
    assert code == 4
    assert "completion_ready" not in result["data"]


def test_explicit_project_avoids_repository_lookup(remote, monkeypatch, capsys):
    def unexpected(_):
        raise AssertionError("explicit project should override context")

    monkeypatch.setattr("wood.planning.story_reference", unexpected)
    code, _ = invoke(capsys, "release", "get", "20", "--project", "3")
    assert code == 0


def test_missing_total_is_unavailable(remote, capsys):
    remote["bad_total"] = True
    code, result = invoke(capsys, "epic", "get", "389")
    assert code == 4
    assert result["errors"][0]["code"] == "INCOMPLETE_COLLECTION"

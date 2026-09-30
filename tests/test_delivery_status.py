from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_story_cli import _story, settings

from wood import delivery_status as status
from wood import operations
from wood.cli import main
from wood.delivery import _available, _missing
from wood_project.openproject import OpenProjectClient, OpenProjectError
from wood_project.story import workflow
from wood_project.story.models import StoryWorkflowError


@pytest.fixture
def authority(monkeypatch):
    state = {
        "story": _story(414, "In progress"),
        "search": {"total_count": 1, "incomplete_results": False, "items": [{"number": 9}]},
        "pr": {
            "merged": True,
            "state": "closed",
            "merge_commit_sha": "b" * 40,
            "head": {"sha": "a" * 40, "ref": "feature/op-414-delivery"},
            "base": {"ref": "main", "repo": {"full_name": "owner/wood-tools"}},
        },
        "run": {"id": 7, "status": "completed", "conclusion": "success", "head_sha": "a" * 40},
        "calls": [],
        "optional": {
            name: _missing("Not configured.", applicable=False)
            for name in (
                "semantic_release",
                "container_image_digest",
                "infrastructure_promotion",
                "deployed_revision",
                "runtime_verification",
            )
        },
    }
    state["optional"]["semantic_release"] = _available(
        {"version": "v1.0.0", "revision": "b" * 40},
        "https://github.com/owner/wood-tools/releases/tag/v1.0.0",
    )
    monkeypatch.setattr(workflow, "_story", lambda *_args: state["story"])
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/wood-tools")
    monkeypatch.setattr(operations, "repository_root", lambda root: root)
    monkeypatch.setattr(
        operations,
        "repo_info",
        lambda root: {"release": {"semantic_release": True}, "deployment": {"applicable": False}},
    )
    monkeypatch.setattr(status, "collect_optional_delivery", lambda *_args: state["optional"])
    monkeypatch.setattr(status, "load_settings", settings)

    def gh(_root, path):
        state["calls"].append(path)
        if path.startswith("search/"):
            return state["search"]
        if "/pulls/" in path:
            return state["pr"]
        return {"workflow_runs": [state["run"]] if state["run"] else []}

    monkeypatch.setattr(operations, "_gh", gh)
    return state


def reconcile():
    return status.reconcile(OpenProjectClient(settings()), Path("."), 414)


def test_delivered_cli_and_normalized_fields(authority, capsys):
    assert main(["delivery", "status", "414", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    data = payload["data"]
    assert data["delivery_stage"] == "delivered"
    assert data["blocker"] is None
    assert payload["mutation"] == "read-only"
    assert data["fields"]["pull_request"]["value"] == {"number": 9, "state": "merged"}
    assert data["fields"]["release"]["value"]["version"] == "v1.0.0"
    assert data["fields"]["verification_state"]["state"] == "not_applicable"
    assert len(authority["calls"]) == 3


@pytest.mark.parametrize(
    "count,stage", [(0, "implementation"), (2, "pull_request"), (51, "pull_request")]
)
def test_discovery_missing_ambiguous_and_bounded(authority, count, stage):
    authority["search"]["total_count"] = count
    authority["search"]["items"] = [{"number": 9}] * count
    result = reconcile()
    assert result["delivery_stage"] == stage
    assert result["blocker"]
    assert len(authority["calls"]) == 1


def test_incomplete_search_and_explicit_selection(authority):
    authority["search"]["incomplete_results"] = True
    assert "incomplete" in reconcile()["blocker"]
    assert (
        status.reconcile(OpenProjectClient(settings()), Path("."), 414, pr_number=9)[
            "delivery_stage"
        ]
        == "delivered"
    )


@pytest.mark.parametrize("field,value", [("ref", "feature/op-415-other"), ("sha", "invalid")])
def test_wrong_story_or_revision(authority, field, value):
    authority["pr"]["head"][field] = value
    assert reconcile()["delivery_stage"] == "pull_request"


def test_repository_mismatch_and_missing(authority):
    authority["story"]["description"]["raw"] = "Primary Repository: other"
    assert "differs" in reconcile()["blocker"]
    authority["story"]["description"]["raw"] = "No repository"
    assert "no Primary Repository" in reconcile()["blocker"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", None])
def test_ci_failure_preserves_other_links(authority, conclusion):
    authority["run"]["conclusion"] = conclusion
    result = reconcile()
    assert result["delivery_stage"] == "ci"
    assert result["fields"]["release"]["state"] == "available"


@pytest.mark.parametrize("run", [None, {"id": 8, "head_sha": "c" * 40}])
def test_missing_or_unrelated_ci(authority, run):
    authority["run"] = run
    assert reconcile()["delivery_stage"] == "ci"


@pytest.mark.parametrize("state", ["open", "closed"])
def test_unmerged_pr(authority, state):
    authority["pr"].update(merged=False, state=state)
    assert reconcile()["delivery_stage"] == "pull_request"


@pytest.mark.parametrize(
    "field,stage",
    [
        ("semantic_release", "release"),
        ("container_image_digest", "container_image"),
        ("infrastructure_promotion", "infrastructure_promotion"),
        ("deployed_revision", "deployment"),
        ("runtime_verification", "runtime_verification"),
    ],
)
def test_optional_chain_stages(authority, field, stage):
    authority["optional"][field] = _missing("Authority evidence unavailable.")
    result = reconcile()
    assert result["delivery_stage"] == stage
    assert result["blocker"] == "Authority evidence unavailable."


def test_environment_and_attestations(authority, monkeypatch):
    for field in ("deployed_revision", "runtime_verification", "infrastructure_promotion"):
        authority["optional"][field] = _available(
            {"revision": "b" * 40, "environment": "prod", "status": "passed"},
            "https://github.com/owner/infra/pull/3",
        )

    def collect(root, slug, merged, environment):
        assert environment == "prod"
        return authority["optional"]

    monkeypatch.setattr(status, "collect_optional_delivery", collect)
    result = status.reconcile(OpenProjectClient(settings()), Path("."), 414, environment="prod")
    assert result["fields"]["deployment_environment"]["value"] == "prod"
    assert result["delivery_stage"] == "delivered"


def test_provider_failure_preserves_story(authority, monkeypatch):
    def fail(*_args):
        raise operations.OperationsError("UNAVAILABLE", "GitHub unavailable")

    monkeypatch.setattr(operations, "_gh", fail)
    result = reconcile()
    assert result["story_id"] == 414
    assert result["blocker"] == "GitHub unavailable"


@pytest.mark.parametrize(
    "exception",
    [
        OpenProjectError("UNAVAILABLE", "secret"),
        StoryWorkflowError("NOT_A_STORY", "Not a Story"),
        operations.OperationsError("UNAVAILABLE", "Unavailable"),
    ],
)
def test_command_authority_errors(authority, monkeypatch, capsys, exception):
    def fail(*_args, **_kwargs):
        raise exception

    monkeypatch.setattr(status, "reconcile", fail)
    assert main(["delivery", "status", "414", "--json"]) != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"]
    assert "secret" not in json.dumps(payload)


@pytest.mark.parametrize("story,pr", [(0, None), (414, 0)])
def test_invalid_ids(authority, story, pr):
    with pytest.raises(StoryWorkflowError):
        status.reconcile(OpenProjectClient(settings()), Path("."), story, pr_number=pr)


def test_invalid_authority_object(authority, monkeypatch):
    monkeypatch.setattr(operations, "_gh", lambda *_args: [])
    assert reconcile()["blocker"] == "Expected an authority object."


def test_missing_merge_revision(authority):
    authority["pr"]["merge_commit_sha"] = None
    assert "merge revision" in reconcile()["blocker"]


def test_bounded_output_and_deterministic_result(authority, capsys):
    authority["optional"]["semantic_release"] = _missing("x" * 10000)
    assert main(["delivery", "status", "414", "--json"]) == 0
    first = capsys.readouterr().out
    assert main(["delivery", "status", "414", "--json"]) == 0
    assert first == capsys.readouterr().out
    assert len(json.loads(first)["data"]["blocker"]) == 500
    assert len(first) < 5000

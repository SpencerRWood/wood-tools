import json
import subprocess
from pathlib import Path

import pytest
from test_story_cli import _story, settings

from wood import milestones, operations
from wood.cli import main
from wood.workflow_files import snapshot_fingerprint
from wood_project.openproject import OpenProjectClient, OpenProjectError
from wood_project.story import session, workflow
from wood_project.story.models import StoryWorkflowError


@pytest.fixture
def authority(tmp_path, monkeypatch):
    root = tmp_path / "wood-tools"
    root.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "--quiet", "-b", "feature/op-410-report")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.test")
    (root / "source.txt").write_text("implementation")
    git("add", ".")
    git("commit", "--quiet", "-m", "initial")
    monkeypatch.chdir(root)
    _, directory = session.locations()
    directory.mkdir()
    claim = {
        "schema_version": 1,
        "story_id": 410,
        "state": "claimed",
        "owner": "owner-1",
        "repository": str(root),
        "worktree": str(root),
        "branch": "feature/op-410-report",
        "server": settings().base_url.rstrip("/"),
        "phase": "implementation",
        "next_action": "Implement",
        "records": [],
        "branching": {
            "strategy": "trunk",
            "base_branch": "main",
            "target_branch": "main",
            "base_ref": "refs/heads/main",
            "base_revision": git("rev-parse", "HEAD"),
        },
    }
    session.save(directory, 410, claim)
    state = {
        "root": root,
        "directory": directory,
        "claim": claim,
        "posts": 0,
        "activities": [],
        "fail": False,
        "ambiguous": False,
    }
    monkeypatch.setattr(workflow, "_story", lambda *_: _story(410, "In progress"))
    monkeypatch.setattr(operations, "_slug", lambda *_: "owner/wood-tools")
    monkeypatch.setattr(operations, "repo_info", lambda *_: {"validation": {"checks": ["ruff"]}})
    monkeypatch.setattr("wood.story.load_settings", settings)

    def request(self, method, path, *, query=None, body=None):
        if state["fail"]:
            raise OpenProjectError("UNAVAILABLE", "private credential response")
        if path == "/api/v3/work_packages/410":
            return _story(410, "In progress")
        if path.endswith("/activities"):
            if method == "GET":
                return {
                    "total": len(state["activities"]),
                    "_embedded": {"elements": state["activities"]},
                }
            state["posts"] += 1
            value = {
                "id": state["posts"],
                "comment": body["comment"],
                "_links": {"workPackage": {"href": "/api/v3/work_packages/410"}},
            }
            state["activities"].append(value)
            if state["ambiguous"]:
                state["ambiguous"] = False
                raise OpenProjectError("UNAVAILABLE", "private token response")
            return value
        return state["activities"][int(path.rsplit("/", 1)[1]) - 1]

    monkeypatch.setattr(OpenProjectClient, "request_json", request)
    return state


def report(**kwargs):
    return milestones.report(
        OpenProjectClient(settings()), 410, owner="owner-1", apply=True, **kwargs
    )


def test_preview_no_mutation_and_duplicate_retries(authority):
    preview = milestones.report(OpenProjectClient(settings()), 410, owner="owner-1", apply=False)
    assert preview["dry_run"] and len(preview["milestones"]) == 1
    assert not Path(preview["outbox"]).exists()
    result = report()
    assert result["pending_count"] == 0 and authority["posts"] == 1
    path = next(Path(result["outbox"]).glob("*.json"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert report()["submitted"] == [] and authority["posts"] == 1


@pytest.mark.parametrize("failure", ["fail", "ambiguous"])
def test_failed_or_ambiguous_submission_retains_exact_retriable_evidence(authority, failure):
    authority[failure] = True
    result = report()
    assert result["pending_count"] == 1
    assert "private credential response" not in json.dumps(result)
    assert "private token response" not in json.dumps(result)
    authority["fail"] = False
    result = report(retry=True)
    assert result["pending_count"] == 0
    assert authority["posts"] == 1


def test_changed_remote_event_is_not_overwritten_or_duplicated(authority):
    authority["ambiguous"] = True
    report()
    authority["activities"][0]["comment"]["raw"] += "changed"
    result = report(retry=True)
    assert result["pending_count"] == 1 and authority["posts"] == 1
    assert result["failures"][0]["code"] == "ACTIVITY_CONFLICT"


def test_claim_binding_tampering_and_unsafe_outbox(authority):
    with pytest.raises(StoryWorkflowError, match="another session"):
        milestones.report(OpenProjectClient(settings()), 410, owner="other", apply=True)
    result = report()
    path = next(Path(result["outbox"]).glob("*.json"))
    value = json.loads(path.read_text())
    value["event"]["story_id"] = 411
    path.write_text(json.dumps(value))
    with pytest.raises(StoryWorkflowError, match="Outbox event changed"):
        report(retry=True)
    path.unlink()
    path.symlink_to(authority["directory"] / "410.json")
    with pytest.raises(StoryWorkflowError, match="unsafe"):
        report(retry=True)


def test_current_validation_and_log_binding(authority, tmp_path):
    log = tmp_path / "check.log"
    log.write_text("passed")
    record = {
        "schema_version": 1,
        "kind": "wood-repository-validation",
        "passed": True,
        "repository_root": str(authority["root"]),
        "source_fingerprint": snapshot_fingerprint(
            authority["root"], authority["root"], Path("/private/tmp")
        ),
        "checks": [{"name": "ruff", "state": "passed", "exit_code": 0, "log_path": str(log)}],
    }
    path = tmp_path / "validation.json"
    path.write_text(json.dumps(record))
    assert report(validation=path)["pending_count"] == 0
    assert authority["posts"] == 2
    log.unlink()
    with pytest.raises(StoryWorkflowError, match="current passing"):
        report(validation=path)
    log.write_text("passed")
    (authority["root"] / "source.txt").write_text("changed")
    with pytest.raises(StoryWorkflowError, match="current passing"):
        report(validation=path)


def test_saved_readiness_scope_and_revisions_are_explicit(authority, tmp_path):
    claim = authority["claim"]
    claim["branching"].update(
        strategy="integration",
        base_branch="development",
        target_branch="development",
        release_id=24,
        scope_reference="approved",
    )
    session.save(authority["directory"], 410, claim)
    revision = claim["branching"]["base_revision"]
    for name in ("main", "development"):
        session.git(authority["root"], "update-ref", f"refs/remotes/origin/{name}", revision)
    value = {
        "branching": claim["branching"],
        "observed_at": "2026-10-19T18:00:00Z",
        "main_revision": revision,
        "integration_revision": revision,
        "ready_for_release_review": True,
        "authorization_granted": False,
    }
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(value))
    result = report(readiness=path)
    assert result["pending_count"] == 0 and authority["posts"] == 2
    value["branching"] = {**value["branching"], "release_id": 26}
    path.write_text(json.dumps(value))
    with pytest.raises(StoryWorkflowError, match="Readiness must match"):
        report(readiness=path)


def test_changed_claim_and_conflicting_retry_inputs(authority):
    with pytest.raises(StoryWorkflowError, match="Retry uses retained"):
        report(retry=True, pr=2)
    authority["claim"]["worktree"] = "/other/worktree"
    session.save(authority["directory"], 410, authority["claim"])
    with pytest.raises(StoryWorkflowError, match="claimed worktree"):
        report()


def test_cli_pending_is_not_success_and_retry_skips_collection(authority, monkeypatch, capsys):
    authority["fail"] = True
    assert main(["story", "milestones", "410", "--owner", "owner-1", "--apply", "--json"]) == 4
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "unavailable" and data["data"]["pending_count"] == 1
    authority["fail"] = False
    monkeypatch.setattr(
        milestones, "_collect", lambda *_: pytest.fail("Retry must retain original events")
    )
    assert report(retry=True)["pending_count"] == 0


def test_delivery_snapshot_is_saved_separately_and_unchanged_observations_deduplicate(
    authority, monkeypatch
):
    snapshot = {
        "observed_at": "now",
        "fields": {
            name: {
                "state": "available",
                "value": value,
                "source": "https://github.com/owner/wood-tools/pull/9",
            }
            for name, value in (
                ("pull_request", {"number": 9, "state": "open"}),
                ("review", {"state": "unreviewed"}),
                ("ci", {"run_id": 3, "state": "queued"}),
                ("merged_revision", "a" * 40),
            )
        },
    }
    monkeypatch.setattr(milestones.delivery_status, "reconcile", lambda *_args, **_kwargs: snapshot)
    result = report(pr=9)
    assert Path(result["delivery_file"]).exists() and authority["posts"] == 5
    snapshot["observed_at"] = "later"
    assert report(pr=9)["submitted"] == [] and authority["posts"] == 5

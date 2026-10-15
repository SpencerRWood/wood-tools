"""Exact-revision routing, hotfix context, and mandatory reconciliation fixtures."""

from __future__ import annotations

import json

import pytest

from wood import operations
from wood import repository_routing as routing
from wood.cli import main
from wood.workflow_files import WorkflowFilesError
from wood_project.story import session


def pr(head, target="development", body=""):
    return {
        "head": {"ref": head, "sha": "a" * 40, "repo": {"full_name": "fixture/repo"}},
        "base": {"ref": target, "sha": "b" * 40, "repo": {"full_name": "fixture/repo"}},
        "body": body,
        "number": 1,
    }


@pytest.mark.parametrize(
    "head,target,body,mode",
    [
        ("feature/op-556-change", "development", "", "story"),
        ("feat/change", "development", "", "story"),
        ("fix/change", "development", "", "story"),
        ("main", "development", "", "reconciliation"),
        ("development", "main", "", "release"),
        (
            "fix/op-556-hotfix",
            "main",
            "Hotfix-Incident: incident:1\nHotfix-Approval: approval:1",
            "hotfix",
        ),
    ],
)
def test_supported_contexts(head, target, body, mode):
    result = routing.context(pr(head, target, body), "fixture/repo", "development")
    assert result["mode"] == mode and not result["authorization_granted"]


@pytest.mark.parametrize(
    "head,target,body",
    [
        ("feature/change", "main", ""),
        ("unapproved/change", "development", ""),
        ("development", "other", ""),
        ("development", "main", "Hotfix-Incident: incident"),
        ("fix/op-556-change", "main", "Hotfix-Approval: approval"),
        ("fix/change", "main", "Hotfix-Incident: incident\nHotfix-Approval: approval"),
        ("feature/change", "development", "Hotfix-Incident: incident\nHotfix-Approval: approval"),
        (
            "fix/op-556-change",
            "main",
            "Hotfix-Incident: first\nHotfix-Incident: second\nHotfix-Approval: approval",
        ),
    ],
)
def test_unsupported_routing_has_no_exception(head, target, body):
    with pytest.raises(WorkflowFilesError):
        routing.context(pr(head, target, body), "fixture/repo", "development")


def test_fork_context_is_rejected():
    value = pr("development", "main")
    value["head"]["repo"]["full_name"] = "fork/repo"
    with pytest.raises(WorkflowFilesError, match="same repository"):
        routing.context(value, "fixture/repo", "development")


def fixture(root):
    session.git(root, "init", "-q", "-b", "main")
    session.git(root, "config", "user.name", "Fixture")
    session.git(root, "config", "user.email", "f@example.test")
    (root / "pyproject.toml").write_text(
        '[tool.wood.branching]\nstrategy = "integration"\nrelease_id = 26\n'
        'integration_branch = "development"\nscope_reference = "approval"\n'
    )
    session.git(root, "add", ".")
    session.git(root, "commit", "-q", "-m", "chore: fixture baseline")
    return session.git(root, "rev-parse", "HEAD")


def test_real_ancestry_and_exact_candidate(tmp_path):
    baseline = fixture(tmp_path)
    session.git(tmp_path, "switch", "-q", "-c", "feature/op-556-change")
    (tmp_path / "change").write_text("fixture")
    session.git(tmp_path, "add", ".")
    session.git(tmp_path, "commit", "-q", "-m", "feat: fixture")
    head = session.git(tmp_path, "rev-parse", "HEAD")
    value = pr("feature/op-556-change")
    value["head"]["sha"], value["base"]["sha"] = head, baseline
    event = {"repository": {"full_name": "fixture/repo"}, "pull_request": value}
    assert routing.route(tmp_path, event)["mode"] == "story"
    value["head"]["sha"] = baseline
    with pytest.raises(WorkflowFilesError, match="exact PR head"):
        routing.route(tmp_path, event)


def test_reconciliation_and_hotfix_require_ancestry(tmp_path):
    baseline = fixture(tmp_path)
    session.git(tmp_path, "update-ref", "refs/remotes/origin/main", baseline)
    value = pr("main")
    value["head"]["sha"] = value["base"]["sha"] = baseline
    event = {"repository": {"full_name": "fixture/repo"}, "pull_request": value}
    assert routing.route(tmp_path, event)["mode"] == "reconciliation"
    value["head"]["ref"] = "fix/op-556-emergency"
    value["base"]["ref"] = "main"
    value["body"] = "Hotfix-Incident: incident:1\nHotfix-Approval: approval:1"
    assert routing.route(tmp_path, event)["reconciliation_required"]


def test_divergent_hotfix_can_reconcile_then_release_requires_back_integration(tmp_path):
    baseline = fixture(tmp_path)
    session.git(tmp_path, "switch", "-qc", "development")
    (tmp_path / "story").write_text("accumulated development work")
    session.git(tmp_path, "add", ".")
    session.git(tmp_path, "commit", "-qm", "feat: story")
    development = session.git(tmp_path, "rev-parse", "HEAD")
    session.git(tmp_path, "switch", "-q", "main")
    (tmp_path / "hotfix").write_text("urgent main fix")
    session.git(tmp_path, "add", ".")
    session.git(tmp_path, "commit", "-qm", "fix: emergency")
    hotfix = session.git(tmp_path, "rev-parse", "HEAD")
    session.git(tmp_path, "update-ref", "refs/remotes/origin/main", hotfix)
    value = pr("main")
    value["head"]["sha"], value["base"]["sha"] = hotfix, development
    event = {"repository": {"full_name": "fixture/repo"}, "pull_request": value}
    assert routing.route(tmp_path, event)["mode"] == "reconciliation"
    session.git(tmp_path, "switch", "-q", "development")
    value["head"]["ref"], value["base"]["ref"] = "development", "main"
    value["head"]["sha"], value["base"]["sha"] = development, hotfix
    from wood_project.story.models import StoryWorkflowError

    with pytest.raises(StoryWorkflowError):
        routing.route(tmp_path, event)
    session.git(tmp_path, "merge", "--no-ff", "main", "-m", "fix: reconcile hotfix")
    value["head"]["sha"] = session.git(tmp_path, "rev-parse", "HEAD")
    assert routing.route(tmp_path, event)["mode"] == "release"
    assert session.git(tmp_path, "merge-base", baseline, hotfix) == baseline


def test_release_context_extracts_references_without_granting_authority(tmp_path, monkeypatch):
    revision = fixture(tmp_path)
    value = pr(
        "fix/op-556-emergency", "main", "Hotfix-Incident: incident\nHotfix-Approval: approval"
    )
    value.update({"merge_commit_sha": revision, "merged_at": "2026-10-14T20:00:00Z"})
    monkeypatch.setattr(operations, "_slug", lambda _: "fixture/repo")
    monkeypatch.setattr(operations, "_gh", lambda *_args: [value])
    result = routing.release_context(tmp_path, revision)
    assert result["incident"] == "incident" and not result["authorization_granted"]
    monkeypatch.setattr(operations, "_gh", lambda *_args: [value, value])
    with pytest.raises(WorkflowFilesError, match="Exactly one"):
        routing.release_context(tmp_path, revision)


def test_routing_cli_and_safe_outputs(tmp_path, monkeypatch, capsys):
    revision = fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    value = pr("feature/op-556-change")
    value["head"]["sha"] = value["base"]["sha"] = revision
    path, output = tmp_path / "event.json", tmp_path / "github-output"
    path.write_text(
        json.dumps({"repository": {"full_name": "fixture/repo"}, "pull_request": value})
    )
    assert (
        main(["repo", "route", "--event-file", str(path), "--github-output", str(output), "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["data"]["mode"] == "story"
    assert output.read_text() == "incident=\napproval=\n"
    path.write_text("{}")
    assert main(["repo", "route", "--event-file", str(path), "--json"]) == 2
    assert "REPOSITORY_INPUT_INVALID" in capsys.readouterr().out

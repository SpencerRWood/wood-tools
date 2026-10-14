"""Emergency start is explicit; release review requires verified back-integration."""

import json
from pathlib import Path

import pytest

from wood.cli import main
from wood_project.story import branching, session, workflow
from wood_project.story.models import StoryWorkflowError

pytest_plugins = ["test_story_session"]


def integration(repository, tmp_path):
    root, _, _, _ = repository
    project = root / "pyproject.toml"
    project.write_text(
        project.read_text() + '\n[tool.wood.branching]\nstrategy = "integration"\n'
        'release_id = 20\nintegration_branch = "development"\nscope_reference = "approved R1"\n'
        "\n[tool.wood.workflow]\n"
        f"output_directory = {json.dumps(str(tmp_path / 'workflow-output'))}\n"
    )
    session.git(root, "add", ".")
    session.git(root, "commit", "-qm", "Approved universal integration")
    remote = tmp_path / "remote.git"
    session.git(root, "init", "--bare", "-q", str(remote))
    session.git(root, "remote", "add", "origin", str(remote))
    session.git(root, "branch", "development")
    session.git(root, "push", "-q", "origin", "main", "development")
    return root


def emergency(repository, **overrides):
    _, target, client, _ = repository
    return workflow.start_story(
        client,
        42,
        apply=True,
        owner="incident-owner",
        worktree=target,
        hotfix_incident="INC-42 production incident",
        hotfix_approval="approved incident decision 7",
        **overrides,
    )


@pytest.mark.parametrize(
    "name", ["application", "wood-tools", "library", "infrastructure", "template"]
)
def test_integration_does_not_depend_on_repository_purpose(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    session.git(root, "init", "-q", "-b", "main")
    plan = branching.configuration(
        root,
        source_text='[tool.wood.branching]\nstrategy = "integration"\n'
        'release_id = 20\nintegration_branch = "development"\nscope_reference = "approved"',
    )
    assert plan["base_branch"] == plan["target_branch"] == "development"


@pytest.mark.parametrize(
    "incident,approval",
    [
        (None, "approved"),
        ("INC-42", None),
        ("", "approved"),
        ("INC-42", " "),
        ("x" * 501, "approved"),
    ],
)
def test_incomplete_hotfix_authority_never_creates_claim(repository, tmp_path, incident, approval):
    root = integration(repository, tmp_path)
    _, target, client, _ = repository
    with pytest.raises(StoryWorkflowError, match="explicit approval"):
        workflow.start_story(
            client,
            42,
            apply=True,
            owner="fixture",
            worktree=target,
            hotfix_incident=incident,
            hotfix_approval=approval,
        )
    assert session.read_record(session.locations()[1], 42) is None
    assert not target.exists()
    assert not session.git(root, "branch", "--list", "fix/op-*")


def test_hotfix_uses_fetched_main_and_keeps_claim_authority(repository, tmp_path):
    root = integration(repository, tmp_path)
    (root / "incident.txt").write_text("Urgent main-only correction\n")
    session.git(root, "add", ".")
    session.git(root, "commit", "-qm", "fix: incident")
    session.git(root, "push", "-q", "origin", "main")
    main_before = session.git(root, "rev-parse", "main")
    started = emergency(repository)
    plan = started["branching"]
    assert plan["strategy"] == "integration" and plan["mode"] == "hotfix"
    assert plan["base_branch"] == plan["target_branch"] == "main"
    assert plan["integration_branch"] == "development"
    assert plan["base_ref"] == "refs/remotes/origin/main"
    assert plan["base_revision"] == main_before
    assert started["session"]["branch"].startswith("fix/op-42-")
    assert session.git(Path(started["worktree"]["path"]), "rev-parse", "HEAD") == main_before
    assert session.git(root, "rev-parse", "main") == main_before
    claim = Path(started["session_file"]).read_bytes()
    _, target, client, _ = repository
    (target / "unfinished.txt").write_text("Keep incident work\n")
    with pytest.raises(StoryWorkflowError, match="do not migrate an active claim"):
        workflow.start_story(
            client,
            42,
            apply=True,
            owner="incident-owner",
            worktree=target,
            hotfix_incident="INC-OTHER",
            hotfix_approval="different approval",
        )
    assert Path(started["session_file"]).read_bytes() == claim
    assert (target / "unfinished.txt").read_text() == "Keep incident work\n"


def test_hotfix_cannot_bypass_normal_target_prefix_or_identity_checks(repository, tmp_path):
    root = integration(repository, tmp_path)
    _, target, client, story = repository
    with pytest.raises(StoryWorkflowError, match="Explicit branches"):
        workflow.start_story(
            client,
            42,
            apply=True,
            owner="fixture",
            worktree=target,
            base_branch="main",
            target_branch="main",
        )
    with pytest.raises(StoryWorkflowError, match="Choose feature"):
        emergency(repository, branch_prefix="hotfix")
    story["description"]["raw"] = "Primary Repository: wrong-repository"
    with pytest.raises(StoryWorkflowError, match="Primary checkout"):
        emergency(repository)
    assert session.read_record(session.locations()[1], 42) is None
    assert session.git(root, "branch", "--list", "fix/op-*") == ""


def test_release_preparation_blocks_unreconciled_main_and_records_reconciliation(
    repository, tmp_path, capsys
):
    root = integration(repository, tmp_path)
    assert main(["repo", "release-readiness", "--json"]) == 0
    ready = json.loads(capsys.readouterr().out)["data"]
    assert ready["main_in_integration"] and not ready["authorization_granted"]
    (root / "hotfix.txt").write_text("Production hotfix\n")
    session.git(root, "add", ".")
    session.git(root, "commit", "-qm", "fix: incident")
    session.git(root, "push", "-q", "origin", "main")
    before = session.git(root, "rev-parse", "HEAD")
    assert main(["repo", "release-readiness", "--json"]) == 3
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "blocked"
    assert blocked["data"]["unreconciled_main_commits"] == [before]
    assert not blocked["data"]["ready_for_release_review"]
    assert session.git(root, "rev-parse", "HEAD") == before
    # Fixture stands in for an approved main → development reconciliation PR.
    session.git(root, "switch", "-q", "development")
    session.git(root, "merge", "--ff-only", "main")
    session.git(root, "push", "-q", "origin", "development")
    assert main(["repo", "release-readiness", "--json"]) == 0
    reconciled = json.loads(capsys.readouterr().out)["data"]
    assert reconciled["main_revision"] == reconciled["integration_revision"] == before
    assert reconciled["unreconciled_main_commits"] == []
    record = json.loads(Path(reconciled["readiness_file"]).read_text())
    assert record == reconciled and record["observed_at"]
    assert not record["authorization_granted"]


def test_hotfix_requires_adoption_and_full_history(repository, tmp_path):
    root, _, _, _ = repository
    with pytest.raises(StoryWorkflowError, match="integration strategy"):
        emergency(repository)
    integration(repository, tmp_path)
    (root / ".git/shallow").write_text(session.git(root, "rev-parse", "HEAD") + "\n")
    with pytest.raises(StoryWorkflowError, match="complete history"):
        branching.release_readiness(root)


def test_next_open_planning_scope_starts_without_losing_old_claim(
    repository, tmp_path, monkeypatch
):
    root = integration(repository, tmp_path)
    _, old_target, client, story = repository
    old = workflow.start_story(client, 42, apply=True, owner="R1-owner", worktree=old_target)
    old_claim = Path(old["session_file"]).read_bytes()
    (old_target / "unfinished.txt").write_text("Retain R1 work and evidence\n")
    # Fixture represents an explicitly reviewed mapping transition, not a migration command.
    session.git(root, "switch", "-q", "development")
    project = root / "pyproject.toml"
    project.write_text(
        project.read_text()
        .replace("release_id = 20", "release_id = 21")
        .replace("approved R1", "approved R2")
    )
    session.git(root, "add", ".")
    session.git(root, "commit", "-qm", "Approved next planning scope")
    session.git(root, "push", "-q", "origin", "development")
    story["id"] = 43
    story["_links"]["version"] = {"title": "R2", "href": "/api/v3/versions/21"}
    next_target = tmp_path / "R2-worktree"
    started = workflow.start_story(client, 43, apply=True, owner="R2-owner", worktree=next_target)
    assert started["branching"]["release_id"] == 21
    assert Path(old["session_file"]).read_bytes() == old_claim
    assert (old_target / "unfinished.txt").read_text() == "Retain R1 work and evidence\n"
    assert session.read_record(session.locations()[1], 42)["branching"]["release_id"] == 20

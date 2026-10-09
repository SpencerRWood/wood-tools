"""Exercise claims against real Git worktrees and separate concurrent processes."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from wood.cli import main
from wood_project.openproject import OpenProjectClient, OpenProjectSettings
from wood_project.story import session, workflow
from wood_project.story.models import StoryWorkflowError


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "example"
    root.mkdir()
    session.git(root, "init", "-q", "-b", "main")
    session.git(root, "config", "user.name", "Fixture")
    session.git(root, "config", "user.email", "fixture@example.invalid")
    (root / "README.md").write_text("Fixture\n")
    (root / "pyproject.toml").write_text(
        "[tool.wood.openproject]\nproject_id = 3\ninitiative_id = 10\n"
    )
    session.git(root, "add", ".")
    session.git(root, "commit", "-qm", "Fixture")
    monkeypatch.chdir(root)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    story = {
        "id": 42,
        "subject": "Ship lifecycle",
        "lockVersion": 1,
        "description": {"raw": "Primary Repository: example\n"},
        "_links": {
            "type": {"title": "Story"},
            "status": {"title": "New"},
            "project": {"href": "/api/v3/projects/3"},
            "parent": {"href": "/api/v3/work_packages/10"},
            "version": {"title": "R1", "href": "/api/v3/versions/20"},
        },
    }
    monkeypatch.setattr(workflow, "_story", lambda *_args: story)
    monkeypatch.setattr(workflow, "api_get_json", lambda *_args: {"status": "open"})
    monkeypatch.setattr(workflow.discovery, "fetch_predecessor_map", lambda *_args: {})
    monkeypatch.setattr(
        workflow,
        "_status",
        lambda *_args, **kwargs: {"status": "In progress", "dry_run": not kwargs["apply"]},
    )
    settings = OpenProjectSettings(
        base_url="https://example.invalid",
        token="fixture",
        token_provider="test",
        user_agent="fixture",
    )
    monkeypatch.setattr("wood.story.load_settings", lambda: settings)
    return root, tmp_path / "story-worktree", OpenProjectClient(settings), story


def start(repository, owner="codex-fixture", apply=True):
    _, target, client, _ = repository
    return workflow.start_story(client, 42, apply=apply, owner=owner, worktree=target)


def test_preview_has_no_claim_branch_worktree_or_status_mutations(repository):
    root, target, _, _ = repository
    result = start(repository, apply=False)
    assert result["dry_run"]
    assert not target.exists()
    assert not (root / ".git/wood-stories").exists()
    assert session.git(root, "branch", "--show-current") == "main"
    assert not session.git(root, "branch", "--list", "feature/*")


def test_dirty_primary_is_preserved_and_new_worktree_uses_main(repository):
    root, target, _, _ = repository
    session.git(root, "switch", "-qc", "other-story")
    (root / "README.md").write_text("Other Story edits\n")
    result = start(repository)
    assert result["session"]["branch"] == "feature/op-42-ship-lifecycle"
    assert (target / "README.md").read_text() == "Fixture\n"
    assert (root / "README.md").read_text() == "Other Story edits\n"
    assert session.git(root, "branch", "--show-current") == "other-story"
    assert Path(result["session_file"]).stat().st_mode & 0o777 == 0o600


def test_same_owner_resumes_dirty_worktree_and_in_progress_without_remote_write(
    repository, monkeypatch
):
    _, target, _, story = repository
    first = start(repository)
    (target / "README.md").write_text("Implementation\n")
    story["_links"]["status"]["title"] = "In progress"
    monkeypatch.setattr(
        workflow, "_status", lambda *_args, **_kwargs: pytest.fail("No redundant status write")
    )
    second = start(repository)
    assert second["status"]["resumed"]
    assert second["worktree"]["reused"] and second["worktree"]["dirty"]
    assert second["session_file"] == first["session_file"]
    assert (target / "README.md").read_text() == "Implementation\n"


def test_other_owner_cannot_start_mutate_or_release(repository, capsys):
    start(repository)
    with pytest.raises(StoryWorkflowError, match="another session"):
        start(repository, owner="pi-fixture")
    assert (
        main(
            [
                "story",
                "block",
                "42",
                "--owner",
                "pi-fixture",
                "--reason",
                "blocked",
                "--apply",
                "--json",
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "CLAIM_CONFLICT"
    with pytest.raises(StoryWorkflowError, match="another session"):
        session.session_command("release", 42, owner="pi-fixture", apply=True)


@pytest.mark.parametrize(
    "scenario", ["path", "branch", "project", "initiative", "closed", "server"]
)
def test_resume_rejects_identity_and_readiness_changes(repository, scenario):
    root, target, client, story = repository
    start(repository)
    if scenario == "path":
        repository = (root, target.parent / "other", client, story)
    elif scenario == "branch":
        session.git(target, "switch", "-qc", "unrelated")
    elif scenario == "project":
        story["_links"]["project"]["href"] = "/api/v3/projects/4"
    elif scenario == "initiative":
        story["_links"]["parent"]["href"] = "/api/v3/work_packages/99"
    elif scenario == "closed":
        story["_links"]["status"]["title"] = "Closed"
    else:
        client.settings = OpenProjectSettings(
            base_url="https://other.invalid",
            token="fixture",
            token_provider="test",
            user_agent="fixture",
        )
    with pytest.raises(StoryWorkflowError):
        start(repository)


def test_dirty_unclaimed_existing_worktree_and_occupied_target_fail(repository):
    root, target, _, _ = repository
    target.mkdir()
    with pytest.raises(StoryWorkflowError, match="Target exists"):
        start(repository)
    target.rmdir()
    session.git(root, "worktree", "add", "-b", "feature/op-42-ship-lifecycle", str(target), "main")
    (target / "untracked.txt").write_text("Pending edits\n")
    with pytest.raises(StoryWorkflowError, match="Unclaimed worktree"):
        start(repository)
    assert (target / "untracked.txt").exists()


def test_clean_existing_worktree_is_reused_and_record_shared_from_linked_checkout(
    repository, monkeypatch
):
    root, target, _, _ = repository
    session.git(root, "worktree", "add", "-b", "feature/op-42-ship-lifecycle", str(target), "main")
    assert start(repository)["worktree"]["reused"]
    monkeypatch.chdir(target)
    assert session.session_command("get", 42, owner=None, apply=False)["session"][
        "repository"
    ] == str(root)


def test_interrupted_remote_activation_retains_claim_for_retry(repository, monkeypatch):
    _, target, _, _ = repository
    with monkeypatch.context() as patch:

        def fail(*_args, **kwargs):
            if not kwargs["apply"]:
                return {"status": "In progress", "dry_run": True}
            raise StoryWorkflowError("STATUS_UPDATE_FAILED", "Fixture interrupted")

        patch.setattr(workflow, "_status", fail)
        with pytest.raises(StoryWorkflowError, match="Fixture interrupted"):
            start(repository)
    assert target.exists()
    assert session.read_record(session.locations()[1], 42)["phase"] == "starting"
    assert start(repository)["session"]["phase"] == "implementation"


def test_checkpoint_and_handoff_retain_records_without_inventing_passes(
    repository, tmp_path, monkeypatch, capsys
):
    start(repository)
    record_file = tmp_path / "validation.json"
    record_file.write_text('{"status":"failed"}\n')
    preview = session.session_command(
        "checkpoint",
        42,
        owner="codex-fixture",
        apply=False,
        phase="review",
        next_action="Review the implementation",
        records=[record_file],
    )
    assert preview["session"]["phase"] == "review"
    assert session.read_record(session.locations()[1], 42)["phase"] == "implementation"
    session.session_command(
        "checkpoint",
        42,
        owner="codex-fixture",
        apply=True,
        phase="review",
        next_action="Review the implementation",
        records=[record_file],
    )
    # Local recovery does not need OpenProject or its credentials.
    monkeypatch.setattr(
        "wood.story.load_settings", lambda: pytest.fail("Local command used credentials")
    )
    assert main(["story", "session", "handoff", "42", "--apply", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    handoff = json.loads(Path(result["handoff_file"]).read_text())
    assert handoff["session"]["records"] == [str(record_file)]
    assert handoff["records_available"] == [True]
    assert handoff["delivery"].startswith("unavailable")
    assert handoff["next_action"] == "Review the implementation"
    record_file.unlink()
    assert session.session_command("get", 42, owner=None, apply=False)["records_available"] == [
        False
    ]


def test_release_is_explicit_and_preview_preserves_claim(repository):
    start(repository)
    session.session_command("release", 42, owner="codex-fixture", apply=False)
    assert session.read_record(session.locations()[1], 42)["state"] == "claimed"
    session.session_command("release", 42, owner="codex-fixture", apply=True)
    assert start(repository, owner="pi-fixture")["session"]["owner"] == "pi-fixture"


def test_process_race_has_exactly_one_claim_owner(repository):
    # Fork inherits only fixture transport. The children still use real separate
    # process locks and durable files; no mocked claim or Git operations.
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    event = context.Event()

    def worker(owner):
        event.wait(5)
        try:
            start(repository, owner=owner)
            queue.put((owner, "success"))
        except StoryWorkflowError as exc:
            queue.put((owner, exc.code))

    children = [
        context.Process(target=worker, args=(owner,)) for owner in ("pi-race", "codex-race")
    ]
    for child in children:
        child.start()
    event.set()
    for child in children:
        child.join(10)
        assert child.exitcode == 0
    results = [queue.get(timeout=5), queue.get(timeout=5)]
    winners = [owner for owner, state in results if state == "success"]
    assert len(winners) == 1
    assert all(state in {"success", "CLAIM_BUSY", "CLAIM_CONFLICT"} for _, state in results)
    assert session.read_record(session.locations()[1], 42)["owner"] == winners[0]


def test_record_corruption_fails_closed(repository):
    started = start(repository)
    path = Path(started["session_file"])
    path.write_text("{invalid")
    with pytest.raises(StoryWorkflowError, match="invalid"):
        start(repository)


def test_interrupted_git_preparation_has_a_readable_recovery_record(repository, monkeypatch):
    with monkeypatch.context() as patch:

        def fail(*_args):
            raise StoryWorkflowError("GIT_COMMAND_FAILED", "Fixture Git failure")

        patch.setattr(session, "prepare", fail)
        with pytest.raises(StoryWorkflowError, match="Fixture Git failure"):
            start(repository)
    recovered = session.session_command("get", 42, owner=None, apply=False)
    assert recovered["revision"] is None
    assert recovered["working_tree_clean"] is None
    assert recovered["worktree_state"].startswith("unavailable")
    assert start(repository)["session"]["phase"] == "implementation"


def test_explicit_selectors_override_only_their_context_and_preserve_record_references(
    repository, tmp_path
):
    root, target, client, _ = repository
    (root / "pyproject.toml").write_text(
        "[tool.wood.openproject]\nproject_id = 'bad'\ninitiative_id = 'bad'\n"
    )
    result = workflow.start_story(
        client,
        42,
        apply=True,
        owner="codex-fixture",
        worktree=target,
        project_id=3,
        initiative_id=10,
    )
    reference = tmp_path / "validation.json"
    reference.write_text("{}\n")
    session.session_command(
        "checkpoint",
        42,
        owner="codex-fixture",
        apply=True,
        phase="review",
        next_action="Review",
        records=[reference],
    )
    session.session_command("release", 42, owner="codex-fixture", apply=True)
    reclaimed = workflow.start_story(
        client, 42, apply=True, owner="pi-fixture", worktree=target, project_id=3, initiative_id=10
    )
    assert reclaimed["session"]["records"] == [str(reference)]
    assert reclaimed["session_file"] == result["session_file"]


def test_claim_guard_checks_owner_and_server_before_live_mutation(repository, monkeypatch, capsys):
    start(repository)
    monkeypatch.setattr(
        "wood.story.load_settings",
        lambda: OpenProjectSettings(
            base_url="https://other.invalid",
            token="fixture",
            token_provider="test",
            user_agent="fixture",
        ),
    )
    monkeypatch.setattr(
        workflow, "block_story", lambda *_args, **_kwargs: pytest.fail("Mismatched server mutated")
    )
    assert (
        main(
            [
                "story",
                "block",
                "42",
                "--owner",
                "codex-fixture",
                "--reason",
                "Fixture",
                "--apply",
                "--json",
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "CLAIM_CONFLICT"


def test_unsafe_paths_and_owner_rejected(repository):
    root, _, client, _ = repository
    for target in (Path("relative"), root / "nested", root / ".git/worktree"):
        with pytest.raises(StoryWorkflowError, match="worktree|Worktree"):
            workflow.start_story(client, 42, apply=False, owner="test", worktree=target)
    with pytest.raises(StoryWorkflowError, match="owner"):
        start(repository, owner="bad owner")

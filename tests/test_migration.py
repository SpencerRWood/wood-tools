"""Disposable real repositories; mocked remote authority, no live publication or enforcement."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from test_initialization import source_repository, specification_fixture

from wood import initialization, migration, operations
from wood.cli import main
from wood.workflow_documents import document, events, read_only, with_events
from wood.workflow_files import WorkflowFilesError
from wood_project.story import session
from wood_project.story.models import StoryWorkflowError


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root, baseline = source_repository(tmp_path)
    spec = specification_fixture()
    spec["repository"] = "fixture/existing"
    # Preserve a real tag and unrelated dirty source work through all operations.
    session.git(root, "tag", "v0.1.0")
    (root / ".github/workflows/release.yml").write_text(
        json.dumps(
            {
                "on": {"push": {"branches": ["main"]}},
                "permissions": {"contents": "write"},
                "jobs": {"release": {"runs-on": "ubuntu-latest", "steps": [{"run": "publish"}]}},
            }
        )
    )
    (root / ".github/release.toml").write_text(
        'version=1\n[python]\nversion="3.14"\n[validation]\nchecks=["pytest"]\n'
        "[release]\nsemantic_release=true\n[build]\npython_package=true\n"
        "[delivery]\nruntime_verification=false\n"
    )
    session.git(root, "add", ".")
    session.git(root, "commit", "-qm", "chore: existing publisher")
    baseline = session.git(root, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    session.git(root, "init", "--bare", "-q", str(remote))
    session.git(root, "remote", "add", "origin", str(remote))
    session.git(root, "push", "-q", "origin", "main", "--tags")
    (root / "user-work.txt").write_text("preserve this dirty work")
    state = {"refs": {"main": baseline}, "pulls": [], "rules": [], "archived": False}
    monkeypatch.setattr(operations, "_slug", lambda _: spec["repository"])

    def github(_root, path):
        if path == f"repos/{spec['repository']}":
            return {"archived": state["archived"], "default_branch": "main"}
        if "/branches?" in path:
            # Read the actual disposable remote after compare-and-create.
            listing = session.git(root, "ls-remote", "--heads", "origin")
            refs = {
                line.split()[1].removeprefix("refs/heads/"): line.split()[0]
                for line in listing.splitlines()
            }
            refs.update(state["refs"])
            return [{"name": key, "commit": {"sha": value}} for key, value in refs.items()]
        if "/pulls?" in path:
            return state["pulls"]
        if "/rules/branches/" in path:
            return state["rules"]
        raise AssertionError(path)

    monkeypatch.setattr(operations, "_gh", github)
    return root, baseline, spec, state, tmp_path / "migration"


def planned(repository):
    root, baseline, spec, _, destination = repository
    return migration.plan(
        root, baseline, "user-approved-wave-1", destination, "feature/op-558-adoption", spec
    )


def test_read_only_plan_and_safe_branch_initialization_preserve_history_and_work(repository):
    root, baseline, _, _, destination = repository
    before = session.git(root, "status", "--porcelain")
    record = planned(repository)
    assert not destination.exists()
    assert record["initialize_development"] and record["enforcement_verified"] is False
    assert "development" not in session.git(root, "ls-remote", "--heads", "origin")
    result = migration.apply(root, record, record["plan_hash"], True)
    assert result["state"] == "prepared-validation-required"
    assert not result["enforcement_verified"] and not result["publication_authorized"]
    assert session.git(root, "rev-parse", "HEAD") == baseline
    assert session.git(root, "rev-parse", "v0.1.0") != baseline
    assert session.git(root, "status", "--porcelain") == before
    assert f"{baseline}\trefs/heads/development" in session.git(
        root, "ls-remote", "--heads", "origin"
    )
    mapping = tomllib.loads((destination / "pyproject.toml").read_text())["tool"]["wood"]
    assert mapping["branching"]["release_id"] == 26
    assert mapping["openproject"] == {"project_id": 3, "initiative_id": 552}
    contract = tomllib.loads((destination / ".github/release.toml").read_text())
    assert contract["build"]["python_package"] is True
    assert contract["delivery"]["runtime_verification"] is False
    assert contract["release"]["tag_merged_commit"] is False
    assert migration.apply(root, record, record["plan_hash"], True)["state"] == result["state"]


def test_existing_development_is_never_moved(repository):
    root, baseline, _, state, _ = repository
    session.git(root, "push", "-q", "origin", "main:development")
    state["refs"]["development"] = baseline
    record = planned(repository)
    assert not record["initialize_development"]
    migration.apply(root, record, record["plan_hash"], False)
    assert f"{baseline}\trefs/heads/development" in session.git(
        root, "ls-remote", "--heads", "origin"
    )


@pytest.mark.parametrize(
    "mutation", ["hash", "payload", "remote", "claim", "destination", "approval"]
)
def test_stale_tampered_or_occupied_plans_preserve_existing_work(repository, mutation):
    root, baseline, _, state, destination = repository
    record = planned(repository)
    plan_hash = record["plan_hash"]
    if mutation == "hash":
        plan_hash = "wrong"
    elif mutation == "payload":
        record["contents"]["AGENTS.md"] = "unreviewed"
    elif mutation == "remote":
        state["refs"]["main"] = "f" * 40
    elif mutation == "claim":
        directory = migration.common(root) / "wood-stories"
        directory.mkdir()
        # Reuse the protocol's validated shape, not a forged partial claim.
        session.save(
            directory,
            999,
            {
                "schema_version": 1,
                "story_id": 999,
                "state": "claimed",
                "owner": "another-owner",
                "repository": str(root),
                "worktree": str(destination),
                "branch": "feature/other",
                "server": "https://example.test",
                "phase": "implementation",
                "next_action": "preserve",
                "records": [],
            },
        )
    elif mutation == "destination":
        destination.mkdir()
        (destination / "user-work").write_text("keep")
    with pytest.raises(WorkflowFilesError):
        migration.apply(root, record, plan_hash, mutation != "approval")
    assert session.git(root, "rev-parse", "HEAD") == baseline
    assert (root / "user-work.txt").read_text() == "preserve this dirty work"


def test_partial_file_failure_retries_without_overwriting_or_recreating_branches(
    repository, monkeypatch
):
    root, baseline, _, _, destination = repository
    record = planned(repository)
    original = Path.write_bytes
    failed = False

    def write(path, content):
        nonlocal failed
        if path.name == "AGENTS.md" and not failed:
            failed = True
            raise OSError("fixture interrupted write")
        return original(path, content)

    with monkeypatch.context() as context:
        context.setattr(Path, "write_bytes", write)
        with pytest.raises(StoryWorkflowError, match="Cannot read or write"):
            migration.apply(root, record, record["plan_hash"], True)
    assert destination.exists()
    assert (
        migration.apply(root, record, record["plan_hash"], True)["state"]
        == "prepared-validation-required"
    )
    assert session.git(root, "rev-parse", "HEAD") == baseline


def test_retry_preserves_user_edits_and_advanced_branch(repository):
    root, _, _, _, destination = repository
    record = planned(repository)
    migration.apply(root, record, record["plan_hash"], True)
    (destination / "AGENTS.md").write_text("user edit after preparation")
    with pytest.raises(WorkflowFilesError, match="Preserve changed"):
        migration.apply(root, record, record["plan_hash"], True)
    assert (destination / "AGENTS.md").read_text() == "user edit after preparation"


def test_reusable_interfaces_and_additional_read_only_workflows_survive(repository):
    root, baseline, spec, _, _ = repository
    files = initialization.baseline_files(root, baseline)
    reusable = {
        "on": {
            "workflow_call": {"inputs": {"custom": {"type": "string"}}},
            "push": {"branches": ["main"]},
        },
        "jobs": {"release": {"runs-on": "ubuntu-latest", "steps": [{"run": "publish"}]}},
    }
    files[".github/workflows/release.yml"] = json.dumps(reusable).encode()
    spec["workflow_prefix"] = "wood-"
    changes = migration.configuration(files, spec)
    preserved = document(changes[".github/workflows/release.yml"].decode())
    assert preserved["jobs"] == reusable["jobs"]
    assert preserved["on"] == {"workflow_call": reusable["on"]["workflow_call"]}
    assert ".github/workflows/wood-release.yml" in changes
    assert document(changes[".github/workflows/wood-release.yml"].decode())["on"] == {
        "push": {"branches": ["main"]}
    }
    assert (
        document(changes[".github/workflows/wood-release.yml"].decode())["jobs"]["context"][
            "permissions"
        ]["pull-requests"]
        == "read"
    )


def test_missing_contract_is_explicit_and_existing_outputs_cannot_be_replaced(repository):
    root, baseline, spec, _, _ = repository
    files = initialization.baseline_files(root, baseline)
    original = files.pop(".github/release.toml").decode()
    with pytest.raises(WorkflowFilesError, match="missing release contract"):
        migration.configuration(files, spec)
    assert ".github/release.toml" in migration.configuration(files, spec, original)
    files[".github/release.toml"] = original.encode()
    with pytest.raises(WorkflowFilesError, match="replace an existing"):
        migration.configuration(files, spec, original.replace("true", "false"))
    spec["images"] = [{"image_name": "unexpected", "dockerfile": "Dockerfile", "context": "."}]
    with pytest.raises(WorkflowFilesError, match="image publication outputs"):
        migration.configuration(files, spec)


def test_existing_manual_publisher_requires_explicit_contract_to_enable_automatic_release(
    repository,
):
    root, baseline, spec, _, _ = repository
    files = initialization.baseline_files(root, baseline)
    approved = files[".github/release.toml"].decode()
    files[".github/release.toml"] = approved.replace(
        "semantic_release=true", "semantic_release=false"
    ).encode()
    with pytest.raises(WorkflowFilesError, match="publication contract"):
        migration.configuration(files, spec)
    changed = migration.configuration(files, spec, approved)
    assert (
        tomllib.loads(changed[".github/release.toml"].decode())["release"]["semantic_release"]
        is True
    )


@pytest.mark.parametrize(
    "trigger", [{"push": None}, {"create": None}, {"workflow_run": {"workflows": ["Validate"]}}]
)
def test_initial_branch_creation_rejects_unsafe_automatic_baseline_events(trigger):
    files = {
        ".github/workflows/publish.yml": json.dumps(
            {
                "on": trigger,
                "permissions": {"contents": "write"},
                "jobs": {"publish": {"runs-on": "ubuntu-latest", "steps": [{"run": "publish"}]}},
            }
        ).encode()
    }
    assert not migration._development_safe(files)


def test_publication_mode_preserves_static_package_build_and_dynamic_modes(repository):
    root, baseline, spec, _, _ = repository
    files = initialization.baseline_files(root, baseline)
    changes = migration.configuration(files, spec)
    contract = tomllib.loads(changes[".github/release.toml"].decode())
    assert contract["build"]["python_package"] is True
    assert contract["release"]["tag_merged_commit"] is False
    source = tomllib.loads(files["pyproject.toml"].decode())
    source["project"].pop("version")
    dynamic_files = {**files, "pyproject.toml": initialization.toml(source).encode()}
    contract["build"]["python_package"] = False
    contract["release"].pop("tag_merged_commit")
    dynamic_files[".github/release.toml"] = initialization.toml(contract).encode()
    result = migration.configuration(dynamic_files, spec)
    assert (
        tomllib.loads(result[".github/release.toml"].decode())["release"]["tag_merged_commit"]
        is True
    )


def test_yaml_on_boolean_and_duplicate_key_handling():
    value = document(
        "on: [push, pull_request]\npermissions: {contents: read}\njobs:\n"
        "  check:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
    )
    assert events(value) == {"push": None, "pull_request": None}
    assert read_only(value)
    duplicate = "on: push\non: workflow_dispatch\njobs: {}\n"
    with pytest.raises(WorkflowFilesError, match="Duplicate"):
        document(duplicate)
    value["jobs"]["check"]["runs-on"] = "self-hosted"
    assert not read_only(value)
    value["jobs"]["check"]["runs-on"] = "ubuntu-latest"
    value["env"] = {"TOKEN": "${{ secrets.DEPLOY_TOKEN }}"}
    assert not read_only(value)


def test_reusable_event_edit_keeps_job_comments_and_input_types():
    original = (
        "name: Existing API\non:\n  push:\n    branches: [development]\n"
        "  workflow_call:\n    inputs:\n      enabled: {type: boolean, default: false}\n"
        "permissions: {contents: read}\njobs:\n  # Keep this public implementation\n"
        "  validate:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n"
    )
    trigger = {"workflow_call": events(document(original))["workflow_call"]}
    updated = with_events(original, trigger)
    assert document(updated)["on"] == trigger
    assert "# Keep this public implementation" in updated
    assert updated[updated.index("jobs:") :] == original[original.index("jobs:") :]


def test_release_branches_archives_and_unreconciled_main_fail_closed(repository):
    root, _, _, state, _ = repository
    state["archived"] = True
    with pytest.raises(WorkflowFilesError, match="Active main"):
        planned(repository)
    state["archived"] = False
    state["refs"]["release/v2.0.0"] = state["refs"]["main"]
    with pytest.raises(WorkflowFilesError, match="release branches"):
        planned(repository)
    state["refs"].pop("release/v2.0.0")
    state["refs"]["main"] = "f" * 40
    with pytest.raises(WorkflowFilesError, match="Fetch complete"):
        planned(repository)
    assert (root / "user-work.txt").is_file()


def test_migration_cli_returns_bounded_records_and_does_not_publish(
    repository, monkeypatch, capsys
):
    root, baseline, spec, _, destination = repository
    monkeypatch.chdir(root)
    source = root.parent / "spec.json"
    source.write_text(json.dumps(spec))
    assert main(["repo", "migrate", "assess", "--json"]) == 0
    assessed = json.loads(capsys.readouterr().out)
    assert assessed["mutation"] == "read-only" and "contents" not in assessed["data"]
    assert (
        main(
            [
                "repo",
                "migrate",
                "plan",
                "--baseline",
                baseline,
                "--baseline-approval",
                "user-wave-1",
                "--destination",
                str(destination),
                "--branch",
                "feature/op-558-migration",
                "--spec",
                str(source),
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["mutation"] == "preview" and not destination.exists()
    assert (
        main(
            [
                "repo",
                "migrate",
                "apply",
                "--plan",
                preview["data"]["record_file"],
                "--plan-hash",
                preview["data"]["plan_hash"],
                "--initialize-development",
                "--json",
            ]
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["mutation"] == "mutating" and not applied["data"]["publication_authorized"]

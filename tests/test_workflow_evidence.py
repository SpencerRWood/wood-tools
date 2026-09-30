from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_story_cli import _story, settings

from wood import evidence, operations, verification, workflow_files
from wood.cli import main
from wood_project.openproject import OpenProjectClient
from wood_project.story import workflow
from wood_project.story.models import StoryWorkflowError


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def delivered(tmp_path, monkeypatch):
    root = tmp_path / "wood-tools"
    root.mkdir()
    output = tmp_path / "outputs"
    (root / "pyproject.toml").write_text(f'[tool.wood.workflow]\noutput_directory = "{output}"\n')
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/validate.yml").write_text("name: Validate\n")
    (root / ".github/release.toml").write_text(
        'version = 1\n[python]\nversion = "3.14"\n[validation]\nchecks = ["ruff"]\n'
    )
    (root / "source.txt").write_text("before\n")
    git(root, "init", "--quiet", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.test")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "initial")
    git(root, "switch", "--quiet", "-c", "feature/op-410-implementation")
    (root / "source.txt").write_text("shipped\n")
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[:2] == ["uv", "run"]:
            return subprocess.CompletedProcess(args, 0, "checks passed\n", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(operations.subprocess, "run", run)
    record = operations.repo_validate(root)
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "ship Story")
    head = git(root, "rev-parse", "HEAD")
    git(root, "switch", "--quiet", "main")
    git(root, "merge", "--quiet", "--no-ff", "feature/op-410-implementation", "-m", "merge")
    merged = git(root, "rev-parse", "HEAD")
    story = _story(410, "In progress")
    state: dict[str, Any] = {
        "root": root,
        "record": record,
        "output": output,
        "story": story,
        "pr": {
            "number": 44,
            "merged": True,
            "merge_commit_sha": merged,
            "head": {"ref": "feature/op-410-implementation", "sha": head},
            "base": {"ref": "main", "repo": {"full_name": "owner/wood-tools"}},
        },
        "run": {
            "id": 123,
            "status": "completed",
            "conclusion": "success",
            "head_sha": head,
            "repository": {"full_name": "owner/wood-tools"},
            "path": ".github/workflows/validate.yml",
        },
        "remote_calls": [],
    }

    def gh(_root, path):
        state["remote_calls"].append(path)
        return state["pr"] if "/pulls/" in path else state["run"]

    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/wood-tools")
    monkeypatch.setattr(operations, "_gh", gh)
    monkeypatch.setattr(workflow, "_story", lambda *_args: story)
    monkeypatch.setattr("wood.story.load_settings", settings)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    monkeypatch.chdir(root)
    return state


def generate(state, *, apply=True, verification_path=None):
    return evidence.generate_evidence(
        OpenProjectClient(settings()),
        410,
        validation_path=Path(state["record"]["validation_file"]),
        pr_number=44,
        run_id=123,
        apply=apply,
        verification_path=verification_path,
    )


@pytest.fixture
def verified_delivery(delivered):
    root = delivered["root"]
    project = root / "pyproject.toml"
    project.write_text(
        project.read_text() + "\n[tool.wood.verify]\nversion = 1\nretry_safe = true\n"
        '[[tool.wood.verify.checks]]\nname = "runtime"\nargv = '
        + json.dumps([sys.executable, "-c", "pass"])
        + "\n"
    )
    git(root, "add", "pyproject.toml")
    git(root, "commit", "--quiet", "-m", "declare verification")
    revision = git(root, "rev-parse", "HEAD")
    delivered["pr"]["head"]["sha"] = revision
    delivered["pr"]["merge_commit_sha"] = revision
    delivered["run"]["head_sha"] = revision
    delivered["record"] = operations.repo_validate(root)
    delivered["verification"] = verification.repo_verify(root)
    return delivered


def test_declared_verification_required_by_story_source(verified_delivery):
    with pytest.raises(StoryWorkflowError, match="provide --verification"):
        generate(verified_delivery)


def test_verification_consumed_and_reverified_without_execution(verified_delivery, monkeypatch):
    path = Path(verified_delivery["verification"]["verification_file"])
    result = generate(verified_delivery, verification_path=path)
    assert "matching_repository_verification" in result["required_criteria"]
    assert (
        "Repository verification required checks passed: runtime"
        in Path(result["update_file"]).read_text()
    )
    record = workflow_files.read_json(Path(result["evidence_file"]))

    def execute(*_args):
        pytest.fail("Evidence consumption must not execute checks")

    monkeypatch.setattr(verification, "repo_verify", execute)
    evidence.verify_generated_evidence(OpenProjectClient(settings()), 410, record)
    Path(verified_delivery["verification"]["checks"][0]["log_path"]).write_text("tampered")
    with pytest.raises(workflow_files.WorkflowFilesError, match="Verification record or logs"):
        evidence.verify_generated_evidence(OpenProjectClient(settings()), 410, record)


def test_verification_cli_input_and_failure_blocks_evidence(verified_delivery, capsys):
    path = verified_delivery["verification"]["verification_file"]
    args = [
        "story",
        "evidence",
        "410",
        "--validation",
        verified_delivery["record"]["validation_file"],
        "--verification",
        path,
        "--pr",
        "44",
        "--ci-run",
        "123",
        "--json",
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["data"]["verification"]["path"] == path
    record = workflow_files.read_json(Path(path))
    record["passed"] = False
    workflow_files.write_json(Path(path), record)
    assert main(args) == 2
    assert (
        json.loads(capsys.readouterr().out)["errors"][0]["code"] == "VERIFICATION_EVIDENCE_INVALID"
    )


def test_later_contract_removal_does_not_bypass_source_requirements(verified_delivery):
    root = verified_delivery["root"]
    project = root / "pyproject.toml"
    project.write_text(project.read_text().split("[tool.wood.verify]", 1)[0])
    git(root, "add", "pyproject.toml")
    git(root, "commit", "--quiet", "-m", "remove contract later")
    with pytest.raises(StoryWorkflowError, match="provide --verification"):
        generate(verified_delivery)


def test_verification_cannot_attach_to_source_without_contract(delivered, tmp_path):
    with pytest.raises(StoryWorkflowError, match="does not declare"):
        generate(delivered, verification_path=tmp_path / "verification.json")


def test_generation_binds_precommit_validation_to_merged_files(delivered):
    result = generate(delivered)
    generated = workflow_files.read_json(Path(result["evidence_file"]))
    assert Path(result["update_file"]).read_text().startswith("Implementation update (WP-410)")
    assert generated["pull_request"]["source_sha"] == delivered["pr"]["head"]["sha"]
    assert generated["repository_checks"] == [{"name": "ruff", "status": "passed"}]
    assert generated["validation_sha256"]
    assert Path(result["evidence_file"]).is_relative_to(delivered["output"])
    assert Path(result["log_dir"]).is_relative_to(delivered["output"])
    evidence.verify_generated_evidence(OpenProjectClient(settings()), 410, generated)
    evidence.verify_generated_evidence(OpenProjectClient(settings()), 410, generated)


def test_preview_does_not_create_generated_files(delivered):
    before = set(delivered["output"].rglob("*"))
    result = generate(delivered, apply=False)
    assert result["dry_run"] is True
    assert "evidence_file" not in result
    assert set(delivered["output"].rglob("*")) == before


def test_delivery_snapshot_is_separate_and_hash_bound(delivered):
    result = generate(delivered)
    record = workflow_files.read_json(Path(result["evidence_file"]))
    snapshot = Path(record["delivery_snapshot"]["path"])
    assert workflow_files.read_json(snapshot) == result["delivery"]
    assert "runtime_verification" in result["delivery"]
    snapshot.write_text("{}")
    with pytest.raises(StoryWorkflowError, match="Delivery snapshot changed"):
        evidence.verify_generated_evidence(OpenProjectClient(settings()), 410, record)


def test_completion_requires_posted_matching_summary(delivered, monkeypatch, capsys):
    result = generate(delivered)
    monkeypatch.setattr("wood_project.story.activity._activities", lambda *_args: [])
    assert main(["story", "complete", "410", "--evidence", result["evidence_file"], "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "VALIDATION_REQUIRED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("conclusion", None),
        ("head_sha", "a" * 40),
        ("repository", {"full_name": "owner/other"}),
        ("path", ".github/workflows/release.yml"),
    ],
)
def test_ci_must_be_current_passed_validation(delivered, field, value):
    delivered["run"][field] = value
    with pytest.raises(StoryWorkflowError, match="CI must"):
        generate(delivered)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("merged", False),
        ("head", {"ref": "feature/op-411-unrelated", "sha": "a" * 40}),
        ("base", {"ref": "other", "repo": {"full_name": "owner/wood-tools"}}),
    ],
)
def test_pr_must_be_merged_and_bound_to_story(delivered, field, value):
    delivered["pr"][field] = value
    with pytest.raises(StoryWorkflowError, match="PR must"):
        generate(delivered)


def test_wrong_story_repository_is_rejected(delivered):
    delivered["story"]["description"]["raw"] = "Primary Repository: another-repo"
    with pytest.raises(StoryWorkflowError, match="Primary Repository"):
        generate(delivered)


@pytest.mark.parametrize(
    "change", ["failed", "missing_check", "missing_log", "stale", "other_repo"]
)
def test_invalid_validation_cannot_become_passing_evidence(delivered, change):
    path = Path(delivered["record"]["validation_file"])
    record = workflow_files.read_json(path)
    if change == "failed":
        record["checks"][0]["state"] = "failed"
    elif change == "missing_check":
        record["checks"] = []
    elif change == "missing_log":
        Path(record["checks"][0]["log_path"]).unlink()
    elif change == "stale":
        record["source_fingerprint"] = "stale"
    else:
        record["repository_root"] = "/another/repository"
    workflow_files.write_json(path, record)
    with pytest.raises(StoryWorkflowError):
        generate(delivered)


def test_changed_sources_and_unmerged_checkout_are_rejected(delivered):
    (delivered["root"] / "source.txt").write_text("changed")
    with pytest.raises(StoryWorkflowError, match="clean checkout"):
        generate(delivered)
    git(delivered["root"], "restore", "source.txt")
    delivered["pr"]["merge_commit_sha"] = "b" * 40
    with pytest.raises(StoryWorkflowError, match="does not contain"):
        generate(delivered)


@pytest.mark.parametrize("change", ["story", "check", "update", "missing", "ci_changed"])
def test_consumption_reverifies_generated_inputs(delivered, change):
    result = generate(delivered)
    value = workflow_files.read_json(Path(result["evidence_file"]))
    if change == "story":
        value["story_id"] = 411
    elif change == "check":
        value["repository_checks"][0]["name"] = "invented"
    elif change == "update":
        Path(result["update_file"]).write_text("changed")
    elif change == "missing":
        Path(value["validation_file"]).unlink()
    else:
        delivered["run"]["status"] = "in_progress"
    with pytest.raises((StoryWorkflowError, workflow_files.WorkflowFilesError)):
        evidence.verify_generated_evidence(OpenProjectClient(settings()), 410, value)


def test_cli_generation_activity_and_complete_consume_outputs(delivered, monkeypatch, capsys):
    args = [
        "story",
        "evidence",
        "410",
        "--validation",
        delivered["record"]["validation_file"],
        "--pr",
        "44",
        "--ci-run",
        "123",
        "--apply",
        "--json",
    ]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "secret-token" not in output
    assert len(output) < 3000
    path = json.loads(output)["data"]["evidence_file"]
    activity_calls = []
    monkeypatch.setattr(
        "wood.story.add_activity",
        lambda _c, _id, comment, *, apply: (
            activity_calls.append((comment, apply)) or {"dry_run": not apply}
        ),
    )
    assert main(["story", "activity", "add", "410", "--evidence", path, "--json"]) == 0
    capsys.readouterr()
    assert activity_calls[0][0].startswith("Implementation update (WP-410)")
    assert activity_calls[0][1] is False
    calls = []
    monkeypatch.setattr("wood.story.require_summary", lambda *_args: None)
    monkeypatch.setattr(
        workflow,
        "complete_story",
        lambda _c, _id, *, evidence, apply: (
            calls.append((evidence, apply)) or {"status": {"dry_run": not apply}}
        ),
    )
    assert main(["story", "complete", "410", "--evidence", path, "--json"]) == 0
    capsys.readouterr()
    assert calls[0][0]["story_id"] == 410
    assert calls[0][1] is False
    delivered["run"]["conclusion"] = "failure"
    assert main(["story", "complete", "410", "--evidence", path, "--apply", "--json"]) == 2
    assert len(calls) == 1
    capsys.readouterr()


def test_missing_generated_file_is_structured(delivered, capsys):
    assert main(["story", "complete", "410", "--evidence", "/missing/evidence.json", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "INVALID_EVIDENCE"


def test_output_configuration_defaults_and_overrides(tmp_path):
    assert workflow_files.output_directory(tmp_path) == Path("/private/tmp")
    project = tmp_path / "pyproject.toml"
    project.write_text('[tool.wood.workflow]\noutput_directory = "generated"\n')
    assert workflow_files.output_directory(tmp_path) == tmp_path / "generated"
    first = workflow_files.run_directory(tmp_path, "run-")
    second = workflow_files.run_directory(tmp_path, "run-")
    assert first != second and first.parent == tmp_path / "generated"
    project.write_text(f'[tool.wood.workflow]\noutput_directory = "{tmp_path / "absolute"}"\n')
    assert workflow_files.run_directory(tmp_path, "run-").parent == tmp_path / "absolute"


@pytest.mark.parametrize(
    "content",
    [
        "bad = [",
        "[tool.wood.workflow]\noutput_directory = 3",
        '[tool.wood.workflow]\noutput_directory = ""',
        '[tool.wood]\nworkflow = "bad"',
        '[tool.wood.workflow]\noutput_directory = "."',
    ],
)
def test_malformed_output_configuration_is_rejected(tmp_path, content):
    (tmp_path / "pyproject.toml").write_text(content)
    with pytest.raises(workflow_files.WorkflowFilesError, match="Output|output|workflow"):
        workflow_files.output_directory(tmp_path)


def test_unwritable_output_has_actionable_error(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.wood.workflow]\noutput_directory = "{tmp_path / "out"}"\n'
    )
    monkeypatch.setattr(
        workflow_files.tempfile,
        "mkdtemp",
        lambda **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(workflow_files.WorkflowFilesError) as error:
        workflow_files.run_directory(tmp_path, "run-")
    assert error.value.code == "WORKFLOW_OUTPUT_UNWRITABLE"

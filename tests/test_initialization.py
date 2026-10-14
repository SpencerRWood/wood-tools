"""Real disposable Git initializations with no remote repository mutations."""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib

import pytest

from wood import initialization as init
from wood.cli import main
from wood.workflow_files import WorkflowFilesError
from wood_project.story import branching, session


def specification_fixture():
    return {
        "repository": "fixture/new-repository",
        "project_id": 3,
        "initiative_id": 552,
        "release_id": 26,
        "scope_reference": "https://example.test/approved-scope",
        "workflow_revision": "a" * 40,
        "wood_revision": "b" * 40,
        "integration_command": "uv run --frozen pytest tests/integration",
        "security_command": "uv run --frozen pip-audit",
        "checks_integration_id": 15368,
        "author_name": "Fixture",
        "author_email": "fixture@example.test",
    }


@pytest.fixture
def spec():
    return specification_fixture()


def source_repository(tmp_path, layout="library"):
    source = tmp_path / "source"
    source.mkdir()
    session.git(source, "init", "-q", "-b", "main")
    session.git(source, "config", "user.name", "Fixture")
    session.git(source, "config", "user.email", "fixture@example.test")
    directory = source / "backend" if layout == "react" else source
    directory.mkdir(exist_ok=True)
    (directory / "pyproject.toml").write_text(
        '[project]\nname = "prepared-consumer"\nversion = "0.1.0"\n'
        'authors = [{ name = "Fixture" }]\n'
        '[tool.semantic_release.branches.main]\nmatch = "^main$"\n'
    )
    if layout == "react":
        (source / "frontend").mkdir()
        (source / "frontend/package.json").write_text('{"name":"prepared-consumer"}')
    github = source / ".github"
    (github / "workflows").mkdir(parents=True)
    contract = 'version = 1\n[python]\nversion = "3.14"\n'
    if layout == "react":
        contract += '[project]\nworking_directory = "backend"\n'
        contract += '[node]\ndirectory = "frontend"\nversion = "24"\nchecks = ["test"]\n'
    contract += '[validation]\nchecks = ["pytest"]\n[release]\nsemantic_release = true\n'
    contract += '[container]\npublish = true\nimage_name = "template-image"\n'
    (github / "release.toml").write_text(contract)
    (github / "workflows/release.yml").write_text("legacy template image and deployment identities")
    (source / "AGENTS.md").write_text("# Source guidance\n\nUse the prepared consumer package.\n")
    (source / "Dockerfile").write_text("FROM scratch\n")
    (source / "worker.Dockerfile").write_text("FROM scratch\n")
    (source / "script.sh").write_text("#!/bin/sh\nexit 0\n")
    (source / "script.sh").chmod(0o755)
    session.git(source, "add", ".")
    session.git(source, "commit", "-q", "-m", "chore: prepared source baseline")
    return source, session.git(source, "rev-parse", "HEAD")


@pytest.mark.parametrize("layout", ["react", "service", "library"])
def test_universal_baseline_and_nested_root_metadata(tmp_path, spec, layout):
    source, baseline = source_repository(tmp_path, layout)
    spec["images"] = (
        [{"image_name": "prepared-api", "dockerfile": "Dockerfile", "context": "."}]
        if layout in {"react", "service"}
        else []
    )
    if layout == "react":
        spec["images"].append(
            {"image_name": "prepared-ui", "dockerfile": "worker.Dockerfile", "context": "frontend"}
        )
    destination = tmp_path / "new-repository"
    record, files = init.plan(source, baseline, "approval:fixture", destination, spec)
    assert not destination.exists()
    assert session.git(source, "rev-parse", "HEAD") == baseline
    assert session.git(source, "status", "--porcelain") == ""
    assert "template-image" not in files[".github/release.toml"].decode()
    assert (
        files[".github/workflows/release.yml"] != b"legacy template image and deployment identities"
    )
    result = init.apply(record, files, record["plan_hash"])
    revision = result["baseline_revision"]
    assert session.git(destination, "rev-parse", "main") == revision
    assert session.git(destination, "rev-parse", "development") == revision
    assert session.git(destination, "branch", "--show-current") == "development"
    assert session.git(destination, "status", "--porcelain") == ""
    assert session.git(destination, "remote", "get-url", "origin") == (
        "https://github.com/fixture/new-repository.git"
    )
    assert (destination / "script.sh").stat().st_mode & 0o111
    assert baseline in session.git(destination, "show", "-s", "--format=%B", "main")
    mapping = branching.configuration(destination)
    assert mapping["strategy"] == "integration" and mapping["base_branch"] == "development"
    assert mapping["release_id"] == 26 and not result["enforcement_verified"]
    assert (
        json.loads((destination / init.MANIFEST).read_text())["baseline_approval"]
        == "approval:fixture"
    )
    if layout == "react":
        assert tomllib.loads((destination / "backend/pyproject.toml").read_text())["project"][
            "name"
        ] == ("prepared-consumer")
        assert "project" not in tomllib.loads((destination / "pyproject.toml").read_text())
    assert init.apply(record, files, record["plan_hash"])["reused"]


@pytest.mark.parametrize("occupied", ["directory", "repository", "symlink", "dirty", "advanced"])
def test_preserves_existing_destination(tmp_path, spec, occupied):
    source, baseline = source_repository(tmp_path)
    target = tmp_path / "target"
    record, files = init.plan(source, baseline, "approval", target, spec)
    if occupied in {"dirty", "advanced"}:
        init.apply(record, files, record["plan_hash"])
        (target / "unrelated.txt").write_text("preserve")
        if occupied == "advanced":
            session.git(target, "add", ".")
            session.git(
                target,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-q",
                "-m",
                "feat: existing work",
            )
    elif occupied == "symlink":
        target.symlink_to(source, target_is_directory=True)
    else:
        target.mkdir()
        (target / "unrelated.txt").write_text("preserve")
        if occupied == "repository":
            session.git(target, "init", "-q", "-b", "main")
    with pytest.raises(WorkflowFilesError):
        init.apply(record, files, record["plan_hash"])
    if occupied != "symlink":
        assert (target / "unrelated.txt").read_text() == "preserve"


def test_stale_plan_and_failure_leave_source_and_destination_safe(tmp_path, spec, monkeypatch):
    source, baseline = source_repository(tmp_path)
    target = tmp_path / "target"
    record, files = init.plan(source, baseline, "approval", target, spec)
    with pytest.raises(WorkflowFilesError, match="stale"):
        init.apply(record, files, "wrong")
    real_git = session.git

    def fail(root, *args):
        if args[:1] == ("branch",):
            raise OSError("fixture branch failure")
        return real_git(root, *args)

    with monkeypatch.context() as m:
        m.setattr(session, "git", fail)
        with pytest.raises(OSError):
            init.apply(record, files, record["plan_hash"])
    assert not target.exists() and not list(tmp_path.glob(".wood-init-*"))
    assert session.git(source, "rev-parse", "HEAD") == baseline
    assert init.apply(record, files, record["plan_hash"])["baseline_revision"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("workflow_revision", "v1"),
        ("wood_revision", "main"),
        ("release_id", True),
        ("repository", "other;command"),
        ("scope_reference", ""),
        ("required_approvals", -1),
        ("integration_command", "line\ncommand"),
        ("images", [{"image_name": "wrong"}]),
        ("images", [{"image_name": "UPPER", "dockerfile": "Dockerfile", "context": "."}]),
        ("images", [{"image_name": "safe", "dockerfile": "../Dockerfile", "context": "."}]),
        ("deployment", {"inputs": {}, "automatic": True, "approval_reference": ""}),
        ("legacy_profile", "application-trunk"),
    ],
)
def test_invalid_specification_fails_closed(spec, key, value):
    with pytest.raises((WorkflowFilesError, TypeError)):
        init.specification({**spec, key: value})


@pytest.mark.parametrize(
    "failure", ["secret", "link", "contract", "layout", "no-approval", "image"]
)
def test_invalid_baseline_never_initializes(tmp_path, spec, failure):
    source, baseline = source_repository(tmp_path)
    if failure == "secret":
        (source / ".env").write_text("fixture-value")
    elif failure == "link":
        (source / "link").symlink_to("/tmp")
    elif failure == "contract":
        (source / ".github/release.toml").unlink()
    elif failure == "layout":
        (source / "pyproject.toml").unlink()
    elif failure == "image":
        spec["images"] = [{"image_name": "new-name", "dockerfile": "missing", "context": "."}]
    session.git(source, "add", "--all")
    session.git(source, "commit", "-q", "--allow-empty", "-m", "chore: fixture error")
    baseline = session.git(source, "rev-parse", "HEAD")
    with pytest.raises(WorkflowFilesError):
        init.plan(
            source, baseline, "" if failure == "no-approval" else "approval", tmp_path / "new", spec
        )
    assert not (tmp_path / "new").exists()


def deployment(automatic):
    return {
        "approval_reference": "approved:standing-deployment",
        "automatic": automatic,
        "inputs": {
            "inventory_path": "ansible/inventory/dev",
            "playbook_path": "ansible/dev.yml",
            "environment_name": "fixture-dev",
            "concurrency_group": "fixture-deploy",
            "health_check_command": "bash checks/health.sh",
        },
    }


@pytest.mark.parametrize("images,deploy", [(0, None), (1, None), (2, True), (0, False)])
def test_generated_actions_yaml_and_output_contracts(tmp_path, spec, images, deploy):
    spec["images"] = [
        {"image_name": f"new-image-{i}", "dockerfile": "Dockerfile", "context": "."}
        for i in range(images)
    ]
    if deploy is not None:
        spec["deployment"] = deployment(deploy)
    spec = init.specification(spec)
    documents = init.workflows(spec)
    parsed = {name: json.loads(text) for name, text in documents.items()}
    assert parsed["validate.yml"]["on"]["push"]["branches"] == ["development"]
    assert parsed["release.yml"]["on"] == {"push": {"branches": ["main"]}}
    assert parsed["validate.yml"]["permissions"] == {"contents": "read"}
    release = parsed["release.yml"]["jobs"]["release"]
    assert "github.ref == 'refs/heads/main'" in release["if"] and release["needs"] == "context"
    assert (
        "release-containers.yml" in release["uses"] if images else "release.yml" in release["uses"]
    )
    if images:
        assert len(json.loads(release["with"]["images"])) == images
    else:
        assert release["with"]["release_integration"] is True
    assert release["with"]["hotfix_incident"] == "${{ needs.context.outputs.incident }}"
    for workflow in parsed.values():
        for job in workflow["jobs"].values():
            if "uses" in job:
                assert job["uses"].endswith("@" + spec["workflow_revision"])
                assert job["with"]["workflow_revision"] == spec["workflow_revision"]
    if deploy is True:
        job = parsed["release.yml"]["jobs"]["deploy"]
        assert job["needs"] == ["release", "context"]
        assert "outputs.released == 'true'" in job["if"]
    elif deploy is False:
        assert "deploy" not in parsed["release.yml"]["jobs"]
        assert "workflow_dispatch" in parsed["deploy.yml"]["on"]
    paths = []
    for name, text in documents.items():
        path = tmp_path / name
        path.write_text(text)
        paths.append(str(path))
    if shutil.which("actionlint") is None:
        pytest.skip("Actionlint unavailable; JSON/YAML and workflow contract assertions passed.")
    result = subprocess.run(
        ["actionlint", "-shellcheck=", "-pyflakes=", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_init_cli_preview_and_apply(tmp_path, spec, monkeypatch, capsys):
    source, baseline = source_repository(tmp_path)
    with (source / "pyproject.toml").open("a") as stream:
        stream.write(f'[tool.wood.workflow]\noutput_directory = "{tmp_path / "workflow-output"}"\n')
    monkeypatch.chdir(source)
    specfile = tmp_path / "spec.json"
    specfile.write_text(json.dumps(spec))
    args = [
        "repo",
        "init",
        "--source",
        str(source),
        "--baseline",
        baseline,
        "--baseline-approval",
        "fixture-approval",
        "--spec",
        str(specfile),
        "--destination",
        str(tmp_path / "destination"),
        "--json",
    ]
    assert main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["mutation"] == "preview" and not (tmp_path / "destination").exists()
    assert main([*args, "--apply", "--plan-hash", value["data"]["plan_hash"]]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["mutation"] == "mutating" and not value["data"]["enforcement_verified"]
    specfile.write_text('{"unknown": "invalid"}')
    assert main(args) == 2
    assert "INITIALIZATION_INVALID" in capsys.readouterr().out


def test_preview_changes_bind_reviewed_plan(tmp_path, spec):
    source, baseline = source_repository(tmp_path)
    args = source, baseline, "approval", tmp_path / "destination"
    original, _ = init.plan(*args, spec)
    changed, _ = init.plan(*args, {**spec, "release_id": 27})
    assert changed["plan_hash"] != original["plan_hash"]
    assert init.toml({"nested": {"list": [True, 1, {"key": "value"}]}})
    with pytest.raises(WorkflowFilesError):
        init.toml({"unsupported": None})


def test_tampering_and_destination_creation_race_fail_safely(tmp_path, spec, monkeypatch):
    source, baseline = source_repository(tmp_path)
    destination = tmp_path / "destination"
    record, files = init.plan(source, baseline, "approval", destination, spec)
    with pytest.raises(WorkflowFilesError, match="files changed"):
        init.apply(record, {**files, "unreviewed": b"change"}, record["plan_hash"])
    with pytest.raises(WorkflowFilesError, match="record changed"):
        init.apply({**record, "baseline_approval": "different"}, files, record["plan_hash"])
    real_git = session.git

    def concurrent_destination(root, *args):
        result = real_git(root, *args)
        if args[:2] == ("remote", "add"):
            destination.mkdir()
            (destination / "unrelated").write_text("preserve")
        return result

    monkeypatch.setattr(session, "git", concurrent_destination)
    with pytest.raises(FileExistsError):
        init.apply(record, files, record["plan_hash"])
    assert (destination / "unrelated").read_text() == "preserve"
    assert not list(tmp_path.glob(".wood-init-*"))


def test_uncommitted_source_and_unrelated_branches_are_preserved(tmp_path, spec):
    source, baseline = source_repository(tmp_path)
    session.git(source, "branch", "feature/unrelated")
    (source / "uncommitted").write_text("existing work")
    record, files = init.plan(source, baseline, "approval", tmp_path / "destination", spec)
    assert "uncommitted" not in files
    assert "feature/unrelated" in session.git(source, "branch")
    assert (source / "uncommitted").read_text() == "existing work"
    assert init.apply(record, files, record["plan_hash"])["baseline_revision"]


def test_archive_timeout_is_bounded_and_never_creates_destination(tmp_path, spec, monkeypatch):
    source, baseline = source_repository(tmp_path)
    real_run = subprocess.run

    def timeout(argv, *args, **kwargs):
        if argv[:2] == ["git", "archive"]:
            raise subprocess.TimeoutExpired(argv, 30)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(WorkflowFilesError, match="baseline archive"):
        init.plan(source, baseline, "approval", tmp_path / "destination", spec)
    assert not (tmp_path / "destination").exists()

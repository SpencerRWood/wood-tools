from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wood import operations
from wood.cli import main
from wood.output import EXIT_CODES


def repository(tmp_path: Path, *, deployed: bool = False, checks: str = '"ruff"') -> Path:
    github = tmp_path / ".github"
    workflows = github / "workflows"
    workflows.mkdir(parents=True)
    (github / "release.toml").write_text(
        'version = 1\n[python]\nversion = "3.14"\n'
        f"[validation]\nchecks = [{checks}]\n"
        "[release]\nsemantic_release = true\n"
    )
    (workflows / "validate.yml").write_text("name: Validate\n")
    (workflows / "release.yml").write_text(
        "uses: SpencerRWood/workflows/.github/workflows/promote-container-to-dev.yml@v2\n"
        if deployed
        else "name: Release\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.wood.workflow]\noutput_directory = "{tmp_path.parent / "workflow-output"}"\n'
    )
    return tmp_path


def test_library_and_service_repository_info(tmp_path: Path) -> None:
    library = repository(tmp_path / "library")
    service = repository(tmp_path / "service", deployed=True)
    assert operations.repo_info(library)["type"] == "python-library"
    assert operations.repo_info(service)["type"] == "python-service"
    assert operations.repo_info(library)["deployment"] == {
        "applicable": False,
        "evidence": None,
        "requirements": {
            "container_image_digest": False,
            "infrastructure_promotion": False,
            "runtime_verification": False,
        },
    }
    assert operations.repo_info(service)["deployment"] == {
        "applicable": True,
        "evidence": "GitHub deployments",
        "requirements": {
            "container_image_digest": True,
            "infrastructure_promotion": True,
            "runtime_verification": True,
        },
    }
    assert operations.deploy_status(library)[0] == "not_applicable"
    standards = operations.repo_standards(library)
    assert standards["conformant"] is True
    assert any(c["requirement"] == "optional" for c in standards["checks"])


def test_configuration_delivery_requirements_are_explicit(tmp_path: Path) -> None:
    root = repository(tmp_path, deployed=True)
    with (root / ".github/release.toml").open("a") as stream:
        stream.write("\n[delivery]\ncontainer_image = false\ninfrastructure_promotion = false\n")
    assert operations.repo_info(root)["deployment"]["requirements"] == {  # type: ignore[index]
        "container_image_digest": False,
        "infrastructure_promotion": False,
        "runtime_verification": True,
    }


@pytest.mark.parametrize(
    "section",
    [
        '[delivery]\ncontainer_image = "false"',
        "[delivery]\ncontainer_image = 0",
        "[delivery]\nunknown = false",
        "[delivery]\nruntime_verification = []",
        "delivery = false",
    ],
)
def test_invalid_delivery_policy_fails_closed(tmp_path: Path, section: str) -> None:
    root = repository(tmp_path, deployed=True)
    path = root / ".github/release.toml"
    # A scalar section belongs at the document root rather than under [release].
    path.write_text(
        section + "\n" + path.read_text()
        if section == "delivery = false"
        else path.read_text() + "\n" + section + "\n"
    )
    with pytest.raises(operations.OperationsError) as caught:
        operations.repo_info(root)
    assert caught.value.code == "CONTRACT_INVALID"
    assert operations.repo_standards(root)["conformant"] is False


def test_new_checkout_policy_cannot_waive_merged_revision_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path, deployed=True)
    path = root / ".github/release.toml"
    original = path.read_text()
    path.write_text(original + "\n[delivery]\ncontainer_image = false\n")
    sha = "a" * 40
    calls = []

    def git(args, _root):
        calls.append(args)
        if args[1] == "ls-tree":
            output = ".github/workflows/release.yml\n"
        elif args[-1].endswith("release.yml"):
            output = "uses: SpencerRWood/workflows/.github/workflows/deploy-ansible.yml@v1\n"
        else:
            output = original
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(operations, "_run", git)
    # Removing deployment from the current checkout must not waive historical evidence.
    (root / ".github/workflows/release.yml").write_text("name: Release\n")
    assert (
        operations.repo_info(root, revision=sha)["deployment"]["requirements"][  # type: ignore[index]
            "container_image_digest"
        ]
        is True
    )
    assert calls == [
        ["git", "show", f"{sha}:.github/release.toml"],
        ["git", "ls-tree", "-r", "--name-only", sha, "--", ".github/workflows"],
        ["git", "show", f"{sha}:.github/workflows/release.yml"],
    ]


def test_missing_revision_workflows_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path, deployed=True)
    contract = (root / ".github/release.toml").read_text()
    monkeypatch.setattr(
        operations,
        "_run",
        lambda args, _root: subprocess.CompletedProcess(
            args, 1 if args[1] == "ls-tree" else 0, contract, ""
        ),
    )
    with pytest.raises(operations.OperationsError) as caught:
        operations.repo_info(root, revision="a" * 40)
    assert caught.value.code == "WORKFLOWS_UNAVAILABLE"


@pytest.mark.parametrize("revision", ["main", "--help", ""])
def test_contract_revision_must_be_full_commit_sha(tmp_path: Path, revision: str) -> None:
    root = repository(tmp_path)
    with pytest.raises(operations.OperationsError) as caught:
        operations.repo_info(root, revision=revision)
    assert caught.value.code == "CONTRACT_INVALID"


def test_missing_revision_contract_is_not_current_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    monkeypatch.setattr(
        operations, "_run", lambda *args: subprocess.CompletedProcess([], 1, "", "")
    )
    with pytest.raises(operations.OperationsError) as caught:
        operations.repo_info(root, revision="a" * 40)
    assert caught.value.code == "CONTRACT_MISSING"


def test_validation_runs_in_disposable_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path / "repo")
    (root / "source.txt").write_text("original")
    ignored_cache = root / "src/wood_project/commands/__pycache__"
    ignored_cache.mkdir(parents=True)
    (ignored_cache / "old.pyc").write_bytes(b"ignored")
    assert operations._copy_ignores(str(root), [".env", ".env.local", ".env.example"]) == {
        ".env",
        ".env.local",
    }
    real_run = operations.subprocess.run

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["uv", "run"]:
            assert not Path(str(kwargs["cwd"]), "src/wood_project/commands").exists()
            Path(str(kwargs["cwd"]), "source.txt").write_text("modified")
            return subprocess.CompletedProcess(args, 0, "ok", "")
        return real_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(operations.subprocess, "run", run)
    result = operations.repo_validate(root)
    assert result["passed"] is True
    assert (root / "source.txt").read_text() == "original"
    assert Path(str(result["checks"][0]["log_path"])).read_text() == "ok"  # type: ignore[index]


def test_ci_failed_job_and_stale_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path)
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(
        operations,
        "_gh",
        lambda _root, path: (
            {
                "workflow_runs": [
                    {
                        "id": 12,
                        "head_sha": "old",
                        "conclusion": "failure",
                        "html_url": "https://example/run",
                    }
                ]
            }
            if "workflows/validate.yml" in path
            else {
                "jobs": [
                    {
                        "name": "test",
                        "conclusion": "failure",
                        "html_url": "https://example/job",
                        "steps": [{"name": "pytest", "conclusion": "failure"}],
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        operations,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "new\n", ""),
    )
    status, detail = operations.ci_status(root)
    assert status == "stale"
    assert detail["state"] == "stale"
    status, failures = operations.ci_failures(root)
    assert status == "stale"
    assert failures["failures"][0]["steps"] == ["pytest"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("deployment_state", "ref", "expected"),
    [
        ("success", "v1.0.0", "success"),
        ("failure", "v1.0.0", "error"),
        ("success", "v0.9.0", "stale"),
        ("failure", "v0.9.0", "error"),
    ],
)
def test_deployment_evidence_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deployment_state: str,
    ref: str,
    expected: str,
) -> None:
    root = repository(tmp_path, deployed=True)
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/repo")

    def gh(_root: Path, path: str) -> object:
        if path.endswith("/deployments?per_page=50"):
            return [{"id": 1, "environment": "dev", "ref": ref, "sha": "digest", "task": "deploy"}]
        if "/statuses" in path:
            return [{"state": deployment_state, "target_url": "https://example/deploy"}]
        if "/commits/" in path:
            return {"sha": "latest-digest"}
        return {"tag_name": "v1.0.0"}

    monkeypatch.setattr(operations, "_gh", gh)
    status, detail = operations.deploy_status(root)
    assert status == expected
    assert detail["environment"] == "dev"


def test_multiple_deployment_environments_are_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path, deployed=True)
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(
        operations,
        "_gh",
        lambda *_args: [{"id": 1, "environment": "dev"}, {"id": 2, "environment": "prod"}],
    )
    with pytest.raises(operations.OperationsError) as caught:
        operations.deploy_status(root)
    assert caught.value.status == "ambiguous"


@pytest.mark.parametrize(
    ("arguments", "result", "expected"),
    [
        (["repo", "info"], {"type": "python"}, "success"),
        (["repo", "standards"], {"conformant": False}, "invalid"),
        (["repo", "validate"], {"passed": False, "checks": [{"state": "failed"}]}, "error"),
        (["ci", "status"], ("stale", {"state": "stale"}), "stale"),
        (["ci", "failures"], ("error", {"failures": ["test"]}), "error"),
        (["deploy", "status"], ("not_applicable", {"reason": "library"}), "not_applicable"),
    ],
)
def test_public_operation_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    result: object,
    expected: str,
) -> None:
    monkeypatch.setattr(operations, "repository_root", lambda _cwd: Path("/repo"))
    name = f"{arguments[0]}_{arguments[1]}"
    monkeypatch.setattr(operations, name, lambda *_args: result)
    code = main([*arguments, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == expected
    assert code == EXIT_CODES[expected]  # type: ignore[index]


def test_public_error_is_structured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(_cwd: Path) -> Path:
        raise operations.OperationsError("NOT_A_REPOSITORY", "No repository.", "invalid")

    monkeypatch.setattr(operations, "repository_root", unavailable)
    assert main(["repo", "info", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "NOT_A_REPOSITORY"


@pytest.mark.parametrize(
    ("content", "status"),
    [
        (None, "unsupported"),
        ("version = 2\n[validation]\nchecks=[]\n", "unsupported"),
        ("invalid = [", "invalid"),
    ],
)
def test_contract_error_states(tmp_path: Path, content: str | None, status: str) -> None:
    root = tmp_path
    if content is not None:
        path = root / ".github/release.toml"
        path.parent.mkdir()
        path.write_text(content)
    with pytest.raises(operations.OperationsError) as caught:
        operations.repo_info(root)
    assert caught.value.status == status


def test_unsupported_validation_check_and_required_standard(tmp_path: Path) -> None:
    root = repository(tmp_path, checks='"future-check"')
    (root / ".github/workflows/validate.yml").unlink()
    assert operations.repo_standards(root)["conformant"] is False
    result = operations.repo_validate(root)
    assert result["passed"] is False
    assert result["checks"][0]["state"] == "unsupported"  # type: ignore[index]


def test_ci_no_runs_and_current_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path)
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(operations, "_gh", lambda *_args: {"workflow_runs": []})
    with pytest.raises(operations.OperationsError) as caught:
        operations.ci_status(root)
    assert caught.value.code == "CI_EVIDENCE_UNAVAILABLE"
    monkeypatch.setattr(
        operations,
        "_gh",
        lambda *_args: {
            "workflow_runs": [{"id": 1, "head_sha": "current", "conclusion": "success"}]
        },
    )
    monkeypatch.setattr(
        operations,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "current\n", ""),
    )
    assert operations.ci_status(root)[0] == "success"


def test_deployment_evidence_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = repository(tmp_path, deployed=True)
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(operations, "_gh", lambda *_args: [])
    with pytest.raises(operations.OperationsError) as caught:
        operations.deploy_status(root)
    assert caught.value.status == "unavailable"


def test_branch_deployment_matches_release_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path, deployed=True)
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/repo")

    def gh(_root: Path, path: str) -> object:
        if path.endswith("/deployments?per_page=50"):
            return [{"id": 1, "environment": "dev", "ref": "main", "sha": "release-sha"}]
        if "/statuses" in path:
            return [{"state": "success"}]
        if "/commits/" in path:
            return {"sha": "release-sha"}
        return {"tag_name": "v1.0.0"}

    monkeypatch.setattr(operations, "_gh", gh)
    status, detail = operations.deploy_status(root)
    assert status == "success"
    assert detail["release"] == "v1.0.0"
    assert detail["deployment_ref"] == "main"


def test_repository_root_and_github_transport_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    assert operations.repository_root(tmp_path) == tmp_path
    assert operations._commands("sqlfluff", {})[-2:] == ["lint", "."]  # type: ignore[index]
    assert operations._commands("dbt-parse", {})[-2:] == ["dbt", "parse"]  # type: ignore[index]
    assert operations._commands("unknown", {}) is None

    monkeypatch.setattr(
        operations,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "error"),
    )
    with pytest.raises(operations.OperationsError) as caught:
        operations._gh(tmp_path, "repos/owner/repo")
    assert caught.value.code == "GITHUB_UNAVAILABLE"
    with pytest.raises(operations.OperationsError) as caught:
        operations._slug(tmp_path)
    assert caught.value.code == "GITHUB_REPOSITORY_UNAVAILABLE"

    monkeypatch.setattr(
        operations,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "not-json", ""),
    )
    with pytest.raises(operations.OperationsError) as caught:
        operations._gh(tmp_path, "repos/owner/repo")
    assert caught.value.code == "GITHUB_INVALID"
    monkeypatch.setattr(
        operations,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "{}", ""),
    )
    assert operations._gh(tmp_path, "repos/owner/repo") == {}


def test_ci_missing_failure_detail_and_deployment_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path, deployed=True)
    monkeypatch.setattr(operations, "_slug", lambda _root: "owner/repo")

    def gh(_root: Path, path: str) -> object:
        if "workflows/validate.yml" in path:
            return {"workflow_runs": [{"id": 7, "head_sha": "current", "conclusion": "failure"}]}
        if "/jobs" in path:
            return {"jobs": []}
        if path.endswith("/deployments?per_page=50"):
            return [
                {"id": 1, "environment": "dev", "ref": "v1.0.0"},
                {"id": 2, "environment": "prod", "ref": "v1.0.0"},
            ]
        if "/statuses" in path:
            return [{"state": "success"}]
        return {"tag_name": "v1.0.0"}

    monkeypatch.setattr(operations, "_gh", gh)
    monkeypatch.setattr(
        operations,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "current\n", ""),
    )
    ci_state, ci_detail = operations.ci_failures(root)
    assert ci_state == "unavailable"
    assert ci_detail["failures"] == []
    assert operations.deploy_status(root, "prod")[0] == "success"

    def no_status(_root: Path, path: str) -> object:
        if "/statuses" in path:
            return []
        return gh(_root, path)

    monkeypatch.setattr(operations, "_gh", no_status)
    with pytest.raises(operations.OperationsError) as caught:
        operations.deploy_status(root, "prod")
    assert caught.value.code == "DEPLOYMENT_EVIDENCE_UNAVAILABLE"


def test_validation_rejects_escape_and_reports_missing_node_dependencies(tmp_path: Path) -> None:
    root = repository(tmp_path / "app", checks="")
    contract = root / ".github/release.toml"
    contract.write_text(
        contract.read_text() + '[node]\ndirectory = "frontend"\nchecks = ["test"]\n'
    )
    (root / "frontend").mkdir()
    result = operations.repo_validate(root)
    states = {entry["name"]: entry["state"] for entry in result["checks"]}  # type: ignore[index]
    assert states["test"] == "unavailable"
    assert operations._checkout_directory(root, "frontend") == root / "frontend"
    with pytest.raises(operations.OperationsError) as caught:
        operations._checkout_directory(root, "../outside")
    assert caught.value.code == "CONTRACT_INVALID"


@pytest.mark.parametrize(("setup_exit", "expected"), [(0, "passed"), (1, "unavailable")])
def test_node_validation_installs_only_in_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup_exit: int, expected: str
) -> None:
    root = repository(tmp_path / "app", checks="")
    contract = root / ".github/release.toml"
    contract.write_text(
        contract.read_text() + '[node]\ndirectory = "frontend"\nchecks = ["test"]\n'
    )
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text("{}")
    real_run = operations.subprocess.run

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["npm", "ci"]:
            assert Path(str(kwargs["cwd"])) != frontend
            return subprocess.CompletedProcess(args, setup_exit, "installed", "")
        if args[:2] == ["npm", "run"]:
            assert Path(str(kwargs["cwd"])) != frontend
            return subprocess.CompletedProcess(args, 0, "tested", "")
        return real_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(operations.subprocess, "run", run)
    result = operations.repo_validate(root)
    assert result["checks"][0]["state"] == expected  # type: ignore[index]
    assert (frontend / "package-lock.json").read_text() == "{}"


def test_python_cli_type_and_invalid_pyproject(tmp_path: Path) -> None:
    root = repository(tmp_path)
    project = root / "pyproject.toml"
    project.write_text('[project]\nname = "tool"\n[project.scripts]\ntool = "tool:main"\n')
    assert operations.repo_info(root)["type"] == "python-cli"
    project.write_text("invalid = [")
    assert operations.repo_info(root)["type"] == "python-library"

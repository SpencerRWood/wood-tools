"""Focused repository, CI, and deployment inspection for Wood repositories."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .output import Status
from .workflow_files import output_directory, run_directory, snapshot_fingerprint, write_json


class OperationsError(Exception):
    def __init__(self, code: str, message: str, status: Status = "unavailable") -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _run(args: list[str], root: Path, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args, cwd=root, text=True, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperationsError("COMMAND_UNAVAILABLE", f"{args[0]} could not complete.") from exc


def repository_root(cwd: Path) -> Path:
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd)
    if result.returncode or not result.stdout.strip():
        raise OperationsError(
            "NOT_A_REPOSITORY", "Current directory is not a Git repository.", "invalid"
        )
    return Path(result.stdout.strip())


def _contract(root: Path) -> dict[str, Any]:
    path = root / ".github" / "release.toml"
    if not path.is_file():
        raise OperationsError(
            "CONTRACT_MISSING", "Repository has no .github/release.toml.", "unsupported"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OperationsError(
            "CONTRACT_INVALID", "Release contract cannot be read.", "invalid"
        ) from exc
    if data.get("version") != 1 or not isinstance(data.get("validation"), dict):
        raise OperationsError(
            "CONTRACT_UNSUPPORTED",
            "Release contract version or validation is unsupported.",
            "unsupported",
        )
    return data


def _workflows(root: Path) -> dict[str, bool]:
    folder = root / ".github" / "workflows"
    files = [p for p in folder.glob("*.y*ml") if p.is_file()]
    texts = [p.read_text(encoding="utf-8") for p in files]
    return {
        "validate": (folder / "validate.yml").is_file(),
        "release": (folder / "release.yml").is_file(),
        "deployment": any(
            token in text
            for text in texts
            for token in ("promote-container-to-dev", "deploy-ansible", "deploy-target.yml")
        ),
    }


def _repo_type(root: Path, contract: dict[str, Any], workflows: dict[str, bool]) -> str:
    if "dbt" in contract:
        return "dbt"
    if "node" in contract and "python" in contract:
        return "python-node"
    if "node" in contract:
        return "node"
    if "python" in contract:
        if workflows["deployment"]:
            return "python-service"
        project_file = root / "pyproject.toml"
        if project_file.is_file():
            try:
                project = tomllib.loads(project_file.read_text(encoding="utf-8"))
                if project.get("project", {}).get("scripts"):
                    return "python-cli"
            except OSError, ValueError:
                pass
        return "python-library"
    return "infrastructure"


def repo_info(root: Path) -> dict[str, object]:
    contract = _contract(root)
    workflows = _workflows(root)
    validation = contract["validation"]
    checks = validation.get("checks")
    if not isinstance(checks, list) or not all(isinstance(x, str) for x in checks):
        raise OperationsError(
            "CONTRACT_INVALID", "Validation checks must be a string list.", "invalid"
        )
    return {
        "root": str(root),
        "type": _repo_type(root, contract, workflows),
        "capabilities": {
            "python": "python" in contract,
            "node": "node" in contract,
            "dbt": "dbt" in contract,
        },
        "release": {
            "contract_version": 1,
            "semantic_release": bool(contract.get("release", {}).get("semantic_release")),
        },
        "validation": {
            "checks": checks + contract.get("node", {}).get("checks", []),
            "working_directory": contract.get("project", {}).get("working_directory", "."),
        },
        "deployment": {
            "applicable": workflows["deployment"],
            "evidence": "GitHub deployments" if workflows["deployment"] else None,
        },
        "workflows": workflows,
    }


def repo_standards(root: Path) -> dict[str, object]:
    checks: list[dict[str, str]] = []
    contract_path = root / ".github" / "release.toml"
    for name, path, requirement in (
        ("release-contract", contract_path, "required"),
        ("validate-workflow", root / ".github/workflows/validate.yml", "required"),
        ("release-workflow", root / ".github/workflows/release.yml", "required"),
        ("repository-guidance", root / "AGENTS.md", "optional"),
    ):
        checks.append(
            {
                "name": name,
                "requirement": requirement,
                "state": "passed" if path.is_file() else "missing",
            }
        )
    if contract_path.is_file():
        try:
            contract = _contract(root)
            checks.append(
                {"name": "contract-version", "requirement": "required", "state": "passed"}
            )
            expected = "pre-commit" in contract["validation"].get("checks", [])
            checks.append(
                {
                    "name": "pre-commit-config",
                    "requirement": "required" if expected else "not-applicable",
                    "state": (
                        "passed" if (root / ".pre-commit-config.yaml").is_file() else "missing"
                    )
                    if expected
                    else "not-applicable",
                }
            )
        except OperationsError as exc:
            checks.append(
                {"name": "contract-version", "requirement": "required", "state": exc.status}
            )
    return {
        "checks": checks,
        "conformant": all(c["state"] == "passed" for c in checks if c["requirement"] == "required"),
    }


def _commands(check: str, contract: dict[str, Any]) -> list[str] | None:
    target = contract.get("coverage", {}).get("target")
    known = {
        "ruff": ["uv", "run", "--active", "--no-sync", "ruff", "check", "."],
        "ruff-format": ["uv", "run", "--active", "--no-sync", "ruff", "format", "--check", "."],
        "mypy": ["uv", "run", "--active", "--no-sync", "mypy", "--follow-imports=silent"],
        "pytest": ["uv", "run", "--active", "--no-sync", "pytest", "-p", "no:cacheprovider"],
        "pytest-coverage": [
            "uv",
            "run",
            "--active",
            "--no-sync",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--cov={target or '.'}",
            "--cov-report=term-missing",
        ],
        "pre-commit": ["uv", "run", "--active", "--no-sync", "pre-commit", "run", "--all-files"],
        "sqlfluff": ["uv", "run", "--active", "--no-sync", "sqlfluff", "lint", "."],
        "dbt-deps": ["uv", "run", "--active", "--no-sync", "dbt", "deps"],
        "dbt-parse": ["uv", "run", "--active", "--no-sync", "dbt", "parse"],
    }
    if check in known:
        return known[check]
    node = contract.get("node", {})
    if check in node.get("checks", []):
        return ["npm", "run", check]
    if check == "docker-compose":
        return ["docker", "compose", "config", "--quiet"]
    return None


def _checkout_directory(checkout: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise OperationsError("CONTRACT_INVALID", "Working directory must be a path.", "invalid")
    candidate = (checkout / relative).resolve()
    if not candidate.is_relative_to(checkout.resolve()):
        raise OperationsError("CONTRACT_INVALID", "Working directory escapes checkout.", "invalid")
    return candidate


def _copy_ignores(directory: str, names: list[str]) -> set[str]:
    ignored = shutil.ignore_patterns(
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".coverage",
        ".env",
        ".env.*",
    )(directory, names)
    return ignored - {".env.example"}


def _prune_empty_directories(root: Path) -> None:
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()


def repo_validate(root: Path) -> dict[str, object]:
    contract = _contract(root)
    checks = contract["validation"].get("checks", [])
    if not isinstance(checks, list) or not all(isinstance(x, str) for x in checks):
        raise OperationsError(
            "CONTRACT_INVALID", "Validation checks must be a string list.", "invalid"
        )
    node_checks = contract.get("node", {}).get("checks", [])
    if not isinstance(node_checks, list) or not all(isinstance(x, str) for x in node_checks):
        raise OperationsError("CONTRACT_INVALID", "Node checks must be a string list.", "invalid")
    checks = checks + node_checks
    results: list[dict[str, object]] = []
    log_dir = run_directory(root, "wood-repo-validate-")
    output_dir = output_directory(root)
    with tempfile.TemporaryDirectory(prefix="wood-repo-checkout-") as temp:
        checkout = Path(temp) / "checkout"
        shutil.copytree(
            root,
            checkout,
            ignore=lambda directory, names: (
                _copy_ignores(directory, names)
                | {
                    name
                    for name in names
                    if output_dir.is_relative_to(root.resolve())
                    and (Path(directory) / name).resolve().is_relative_to(output_dir)
                }
            ),
        )
        _prune_empty_directories(checkout)
        fingerprint = snapshot_fingerprint(root, checkout, output_dir)
        # pre-commit requires a repository, and all hooks run against this disposable copy.
        initialized = _run(["git", "init", "--quiet"], checkout).returncode == 0
        initialized = (
            initialized
            and _run(["git", "switch", "--quiet", "-c", "wood-validation"], checkout).returncode
            == 0
        )
        initialized = initialized and _run(["git", "add", "--all"], checkout).returncode == 0
        env = dict(os.environ)
        environment = root / ".venv"
        if environment.is_dir():
            env["VIRTUAL_ENV"] = str(environment)
            env["PATH"] = f"{environment / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
                "COVERAGE_FILE": str(log_dir / ".coverage"),
            }
        )
        node_ready = True
        if node_checks:
            node_dir = _checkout_directory(checkout, contract["node"].get("directory", "."))
            if not (node_dir / "package-lock.json").is_file():
                node_ready = False
            else:
                try:
                    setup = subprocess.run(
                        ["npm", "ci", "--ignore-scripts", "--prefer-offline"],
                        cwd=node_dir,
                        env=env,
                        text=True,
                        capture_output=True,
                        timeout=300,
                        check=False,
                    )
                    (log_dir / "00-npm-ci.log").write_text(
                        setup.stdout + setup.stderr, encoding="utf-8"
                    )
                    node_ready = setup.returncode == 0
                except OSError, subprocess.TimeoutExpired:
                    node_ready = False
        for check in checks:
            command = _commands(check, contract)
            if command is None:
                results.append({"name": check, "state": "unsupported"})
                continue
            if check in node_checks and not node_ready:
                results.append(
                    {
                        "name": check,
                        "state": "unavailable",
                        "reason": "npm dependencies are unavailable in the disposable checkout",
                    }
                )
                continue
            if check == "pre-commit":
                if not initialized:
                    results.append({"name": check, "state": "unavailable"})
                    continue
            cwd = _checkout_directory(
                checkout,
                (
                    contract.get("node", {}).get("directory", ".")
                    if command[0] == "npm"
                    else contract.get("project", {}).get("working_directory", ".")
                ),
            )
            try:
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=300,
                    check=False,
                )
                safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", check)[:60]
                log_path = log_dir / f"{len(results) + 1:02d}-{safe_name}.log"
                log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
                results.append(
                    {
                        "name": check,
                        "state": "passed" if result.returncode == 0 else "failed",
                        "exit_code": result.returncode,
                        "log_path": str(log_path),
                    }
                )
            except OSError, subprocess.TimeoutExpired:
                results.append({"name": check, "state": "unavailable"})
    record: dict[str, Any] = {
        "schema_version": 1,
        "kind": "wood-repository-validation",
        "repository_root": str(root.resolve()),
        "source_fingerprint": fingerprint,
        "checks": results,
        "passed": bool(results) and all(r["state"] == "passed" for r in results),
        "log_dir": str(log_dir),
    }
    record_path = log_dir / "validation.json"
    write_json(record_path, record)
    return {**record, "validation_file": str(record_path)}


def _gh(root: Path, path: str) -> dict[str, Any] | list[Any]:
    result = _run(["gh", "api", path], root)
    if result.returncode:
        raise OperationsError("GITHUB_UNAVAILABLE", "GitHub API request failed.")
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise OperationsError("GITHUB_INVALID", "GitHub returned invalid JSON.") from exc
    if not isinstance(data, dict | list):
        raise OperationsError("GITHUB_INVALID", "GitHub returned an unsupported response.")
    return data


def _slug(root: Path) -> str:
    result = _run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], root)
    slug = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
        raise OperationsError(
            "GITHUB_REPOSITORY_UNAVAILABLE", "GitHub repository could not be identified."
        )
    return slug


def _latest_run(root: Path) -> tuple[str, dict[str, Any]]:
    slug = _slug(root)
    data = _gh(root, f"repos/{slug}/actions/workflows/validate.yml/runs?per_page=10")
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        raise OperationsError("CI_EVIDENCE_INVALID", "Workflow runs response is invalid.")
    runs = data["workflow_runs"]
    if not runs:
        raise OperationsError("CI_EVIDENCE_UNAVAILABLE", "No validation workflow run is available.")
    return slug, runs[0]


def ci_status(root: Path) -> tuple[Status, dict[str, object]]:
    slug, run = _latest_run(root)
    head = _run(["git", "rev-parse", "HEAD"], root).stdout.strip()
    state = (
        "stale"
        if run.get("head_sha") != head
        else (run.get("conclusion") or run.get("status") or "unknown")
    )
    return (
        "success"
        if state in {"success", "queued", "in_progress", "waiting", "pending"}
        else "unavailable"
        if state == "unknown"
        else "error"
        if state in {"failure", "cancelled", "timed_out"}
        else "stale"
    ), {
        "repository": slug,
        "run_id": run.get("id"),
        "url": run.get("html_url"),
        "state": state,
        "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
        "current_sha": head,
    }


def ci_failures(root: Path) -> tuple[Status, dict[str, object]]:
    slug, run = _latest_run(root)
    head = _run(["git", "rev-parse", "HEAD"], root).stdout.strip()
    run_id = run.get("id")
    if not isinstance(run_id, int):
        raise OperationsError("CI_EVIDENCE_INVALID", "Workflow run has no ID.")
    jobs = _gh(root, f"repos/{slug}/actions/runs/{run_id}/jobs?per_page=100")
    if not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list):
        raise OperationsError("CI_EVIDENCE_INVALID", "Workflow jobs response is invalid.")
    failed: list[dict[str, object]] = []
    for job in jobs["jobs"]:
        if not isinstance(job, dict) or job.get("conclusion") not in {
            "failure",
            "timed_out",
            "cancelled",
        }:
            continue
        steps = [
            s.get("name")
            for s in job.get("steps", [])
            if isinstance(s, dict) and s.get("conclusion") == "failure"
        ]
        failed.append(
            {
                "job": job.get("name"),
                "conclusion": job.get("conclusion"),
                "steps": steps[:10],
                "url": job.get("html_url"),
            }
        )
    state: Status = (
        "stale"
        if run.get("head_sha") != head
        else "error"
        if failed
        else "unavailable"
        if run.get("conclusion") in {"failure", "timed_out", "cancelled"}
        else "success"
    )
    return state, {
        "run_id": run_id,
        "run_url": run.get("html_url"),
        "head_sha": run.get("head_sha"),
        "current_sha": head,
        "failures": failed[:20],
        "truncated": len(failed) > 20,
    }


def _delivery_attestation(payload: object, field: str) -> dict[str, str] | None:
    value = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return None
    keys = ("revision", "environment", "status", "url")
    if not all(isinstance(value.get(key), str) for key in keys):
        return None
    return {key: value[key] for key in keys}


def deploy_status(root: Path, environment: str | None = None) -> tuple[Status, dict[str, object]]:
    applicable = _workflows(root)["deployment"]
    if not applicable:
        return "not_applicable", {"reason": "No deployment workflow is configured."}
    slug = _slug(root)
    data = _gh(root, f"repos/{slug}/deployments?per_page=50")
    if not isinstance(data, list):
        raise OperationsError("DEPLOYMENT_EVIDENCE_INVALID", "Deployments response is invalid.")
    deployments = [
        d
        for d in data
        if isinstance(d, dict) and (environment is None or d.get("environment") == environment)
    ]
    if not deployments:
        raise OperationsError(
            "DEPLOYMENT_EVIDENCE_UNAVAILABLE", "No deployment record is available."
        )
    environments = {str(d.get("environment")) for d in deployments}
    if len(environments) > 1:
        raise OperationsError(
            "DEPLOYMENT_AMBIGUOUS",
            "Multiple deployment environments exist; specify --environment.",
            "ambiguous",
        )
    deployment = deployments[0]
    deployment_id = deployment.get("id")
    if not isinstance(deployment_id, int):
        raise OperationsError("DEPLOYMENT_EVIDENCE_INVALID", "Deployment has no ID.")
    statuses = _gh(root, f"repos/{slug}/deployments/{deployment_id}/statuses?per_page=1")
    if not isinstance(statuses, list) or not statuses:
        raise OperationsError(
            "DEPLOYMENT_EVIDENCE_UNAVAILABLE", "Deployment has no status evidence."
        )
    latest = statuses[0]
    release = _gh(root, f"repos/{slug}/releases/latest")
    tag = release.get("tag_name") if isinstance(release, dict) else None
    ref = deployment.get("ref")
    state = latest.get("state") if isinstance(latest, dict) else None
    if not isinstance(state, str):
        raise OperationsError("DEPLOYMENT_EVIDENCE_INVALID", "Deployment status is invalid.")
    if not isinstance(tag, str):
        raise OperationsError("DEPLOYMENT_EVIDENCE_INVALID", "Latest release has no tag.")
    if ref == tag:
        stale = False
    else:
        release_commit = _gh(root, f"repos/{slug}/commits/{tag}")
        release_sha = release_commit.get("sha") if isinstance(release_commit, dict) else None
        deployment_sha = deployment.get("sha")
        if not isinstance(release_sha, str) or not isinstance(deployment_sha, str):
            raise OperationsError(
                "DEPLOYMENT_EVIDENCE_UNAVAILABLE", "Release or deployment commit is missing."
            )
        stale = deployment_sha != release_sha
    status: Status = (
        "error"
        if state in {"failure", "error"}
        else "stale"
        if stale
        else "success"
        if state == "success"
        else "unavailable"
    )
    payload = deployment.get("payload")
    digest = payload.get("digest") if isinstance(payload, dict) else None
    return status, {
        "repository": slug,
        "environment": deployment.get("environment"),
        "target": deployment.get("task"),
        "release": tag if not stale else ref,
        "deployment_ref": ref,
        "latest_release": tag,
        "commit_sha": deployment.get("sha"),
        "digest": digest,
        "infrastructure_promotion": _delivery_attestation(payload, "infrastructure_promotion"),
        "runtime_verification": _delivery_attestation(payload, "runtime_verification"),
        "state": state,
        "status_url": latest.get("target_url"),
        "stale": stale,
    }

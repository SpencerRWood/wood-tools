from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .models import ReleaseWorkflowError
from .version import read_static_version, validate_semver


def run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise ReleaseWorkflowError(
            "GIT_COMMAND_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git command failed",
        )
    return result


def resolve_version(version: str | None, pyproject: Path) -> str:
    if version is None:
        return read_static_version(pyproject)
    validate_semver(version)
    return version


def normalize_tag(version: str) -> str:
    return f"v{version}"


def inspect_repo_state() -> tuple[Path, dict[str, Any]]:
    repo_root_result = run_git(["rev-parse", "--show-toplevel"], check=False)
    if repo_root_result.returncode != 0:
        raise ReleaseWorkflowError("TAG_CREATION_FAILED", "Not inside a git repository.")

    repo_root = Path(repo_root_result.stdout.strip())
    current_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).stdout.strip()
    head = run_git(["rev-parse", "--short", "HEAD"], cwd=repo_root).stdout.strip()
    status_lines = run_git(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=repo_root,
    ).stdout.splitlines()
    untracked_files = [line[3:] for line in status_lines if line.startswith("?? ")]
    modified_files = [line[3:] for line in status_lines if not line.startswith("?? ")]

    upstream = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=repo_root,
        check=False,
    )
    if upstream.returncode == 0:
        counts = run_git(
            ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
            cwd=repo_root,
        )
        left, right = counts.stdout.strip().split()
        behind = int(left)
        ahead = int(right)
    else:
        ahead = 0
        behind = 0

    return repo_root, {
        "path": str(repo_root),
        "current_branch": current_branch,
        "working_tree_clean": not status_lines,
        "modified_files": modified_files,
        "untracked_files": untracked_files,
        "ahead": ahead,
        "behind": behind,
        "head": head,
    }


def tag_exists(repo_root: Path, tag_name: str) -> bool:
    return tag_name in run_git(["tag", "--list", tag_name], cwd=repo_root).stdout.split()


def create_tag(
    *,
    version: str | None,
    pyproject: Path,
    apply: bool,
    allow_dirty: bool,
) -> dict[str, Any]:
    resolved_version = resolve_version(version, pyproject)
    tag_name = normalize_tag(resolved_version)
    repo_root, repo = inspect_repo_state()

    if not repo["working_tree_clean"] and not allow_dirty:
        raise ReleaseWorkflowError(
            "DIRTY_WORKTREE",
            "Working tree is dirty. Refusing to create tag without --allow-dirty.",
        )
    if tag_exists(repo_root, tag_name):
        raise ReleaseWorkflowError(
            "TAG_ALREADY_EXISTS", f"Git tag already exists locally: {tag_name}"
        )

    tag_payload = {
        "name": tag_name,
        "version": resolved_version,
        "target_commit": repo["head"],
    }
    if apply:
        run_git(["tag", tag_name], cwd=repo_root)
        tag_payload["created"] = True
    else:
        tag_payload["would_create"] = True

    return {
        "ok": True,
        "dry_run": not apply,
        "repo": repo,
        "tag": tag_payload,
        "mutation": {"system": "git", "action": "create_tag"},
    }

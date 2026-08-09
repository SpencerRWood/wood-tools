from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .discovery import slugify
from .models import StoryWorkflowError


def run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise StoryWorkflowError(
            "GIT_COMMAND_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git command failed",
        )
    return result


def repo_state() -> dict[str, Any]:
    root = Path(run_git(["rev-parse", "--show-toplevel"]).stdout.strip())
    branch = run_git(["branch", "--show-current"]).stdout.strip()
    status_lines = run_git(["status", "--porcelain", "--untracked-files=all"]).stdout.splitlines()
    return {
        "path": str(root),
        "current_branch": branch,
        "working_tree_clean": not status_lines,
        "untracked_files": [line[3:] for line in status_lines if line.startswith("?? ")],
    }


def branch_exists(branch_name: str) -> bool:
    return (
        run_git(["show-ref", "--verify", f"refs/heads/{branch_name}"], check=False).returncode == 0
    )


def create_branch(
    *,
    work_package_id: int,
    title: str | None,
    apply: bool,
    allow_dirty: bool,
) -> dict[str, Any]:
    state = repo_state()
    branch_name = (
        f"feature/op-{work_package_id}-{slugify(title or '')}"
        if title
        else f"feature/op-{work_package_id}"
    )
    exists = branch_exists(branch_name)
    if not state["working_tree_clean"] and not allow_dirty:
        raise StoryWorkflowError(
            "DIRTY_WORKTREE",
            "Working tree is dirty. Re-run with --allow-dirty only after explicit approval.",
        )

    if not apply:
        return {
            "ok": True,
            "dry_run": True,
            "repo": state,
            "branch": {
                "name": branch_name,
                "would_create": not exists,
                "would_checkout": state["current_branch"] != branch_name,
            },
            "mutation": {"system": "git", "action": "create_branch"},
        }

    if exists:
        if state["current_branch"] != branch_name:
            run_git(["checkout", branch_name])
    else:
        run_git(["checkout", "-b", branch_name])
    return {
        "ok": True,
        "dry_run": False,
        "repo": repo_state(),
        "branch": {"name": branch_name, "created": not exists, "checked_out": True},
        "mutation": {"system": "git", "action": "create_branch"},
    }

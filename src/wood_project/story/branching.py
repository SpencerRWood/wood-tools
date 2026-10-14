"""Opt-in repository branching policy, independent of publication and deployment."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from wood_project.openproject import link_href

from . import session
from .models import StoryWorkflowError
from .openproject import extract_id_from_href

RELEASE_BRANCH = r"release/v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"


def contract() -> dict[str, Any]:
    """Let launchers negotiate branching support before creating a claim."""
    return {
        "version": 1,
        "strategies": ["trunk", "release", "integration"],
        "branch_prefixes": ["feature", "feat", "fix"],
        "target_strategy": "integration",
        "target_integration_branch": "development",
        "hotfix": {"version": 1, "reconciliation": "main-ancestor-of-integration"},
    }


def configuration(root: Path, *, source_text: str | None = None) -> dict[str, Any]:
    """Missing adoption metadata preserves the existing trunk workflow."""
    try:
        text = (
            source_text
            if source_text is not None
            else (
                (root / "pyproject.toml").read_text(encoding="utf-8")
                if (root / "pyproject.toml").exists()
                else ""
            )
        )
        section = tomllib.loads(text).get("tool", {}).get("wood", {}).get("branching")
        if section is None:
            return {
                "strategy": "trunk",
                "adopted": False,
                "base_branch": "main",
                "target_branch": "main",
            }
        if not isinstance(section, dict) or set(section) - {
            "strategy",
            "release_id",
            "release_branch",
            "integration_branch",
            "scope_reference",
        }:
            raise ValueError("invalid keys")
        strategy = section.get("strategy")
        if strategy == "trunk" and set(section) == {"strategy"}:
            return {
                "strategy": "trunk",
                "adopted": True,
                "base_branch": "main",
                "target_branch": "main",
            }
        if strategy not in {"release", "integration"}:
            raise ValueError("invalid strategy")
        branch_key = "release_branch" if strategy == "release" else "integration_branch"
        if set(section) != {"strategy", "release_id", branch_key, "scope_reference"}:
            raise ValueError("invalid keys")
        release_id, branch, scope = (
            section.get(k) for k in ("release_id", branch_key, "scope_reference")
        )
        if (
            type(release_id) is not int
            or release_id <= 0
            or not isinstance(branch, str)
            or (strategy == "release" and not re.fullmatch(RELEASE_BRANCH, branch))
            or not isinstance(scope, str)
            or not scope.strip()
            or len(scope) > 500
        ):
            raise ValueError("invalid mapping")
        if strategy == "integration":
            if branch == "main" or branch.startswith("-"):
                raise ValueError("invalid integration branch")
            try:
                session.git(root, "check-ref-format", "--branch", branch)
            except StoryWorkflowError as exc:
                raise ValueError("invalid integration branch") from exc
        return {
            "strategy": strategy,
            "adopted": True,
            "base_branch": branch,
            "target_branch": branch,
            "release_id": release_id,
            "scope_reference": scope,
        }
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise StoryWorkflowError(
            "BRANCHING_INVALID",
            "Use a valid [tool.wood.branching] strategy and approved release mapping.",
        ) from exc


def select(
    root: Path,
    *,
    story: dict[str, Any] | None = None,
    base_branch: str | None = None,
    target_branch: str | None = None,
    source_text: str | None = None,
    hotfix_incident: str | None = None,
    hotfix_approval: str | None = None,
) -> dict[str, Any]:
    plan = configuration(root, source_text=source_text)
    if hotfix_incident is not None or hotfix_approval is not None:
        if plan["strategy"] != "integration" or any(
            not isinstance(value, str) or not value.strip() or len(value) > 500
            for value in (hotfix_incident, hotfix_approval)
        ):
            raise StoryWorkflowError(
                "HOTFIX_AUTHORIZATION_REQUIRED",
                "Hotfix requires integration strategy, incident and explicit approval references.",
            )
        plan.update(
            mode="hotfix",
            integration_branch=plan["base_branch"],
            base_branch="main",
            target_branch="main",
            hotfix_incident=hotfix_incident,
            hotfix_approval=hotfix_approval,
        )
    if (base_branch is not None and base_branch != plan["base_branch"]) or (
        target_branch is not None and target_branch != plan["target_branch"]
    ):
        raise StoryWorkflowError(
            "BRANCH_TARGET_MISMATCH",
            "Explicit branches must match the adopted repository strategy and mapping.",
        )
    if story is not None and plan["strategy"] != "trunk":
        release_id = extract_id_from_href(link_href(story, "version"), "versions")
        if release_id != plan["release_id"]:
            raise StoryWorkflowError(
                "RELEASE_MISMATCH",
                "Story planning release differs from the approved Git branch mapping.",
            )
        plan["release_name"] = story.get("_links", {}).get("version", {}).get("title")
    return plan


def release_readiness(root: Path) -> dict[str, Any]:
    """Require main (including emergency fixes) back in integration before release review."""
    plan = configuration(root)
    if plan["strategy"] != "integration":
        raise StoryWorkflowError(
            "INTEGRATION_REQUIRED",
            "Release readiness requires an explicitly adopted integration strategy.",
        )
    if session.git(root, "rev-parse", "--is-shallow-repository") != "false":
        raise StoryWorkflowError(
            "HISTORY_INCOMPLETE", "Fetch complete history before release preparation."
        )
    integration = plan["base_branch"]
    session.git(
        root,
        "fetch",
        "--no-tags",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
        f"+refs/heads/{integration}:refs/remotes/origin/{integration}",
    )
    main = session.git(root, "rev-parse", "refs/remotes/origin/main^{commit}")
    development = session.git(root, "rev-parse", f"refs/remotes/origin/{integration}^{{commit}}")
    missing = session.git(
        root, "rev-list", "--max-count=21", main, "--not", development
    ).splitlines()
    return {
        "branching": plan,
        "main_revision": main,
        "integration_revision": development,
        "main_in_integration": not missing,
        "ready_for_release_review": not missing,
        "unreconciled_main_commits": missing[:20],
        "commits_truncated": len(missing) > 20,
        "authorization_granted": False,
        "next_action": (
            "Validate scope, planning release, protected-main checks and explicit release approval."
            if not missing
            else "Reconcile main into integration through a reviewed PR, then recheck."
        ),
    }


def discover(root: Path) -> dict[str, Any]:
    plan = select(root)
    ref = (
        f"refs/remotes/origin/{plan['base_branch']}"
        if plan["strategy"] != "trunk"
        else "refs/heads/main"
    )
    # This informational operation never fetches or creates branches.
    matches = session.git(root, "for-each-ref", "--format=%(objectname)", ref).splitlines()
    plan["observed_base_revision"] = matches[0] if len(matches) == 1 else None
    plan["remote_verified"] = False
    plan["next_action"] = (
        "Preview story start to verify the live planning release and fetch the mapped branch; "
        "no release branch is created."
    )
    return plan


def resolve_base(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    plan = dict(plan)
    if plan["strategy"] != "trunk":
        branch = plan["base_branch"]
        # Fetch this exact branch; a stale local ref cannot mask a missing remote.
        session.git(
            root,
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        )
        ref = f"refs/remotes/origin/{branch}"
    else:
        ref = "refs/heads/main"
    plan["base_revision"] = session.git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    plan["base_ref"] = ref
    return plan

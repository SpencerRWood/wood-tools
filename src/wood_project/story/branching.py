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
        if strategy != "release":
            raise ValueError("invalid strategy")
        release_id, branch, scope = (
            section.get(k) for k in ("release_id", "release_branch", "scope_reference")
        )
        if (
            type(release_id) is not int
            or release_id <= 0
            or not isinstance(branch, str)
            or not re.fullmatch(RELEASE_BRANCH, branch)
            or not isinstance(scope, str)
            or not scope.strip()
            or len(scope) > 500
        ):
            raise ValueError("invalid mapping")
        return {
            "strategy": "release",
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
) -> dict[str, Any]:
    plan = configuration(root, source_text=source_text)
    if (base_branch is not None and base_branch != plan["base_branch"]) or (
        target_branch is not None and target_branch != plan["target_branch"]
    ):
        raise StoryWorkflowError(
            "BRANCH_TARGET_MISMATCH",
            "Explicit branches must match the adopted repository strategy and mapping.",
        )
    if story is not None and plan["strategy"] == "release":
        release_id = extract_id_from_href(link_href(story, "version"), "versions")
        if release_id != plan["release_id"]:
            raise StoryWorkflowError(
                "RELEASE_MISMATCH",
                "Story planning release differs from the approved Git branch mapping.",
            )
        plan["release_name"] = story.get("_links", {}).get("version", {}).get("title")
    return plan


def discover(root: Path) -> dict[str, Any]:
    plan = select(root)
    ref = (
        f"refs/remotes/origin/{plan['base_branch']}"
        if plan["strategy"] == "release"
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
    if plan["strategy"] == "release":
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

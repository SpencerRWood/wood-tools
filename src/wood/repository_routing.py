"""PR routing and context extraction; BR-02 retains release authorization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from wood_project.story import branching, session

from . import operations
from .initialization import SHA, require


def references(body: str) -> tuple[str, str]:
    values = []
    for marker in ("Hotfix-Incident", "Hotfix-Approval"):
        matches = re.findall(r"^" + marker + r": (\S[^\r\n]{0,499})$", body, re.MULTILINE)
        require(len(matches) <= 1, "Hotfix authority references are ambiguous.")
        values.append(matches[0].strip() if matches else "")
    require(
        bool(values[0]) == bool(values[1]), "Hotfix requires both incident and approval references."
    )
    return values[0], values[1]


def context(pr: dict[str, Any], repository: str, integration: str) -> dict[str, Any]:
    require(
        pr["base"]["repo"]["full_name"].casefold() == repository.casefold()
        and pr["head"]["repo"]["full_name"].casefold() == repository.casefold(),
        "Routing requires a PR within the same repository.",
    )
    head, base = pr["head"]["ref"], pr["base"]["ref"]
    incident, approval = references(pr.get("body") or "")
    feature = bool(re.fullmatch(r"(?:feature|feat|fix)/[A-Za-z0-9][A-Za-z0-9._/-]*", head))
    maintenance = bool(re.fullmatch(r"renovate/[A-Za-z0-9][A-Za-z0-9._/-]*", head))
    if base == integration:
        require(
            not incident and (feature or maintenance or head == "main"),
            "Development accepts supported working branches or main reconciliation.",
        )
        mode = "reconciliation" if head == "main" else "maintenance" if maintenance else "story"
    else:
        require(base == "main", "PR target must match the declared integration or main branch.")
        if head == integration:
            require(not incident, "Routine integration cannot claim a hotfix exception.")
            mode = "release"
        else:
            require(
                feature
                and re.fullmatch(r"(?:feature|feat|fix)/op-\d+-[a-z0-9-]+", head)
                and incident,
                "Main accepts development or an explicitly authorized hotfix Story.",
            )
            mode = "hotfix"
    return {
        "mode": mode,
        "incident": incident,
        "approval": approval,
        "head_branch": head,
        "target_branch": base,
        "authorization_granted": False,
    }


def route(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    mapping = branching.configuration(root)
    require(
        mapping["strategy"] == "integration", "PR routing requires adopted integration metadata."
    )
    pr = event["pull_request"]
    result = context(pr, event["repository"]["full_name"], mapping["base_branch"])
    head, base = pr["head"]["sha"], pr["base"]["sha"]
    require(
        isinstance(head, str)
        and re.fullmatch(SHA, head)
        and isinstance(base, str)
        and re.fullmatch(SHA, base),
        "Full PR revisions are required.",
    )
    require(
        session.git(root, "rev-parse", "HEAD") == head, "Checkout must match the exact PR head."
    )
    # Strict required checks force a current candidate, including mandatory
    # main back-integration. Do not bless a branch using its name alone.
    if result["mode"] == "reconciliation":
        require(
            session.git(root, "rev-parse", "refs/remotes/origin/main") == head,
            "Reconciliation must use the current main revision.",
        )
        # Main and development normally diverge. The PR merge validation checks
        # compatibility; requiring development to be an ancestor of main would
        # make back-integration impossible without first releasing development.
        session.git(root, "merge-base", base, head)
    else:
        session.git(root, "merge-base", "--is-ancestor", base, head)
    return {
        **result,
        "head_revision": head,
        "base_revision": base,
        "reconciliation_required": result["mode"] == "hotfix",
    }


def release_context(root: Path, revision: str) -> dict[str, Any]:
    """Extract the merged PR's explicit references for the shared verifier."""
    require(
        re.fullmatch(SHA, revision) and session.git(root, "rev-parse", "HEAD") == revision,
        "Release context requires the exact event checkout.",
    )
    repository = operations._slug(root)
    mapping = branching.configuration(root)
    require(mapping["strategy"] == "integration", "Release context requires adopted integration.")
    prs: Any = operations._gh(root, f"repos/{repository}/commits/{revision}/pulls?per_page=100")
    require(
        isinstance(prs, list) and len(prs) < 100, "Merged PR authority is unavailable or truncated."
    )
    matches = [
        pr
        for pr in prs
        if pr.get("merged_at")
        and pr.get("merge_commit_sha") == revision
        and pr.get("base", {}).get("ref") == "main"
    ]
    require(len(matches) == 1, "Exactly one merged main PR must identify this release event.")
    result = context(matches[0], repository, mapping["base_branch"])
    return {**result, "revision": revision, "pr": matches[0]["number"]}


def github_outputs(path: Path, result: dict[str, Any]) -> None:
    # References are single-line bounded values, never executed as shell code.
    with path.open("a", encoding="utf-8") as stream:
        for name in ("incident", "approval"):
            stream.write(f"{name}={result[name]}\n")

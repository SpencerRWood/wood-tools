"""Objective delivery snapshots; optional providers never imply completion."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import operations


def _missing(reason: str, *, applicable: bool = True) -> dict[str, Any]:
    return {"state": "unavailable" if applicable else "not_applicable", "reason": reason}


def _available(value: object, source: str) -> dict[str, Any]:
    return {"state": "available", "value": value, "source": source}


def _contains(root: Path, slug: str, merged: str, revision: str) -> bool:
    if revision == merged:
        return True
    comparison = operations._gh(root, f"repos/{slug}/compare/{merged}...{revision}")
    return isinstance(comparison, dict) and comparison.get("status") == "ahead"


def collect_delivery(root: Path, evidence: dict[str, Any], branch: str) -> dict[str, Any]:
    slug = evidence["repository"]
    pr = evidence["pull_request"]
    merged = pr["merge_sha"]
    result = {
        "repository": _available(slug, "Git origin and Story Primary Repository"),
        "branch": _available(branch, pr["url"]),
        "source_commit": _available(pr["source_sha"], pr["url"]),
        "pull_request": _available(pr["number"], pr["url"]),
        "validation": _available(evidence["repository_checks"], evidence["validation_file"]),
        "ci": _available(evidence["ci"]["run_id"], evidence["ci"]["url"]),
        "merged_revision": _available(merged, pr["url"]),
        "semantic_release": _missing("No verified semantic release contains the merge."),
    }
    result.update(collect_optional_delivery(root, slug, merged))
    return result


def collect_optional_delivery(
    root: Path, slug: str, merged: str, environment: str | None = None
) -> dict[str, Any]:
    """Resolve optional authorities against the Story merge, never the local HEAD."""
    info = operations.repo_info(root)
    result = {"semantic_release": _missing("No verified semantic release contains the merge.")}
    release_info = info["release"]
    assert isinstance(release_info, dict)
    if not release_info["semantic_release"]:
        result["semantic_release"] = _missing(
            "Semantic release is not configured.", applicable=False
        )
    else:
        try:
            release = operations._gh(root, f"repos/{slug}/releases/latest")
            if (
                isinstance(release, dict)
                and isinstance(tag := release.get("tag_name"), str)
                and re.fullmatch(r"v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", tag)
            ):
                commit = operations._gh(root, f"repos/{slug}/commits/{tag}")
                sha = commit.get("sha") if isinstance(commit, dict) else None
                if (
                    isinstance(sha, str)
                    and re.fullmatch(r"[0-9a-f]{40,64}", sha)
                    and not release.get("draft")
                    and not release.get("prerelease")
                    and _contains(root, slug, merged, sha)
                ):
                    result["semantic_release"] = _available(
                        {"version": tag, "revision": sha},
                        f"https://github.com/{slug}/releases/tag/{tag}",
                    )
        except operations.OperationsError:
            result["semantic_release"] = _missing("GitHub release evidence is unavailable.")
    deployment_info = info["deployment"]
    assert isinstance(deployment_info, dict)
    applicable = bool(deployment_info["applicable"])
    for field in (
        "container_image_digest",
        "infrastructure_promotion",
        "deployed_revision",
        "runtime_verification",
    ):
        result[field] = _missing(
            "No verified provider evidence."
            if applicable
            else "No deployment workflow is configured.",
            applicable=applicable,
        )
    if not applicable:
        return result
    try:
        status, deployed = (
            operations.deploy_status(root)
            if environment is None
            else operations.deploy_status(root, environment)
        )
        if status in {"error", "stale", "unavailable"}:
            result["deployed_revision"] = _missing(
                f"Deployment is {status}; no successful revision-bound deployment verified."
            )
        sha = deployed.get("commit_sha")
        if (
            status != "success"
            or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{40,64}", sha)
            or not _contains(root, slug, merged, sha)
        ):
            return result
        deployed_environment = deployed.get("environment")
        if not isinstance(deployed_environment, str) or not deployed_environment:
            return result
        source = f"https://github.com/{slug}/deployments"
        result["deployed_revision"] = _available(
            {"revision": sha, "environment": deployed_environment}, source
        )
        digest = deployed.get("digest")
        if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            result["container_image_digest"] = _available(digest, source)
        # Generic providers can expose revision-bound promotion/runtime attestations.
        for field in ("infrastructure_promotion", "runtime_verification"):
            attestation = deployed.get(field)
            if (
                isinstance(attestation, dict)
                and attestation.get("revision") == sha
                and attestation.get("environment") == deployed_environment
                and attestation.get("status") == "passed"
                and isinstance(url := attestation.get("url"), str)
                and url.startswith("https://")
            ):
                result[field] = _available(
                    {"revision": sha, "environment": deployed_environment, "status": "passed"}, url
                )
    except operations.OperationsError as exc:
        result["deployed_revision"] = _missing(str(exc))
    return result

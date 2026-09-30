"""Generate and reverify Story delivery inputs from validation and GitHub state."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectClient
from wood_project.story import workflow
from wood_project.story.activity import MAX_COMMENT_BYTES
from wood_project.story.models import StoryWorkflowError
from wood_project.story.openproject import work_package_description_text

from . import operations
from .delivery import collect_delivery
from .workflow_files import (
    WorkflowFilesError,
    output_directory,
    read_json,
    revision_fingerprint,
    run_directory,
    write_json,
)

KIND = "wood-story-evidence"


def _invalid(message: str) -> StoryWorkflowError:
    return StoryWorkflowError("INVALID_EVIDENCE", message)


def _document(root: Path, path: str) -> dict[str, Any]:
    value = operations._gh(root, path)
    if not isinstance(value, dict):
        raise _invalid("GitHub evidence response must be an object.")
    return value


def _repository_name(value: object) -> str | None:
    return value.get("full_name") if isinstance(value, dict) else None


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise _invalid("Generated input is missing; regenerate it with story evidence.") from exc


def _verify(
    client: OpenProjectClient,
    root: Path,
    story_id: int,
    validation_path: Path,
    pr_number: int,
    run_id: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if pr_number <= 0 or run_id <= 0:
        raise _invalid("PR and CI run IDs must be positive integers.")
    story = workflow._story(client, story_id)
    slug = operations._slug(root)
    repository = workflow._repository(work_package_description_text(story))
    if repository != slug.split("/")[1] or root.name != repository:
        raise _invalid("Story Primary Repository must match the current repository.")
    dirty = operations._run(["git", "status", "--porcelain"], root)
    if dirty.returncode or dirty.stdout.strip():
        raise _invalid("Evidence requires a clean checkout of the merged change.")
    record = read_json(validation_path)
    if (
        record.get("schema_version") != 1
        or record.get("kind") != "wood-repository-validation"
        or record.get("repository_root") != str(root.resolve())
        or record.get("passed") is not True
        or not isinstance(record.get("log_dir"), str)
    ):
        raise _invalid(
            "Validation file must be a passing Wood validation record for this repository."
        )
    required = operations.repo_info(root)["validation"]
    assert isinstance(required, dict)
    checks = record.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(check, dict)
            or check.get("state") != "passed"
            or check.get("exit_code") != 0
            or not isinstance(check.get("name"), str)
            or not isinstance(check.get("log_path"), str)
            or not Path(check["log_path"]).is_file()
            for check in checks
        )
        or sorted(check["name"] for check in checks) != sorted(required["checks"])
    ):
        raise _invalid("All required checks and their full logs must be present and passed.")
    pr = _document(root, f"repos/{slug}/pulls/{pr_number}")
    head = pr.get("head")
    base = pr.get("base")
    if (
        pr.get("number") != pr_number
        or pr.get("merged") is not True
        or not isinstance(head, dict)
        or not isinstance(base, dict)
        or base.get("ref") != "main"
        or _repository_name(base.get("repo")) != slug
        or not re.fullmatch(rf"feature/op-{story_id}-.+", str(head.get("ref") or ""))
    ):
        raise _invalid(
            "PR must be merged into this repository's main and belong to this Story branch."
        )
    source_sha = head.get("sha")
    merge_sha = pr.get("merge_commit_sha")
    if (
        not isinstance(source_sha, str)
        or not isinstance(merge_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", source_sha)
        or not re.fullmatch(r"[0-9a-f]{40,64}", merge_sha)
    ):
        raise _invalid("PR source and merge revisions are missing or invalid.")
    merged = operations._run(["git", "merge-base", "--is-ancestor", merge_sha, "HEAD"], root)
    if merged.returncode:
        raise _invalid("Current checkout does not contain the PR merge; fetch and update main.")
    fingerprint = revision_fingerprint(root, source_sha)
    if record.get("source_fingerprint") != fingerprint:
        raise _invalid(
            "Validation results do not match the PR's implementation files; validate again."
        )
    run = _document(root, f"repos/{slug}/actions/runs/{run_id}")
    if (
        run.get("id") != run_id
        or _repository_name(run.get("repository")) != slug
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("head_sha") not in {source_sha, merge_sha}
        or str(run.get("path") or "").split("@", 1)[0] != ".github/workflows/validate.yml"
    ):
        raise _invalid("CI must be a passed validation run for this PR revision and repository.")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "kind": KIND,
        "story_id": story_id,
        "repository": slug,
        "source_fingerprint": fingerprint,
        "validation_file": str(validation_path.resolve()),
        "validation_sha256": _file_hash(validation_path),
        "repository_checks": [{"name": check["name"], "status": "passed"} for check in checks],
        "ci": {
            "run_id": run_id,
            "status": "passed",
            "url": f"https://github.com/{slug}/actions/runs/{run_id}",
            "head_sha": run["head_sha"],
        },
        "pull_request": {
            "number": pr_number,
            "url": f"https://github.com/{slug}/pull/{pr_number}",
            "source_sha": source_sha,
            "merge_sha": merge_sha,
        },
    }
    return evidence, story, record, pr


def generate_evidence(
    client: OpenProjectClient,
    story_id: int,
    *,
    validation_path: Path,
    pr_number: int,
    run_id: int,
    apply: bool,
) -> dict[str, Any]:
    root = operations.repository_root(Path.cwd())
    evidence, story, record, pr = _verify(
        client, root, story_id, validation_path, pr_number, run_id
    )
    delivery = collect_delivery(root, evidence, pr["head"]["ref"])
    checks = ", ".join(check["name"] for check in evidence["repository_checks"])
    update = (
        f"Implementation update (WP-{story_id})\n\n"
        f"Shipped: {str(story.get('subject') or '')[:160]}\n"
        f"PR: {evidence['pull_request']['url']}\n"
        f"Merge commit: {evidence['pull_request']['merge_sha']}\n"
        f"Validated implementation: {evidence['pull_request']['source_sha']}\n"
        f"Repository checks passed: {checks}\n"
        f"CI passed: {evidence['ci']['url']}\n"
        "Evidence verified against the current merged PR and CI state.\n"
    )
    if len(update.encode("utf-8")) > MAX_COMMENT_BYTES:
        raise _invalid("Implementation update exceeds the supported activity size.")
    result: dict[str, Any] = {
        "story_id": story_id,
        "dry_run": not apply,
        "output_directory": str(output_directory(root)),
        "validation_file": str(validation_path.resolve()),
        "log_dir": record["log_dir"],
        "pr_url": evidence["pull_request"]["url"],
        "ci_url": evidence["ci"]["url"],
        "delivery": delivery,
        "required_criteria": [
            "matching_repository_validation",
            "merged_story_pr",
            "passed_revision_ci",
            "posted_implementation_summary",
        ],
    }
    if apply:
        directory = run_directory(root, f"wood-story-{story_id}-evidence-")
        update_path = directory / "implementation-update.md"
        try:
            update_path.write_text(update, encoding="utf-8")
            update_path.chmod(0o600)
        except OSError as exc:
            raise WorkflowFilesError(
                "WORKFLOW_WRITE_FAILED", "Cannot write implementation update."
            ) from exc
        evidence["implementation_update"] = {
            "path": str(update_path),
            "sha256": _file_hash(update_path),
        }
        delivery_path = directory / "delivery-snapshot.json"
        write_json(delivery_path, delivery)
        evidence["delivery_snapshot"] = {
            "path": str(delivery_path),
            "sha256": _file_hash(delivery_path),
        }
        evidence_path = directory / "completion-evidence.json"
        write_json(evidence_path, evidence)
        result.update(evidence_file=str(evidence_path), update_file=str(update_path))
    return result


def verify_generated_evidence(
    client: OpenProjectClient, story_id: int, evidence: dict[str, Any]
) -> None:
    if evidence.get("kind") != KIND or evidence.get("schema_version") != 1:
        raise _invalid("Expected generated Wood Story evidence.")
    if type(evidence.get("story_id")) is not int or evidence["story_id"] != story_id:
        raise _invalid("Generated evidence belongs to another Story.")
    try:
        validation_path = Path(evidence["validation_file"])
        pr_number = evidence["pull_request"]["number"]
        run_id = evidence["ci"]["run_id"]
        if type(pr_number) is not int or type(run_id) is not int:
            raise ValueError("invalid IDs")
        fresh, *_ = _verify(
            client,
            operations.repository_root(Path.cwd()),
            story_id,
            validation_path,
            pr_number,
            run_id,
        )
        for key, value in fresh.items():
            if evidence.get(key) != value:
                raise _invalid(
                    "Generated evidence changed or no longer matches current verified results."
                )
        update = evidence["implementation_update"]
        if _file_hash(Path(update["path"])) != update["sha256"]:
            raise _invalid("Generated implementation update changed; regenerate it.")
        snapshot = evidence.get("delivery_snapshot")
        if snapshot is not None:
            if (
                not isinstance(snapshot, dict)
                or _file_hash(Path(snapshot["path"])) != snapshot["sha256"]
            ):
                raise _invalid("Delivery snapshot changed; regenerate it.")
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid("Generated evidence structure is invalid; regenerate it.") from exc

"""Bounded, read-only reconciliation of one Story's authoritative delivery links."""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from wood_project.openproject import OpenProjectClient, OpenProjectError, load_settings
from wood_project.story import workflow
from wood_project.story.models import StoryWorkflowError
from wood_project.story.openproject import work_package_description_text

from . import operations
from .delivery import _available, _missing, collect_optional_delivery
from .output import envelope
from .release_pipeline import collect_release_pipeline


def add_delivery_parser(commands: argparse._SubParsersAction[Any]) -> None:
    parser = commands.add_parser("delivery", help="Reconcile authoritative Story delivery links")
    actions = parser.add_subparsers(dest="delivery_command", required=True)
    status = actions.add_parser("status", help="Inspect one point-in-time delivery reconciliation")
    status.add_argument("id", type=int)
    status.add_argument(
        "--pr", type=int, help="Select an exact Story PR when discovery is ambiguous"
    )
    status.add_argument("--environment", help="Select the deployment environment")
    status.add_argument("--json", dest="delivery_json", action="store_true")


def _document(root: Path, path: str) -> dict[str, Any]:
    data = operations._gh(root, path)
    if not isinstance(data, dict):
        raise operations.OperationsError("DELIVERY_INVALID", "Expected an authority object.")
    return data


def _revision(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40,64}", value) is not None


def reconcile(
    client: OpenProjectClient,
    root: Path,
    story_id: int,
    *,
    pr_number: int | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    if story_id <= 0 or (pr_number is not None and pr_number <= 0):
        raise StoryWorkflowError("INVALID_ID", "Story and PR IDs must be positive.")
    story = workflow._story(client, story_id)
    fields = {
        name: _missing("A merged Story PR is required to resolve this link.")
        for name in (
            "repository",
            "source_revision",
            "merged_revision",
            "pull_request",
            "ci",
            "release",
            "release_run",
            "image_digest",
            "infrastructure_promotion",
            "deployment_environment",
            "deployed_revision",
            "verification_state",
        )
    }
    result: dict[str, Any] = {
        "story_id": story_id,
        "observed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "release_jobs": [],
        "release_jobs_truncated": False,
        "story": _available(story_id, client.settings.base_url + f"/work_packages/{story_id}"),
        "fields": fields,
        "delivery_stage": "repository",
        "blocker": None,
        "next_action": "Resolve the Story Primary Repository and run from its checkout.",
    }

    def stop(stage: str, reason: str, action: str) -> dict[str, Any]:
        result.update(delivery_stage=stage, blocker=reason, next_action=action)
        return result

    repository = workflow._repository(work_package_description_text(story))
    if not repository:
        return stop("repository", "Story has no Primary Repository linkage.", result["next_action"])
    try:
        slug = operations._slug(root)
        if repository != slug.split("/")[1]:
            return stop(
                "repository",
                "Story Primary Repository differs from this checkout.",
                result["next_action"],
            )
        fields["repository"] = _available(slug, f"https://github.com/{slug}")
        result["delivery_stage"] = "pull_request"
        info = operations.repo_info(root)
        deployment = info["deployment"]
        release = info["release"]
        assert isinstance(deployment, dict) and isinstance(release, dict)
        if not release["semantic_release"]:
            fields["release"] = _missing("Semantic release is not configured.", applicable=False)
            fields["release_run"] = _missing(
                "Semantic release is not configured.", applicable=False
            )
        if not deployment["applicable"]:
            for name in (
                "image_digest",
                "infrastructure_promotion",
                "deployment_environment",
                "deployed_revision",
                "verification_state",
            ):
                fields[name] = _missing("No deployment workflow is configured.", applicable=False)
        if pr_number is None:
            query = urlencode(
                {"q": f"repo:{slug} is:pr head:feature/op-{story_id}-", "per_page": 50}
            )
            search = _document(root, f"search/issues?{query}")
            if (
                search.get("incomplete_results") is not False
                or not isinstance(search.get("total_count"), int)
                or search["total_count"] > 50
                or not isinstance(search.get("items"), list)
            ):
                return stop(
                    "pull_request",
                    "PR discovery is incomplete or exceeds 50 results.",
                    "Specify --pr with the exact Story PR number.",
                )
            candidates = search["items"]
            if not candidates:
                fields["pull_request"] = _missing("No Story branch PR was found in GitHub.")
                return stop(
                    "implementation",
                    "No Story PR linkage found.",
                    "Implement and validate the Story, then open its Story branch PR.",
                )
            if len(candidates) != 1:
                return stop(
                    "pull_request",
                    "Multiple Story PR candidates were found.",
                    "Specify --pr with the intended Story PR number.",
                )
            pr_number = candidates[0].get("number")
            if type(pr_number) is not int or pr_number <= 0:
                raise operations.OperationsError(
                    "DELIVERY_INVALID", "PR candidate has no valid ID."
                )
        pr = _document(root, f"repos/{slug}/pulls/{pr_number}")
        head, base = pr.get("head"), pr.get("base")
        if (
            not isinstance(head, dict)
            or not isinstance(base, dict)
            or not re.fullmatch(rf"feature/op-{story_id}-.+", str(head.get("ref", "")))
            or base.get("ref") != "main"
            or not isinstance(base.get("repo"), dict)
            or base["repo"].get("full_name") != slug
            or not _revision(head.get("sha"))
        ):
            return stop(
                "pull_request",
                "PR does not resolve to this Story branch and repository main.",
                "Specify --pr with a verified Story branch PR targeting repository main.",
            )
        url = f"https://github.com/{slug}/pull/{pr_number}"
        state = "merged" if pr.get("merged") is True else pr.get("state")
        fields["pull_request"] = _available({"number": pr_number, "state": state}, url)
        fields["source_revision"] = _available(head["sha"], url)
        result["delivery_stage"] = "ci"
        merged = pr.get("merge_commit_sha") if state == "merged" else None
        pipeline = None
        if isinstance(merged, str) and _revision(merged):
            fields["merged_revision"] = _available(merged, url)
            optional = collect_optional_delivery(root, slug, merged, environment)
            for target, source in (
                ("release", "semantic_release"),
                ("image_digest", "container_image_digest"),
                ("infrastructure_promotion", "infrastructure_promotion"),
                ("deployed_revision", "deployed_revision"),
                ("verification_state", "runtime_verification"),
            ):
                fields[target] = optional[source]
            deployed = fields["deployed_revision"]
            fields["deployment_environment"] = (
                _available(deployed["value"]["environment"], deployed["source"])
                if deployed["state"] == "available"
                else dict(deployed)
            )
            if release["semantic_release"]:
                release_revisions = [merged]
                if fields["release"]["state"] == "available":
                    revision = fields["release"]["value"].get("revision")
                    if _revision(revision):
                        release_revisions.append(revision)
                pipeline = collect_release_pipeline(root, slug, release_revisions)
                fields["release_run"] = pipeline["field"]
                result["release_jobs"] = pipeline["jobs"]
                result["release_jobs_truncated"] = pipeline["jobs_truncated"]
        revisions = {head["sha"], merged} if _revision(merged) else {head["sha"]}
        runs = _document(
            root,
            f"repos/{slug}/actions/workflows/validate.yml/runs?"
            + urlencode({"head_sha": head["sha"], "per_page": 1}),
        )
        matching = runs.get("workflow_runs")
        if not isinstance(matching, list) or not matching:
            if _revision(merged):
                runs = _document(
                    root,
                    f"repos/{slug}/actions/workflows/validate.yml/runs?"
                    + urlencode({"head_sha": merged, "per_page": 1}),
                )
                matching = runs.get("workflow_runs")
        run = matching[0] if isinstance(matching, list) and matching else None
        if (
            not isinstance(run, dict)
            or run.get("head_sha") not in revisions
            or type(run.get("id")) is not int
        ):
            return stop(
                "ci",
                "No revision-bound validation run is available.",
                "Run centralized validation for the Story PR revision.",
            )
        ci_state = run.get("conclusion") or run.get("status")
        fields["ci"] = _available(
            {"state": ci_state, "run_id": run.get("id")},
            f"https://github.com/{slug}/actions/runs/{run.get('id')}",
        )
        if ci_state != "success" or run.get("status") != "completed":
            return stop(
                "ci",
                "Validation has not completed successfully.",
                "Resolve failed validation or await the pending validation run.",
            )
        if state != "merged":
            return stop(
                "pull_request",
                "Story PR is closed without merge."
                if state == "closed"
                else "Story PR awaits review and merge.",
                "Review the Story PR and obtain approval before merging.",
            )
        if not _revision(merged):
            return stop(
                "pull_request",
                "Merged PR has no valid merge revision.",
                "Repair the missing GitHub merge revision linkage.",
            )
        result["delivery_stage"] = "release"
        if pipeline is not None and pipeline["blocker"] is not None:
            blocker = pipeline["blocker"]
            return stop(blocker["stage"], blocker["reason"], blocker["action"])
        for field, stage, action in (
            (
                "release",
                "release",
                "Inspect semantic release for a published release containing the Story merge.",
            ),
            (
                "image_digest",
                "container_image",
                "Resolve a revision-bound container digest from deployment authority.",
            ),
            (
                "infrastructure_promotion",
                "infrastructure_promotion",
                "Resolve revision-bound infrastructure promotion evidence.",
            ),
            (
                "deployed_revision",
                "deployment",
                "Inspect deployment status; select --environment if needed.",
            ),
            (
                "verification_state",
                "runtime_verification",
                "Run and publish revision-bound runtime verification.",
            ),
        ):
            if fields[field]["state"] == "unavailable":
                return stop(stage, fields[field]["reason"], action)
        result.update(
            delivery_stage="delivered",
            blocker=None,
            next_action="Review Story completion evidence and close through the Story workflow.",
        )
        return result
    except operations.OperationsError as exc:
        return stop(
            result["delivery_stage"],
            str(exc),
            "Restore authority access or repair linkage, then rerun delivery status.",
        )


def run_delivery_command(args: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    try:
        client = OpenProjectClient(load_settings())
        data = reconcile(
            client,
            operations.repository_root(cwd),
            args.id,
            pr_number=args.pr,
            environment=args.environment,
        )
        return envelope(
            command="delivery status",
            status="success",
            summary=f"Story {args.id}: {data['delivery_stage']}.",
            data=data,
            next_actions=[data["next_action"]],
        )
    except (StoryWorkflowError, operations.OperationsError) as exc:
        return envelope(
            command="delivery status",
            status="invalid" if isinstance(exc, StoryWorkflowError) else exc.status,
            summary=str(exc),
            errors=[{"code": exc.code, "message": str(exc)}],
        )
    except OpenProjectError as exc:
        return envelope(
            command="delivery status",
            status="unavailable",
            summary="OpenProject request failed.",
            errors=[{"code": exc.code, "message": "Check connection, credentials, and access."}],
        )

"""Deterministic milestones with a private durable outbox and duplicate-safe submission."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectClient
from wood_project.story import session, workflow
from wood_project.story.activity import add_milestone
from wood_project.story.models import StoryWorkflowError
from wood_project.story.openproject import work_package_description_text

from . import delivery_status, operations
from .delivery import _available
from .workflow_files import output_directory, read_json, snapshot_fingerprint


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _invalid(message: str) -> StoryWorkflowError:
    return StoryWorkflowError("MILESTONE_INVALID", message)


def _validation(root: Path, path: Path) -> dict[str, Any]:
    record = read_json(path)
    checks = record.get("checks")
    required = operations.repo_info(root)["validation"]
    assert isinstance(required, dict)
    if (
        record.get("kind") != "wood-repository-validation"
        or record.get("schema_version") != 1
        or record.get("repository_root") != str(root)
        or record.get("passed") is not True
        or record.get("source_fingerprint") is None
        or record["source_fingerprint"] != snapshot_fingerprint(root, root, output_directory(root))
        or not isinstance(checks, list)
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
        raise _invalid("Supply current passing Wood validation with all required checks and logs.")
    return _available(
        {
            "state": "passed",
            "source_fingerprint": record["source_fingerprint"],
            "checks": sorted(check["name"] for check in checks),
        },
        str(path.resolve()),
    )


def _collect(
    client: OpenProjectClient,
    root: Path,
    story_id: int,
    claim: dict[str, Any],
    validation: Path | None,
    pr: int | None,
    operator_approval: dict[str, str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    story = workflow._story(client, story_id)
    slug = operations._slug(root)
    if not workflow.repository_matches(
        workflow._repository(work_package_description_text(story)), slug
    ):
        raise _invalid("Story Primary Repository must match the current repository.")
    events = {
        "started": _available(
            {
                "branch": claim["branch"],
                "base_revision": claim["branching"]["base_revision"],
                "target_branch": claim["branching"]["target_branch"],
            },
            client.settings.base_url.rstrip("/") + f"/work_packages/{story_id}",
        )
    }
    if validation is not None:
        events["validation"] = _validation(root, validation)
    snapshot = None
    if pr is not None:
        snapshot = delivery_status.reconcile(
            client,
            root,
            story_id,
            pr_number=pr,
            operator_approval=operator_approval,
            hotfix_incident=claim["branching"].get("hotfix_incident"),
            hotfix_approval=claim["branching"].get("hotfix_approval"),
        )
        for name in ("pull_request", "review", "ci", "merged_revision"):
            field = snapshot["fields"][name]
            if field["state"] == "available":
                events[name] = field
        # Optional provider states are retained in the saved snapshot, separately
        # from the compact activity. Observations never authorize a release/deploy.
    return events, snapshot


def _readiness(root: Path, path: Path, claim: dict[str, Any]) -> dict[str, Any]:
    record = read_json(path)
    plan = record.get("branching")
    expected = claim["branching"]
    if (
        not isinstance(plan, dict)
        or plan.get("strategy") != "integration"
        or any(
            plan.get(key) != expected.get(key)
            for key in ("strategy", "release_id", "base_branch", "target_branch", "scope_reference")
        )
        or type(record.get("ready_for_release_review")) is not bool
        or not isinstance(record.get("observed_at"), str)
        or record.get("authorization_granted") is not False
        or record.get("main_revision") != session.git(root, "rev-parse", "refs/remotes/origin/main")
        or record.get("integration_revision")
        != session.git(root, "rev-parse", f"refs/remotes/origin/{plan['base_branch']}")
    ):
        raise _invalid("Readiness must match the claim scope and cached remote revisions.")
    return _available(
        {
            key: record[key]
            for key in (
                "observed_at",
                "main_revision",
                "integration_revision",
                "ready_for_release_review",
            )
        },
        str(path.resolve()),
    )


def report(
    client: OpenProjectClient,
    story_id: int,
    *,
    owner: str,
    apply: bool,
    validation: Path | None = None,
    pr: int | None = None,
    retry: bool = False,
    readiness: Path | None = None,
    operator_approval: dict[str, str] | None = None,
) -> dict[str, Any]:
    root, directory = session.locations()
    with session.locked(directory, apply=apply):
        claim = session.require_owner(session.read_record(directory, story_id), owner)
        if (
            claim["worktree"] != str(root)
            or claim["branch"] != session.git(root, "branch", "--show-current")
            or claim["server"] != client.settings.base_url.rstrip("/")
        ):
            raise _invalid("Milestone reporting must use the claimed worktree, branch and server.")
        if retry and (
            validation is not None
            or pr is not None
            or readiness is not None
            or operator_approval is not None
        ):
            raise _invalid("Retry uses retained evidence; omit new validation and PR selectors.")
        events, snapshot = (
            ({}, None)
            if retry
            else _collect(client, root, story_id, claim, validation, pr, operator_approval)
        )
        if operator_approval is not None and pr is None:
            raise _invalid("An operator approval requires an explicit PR selection.")
        if readiness is not None:
            events["release_readiness"] = _readiness(root, readiness, claim)
            if snapshot is not None:
                snapshot["fields"]["release_readiness"] = events["release_readiness"]
        outbox = directory / f"{story_id}-milestones"
        if outbox.is_symlink():
            raise _invalid("Milestone outbox must not be a symlink.")
        binding = {key: claim[key] for key in ("server", "story_id", "repository", "branch")}
        planned: list[dict[str, Any]] = []
        for kind, observation in events.items():
            event = {"schema_version": 1, **binding, "kind": kind, "observation": observation}
            key = _hash(event)
            planned.append({"event": event, "key": key})
        if not apply:
            return {
                "story_id": story_id,
                "dry_run": True,
                "milestones": planned,
                "delivery": snapshot,
                "outbox": str(outbox),
                "next_action": "Apply to submit milestones; --retry uses pending evidence.",
            }
        outbox.mkdir(mode=0o700, exist_ok=True)
        # Save every new event before the first network write. Interrupted or
        # ambiguous POST/readback failures remain pending and safe to reconcile.
        for item in planned:
            path = outbox / f"{item['key']}.json"
            if not path.exists():
                session.write_atomic(
                    path,
                    {
                        **item,
                        "status": "pending",
                        "activity_id": None,
                        "observed_at": datetime.now(UTC).isoformat(),
                    },
                )
        snapshot_path = None
        if snapshot is not None:
            snapshot_path = outbox / "delivery.json"
            session.write_atomic(snapshot_path, snapshot)
        paths = sorted(outbox.glob("*.json"))
        if len(paths) > 1001:
            raise _invalid("Outbox exceeds 1000 events; retain it and resolve before reporting.")
        pending: list[tuple[Path, dict[str, Any]]] = []
        for path in paths:
            if path.name == "delivery.json":
                continue
            if path.is_symlink() or path.stat().st_size > 16384:
                raise _invalid("Outbox record is unsafe or too large.")
            item = read_json(path)
            saved_event = item.get("event")
            if (
                not isinstance(saved_event, dict)
                or any(saved_event.get(k) != v for k, v in binding.items())
                or item.get("key") != _hash(saved_event)
                or path.stem != item["key"]
                or item.get("status") not in {"pending", "sent"}
                or not isinstance(item.get("observed_at"), str)
            ):
                raise _invalid("Outbox event changed or belongs to another Story binding.")
            if item["status"] == "pending":
                pending.append((path, item))
        sent, failures = [], []
        for path, item in pending[:20]:
            event = item["event"]
            comment = f"Wood milestone (WP-{story_id}; {item['key']})\n\n" + json.dumps(
                {
                    "story_id": story_id,
                    "kind": event["kind"],
                    "observation": event["observation"],
                    "observed_at": item["observed_at"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(comment.encode()) > 4096:
                raise _invalid("Milestone exceeds activity size; retained for inspection.")
            try:
                posted = add_milestone(client, story_id, item["key"], comment)
            except StoryWorkflowError as exc:
                failures.append({"key": item["key"], "code": exc.code})
                break
            item.update(status="sent", activity_id=posted["activity_id"])
            session.write_atomic(path, item)
            sent.append({"key": item["key"], "activity_id": posted["activity_id"]})
        remaining = len(pending) - len(sent)
        return {
            "story_id": story_id,
            "dry_run": False,
            "observed_at": datetime.now(UTC).isoformat(),
            "outbox": str(outbox),
            "submitted": sent,
            "pending_count": remaining,
            "failures": failures,
            "delivery_file": str(snapshot_path) if snapshot_path else None,
            "delivery": snapshot,
            "limitations": [
                "Host-local locking coordinates worktrees, not independent hosts or clones.",
                "Reporting does not run validation, publish, deploy or close a Story.",
                "Readiness is a saved observation against cached refs, not live remote state.",
            ],
            "next_action": "Retry pending milestones with --retry --apply."
            if remaining
            else "Milestones submitted; continue the Story workflow.",
        }

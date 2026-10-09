"""Public Story workflow command adapter."""

from __future__ import annotations

import argparse
import json
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectClient, OpenProjectError, load_settings
from wood_project.story import session, workflow
from wood_project.story.activity import (
    add_activity,
    inspect_activities,
    read_comment,
    require_summary,
    upsert_summary,
)
from wood_project.story.models import StoryWorkflowError
from wood_project.story.repository_context import story_reference

from . import evidence as story_evidence
from . import operations
from .output import Mutation, Status, envelope
from .workflow_files import WorkflowFilesError, read_json


def add_story_parser(commands: argparse._SubParsersAction[Any]) -> None:
    parser = commands.add_parser("story", help="Discover and manage OpenProject Stories")
    actions = parser.add_subparsers(dest="story_command", required=True)
    for action in ("list", "next"):
        item = actions.add_parser(action)
        item.add_argument("ref", nargs="?", help="Override repository Initiative with ID or name")
        if action == "list":
            item.add_argument("--status")
            item.add_argument("--version")
            item.add_argument("--offset", type=int, default=0)
        item.add_argument("--json", dest="story_json", action="store_true")
    item = actions.add_parser("get")
    item.add_argument("id", type=int)
    item.add_argument("--offset", type=int, default=0)
    item.add_argument("--json", dest="story_json", action="store_true")
    item = actions.add_parser(
        "evidence", help="Generate verified delivery inputs for a merged Story"
    )
    item.add_argument("id", type=int)
    item.add_argument("--validation", type=Path, required=True)
    item.add_argument("--verification", type=Path)
    item.add_argument("--pr", type=int, required=True)
    item.add_argument("--ci-run", type=int, required=True)
    item.add_argument("--apply", action="store_true")
    item.add_argument("--json", dest="story_json", action="store_true")
    item = actions.add_parser("create")
    item.add_argument("--project", type=int, required=True)
    item.add_argument("--initiative", type=int, required=True)
    item.add_argument("--epic", type=int, required=True)
    item.add_argument("--subject", required=True)
    item.add_argument("--goal", required=True)
    item.add_argument("--requirements", required=True)
    item.add_argument("--acceptance", action="append", required=True)
    item.add_argument("--repository")
    item.add_argument("--version", type=int, required=True)
    item.add_argument("--apply", action="store_true")
    item.add_argument("--json", dest="story_json", action="store_true")
    for action in ("set-status", "start", "block", "complete"):
        item = actions.add_parser(action)
        item.add_argument("id", type=int)
        item.add_argument("--owner", required=action == "start")
        if action == "start":
            item.add_argument("--worktree", type=Path, required=True)
            item.add_argument("--project", type=int)
            item.add_argument("--initiative", type=int)
        if action == "set-status":
            item.add_argument("target")
        if action == "block":
            item.add_argument("--reason", required=True)
        if action == "complete":
            item.add_argument("--evidence", type=Path, required=True)
        item.add_argument("--apply", action="store_true")
        item.add_argument("--json", dest="story_json", action="store_true")
    activity = actions.add_parser("activity")
    activity_actions = activity.add_subparsers(dest="activity_command", required=True)
    listing = activity_actions.add_parser("list")
    listing.add_argument("id", type=int)
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--json", dest="story_json", action="store_true")
    add = activity_actions.add_parser("add")
    add.add_argument("id", type=int)
    source = add.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--evidence", type=Path)
    add.add_argument("--apply", action="store_true")
    add.add_argument("--owner")
    add.add_argument("--json", dest="story_json", action="store_true")
    summary = activity_actions.add_parser("summary")
    summary.add_argument("id", type=int)
    source = summary.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--evidence", type=Path)
    summary.add_argument("--expected-sha256", required=True)
    summary.add_argument("--apply", action="store_true")
    summary.add_argument("--owner")
    summary.add_argument("--json", dest="story_json", action="store_true")
    local = actions.add_parser(
        "session", help="Inspect, checkpoint, hand off or release a local claim"
    )
    local_actions = local.add_subparsers(dest="session_command", required=True)
    for action in ("get", "checkpoint", "handoff", "release"):
        item = local_actions.add_parser(action)
        item.add_argument("id", type=int)
        item.add_argument("--owner", required=action in {"checkpoint", "release"})
        if action == "checkpoint":
            item.add_argument(
                "--phase",
                choices=("implementation", "review", "delivery", "blocked", "complete"),
                required=True,
            )
            item.add_argument("--next-action", required=True)
            item.add_argument("--record", type=Path, action="append", default=[])
        if action != "get":
            item.add_argument("--apply", action="store_true")
        item.add_argument("--json", dest="story_json", action="store_true")


def run_story_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.story_command
    command = f"story {action}" + (
        f" {args.activity_command}"
        if action == "activity"
        else f" {args.session_command}"
        if action == "session"
        else ""
    )
    apply = getattr(args, "apply", False)
    try:
        if action == "session":
            data = session.session_command(
                args.session_command,
                args.id,
                owner=args.owner,
                apply=apply,
                phase=getattr(args, "phase", None),
                next_action=getattr(args, "next_action", None),
                records=getattr(args, "record", None),
            )
            return envelope(
                command=command,
                status="success",
                summary="Local Story session inspected.",
                mutation="mutating"
                if apply
                else "read-only"
                if args.session_command == "get"
                else "preview",
                data=data,
                next_actions=[data["next_action"]],
            )
        with ExitStack() as stack:
            claimed_server = None
            # Start owns its transaction. Serialize other repository lifecycle writes
            # with claims, retaining existing non-repository planning operations.
            if action in {"set-status", "block", "complete"} or (
                action == "activity" and args.activity_command != "list"
            ):
                try:
                    _, directory = session.locations()
                except StoryWorkflowError as exc:
                    if exc.code != "GIT_COMMAND_FAILED":
                        raise
                else:
                    stack.enter_context(session.locked(directory, apply=apply))
                    record = session.read_record(directory, args.id)
                    if record is not None and record["state"] == "claimed":
                        session.require_owner(record, args.owner)
                        claimed_server = record["server"]
            return _remote_story_command(
                args, command, action, apply, claimed_server=claimed_server
            )
    except StoryWorkflowError as exc:
        return _story_error(command, apply, exc)


def _remote_story_command(
    args: argparse.Namespace,
    command: str,
    action: str,
    apply: bool,
    *,
    claimed_server: str | None = None,
) -> dict[str, Any]:
    try:
        client = OpenProjectClient(load_settings())
        if claimed_server is not None and client.settings.base_url.rstrip("/") != claimed_server:
            raise StoryWorkflowError(
                "CLAIM_CONFLICT", "OpenProject server differs from the recorded claim."
            )
        if action == "list":
            ref, configured_project_id = story_reference(args.ref)
            data = workflow.list_stories(
                client,
                ref,
                status=args.status,
                version=args.version,
                offset=args.offset,
                configured_project_id=configured_project_id,
            )
        elif action == "get":
            data = workflow.get_story(client, args.id, offset=args.offset)
        elif action == "next":
            ref, configured_project_id = story_reference(args.ref)
            data = workflow.next_story(client, ref, configured_project_id=configured_project_id)
        elif action == "evidence":
            data = story_evidence.generate_evidence(
                client,
                args.id,
                validation_path=args.validation,
                verification_path=args.verification,
                pr_number=args.pr,
                run_id=args.ci_run,
                apply=apply,
            )
        elif action == "create":
            data = workflow.create_story(
                client,
                project_id=args.project,
                initiative_id=args.initiative,
                epic_id=args.epic,
                subject=args.subject,
                goal=args.goal,
                requirements=args.requirements,
                acceptance=args.acceptance,
                repository=args.repository,
                version_id=args.version,
                apply=apply,
            )
        elif action == "set-status":
            data = workflow.set_story_status(client, args.id, args.target, apply=apply)
        elif action == "start":
            data = workflow.start_story(
                client,
                args.id,
                apply=apply,
                owner=args.owner,
                worktree=args.worktree,
                project_id=args.project,
                initiative_id=args.initiative,
            )
        elif action == "block":
            data = workflow.block_story(client, args.id, args.reason, apply=apply)
        elif action == "activity":
            if args.activity_command == "list":
                data = inspect_activities(client, args.id, offset=args.offset)
            elif args.evidence:
                generated = read_json(args.evidence)
                story_evidence.verify_generated_evidence(client, args.id, generated)
                comment_path = Path(generated["implementation_update"]["path"])
            else:
                comment_path = args.file
            if args.activity_command == "summary":
                data = upsert_summary(
                    client,
                    args.id,
                    read_comment(comment_path),
                    expected_sha256=args.expected_sha256,
                    apply=apply,
                )
            elif args.activity_command == "add":
                data = add_activity(client, args.id, read_comment(comment_path), apply=apply)
        else:
            try:
                evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise StoryWorkflowError(
                    "INVALID_EVIDENCE", "Validation evidence must be a readable JSON file."
                ) from exc
            if not isinstance(evidence, dict):
                raise StoryWorkflowError(
                    "INVALID_EVIDENCE", "Validation evidence must be an object."
                )
            if evidence.get("kind") == story_evidence.KIND:
                story_evidence.verify_generated_evidence(client, args.id, evidence)
                require_summary(
                    client, args.id, read_comment(Path(evidence["implementation_update"]["path"]))
                )
            data = workflow.complete_story(client, args.id, evidence=evidence, apply=apply)
        mutation: Mutation = (
            "read-only"
            if action in {"list", "get", "next"}
            or (action == "activity" and args.activity_command == "list")
            else "mutating"
            if apply
            else "preview"
        )
        summary = f"Story {action} completed."
        epic = data.get("epic")
        if action == "complete" and isinstance(epic, dict) and epic.get("automatically_completed"):
            summary += f" Parent Epic WP-{epic['id']} automatically completed ({epic['status']})."
        return envelope(
            command=command,
            status="success",
            mutation=mutation,
            summary=summary,
            data=data,
            next_actions=[data["next_action"]] if "next_action" in data else [],
        )
    except (operations.OperationsError, WorkflowFilesError) as exc:
        return envelope(
            command=command,
            status=exc.status,
            mutation="mutating" if apply else "read-only",
            summary=str(exc),
            errors=[{"code": exc.code, "message": str(exc)}],
        )
    except StoryWorkflowError as exc:
        return _story_error(command, apply, exc)
    except OpenProjectError as exc:
        return envelope(
            command=command,
            status="unavailable",
            mutation="mutating" if apply else "read-only",
            summary="OpenProject request failed.",
            errors=[{"code": exc.code, "message": "Check connection, credentials, and access."}],
        )


def _story_error(command: str, apply: bool, exc: StoryWorkflowError) -> dict[str, Any]:
    status: Status = (
        "blocked"
        if exc.code
        in {
            "DEPENDENCY_BLOCKED",
            "NOT_READY",
            "VALIDATION_REQUIRED",
            "INVALID_TRANSITION",
            "CLAIM_BUSY",
            "CLAIM_CONFLICT",
            "CLAIM_REQUIRED",
            "WORKTREE_CONFLICT",
            "DIRTY_WORKTREE",
        }
        else "ambiguous"
        if exc.code == "AMBIGUOUS_SELECTOR"
        else "invalid"
    )
    return envelope(
        command=command,
        status=status,
        mutation="mutating" if apply else "read-only",
        summary=str(exc),
        errors=[{"code": exc.code, "message": str(exc)}],
    )

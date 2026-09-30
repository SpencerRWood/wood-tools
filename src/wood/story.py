"""Public Story workflow command adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectClient, OpenProjectError, load_settings
from wood_project.story import workflow
from wood_project.story.activity import add_activity, read_comment
from wood_project.story.models import StoryWorkflowError
from wood_project.story.repository_context import story_reference

from .output import Mutation, Status, envelope


def add_story_parser(commands: argparse._SubParsersAction[Any]) -> None:
    parser = commands.add_parser("story", help="Discover and manage OpenProject Stories")
    actions = parser.add_subparsers(dest="story_command", required=True)
    for action in ("list", "next"):
        item = actions.add_parser(action)
        item.add_argument("ref", nargs="?")
        if action == "list":
            item.add_argument("--status")
            item.add_argument("--version")
            item.add_argument("--offset", type=int, default=0)
        item.add_argument("--json", dest="story_json", action="store_true")
    item = actions.add_parser("get")
    item.add_argument("id", type=int)
    item.add_argument("--offset", type=int, default=0)
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
    add = activity_actions.add_parser("add")
    add.add_argument("id", type=int)
    add.add_argument("--file", type=Path, required=True)
    add.add_argument("--apply", action="store_true")
    add.add_argument("--json", dest="story_json", action="store_true")


def run_story_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.story_command
    command = f"story {action}" + (" add" if action == "activity" else "")
    apply = getattr(args, "apply", False)
    try:
        client = OpenProjectClient(load_settings())
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
            data = workflow.start_story(client, args.id, apply=apply)
        elif action == "block":
            data = workflow.block_story(client, args.id, args.reason, apply=apply)
        elif action == "activity":
            data = add_activity(client, args.id, read_comment(args.file), apply=apply)
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
            data = workflow.complete_story(client, args.id, evidence=evidence, apply=apply)
        mutation: Mutation = (
            "read-only" if action in {"list", "get", "next"} else "mutating" if apply else "preview"
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
    except StoryWorkflowError as exc:
        status: Status = (
            "blocked"
            if exc.code
            in {"DEPENDENCY_BLOCKED", "NOT_READY", "VALIDATION_REQUIRED", "INVALID_TRANSITION"}
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
    except OpenProjectError as exc:
        return envelope(
            command=command,
            status="unavailable",
            mutation="mutating" if apply else "read-only",
            summary="OpenProject request failed.",
            errors=[{"code": exc.code, "message": "Check connection, credentials, and access."}],
        )

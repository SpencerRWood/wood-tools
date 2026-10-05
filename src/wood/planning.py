"""Read-only OpenProject Epic and planning Release commands."""

from __future__ import annotations

import argparse
import json
from typing import Any

from wood_project.openproject import OpenProjectClient, OpenProjectError, load_settings
from wood_project.openproject.context import RepositoryContextError, repository_context
from wood_project.story.discovery import fetch_collection
from wood_project.story.epics import completed_status_names, epic_stories, incomplete_stories
from wood_project.story.models import StoryWorkflowError
from wood_project.story.openproject import (
    extract_id_from_href,
    work_package_status_name,
    work_package_type_name,
)

from .output import Status, envelope
from .project import ProjectLookupError, _resolve


def add_planning_parsers(commands: argparse._SubParsersAction[Any]) -> None:
    for kind in ("epic", "release"):
        parser = commands.add_parser(kind, help=f"Inspect OpenProject {kind}s")
        actions = parser.add_subparsers(dest="planning_action", required=True)
        for action in ("list", "get"):
            item = actions.add_parser(action)
            if action == "get":
                item.add_argument("ref", help="Numeric ID or exact name")
            item.add_argument(
                "--project", type=int, help="Override repository OpenProject project ID"
            )
            item.add_argument("--status", help="Exact status filter (list only)")
            item.add_argument("--offset", type=int, default=0)
            item.add_argument("--json", dest="planning_json", action="store_true")


def _page(items: list[dict[str, Any]], offset: int) -> dict[str, Any]:
    return {
        "items": items[offset : offset + 50],
        "total": len(items),
        "offset": offset,
        "next_offset": offset + 50 if offset + 50 < len(items) else None,
    }


def _summary(item: dict[str, Any], kind: str) -> dict[str, Any]:
    project = item.get("_links", {}).get("definingProject" if kind == "release" else "project", {})
    result = {
        "id": item["id"],
        "name": item.get("subject") if kind == "epic" else item.get("name"),
        "status": work_package_status_name(item) if kind == "epic" else item.get("status"),
        "project": {
            "id": extract_id_from_href(project.get("href"), "projects"),
            "name": project.get("title"),
        },
    }
    if kind == "release":
        result.update(start_date=item.get("startDate"), end_date=item.get("endDate"))
    return result


def inspect_planning(client: OpenProjectClient, args: argparse.Namespace) -> dict[str, Any]:
    kind = args.command
    if args.offset < 0 or (args.project is not None and args.project <= 0):
        raise ProjectLookupError(
            "INVALID_INPUT", "Project must be positive and offset nonnegative."
        )
    if args.planning_action == "get" and args.status:
        raise ProjectLookupError("INVALID_INPUT", "Use --status with list only.")
    project_id = args.project
    if project_id is None:
        project_id = repository_context(keys=("project_id",)).project_id
        if project_id is None:
            raise ProjectLookupError(
                "INVALID_CONTEXT", "Configure [tool.wood.openproject].project_id or pass --project."
            )
    if kind == "epic":
        path = "/api/v3/work_packages"
        filters = [
            {"project": {"operator": "=", "values": [str(project_id)]}},
            {"status": {"operator": "*", "values": []}},
        ]
        query = {"filters": json.dumps(filters)}
    else:
        path = f"/api/v3/projects/{project_id}/versions"
        query = {}
    items = fetch_collection(client, path, query=query, page_size=100, require_total=True)
    if kind == "epic":
        items = [item for item in items if work_package_type_name(item) == "Epic"]
    items.sort(key=lambda item: int(item["id"]))
    if args.planning_action == "list":
        rows = [_summary(item, kind) for item in items]
        if args.status:
            rows = [row for row in rows if str(row["status"]).casefold() == args.status.casefold()]
        return {kind + "s": _page(rows, args.offset), "project_id": project_id}
    item = _resolve(items, args.ref, kind=kind.title())
    result: dict[str, Any] = {kind: _summary(item, kind), "project_id": project_id}
    if kind == "epic":
        statuses = fetch_collection(
            client, "/api/v3/statuses", query={}, page_size=100, require_total=True
        )
        completed = completed_status_names(statuses)
        stories = sorted(epic_stories(client, int(item["id"])), key=lambda child: int(child["id"]))
        incomplete = incomplete_stories(stories, completed)
        result.update(
            stories=_page([_summary(child, "epic") for child in stories], args.offset),
            incomplete_story_count=len(incomplete),
            incomplete_story_ids=sorted(int(child["id"]) for child in incomplete)[:50],
            already_complete=work_package_status_name(item).casefold() in completed,
            completion_ready=bool(stories) and not incomplete,
        )
    elif args.offset:
        raise ProjectLookupError("INVALID_INPUT", "Release get has no paginated children.")
    return result


def run_planning_command(args: argparse.Namespace) -> dict[str, Any]:
    command = f"{args.command} {args.planning_action}"
    try:
        data = inspect_planning(OpenProjectClient(load_settings()), args)
        return envelope(
            command=command, status="success", summary=f"{command}: success.", data=data
        )
    except (
        ProjectLookupError,
        StoryWorkflowError,
        OpenProjectError,
        RepositoryContextError,
    ) as exc:
        status: Status = (
            "ambiguous"
            if exc.code == "AMBIGUOUS_SELECTOR"
            else "unavailable"
            if isinstance(exc, OpenProjectError)
            or isinstance(exc.__cause__, OpenProjectError)
            or exc.code == "INCOMPLETE_COLLECTION"
            else "invalid"
        )
        return envelope(
            command=command,
            status=status,
            summary=f"{command}: {status}.",
            errors=[
                {
                    "code": exc.code,
                    "message": "Check OpenProject connection, credentials, and access."
                    if isinstance(exc, OpenProjectError)
                    or isinstance(exc.__cause__, OpenProjectError)
                    else str(exc),
                }
            ],
            data={"candidates": exc.candidates} if isinstance(exc, ProjectLookupError) else {},
        )

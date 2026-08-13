from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..openproject import OpenProjectClient, load_settings
from ..story import StoryWorkflowError, create_branch, discover_next_story, set_status

COMMAND = "story"


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-path", type=Path, help="Override wood-config path")
    parser.add_argument("--profile", help="wood-config profile to read")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("story", help="Manage OpenProject Story workflows")
    story_subparsers = parser.add_subparsers(dest="story_command", required=True)

    show_parser = story_subparsers.add_parser("show", help="Inspect a Story and its relations")
    show_parser.add_argument("work_package_id", type=int)
    _add_connection_options(show_parser)

    next_parser = story_subparsers.add_parser(
        "next", help="Discover the next dependency-ready Story"
    )
    next_parser.add_argument("root_work_package_id", nargs="?", type=int)
    next_parser.add_argument("--status", default="New")
    next_parser.add_argument("--type", default="Story")
    next_parser.add_argument("--page-size", type=int, default=1000)
    _add_connection_options(next_parser)

    status_parser = story_subparsers.add_parser("set-status", help="Preview or update Story status")
    status_parser.add_argument("work_package_id", type=int)
    status_parser.add_argument("target_status")
    status_parser.add_argument("--apply", action="store_true", help="Apply the status mutation")
    _add_connection_options(status_parser)

    branch_parser = story_subparsers.add_parser(
        "create-branch", help="Preview or create a Story branch"
    )
    branch_parser.add_argument("work_package_id", type=int)
    branch_parser.add_argument("--title")
    branch_parser.add_argument("--apply", action="store_true", help="Apply the git mutation")
    branch_parser.add_argument("--allow-dirty", action="store_true")
    branch_parser.add_argument("--json", action="store_true", help="Emit JSON output")


def handles(args: argparse.Namespace) -> bool:
    return args.command == COMMAND


def _client(config_path: Path | None, profile: str | None) -> OpenProjectClient:
    return OpenProjectClient(load_settings(config_path=config_path, profile=profile))


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    command = f"story-{args.story_command}"
    if args.story_command == "show":
        payload = _client(args.config_path, args.profile).story_context(args.work_package_id)
        return command, payload, None

    if args.story_command == "next":
        client = _client(args.config_path, args.profile)
        root_work_package_id = args.root_work_package_id or client.settings.initiative_id
        if root_work_package_id is None:
            raise StoryWorkflowError(
                "OPENPROJECT_INITIATIVE_UNAVAILABLE",
                (
                    "Pass <root-work-package-id> or set "
                    "initiative_id in the selected integrations.openproject.projects entry."
                ),
            )
        payload = discover_next_story(
            client=client,
            root_work_package_id=root_work_package_id,
            target_status=args.status,
            story_type=args.type,
            page_size=args.page_size,
        )
        return command, payload, None

    if args.story_command == "set-status":
        payload = set_status(
            work_package_id=args.work_package_id,
            target_status=args.target_status,
            client=_client(args.config_path, args.profile) if args.apply else None,
            apply=args.apply,
        )
        return command, payload, args.apply

    if args.story_command == "create-branch":
        payload = create_branch(
            work_package_id=args.work_package_id,
            title=args.title,
            apply=args.apply,
            allow_dirty=args.allow_dirty,
        )
        return command, payload, args.apply

    raise StoryWorkflowError("UNKNOWN_COMMAND", f"Unknown Story command: {args.story_command}")

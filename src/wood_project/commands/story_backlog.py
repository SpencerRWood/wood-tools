from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..story_backlog import export_snapshot, upload_plan

COMMANDS = {"backlog", "story-backlog"}


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env.resolved"),
        help="Resolved environment file path. Default: .env.resolved.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")


def _configure_parser(parser: argparse.ArgumentParser, *, legacy: bool) -> None:
    backlog_subparsers = parser.add_subparsers(dest="story_backlog_command", required=True)

    export_parser = backlog_subparsers.add_parser(
        "export", help="Export a read-only Story Backlog snapshot"
    )
    export_parser.add_argument("root_work_package_id", nargs="?", type=int)
    export_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/wood-tools/story-backlog"),
        help="Directory for story_backlog.json and story_backlog.xlsx.",
    )
    export_parser.add_argument("--story-type", default="Story")
    export_parser.add_argument("--epic-type", default="Epic")
    export_parser.add_argument("--closed-status", action="append", default=[])
    export_parser.add_argument("--story-id-field", default="")
    export_parser.add_argument("--requirement-ids-field", default="")
    export_parser.add_argument("--page-size", type=int, default=500)
    _add_common_options(export_parser)

    upload_name = "upload-plan" if legacy else "upload"
    upload_parser = backlog_subparsers.add_parser(
        upload_name, help="Build a deterministic Story Backlog upload plan without applying it"
    )
    upload_parser.add_argument("workbook", type=Path)
    upload_parser.add_argument("--sheet-name", default="Story Backlog")
    if legacy:
        upload_parser.add_argument("--parent-id", type=int)
    else:
        upload_parser.add_argument("--initiative-id", type=int)
        upload_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Explicitly request the default non-mutating upload plan.",
        )
    _add_common_options(upload_parser)


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "backlog", help="Export Story Backlog snapshots and preview upload plans"
    )
    _configure_parser(parser, legacy=False)

    legacy_parser = subparsers.add_parser("story-backlog", help=argparse.SUPPRESS)
    _configure_parser(legacy_parser, legacy=True)


def handles(args: argparse.Namespace) -> bool:
    return args.command in COMMANDS


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    command = (
        f"story-backlog-{args.story_backlog_command}"
        if args.command == "story-backlog"
        else f"backlog-{args.story_backlog_command}"
    )
    if args.story_backlog_command == "export":
        return (
            command,
            export_snapshot(
                root_work_package_id=args.root_work_package_id,
                env_file=args.env_file,
                output_dir=args.output_dir,
                story_type=args.story_type,
                epic_type=args.epic_type,
                closed_statuses=args.closed_status,
                story_id_field=args.story_id_field,
                requirement_ids_field=args.requirement_ids_field,
                page_size=args.page_size,
            ),
            None,
        )
    if args.story_backlog_command in {"upload", "upload-plan"}:
        return (
            command,
            upload_plan(
                workbook=args.workbook,
                env_file=args.env_file,
                sheet_name=args.sheet_name,
                parent_id=(args.parent_id if hasattr(args, "parent_id") else args.initiative_id),
            ),
            None,
        )
    raise AssertionError(f"Unknown story-backlog command: {args.story_backlog_command}")

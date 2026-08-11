from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..implementation import apply_workbook, export_workbook, plan_workbook

COMMAND = "implementation"


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env.resolved"),
        help="Resolved environment file path. Default: .env.resolved.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")


def _configure_parser(parser: argparse.ArgumentParser) -> None:
    implementation_subparsers = parser.add_subparsers(dest="implementation_command", required=True)

    export_parser = implementation_subparsers.add_parser(
        "export", help="Export a read-only implementation workbook snapshot"
    )
    export_parser.add_argument("initiative_id", nargs="?", type=int)
    export_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/wood-tools/implementation-workbook"),
        help="Directory for implementation_workbook.json and implementation_workbook.xlsx.",
    )
    export_parser.add_argument("--story-type", default="Story")
    export_parser.add_argument("--epic-type", default="Epic")
    export_parser.add_argument("--closed-status", action="append", default=[])
    export_parser.add_argument("--story-id-field", default="")
    export_parser.add_argument("--requirement-ids-field", default="")
    export_parser.add_argument("--page-size", type=int, default=500)
    _add_common_options(export_parser)

    plan_parser = implementation_subparsers.add_parser(
        "plan", help="Build a deterministic implementation workbook plan without applying it"
    )
    plan_parser.add_argument("workbook", type=Path)
    plan_parser.add_argument("--sheet-name", default="Implementation")
    plan_parser.add_argument("--initiative-id", type=int)
    _add_common_options(plan_parser)

    apply_parser = implementation_subparsers.add_parser(
        "apply", help="Apply the approved implementation workbook plan to OpenProject"
    )
    apply_parser.add_argument("workbook", type=Path)
    apply_parser.add_argument("--sheet-name", default="Implementation")
    apply_parser.add_argument("--initiative-id", type=int)
    _add_common_options(apply_parser)


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "implementation", help="Export, plan, and apply implementation workbook workflows"
    )
    _configure_parser(parser)


def handles(args: argparse.Namespace) -> bool:
    return args.command == COMMAND


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    command = f"implementation-{args.implementation_command}"
    if args.implementation_command == "export":
        return (
            command,
            export_workbook(
                root_work_package_id=args.initiative_id,
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
    if args.implementation_command == "plan":
        return (
            command,
            plan_workbook(
                workbook=args.workbook,
                env_file=args.env_file,
                sheet_name=args.sheet_name,
                initiative_id=args.initiative_id,
            ),
            False,
        )
    if args.implementation_command == "apply":
        return (
            command,
            apply_workbook(
                workbook=args.workbook,
                env_file=args.env_file,
                sheet_name=args.sheet_name,
                initiative_id=args.initiative_id,
            ),
            True,
        )
    raise AssertionError(f"Unknown implementation command: {args.implementation_command}")

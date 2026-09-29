#!/usr/bin/env python3
"""Export a read-only OpenProject implementation workbook snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wood_project.openproject import OpenProjectError

from . import openproject as op
from .export_snapshot import export_snapshot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root_work_package_id",
        nargs="?",
        type=int,
        help=(
            "Root OpenProject work-package ID. If omitted, uses OPENPROJECT_INITIATIVE_ID, "
            "OPENPROJECT_ROOT_WORK_PACKAGE_ID, or OPENPROJECT_ROOT_ID from --env-file."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional environment file path. Injected variables take precedence.",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/wood-tools/implementation-workbook",
        help="Directory for implementation_workbook.json and implementation_workbook.xlsx.",
    )
    parser.add_argument("--story-type", default="Story", help="Story type name. Default: Story.")
    parser.add_argument("--epic-type", default="Epic", help="Epic type name. Default: Epic.")
    parser.add_argument(
        "--closed-status",
        action="append",
        default=[],
        help="Closed status name. Can be repeated. Default: Closed.",
    )
    parser.add_argument(
        "--story-id-field",
        default="",
        help="Optional OpenProject field key for the secondary planning Story ID.",
    )
    parser.add_argument(
        "--requirement-ids-field",
        default="",
        help="Optional OpenProject field key for Requirement IDs.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        help="OpenProject collection page size. Default: 500.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = export_snapshot(
            root_work_package_id=args.root_work_package_id,
            env_file=Path(args.env_file),
            output_dir=Path(args.output_dir),
            story_type=args.story_type,
            epic_type=args.epic_type,
            closed_statuses=args.closed_status,
            story_id_field=args.story_id_field,
            requirement_ids_field=args.requirement_ids_field,
            page_size=args.page_size,
        )
    except (op.ScriptError, OpenProjectError) as err:
        payload = {"ok": False, "error": {"code": err.code, "message": str(err)}}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Error: {err}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wrote implementation workbook snapshot to {payload['output_dir']}")
        print(f"- {Path(payload['json']).name}")
        print(f"- {Path(payload['xlsx']).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

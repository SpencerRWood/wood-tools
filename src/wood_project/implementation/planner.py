#!/usr/bin/env python3
"""Plan or apply an implementation workbook to OpenProject."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectError, embedded_elements

from . import openproject as op
from .apply import (
    apply_plan,
    write_initiative_metadata_to_workbook,
    write_openproject_ids_to_workbook,
)
from .planning import (
    build_implementation_plan,
    env_initiative_id,
    flat_plan,
    project_identifier,
    project_name,
    resolve_initiative_plan,
    resolve_project,
    unique_workbook_value,
)
from .workbook import workbook_metadata, workbook_rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="Path to the implementation workbook .xlsx file.")
    parser.add_argument(
        "--env-file",
        default=".env.resolved",
        help="Resolved environment file path. Default: .env.resolved.",
    )
    parser.add_argument(
        "--sheet-name",
        default="Implementation",
        help='Workbook sheet name. Default: "Implementation".',
    )
    parser.add_argument(
        "--initiative-id",
        type=int,
        help=(
            "Root initiative work-package ID for implementation planning. If omitted, "
            "uses OPENPROJECT_INITIATIVE_ID, OPENPROJECT_ROOT_WORK_PACKAGE_ID, or "
            "OPENPROJECT_ROOT_ID from --env-file."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="PATCH OpenProject. Omit for dry-run.",
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output.")
    return parser.parse_args(argv)


def build_error_payload(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def emit_non_json(payload: dict[str, Any]) -> int:
    if not payload["ok"]:
        print(f"Error: {payload['error']['message']}", file=sys.stderr)
        return 2
    action = "Applied" if payload["applied"] else "Dry run"
    print(f"{action}: {payload['row_count']} workbook row(s).")
    phases = payload["phases"]
    for epic in phases["epics"]:
        wp_label = f"WP-{epic['work_package_id']}" if epic["work_package_id"] else "new epic"
        print(f"- Epic: {epic['action']} {wp_label} {epic['subject']}")
        for warning in epic["warnings"]:
            print(f"  Warning: {warning}")
    for story in phases["stories"]:
        wp_label = f"WP-{story['work_package_id']}" if story["work_package_id"] else "new story"
        print(
            f"- Row {story['row_number']}: {story['action']} {wp_label} "
            f"{story['subject']} under {story['epic_key'] or 'root'}"
        )
        for warning in story["warnings"]:
            print(f"  Warning: {warning}")
    for relation in phases["relations"]:
        print(
            f"- Relation: {relation['from_story_key']} {relation['relation_type']} "
            f"{relation['to_story_key']}"
        )
        for warning in relation["warnings"]:
            print(f"  Warning: {warning}")
    if not payload["applied"]:
        print("Run again with --apply to mutate OpenProject.")
    return 0


def implementation_plan_payload(
    *,
    workbook: Path,
    env_file: Path,
    sheet_name: str,
    initiative_id: int | None,
    apply: bool,
) -> dict[str, Any]:
    env = op.parse_env_file(env_file)
    op.require_env(
        env,
        ["OPENPROJECT_URL", "OPENPROJECT_API_TOKEN"],
    )
    base_url = op.env_value(env, "OPENPROJECT_URL").rstrip("/")
    token = op.env_value(env, "OPENPROJECT_API_TOKEN")
    client = op.client_from_env(
        base_url,
        token,
        project_id=op.env_value(env, "OPENPROJECT_PROJECT_ID"),
    )

    rows = workbook_rows(workbook, sheet_name)
    metadata = workbook_metadata(workbook)
    workbook_project = unique_workbook_value(rows, "Project")
    project = resolve_project(
        client=client,
        configured_project_id=env.get("OPENPROJECT_PROJECT_ID", ""),
        workbook_project=workbook_project,
    )
    project_id = project_identifier(project)
    statuses = embedded_elements(client.get_json("/api/v3/statuses"))
    types = embedded_elements(client.get_json(f"/api/v3/projects/{project_id}/types"))
    versions = embedded_elements(client.get_json(f"/api/v3/projects/{project_id}/versions"))
    initiative = resolve_initiative_plan(
        client=client,
        project=project,
        statuses=statuses,
        types=types,
        rows=rows,
        metadata=metadata,
        explicit_initiative_id=initiative_id,
        configured_initiative_id=env_initiative_id(env),
    )
    phases = build_implementation_plan(
        rows=rows,
        client=client,
        statuses=statuses,
        types=types,
        versions=versions,
        initiative=initiative,
    )
    workbook_update = {
        "updated": False,
        "updated_rows": [],
        "updated_metadata": {"updated": False, "updated_fields": []},
    }
    if apply:
        applied = apply_plan(client, project, phases)
        story_update = write_openproject_ids_to_workbook(
            workbook,
            sheet_name=sheet_name,
            rows=rows,
            applied=applied,
        )
        metadata_update = write_initiative_metadata_to_workbook(
            workbook,
            applied=applied,
            base_url=base_url,
        )
        workbook_update = {
            **story_update,
            "updated_metadata": metadata_update,
        }
    else:
        applied = {
            "initiative": [],
            "versions": [],
            "epics": [],
            "stories": [],
            "relations": [],
        }
    return {
        "ok": True,
        "applied": apply,
        "row_count": len(rows),
        "project": {
            "id": project.get("id"),
            "identifier": project_identifier(project),
            "name": project_name(project),
        },
        "phases": phases,
        "plan": flat_plan(phases),
        "applied_openproject": applied,
        "workbook_update": workbook_update,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = implementation_plan_payload(
            workbook=Path(args.workbook),
            env_file=Path(args.env_file),
            sheet_name=args.sheet_name,
            initiative_id=args.initiative_id,
            apply=args.apply,
        )
    except (op.ScriptError, OpenProjectError) as err:
        payload = build_error_payload(err.code, str(err))

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 2
    return emit_non_json(payload)


if __name__ == "__main__":
    sys.exit(main())

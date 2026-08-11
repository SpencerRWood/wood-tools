from __future__ import annotations

from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectError

from . import export_snapshot as export_snapshot_module
from . import openproject as op
from . import planner as planner_module
from .models import ImplementationWorkflowError


def export_workbook(
    *,
    root_work_package_id: int | None,
    env_file: Path,
    output_dir: Path,
    story_type: str,
    epic_type: str,
    closed_statuses: list[str],
    story_id_field: str,
    requirement_ids_field: str,
    page_size: int,
) -> dict[str, Any]:
    try:
        payload = export_snapshot_module.export_snapshot(
            root_work_package_id=root_work_package_id,
            env_file=env_file,
            output_dir=output_dir,
            story_type=story_type,
            epic_type=epic_type,
            closed_statuses=closed_statuses,
            story_id_field=story_id_field,
            requirement_ids_field=requirement_ids_field,
            page_size=page_size,
        )
    except (op.ScriptError, OpenProjectError) as err:
        raise ImplementationWorkflowError(err.code, str(err)) from err
    return {
        "ok": True,
        "read_only": True,
        "output_dir": payload["output_dir"],
        "json": payload["json"],
        "xlsx": payload["xlsx"],
    }


def implementation_plan(
    *,
    workbook: Path,
    env_file: Path,
    sheet_name: str,
    initiative_id: int | None,
    apply: bool,
) -> dict[str, Any]:
    try:
        payload = planner_module.implementation_plan_payload(
            workbook=workbook,
            env_file=env_file,
            sheet_name=sheet_name,
            initiative_id=initiative_id,
            apply=apply,
        )
    except (op.ScriptError, OpenProjectError) as err:
        raise ImplementationWorkflowError(err.code, str(err)) from err
    return {
        "ok": True,
        "read_only": not apply,
        "applied": payload["applied"],
        "row_count": payload["row_count"],
        "phases": payload["phases"],
        "plan": payload["plan"],
        "applied_openproject": payload["applied_openproject"],
        "workbook_update": payload.get("workbook_update", {"updated": False, "updated_rows": []}),
    }


def plan_workbook(
    *,
    workbook: Path,
    env_file: Path,
    sheet_name: str,
    initiative_id: int | None,
) -> dict[str, Any]:
    return implementation_plan(
        workbook=workbook,
        env_file=env_file,
        sheet_name=sheet_name,
        initiative_id=initiative_id,
        apply=False,
    )


def apply_workbook(
    *,
    workbook: Path,
    env_file: Path,
    sheet_name: str,
    initiative_id: int | None,
) -> dict[str, Any]:
    return implementation_plan(
        workbook=workbook,
        env_file=env_file,
        sheet_name=sheet_name,
        initiative_id=initiative_id,
        apply=True,
    )

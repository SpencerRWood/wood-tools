from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

from . import export as export_module
from . import planner as planner_module
from .models import ImplementationWorkflowError


def _run_json_module(main: Any, args: list[str]) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return_code = int(main([*args, "--json"]) or 0)
    raw_stdout = stdout.getvalue()
    raw_stderr = stderr.getvalue()
    try:
        payload = json.loads(raw_stdout)
    except json.JSONDecodeError as err:
        message = raw_stderr.strip() or raw_stdout.strip() or "workflow did not emit JSON"
        raise ImplementationWorkflowError("IMPLEMENTATION_COMMAND_FAILED", message) from err

    if return_code != 0 or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "IMPLEMENTATION_COMMAND_FAILED")
        message = str(error.get("message") or raw_stderr.strip() or "implementation command failed")
        raise ImplementationWorkflowError(code, message)
    return payload


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
    args: list[str] = []
    if root_work_package_id is not None:
        args.append(str(root_work_package_id))
    args.extend(
        [
            "--env-file",
            str(env_file),
            "--output-dir",
            str(output_dir),
            "--story-type",
            story_type,
            "--epic-type",
            epic_type,
            "--story-id-field",
            story_id_field,
            "--requirement-ids-field",
            requirement_ids_field,
            "--page-size",
            str(page_size),
        ]
    )
    for status in closed_statuses:
        args.extend(["--closed-status", status])
    payload = _run_json_module(export_module.main, args)
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
    args = [str(workbook), "--env-file", str(env_file), "--sheet-name", sheet_name]
    if initiative_id is not None:
        args.extend(["--initiative-id", str(initiative_id)])
    if apply:
        args.append("--apply")
    payload = _run_json_module(planner_module.main, args)
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

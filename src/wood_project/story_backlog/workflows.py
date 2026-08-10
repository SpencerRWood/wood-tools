from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import StoryBacklogWorkflowError


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_json_script(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, *args, "--json"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as err:
        message = result.stderr.strip() or result.stdout.strip() or "script did not emit JSON"
        raise StoryBacklogWorkflowError("STORY_BACKLOG_COMMAND_FAILED", message) from err

    if result.returncode != 0 or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "STORY_BACKLOG_COMMAND_FAILED")
        message = str(
            error.get("message") or result.stderr.strip() or "story backlog command failed"
        )
        raise StoryBacklogWorkflowError(code, message)
    return payload


def export_snapshot(
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
    args = ["scripts/story_backlog_sync/export_story_backlog.py"]
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
    payload = _run_json_script(args)
    return {
        "ok": True,
        "read_only": True,
        "output_dir": payload["output_dir"],
        "json": payload["json"],
        "xlsx": payload["xlsx"],
    }


def upload_plan(
    *,
    workbook: Path,
    env_file: Path,
    sheet_name: str,
    parent_id: int | None,
) -> dict[str, Any]:
    args = [
        "scripts/story_backlog_sync/upload_story_backlog.py",
        str(workbook),
        "--env-file",
        str(env_file),
        "--sheet-name",
        sheet_name,
    ]
    if parent_id is not None:
        args.extend(["--parent-id", str(parent_id)])
    payload = _run_json_script(args)
    return {
        "ok": True,
        "read_only": True,
        "applied": False,
        "row_count": payload["row_count"],
        "phases": payload["phases"],
        "plan": payload["plan"],
    }

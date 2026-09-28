"""Record an actual artifact version on a shipped Story and its workbook row."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from wood_project.openproject import embedded_elements, link_title

from . import openproject as op
from . import workbook
from .packets import extract_labeled_value

_ARTIFACT_VERSION = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


def released_description(description: str, version: str) -> str:
    lines = [
        line for line in description.splitlines() if not re.match(r"^Released In\s*:", line, re.I)
    ]
    insert_at = next(
        (index + 1 for index, line in enumerate(lines) if line.strip() == "OpenProject"),
        len(lines),
    )
    lines.insert(insert_at, f"Released In: {version}")
    return "\n".join(lines).strip()


def record_released_in(
    *,
    path: Path,
    sheet_name: str,
    work_package_id: int,
    version: str,
    env_file: Path,
    apply: bool,
) -> dict[str, Any]:
    if not _ARTIFACT_VERSION.fullmatch(version) or version.upper().startswith("R"):
        raise op.ScriptError(
            "INVALID_RELEASE_VERSION", "Released In requires an actual SemVer artifact version."
        )
    rows = workbook.workbook_rows(path, sheet_name)
    matches = [row for row in rows if row.values["OpenProject ID"] == str(work_package_id)]
    if len(matches) != 1:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH", f"Expected one workbook row for WP-{work_package_id}."
        )
    row = matches[0]
    previous_workbook = row.values["Released In"].strip()
    if previous_workbook and previous_workbook != version:
        raise op.ScriptError(
            "RELEASE_CONFLICT", f"WP-{work_package_id} already has Released In {previous_workbook}."
        )

    env = op.parse_env_file(env_file)
    op.require_env(env, ["OPENPROJECT_URL", "OPENPROJECT_API_TOKEN"])
    client = op.client_from_env(
        op.env_value(env, "OPENPROJECT_URL").rstrip("/"),
        op.env_value(env, "OPENPROJECT_API_TOKEN"),
        project_id=op.env_value(env, "OPENPROJECT_PROJECT_ID"),
    )
    story = client.get_json(f"/api/v3/work_packages/{work_package_id}")
    if link_title(story, "type") != "Story":
        raise op.ScriptError("WORKBOOK_SCHEMA_MISMATCH", f"WP-{work_package_id} is not a Story.")
    statuses = embedded_elements(client.get_json("/api/v3/statuses"))
    closed = {str(status.get("name")) for status in statuses if status.get("isClosed")}
    if link_title(story, "status") not in closed:
        raise op.ScriptError("RELEASE_NOT_SHIPPED", f"WP-{work_package_id} is not closed.")
    description = str((story.get("description") or {}).get("raw") or "")
    previous_story = extract_labeled_value(description, "Released In")
    if previous_story and previous_story != version:
        raise op.ScriptError(
            "RELEASE_CONFLICT", f"WP-{work_package_id} already has Released In {previous_story}."
        )
    changed_story = previous_story != version
    changed_workbook = previous_workbook != version
    if apply and changed_story:
        client.request_json(
            "PATCH",
            f"/api/v3/work_packages/{work_package_id}",
            body={
                "lockVersion": story["lockVersion"],
                "description": {"raw": released_description(description, version)},
            },
        )
    if apply and changed_workbook:
        table = workbook.read_xlsx_rows(path, sheet_name)
        headers = table[0]
        if "Released In" not in headers:
            headers.append("Released In")
        column = headers.index("Released In")
        target = table[row.row_number - 1]
        target.extend([""] * (column + 1 - len(target)))
        target[column] = version
        with zipfile.ZipFile(path) as source:
            sheet_path = workbook.workbook_sheet_target(source, sheet_name)
            entries = {name: source.read(name) for name in source.namelist() if name != sheet_path}
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, content in entries.items():
                output.writestr(name, content)
            output.writestr(sheet_path, workbook.sheet_xml(table))
        temporary.replace(path)
    return {
        "work_package_id": work_package_id,
        "released_in": version,
        "changed": changed_story or changed_workbook,
        "applied": apply,
        "workbook_updated": apply and changed_workbook,
        "openproject_updated": apply and changed_story,
    }

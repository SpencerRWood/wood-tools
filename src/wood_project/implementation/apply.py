from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectClient

from . import openproject as op
from . import workbook as workbook_module
from .planning import WorkbookRow, link_href, story_key

read_xlsx_rows = workbook_module.read_xlsx_rows


def resolved_patch(
    patch: dict[str, Any],
    initiative_id: int,
    epic_ids: dict[str, int],
    version_hrefs: dict[str, str],
) -> dict[str, Any]:
    copied = json.loads(json.dumps(patch))
    links = copied.get("_links") or {}
    parent = (links.get("parent") or {}).get("href")
    if isinstance(parent, str):
        if parent == "planned:initiative":
            copied["_links"]["parent"]["href"] = f"/api/v3/work_packages/{initiative_id}"
        elif parent.startswith("planned:epic:"):
            epic_key = parent.removeprefix("planned:epic:")
            copied["_links"]["parent"]["href"] = f"/api/v3/work_packages/{epic_ids[epic_key]}"
    version = (links.get("version") or {}).get("href")
    if isinstance(version, str) and version.startswith("planned:version:"):
        version_key = version.removeprefix("planned:version:")
        copied["_links"]["version"]["href"] = version_hrefs[version_key]
    return copied


def relation_link_id(relation: dict[str, Any], name: str) -> int | None:
    href = link_href(relation, name)
    match = re.search(r"/work_packages/(\d+)$", href)
    return int(match.group(1)) if match else None


def apply_plan(
    client: OpenProjectClient,
    project_id: str,
    phases: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    applied: dict[str, list[dict[str, Any]]] = {
        "initiative": [],
        "versions": [],
        "epics": [],
        "stories": [],
        "relations": [],
    }
    initiative_id: int | None = None
    version_hrefs: dict[str, str] = {}
    epic_ids: dict[str, int] = {}
    story_ids: dict[str, int] = {}

    initiative = phases["initiative"]
    if initiative["action"] == "reuse":
        initiative_id = int(initiative["work_package_id"])
        verified = client.get_json(f"/api/v3/work_packages/{initiative_id}")
        applied["initiative"].append(
            {
                "action": "reuse",
                "key": initiative["key"],
                "work_package_id": op.work_package_id(verified),
                "subject": op.work_package_subject(verified),
                "type": op.work_package_type_name(verified),
                "verified": True,
            }
        )
    else:
        updated = client.request_json(
            "POST",
            f"/api/v3/projects/{project_id}/work_packages",
            body=initiative["patch"],
        )
        initiative_id = op.work_package_id(updated)
        verified = client.get_json(f"/api/v3/work_packages/{initiative_id}")
        applied["initiative"].append(
            {
                "action": "create",
                "key": initiative["key"],
                "work_package_id": op.work_package_id(verified),
                "subject": op.work_package_subject(verified),
                "type": op.work_package_type_name(verified),
                "verified": True,
            }
        )

    for item in phases["versions"]:
        if item["action"] == "reuse":
            version_hrefs[item["key"]] = item["href"]
            applied["versions"].append(item)
            continue
        updated = client.request_json(
            "POST",
            f"/api/v3/projects/{project_id}/versions",
            body=item["patch"],
        )
        href = str(((updated.get("_links") or {}).get("self") or {}).get("href") or "")
        if not href:
            raise op.ScriptError(
                "WORKBOOK_UPLOAD_FAILED",
                f"Created Version {item['name']!r} did not include a self href.",
            )
        verified = client.get_json(href)
        version_hrefs[item["key"]] = href
        applied["versions"].append(
            {
                "action": "create",
                "key": item["key"],
                "version_id": verified.get("id"),
                "name": verified.get("name") or item["name"],
                "href": href,
                "verified": True,
            }
        )

    for item in phases["epics"]:
        if item["action"] == "reuse":
            epic_ids[item["key"]] = int(item["work_package_id"])
            applied["epics"].append(item)
            continue
        patch = resolved_patch(item["patch"], initiative_id, epic_ids, version_hrefs)
        updated = client.request_json(
            "POST",
            f"/api/v3/projects/{project_id}/work_packages",
            body=patch,
        )
        epic_id = op.work_package_id(updated)
        verified = client.get_json(f"/api/v3/work_packages/{epic_id}")
        epic_ids[item["key"]] = epic_id
        applied["epics"].append(
            {
                "action": "create",
                "key": item["key"],
                "work_package_id": epic_id,
                "subject": op.work_package_subject(verified),
                "status": op.work_package_status_name(verified),
                "verified": True,
            }
        )

    for item in phases["stories"]:
        patch = resolved_patch(item["patch"], initiative_id, epic_ids, version_hrefs)
        if item["action"] == "create":
            updated = client.request_json(
                "POST",
                f"/api/v3/projects/{project_id}/work_packages",
                body=patch,
            )
        else:
            work_package_id = item["work_package_id"]
            updated = client.request_json(
                "PATCH",
                f"/api/v3/work_packages/{work_package_id}",
                body=patch,
            )
        story_id = op.work_package_id(updated)
        verified = client.get_json(f"/api/v3/work_packages/{story_id}")
        story_ids[item["key"]] = story_id
        applied["stories"].append(
            {
                "action": item["action"],
                "key": item["key"],
                "work_package_id": story_id,
                "subject": op.work_package_subject(verified),
                "status": op.work_package_status_name(verified),
                "verified": True,
            }
        )

    for item in phases["relations"]:
        if item["action"] == "reuse":
            applied["relations"].append(item)
            continue
        from_id = item["from_work_package_id"] or story_ids[item["from_story_key"]]
        to_id = item["to_work_package_id"] or story_ids[item["to_story_key"]]
        created = client.request_json(
            "POST",
            f"/api/v3/work_packages/{from_id}/relations",
            body={
                "type": item["relation_type"],
                "_links": {"to": {"href": f"/api/v3/work_packages/{to_id}"}},
            },
        )
        relation_id = created.get("id")
        verified = (
            client.get_json(f"/api/v3/relations/{relation_id}")
            if isinstance(relation_id, int)
            else created
        )
        applied["relations"].append(
            {
                "action": "create",
                "id": verified.get("id"),
                "from_work_package_id": relation_link_id(verified, "from") or from_id,
                "to_work_package_id": relation_link_id(verified, "to") or to_id,
                "relation_type": verified.get("type") or item["relation_type"],
                "verified": True,
            }
        )
    return applied


def story_ids_by_key(applied: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for item in applied["stories"]:
        key = str(item.get("key") or "")
        work_package_id = item.get("work_package_id")
        if key and isinstance(work_package_id, int):
            ids[key] = work_package_id
    return ids


def write_openproject_ids_to_workbook(
    path: Path,
    *,
    sheet_name: str,
    rows: list[WorkbookRow],
    applied: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    ids_by_key = story_ids_by_key(applied)
    if not ids_by_key:
        return {"updated": False, "updated_rows": []}

    table = read_xlsx_rows(path, sheet_name)
    if not table:
        return {"updated": False, "updated_rows": []}
    headers = table[0]
    try:
        id_index = headers.index("OpenProject ID")
    except ValueError as err:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            "Workbook is missing required column: OpenProject ID",
        ) from err

    updated_rows: list[dict[str, Any]] = []
    for row in rows:
        work_package_id = ids_by_key.get(story_key(row))
        if work_package_id is None:
            continue
        table_index = row.row_number - 1
        while len(table[table_index]) <= id_index:
            table[table_index].append("")
        previous = table[table_index][id_index]
        table[table_index][id_index] = str(work_package_id)
        if previous != str(work_package_id):
            updated_rows.append(
                {
                    "row_number": row.row_number,
                    "story_key": story_key(row),
                    "openproject_id": work_package_id,
                    "previous_openproject_id": previous,
                }
            )

    if not updated_rows:
        return {"updated": False, "updated_rows": []}

    with zipfile.ZipFile(path) as workbook:
        sheet_path = workbook_module.workbook_sheet_target(workbook, sheet_name)
        entries = {name: workbook.read(name) for name in workbook.namelist() if name != sheet_path}

    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        for name, content in entries.items():
            workbook.writestr(name, content)
        workbook.writestr(sheet_path, workbook_module.sheet_xml(table))
    tmp_path.replace(path)
    return {"updated": True, "updated_rows": updated_rows}


def write_initiative_metadata_to_workbook(
    path: Path,
    *,
    sheet_name: str = "Sync Metadata",
    applied: dict[str, list[dict[str, Any]]],
    base_url: str,
) -> dict[str, Any]:
    initiative_items = applied.get("initiative") or []
    if not initiative_items:
        return {"updated": False, "updated_fields": []}
    initiative = initiative_items[0]
    work_package_id = initiative.get("work_package_id")
    if not isinstance(work_package_id, int):
        return {"updated": False, "updated_fields": []}

    try:
        table = read_xlsx_rows(path, sheet_name)
    except op.ScriptError:
        return {"updated": False, "updated_fields": []}
    if not table:
        return {"updated": False, "updated_fields": []}

    values = {
        "Verified Root Work Package ID": str(work_package_id),
        "Root Work Package Subject": str(initiative.get("subject") or ""),
        "Root Work Package Type": str(initiative.get("type") or ""),
        "Root Work Package OpenProject URL": (
            f"{base_url.rstrip('/')}/work_packages/{work_package_id}"
        ),
    }
    existing_rows = {row[0]: index for index, row in enumerate(table) if row}
    updated_fields: list[dict[str, str]] = []
    for field, value in values.items():
        if not value:
            continue
        row_index = existing_rows.get(field)
        if row_index is None:
            table.append([field, value])
            updated_fields.append({"field": field, "previous": "", "value": value})
            continue
        while len(table[row_index]) < 2:
            table[row_index].append("")
        previous = table[row_index][1]
        table[row_index][1] = value
        if previous != value:
            updated_fields.append({"field": field, "previous": previous, "value": value})

    if not updated_fields:
        return {"updated": False, "updated_fields": []}

    with zipfile.ZipFile(path) as workbook:
        sheet_path = workbook_module.workbook_sheet_target(workbook, sheet_name)
        entries = {name: workbook.read(name) for name in workbook.namelist() if name != sheet_path}

    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        for name, content in entries.items():
            workbook.writestr(name, content)
        workbook.writestr(sheet_path, workbook_module.sheet_xml(table))
    tmp_path.replace(path)
    return {"updated": True, "updated_fields": updated_fields}

#!/usr/bin/env python3
"""Plan or apply an implementation workbook to OpenProject."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from . import openproject as op
from . import workbook as workbook_module

IMPLEMENTATION_WORKBOOK_COLUMNS = workbook_module.IMPLEMENTATION_WORKBOOK_COLUMNS
WorkbookRow = workbook_module.WorkbookRow
read_xlsx_rows = workbook_module.read_xlsx_rows
workbook_metadata = workbook_module.workbook_metadata
workbook_rows = workbook_module.workbook_rows


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


def unique_workbook_value(rows: list[WorkbookRow], column: str) -> str:
    values = sorted({row.values[column].strip() for row in rows if row.values[column].strip()})
    if len(values) > 1:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            f"Workbook has conflicting {column} values: {', '.join(values)}.",
        )
    return values[0] if values else ""


def required_int(value: str, *, field_name: str, row_number: int) -> int:
    try:
        return int(value)
    except ValueError as err:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            f"Row {row_number} has invalid {field_name}: {value!r}",
        ) from err


def optional_int(value: str, *, field_name: str, row_number: int) -> int | None:
    if not value.strip():
        return None
    return required_int(value, field_name=field_name, row_number=row_number)


def compose_description(values: dict[str, str]) -> str:
    lines = ["Codex Implementation Packet"]
    openproject_lines = []
    if values["OpenProject ID"].strip():
        openproject_lines.append(f"Work package ID: {values['OpenProject ID'].strip()}")
    if values["Story ID"].strip():
        openproject_lines.append(f"External story ID: {values['Story ID'].strip()}")
    if values["Version"].strip():
        openproject_lines.append(f"Release/version: {values['Version'].strip()}")
    if values["Branch Name"].strip():
        openproject_lines.append(f"Branch: {values['Branch Name'].strip()}")
    if openproject_lines:
        lines.extend(["", "OpenProject", *openproject_lines])

    for heading, body in (
        ("Goal", values["Goal"]),
        ("Implementation Notes", values["Implementation Notes"]),
        ("Requirement IDs", values["Requirement IDs"]),
        ("Acceptance Criteria", values["Acceptance Criteria"]),
        ("Dependencies", values["Predecessors"] or "None specified."),
        ("Non-Goals", values["Non-Goals"]),
        ("Notes", values["Notes"]),
    ):
        if body.strip():
            lines.extend(["", heading, body.strip()])
    return "\n".join(lines).strip()


def find_href(elements: list[dict[str, Any]], name: str) -> str:
    element = op.find_named_element(elements, name)
    href = str(((element.get("_links") or {}).get("self") or {}).get("href") or "")
    if not href:
        raise op.ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"OpenProject did not provide a href for {name!r}.",
        )
    return href


def optional_href(elements: list[dict[str, Any]], name: str) -> str | None:
    for element in elements:
        if str(element.get("name") or "") == name:
            href = str(((element.get("_links") or {}).get("self") or {}).get("href") or "")
            return href or None
    return None


def fetch_collection(
    base_url: str,
    token: str,
    path: str,
    *,
    query: dict[str, str],
    page_size: int = 500,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    offset = 1
    while True:
        page_query = dict(query)
        page_query["pageSize"] = str(page_size)
        page_query["offset"] = str(offset)
        document = op.api_get_json(base_url, token, path, query=page_query)
        page = op.embedded_elements(document)
        elements.extend(page)
        total = int(document.get("total") or len(elements))
        if len(elements) >= total or not page:
            break
        offset += len(page)
    return elements


def fetch_descendants(
    base_url: str,
    token: str,
    root_work_package_id: int,
) -> list[dict[str, Any]]:
    filters = [{"ancestor": {"operator": "=", "values": [str(root_work_package_id)]}}]
    return fetch_collection(
        base_url,
        token,
        "/api/v3/work_packages",
        query={"filters": json.dumps(filters)},
    )


def work_package_description_text(work_package: dict[str, Any]) -> str:
    description = work_package.get("description")
    if isinstance(description, dict):
        return str(description.get("raw") or "")
    return ""


def external_story_id(work_package: dict[str, Any]) -> str:
    pattern = re.compile(r"^\s*(?:[-*]\s*)?External story ID\s*:\s*(.+?)\s*$", re.IGNORECASE)
    for line in work_package_description_text(work_package).splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip().strip("`")
    return ""


def unique_by_key(
    items: list[dict[str, Any]],
    *,
    key_name: str,
    value_name: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        value = str(item.get(value_name) or "").strip()
        if value:
            grouped.setdefault(value, []).append(item)

    unique: dict[str, dict[str, Any]] = {}
    conflicts: list[str] = []
    for value, matches in grouped.items():
        if len(matches) == 1:
            unique[value] = matches[0]
            continue
        ids = ", ".join(str(match.get("id") or "?") for match in matches)
        conflicts.append(f"{key_name} {value!r} matched multiple OpenProject objects: {ids}")
    if conflicts:
        raise op.ScriptError("AMBIGUOUS_OPENPROJECT_MATCH", "; ".join(conflicts))
    return unique


def existing_epics_by_subject(descendants: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    epics: list[dict[str, Any]] = []
    for work_package in descendants:
        if op.work_package_type_name(work_package) != "Epic":
            continue
        epics.append(
            {
                "id": op.work_package_id(work_package),
                "subject": op.work_package_subject(work_package),
                "work_package": work_package,
            }
        )
    return {
        subject: item["work_package"]
        for subject, item in unique_by_key(
            epics, key_name="Epic subject", value_name="subject"
        ).items()
    }


def existing_stories_by_external_id(descendants: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    for work_package in descendants:
        if op.work_package_type_name(work_package) != "Story":
            continue
        stories.append(
            {
                "id": op.work_package_id(work_package),
                "story_id": external_story_id(work_package),
                "work_package": work_package,
            }
        )
    return {
        story_id: item["work_package"]
        for story_id, item in unique_by_key(
            stories, key_name="Story ID", value_name="story_id"
        ).items()
    }


def existing_stories_by_subject(descendants: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    for work_package in descendants:
        if op.work_package_type_name(work_package) != "Story":
            continue
        stories.append(
            {
                "id": op.work_package_id(work_package),
                "subject": op.work_package_subject(work_package),
                "work_package": work_package,
            }
        )
    return {
        subject: item["work_package"]
        for subject, item in unique_by_key(
            stories, key_name="Story subject", value_name="subject"
        ).items()
    }


def unique_rows_by_epic(rows: list[WorkbookRow]) -> dict[str, list[WorkbookRow]]:
    grouped: dict[str, list[WorkbookRow]] = {}
    for row in rows:
        epic = row.values["Epic"].strip()
        if epic:
            grouped.setdefault(epic, []).append(row)
    return grouped


def story_key(row: WorkbookRow) -> str:
    return row.values["Story ID"].strip() or f"row:{row.row_number}"


def split_predecessors(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]


def build_epic_description(epic: str, rows: list[WorkbookRow]) -> str:
    versions = sorted(
        {row.values["Version"].strip() for row in rows if row.values["Version"].strip()}
    )
    story_ids = [story_key(row) for row in rows]
    lines = ["Codex Implementation Packet", "", "OpenProject"]
    if versions:
        lines.append(f"Release/version: {versions[0]}")
    lines.extend(["", "Goal", f"Track implementation stories for {epic}."])
    if story_ids:
        lines.extend(["", "Stories", ", ".join(story_ids)])
    return "\n".join(lines).strip()


def build_epic_create_payload(
    *,
    epic: str,
    rows: list[WorkbookRow],
    statuses: list[dict[str, Any]],
    types: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    root_work_package_id: int | None,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    version_names = sorted(
        {row.values["Version"].strip() for row in rows if row.values["Version"].strip()}
    )
    if len(version_names) > 1:
        warnings.append(f"Epic spans multiple versions; using {version_names[0]!r}.")

    links: dict[str, dict[str, str]] = {
        "parent": {
            "href": (
                f"/api/v3/work_packages/{root_work_package_id}"
                if root_work_package_id is not None
                else "planned:initiative"
            )
        },
        "type": {"href": find_href(types, "Epic")},
    }
    status_href = optional_href(statuses, "New")
    if status_href:
        links["status"] = {"href": status_href}
    if version_names:
        links["version"] = {"href": version_href(versions, version_names[0])}

    return (
        {
            "subject": epic,
            "description": {"raw": build_epic_description(epic, rows)},
            "_links": links,
        },
        warnings,
    )


def build_patch_payload(
    *,
    values: dict[str, str],
    current: dict[str, Any],
    statuses: list[dict[str, Any]],
    types: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    parent_ref: str,
) -> tuple[dict[str, Any], list[str]]:
    lock_version = current.get("lockVersion")
    if not isinstance(lock_version, int):
        raise op.ScriptError(
            "WORKBOOK_UPLOAD_FAILED",
            f"WP-{current.get('id')} did not include a usable lockVersion.",
        )

    payload: dict[str, Any] = {"lockVersion": lock_version}
    links: dict[str, dict[str, str]] = {}
    warnings: list[str] = []

    if values["Subject"]:
        payload["subject"] = values["Subject"]
    description = compose_description(values)
    if description:
        payload["description"] = {"raw": description}
    if values["Status"]:
        links["status"] = {"href": find_href(statuses, values["Status"])}
    if values["Type"]:
        links["type"] = {"href": find_href(types, values["Type"])}
    if values["Version"]:
        links["version"] = {"href": version_href(versions, values["Version"])}
    links["parent"] = {"href": parent_ref}
    if links:
        payload["_links"] = links

    current_links = current.get("_links") or {}
    for column, link_name in (("Project", "project"),):
        expected = values[column]
        actual = str((current_links.get(link_name) or {}).get("title") or "")
        if expected and actual and expected != actual:
            warnings.append(f"{column} mismatch: workbook={expected!r}, openproject={actual!r}")
    for title_only_column in ("Root Work Package", "Predecessors"):
        if values[title_only_column]:
            warnings.append(f"{title_only_column} is workbook context only; not sent.")
    if values["Epic"]:
        warnings.append(f"Parent resolved from Epic: {values['Epic']!r}.")
    for description_column in (
        "Story ID",
        "Goal",
        "Acceptance Criteria",
        "Non-Goals",
        "Implementation Notes",
        "Requirement IDs",
        "Branch Name",
        "Notes",
    ):
        if values[description_column]:
            warnings.append(f"{description_column} included inside description.")

    return payload, warnings


def element_self_href(element: dict[str, Any]) -> str:
    return str(((element.get("_links") or {}).get("self") or {}).get("href") or "")


def link_title(element: dict[str, Any], name: str) -> str:
    return str(((element.get("_links") or {}).get(name) or {}).get("title") or "")


def link_href(element: dict[str, Any], name: str) -> str:
    return str(((element.get("_links") or {}).get(name) or {}).get("href") or "")


def project_identifier(project: dict[str, Any]) -> str:
    return str(project.get("identifier") or project.get("id") or "")


def project_filter_id(project: dict[str, Any]) -> str:
    return str(project.get("id") or project_identifier(project))


def project_name(project: dict[str, Any]) -> str:
    return str(project.get("name") or "")


def fetch_projects(base_url: str, token: str) -> list[dict[str, Any]]:
    return fetch_collection(base_url, token, "/api/v3/projects", query={})


def fetch_project(base_url: str, token: str, project_id: str) -> dict[str, Any]:
    return op.api_get_json(base_url, token, f"/api/v3/projects/{project_id}")


def resolve_project(
    *,
    base_url: str,
    token: str,
    configured_project_id: str,
    workbook_project: str,
) -> dict[str, Any]:
    if configured_project_id:
        project = fetch_project(base_url, token, configured_project_id)
        if workbook_project and workbook_project not in {
            project_identifier(project),
            project_name(project),
        }:
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                (
                    "Workbook Project does not match configured OpenProject project: "
                    f"workbook={workbook_project!r}, configured={project_name(project)!r}."
                ),
            )
        return project

    if not workbook_project:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            "Workbook metadata must identify Project when OPENPROJECT_PROJECT_ID is not set.",
        )

    matches = [
        project
        for project in fetch_projects(base_url, token)
        if workbook_project in {project_identifier(project), project_name(project)}
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise op.ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"Workbook Project did not match an OpenProject project: {workbook_project!r}.",
        )
    ids = ", ".join(project_identifier(project) for project in matches)
    raise op.ScriptError(
        "AMBIGUOUS_OPENPROJECT_MATCH",
        f"Workbook Project {workbook_project!r} matched multiple OpenProject projects: {ids}.",
    )


def env_initiative_id(env: dict[str, str]) -> int | None:
    for key in op.ROOT_ID_ENV_KEYS:
        raw_value = op.env_value(env, key)
        if not raw_value:
            continue
        try:
            return int(raw_value)
        except ValueError as err:
            raise op.ScriptError(
                "INVALID_ROOT_WORK_PACKAGE_ID",
                f"{key} must be an integer.",
            ) from err
    return None


def workbook_initiative_id(metadata: dict[str, str]) -> int | None:
    raw_value = (
        metadata.get("Verified Root Work Package ID")
        or metadata.get("Configured Root Work Package ID")
        or ""
    ).strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError as err:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            f"Workbook Sync Metadata has invalid Root Work Package ID: {raw_value!r}.",
        ) from err


def project_work_packages(
    *,
    base_url: str,
    token: str,
    project_id: str,
) -> list[dict[str, Any]]:
    filters = [{"project": {"operator": "=", "values": [str(project_id)]}}]
    return fetch_collection(
        base_url,
        token,
        "/api/v3/work_packages",
        query={"filters": json.dumps(filters)},
    )


def root_type_name(metadata: dict[str, str], types: list[dict[str, Any]]) -> str:
    configured = metadata.get("Root Work Package Type", "").strip()
    if configured:
        return configured
    if optional_href(types, "Initiative"):
        return "Initiative"
    raise op.ScriptError(
        "WORKBOOK_SCHEMA_MISMATCH",
        (
            "Workbook Sync Metadata must include Root Work Package Type when no "
            "Initiative type exists."
        ),
    )


def build_initiative_create_payload(
    *,
    subject: str,
    project: dict[str, Any],
    root_type: str,
    statuses: list[dict[str, Any]],
    types: list[dict[str, Any]],
) -> dict[str, Any]:
    links: dict[str, dict[str, str]] = {
        "type": {"href": find_href(types, root_type)},
    }
    status_href = optional_href(statuses, "New")
    if status_href:
        links["status"] = {"href": status_href}
    return {
        "subject": subject,
        "description": {
            "raw": (
                "Codex Implementation Packet\n\n"
                "Goal\n"
                "Track implementation work for "
                f"{project_name(project) or project_identifier(project)}."
            )
        },
        "_links": links,
    }


def resolve_initiative_plan(
    *,
    base_url: str,
    token: str,
    project: dict[str, Any],
    statuses: list[dict[str, Any]],
    types: list[dict[str, Any]],
    rows: list[WorkbookRow],
    metadata: dict[str, str],
    explicit_initiative_id: int | None,
    configured_initiative_id: int | None,
) -> dict[str, Any]:
    workbook_subject = metadata.get(
        "Root Work Package Subject", ""
    ).strip() or unique_workbook_value(rows, "Root Work Package")
    if not workbook_subject:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            "Workbook must identify a Root Work Package in Sync Metadata or workbook rows.",
        )

    root_id = explicit_initiative_id or workbook_initiative_id(metadata) or configured_initiative_id
    if root_id is not None:
        existing = op.api_get_json(base_url, token, f"/api/v3/work_packages/{root_id}")
        existing_subject = op.work_package_subject(existing)
        existing_project = link_title(existing, "project")
        if workbook_subject and existing_subject != workbook_subject:
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                (
                    "Workbook Root Work Package conflicts with resolved OpenProject object: "
                    f"workbook={workbook_subject!r}, openproject={existing_subject!r}."
                ),
            )
        expected_project_names = {project_name(project), project_identifier(project)}
        if existing_project and existing_project not in expected_project_names:
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                (
                    "Resolved Root Work Package belongs to a different project: "
                    f"{existing_project!r}."
                ),
            )
        return {
            "key": workbook_subject,
            "subject": existing_subject,
            "action": "reuse",
            "work_package_id": op.work_package_id(existing),
            "type": op.work_package_type_name(existing),
            "patch": None,
            "warnings": ["Existing Root Work Package matched by OpenProject ID."],
        }

    root_type = root_type_name(metadata, types)
    subject_matches = [
        work_package
        for work_package in project_work_packages(
            base_url=base_url,
            token=token,
            project_id=project_filter_id(project),
        )
        if op.work_package_subject(work_package) == workbook_subject
    ]
    type_conflicts = [
        work_package
        for work_package in subject_matches
        if op.work_package_type_name(work_package) != root_type
    ]
    if type_conflicts:
        conflicts = ", ".join(
            f"WP-{work_package.get('id')} type={op.work_package_type_name(work_package)!r}"
            for work_package in type_conflicts
        )
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            (
                f"Root Work Package {workbook_subject!r} matched OpenProject objects with "
                f"conflicting types: {conflicts}."
            ),
        )
    candidates = [
        work_package
        for work_package in subject_matches
        if op.work_package_type_name(work_package) == root_type
    ]
    if len(candidates) == 1:
        existing = candidates[0]
        return {
            "key": workbook_subject,
            "subject": workbook_subject,
            "action": "reuse",
            "work_package_id": op.work_package_id(existing),
            "type": op.work_package_type_name(existing),
            "patch": None,
            "warnings": ["Existing Root Work Package matched by subject and type."],
        }
    if len(candidates) > 1:
        ids = ", ".join(str(candidate.get("id") or "?") for candidate in candidates)
        raise op.ScriptError(
            "AMBIGUOUS_OPENPROJECT_MATCH",
            (
                f"Root Work Package {workbook_subject!r} matched multiple OpenProject "
                f"objects: {ids}."
            ),
        )
    return {
        "key": workbook_subject,
        "subject": workbook_subject,
        "action": "create",
        "work_package_id": None,
        "type": root_type,
        "patch": build_initiative_create_payload(
            subject=workbook_subject,
            project=project,
            root_type=root_type,
            statuses=statuses,
            types=types,
        ),
        "warnings": [],
    }


def build_create_payload(
    *,
    values: dict[str, str],
    statuses: list[dict[str, Any]],
    types: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    parent_ref: str,
) -> tuple[dict[str, Any], list[str]]:
    subject = values["Subject"].strip()
    if not subject:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            "New Story rows require Subject.",
        )

    links: dict[str, dict[str, str]] = {"parent": {"href": parent_ref}}
    if values["Status"]:
        links["status"] = {"href": find_href(statuses, values["Status"])}
    if values["Type"]:
        links["type"] = {"href": find_href(types, values["Type"])}
    if values["Version"]:
        links["version"] = {"href": version_href(versions, values["Version"])}

    payload: dict[str, Any] = {"subject": subject, "_links": links}
    description = compose_description(values)
    if description:
        payload["description"] = {"raw": description}

    warnings = ["New row will be created under the resolved workbook Epic parent."]
    for title_only_column in ("Project", "Root Work Package", "Parent", "Predecessors"):
        if values[title_only_column]:
            warnings.append(f"{title_only_column} is workbook context only; not sent.")
    if values["Epic"]:
        warnings.append(f"Parent resolved from Epic: {values['Epic']!r}.")
    for description_column in (
        "Story ID",
        "Goal",
        "Acceptance Criteria",
        "Non-Goals",
        "Implementation Notes",
        "Requirement IDs",
        "Branch Name",
        "Notes",
    ):
        if values[description_column]:
            warnings.append(f"{description_column} included inside description.")
    return payload, warnings


def version_href(versions: list[dict[str, Any]], name: str) -> str:
    href = optional_href(versions, name)
    if href:
        return href
    return f"planned:version:{name}"


def required_version_names(rows: list[WorkbookRow]) -> list[str]:
    return sorted({row.values["Version"].strip() for row in rows if row.values["Version"].strip()})


def existing_versions_by_name(versions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized = [
        {
            "id": element.get("id"),
            "name": str(element.get("name") or ""),
            "href": str(((element.get("_links") or {}).get("self") or {}).get("href") or ""),
        }
        for element in versions
    ]
    return unique_by_key(normalized, key_name="Version name", value_name="name")


def build_version_plan(
    rows: list[WorkbookRow],
    versions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_name = existing_versions_by_name(versions)
    planned: list[dict[str, Any]] = []
    for name in required_version_names(rows):
        existing = existing_by_name.get(name)
        if existing:
            planned.append(
                {
                    "key": name,
                    "name": name,
                    "action": "reuse",
                    "version_id": existing.get("id"),
                    "href": existing.get("href"),
                    "patch": None,
                    "warnings": ["Existing Version matched by name."],
                }
            )
            continue
        planned.append(
            {
                "key": name,
                "name": name,
                "action": "create",
                "version_id": None,
                "href": None,
                "patch": {"name": name},
                "warnings": [],
            }
        )
    return planned


def resolve_story_match(
    *,
    row: WorkbookRow,
    base_url: str,
    token: str,
    stories_by_id: dict[int, dict[str, Any]],
    stories_by_external_id: dict[str, dict[str, Any]],
    stories_by_subject: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    work_package_id = optional_int(
        row.values["OpenProject ID"],
        field_name="OpenProject ID",
        row_number=row.row_number,
    )
    if work_package_id is not None:
        existing = op.api_get_json(
            base_url,
            token,
            f"/api/v3/work_packages/{work_package_id}",
        )
        if op.work_package_type_name(existing) != "Story":
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                f"Row {row.row_number} OpenProject ID WP-{work_package_id} is not a Story.",
            )
        if work_package_id not in stories_by_id:
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                (
                    f"Row {row.row_number} OpenProject ID WP-{work_package_id} is not a Story "
                    "beneath the resolved Root Work Package."
                ),
            )
        return (existing, ["Existing Story matched by OpenProject ID."])

    story_id = row.values["Story ID"].strip()
    if story_id and story_id in stories_by_external_id:
        return stories_by_external_id[story_id], ["Existing Story matched by Story ID."]

    subject = row.values["Subject"].strip()
    if subject and subject in stories_by_subject:
        return stories_by_subject[subject], ["Existing Story matched by subject."]

    return None, []


def build_implementation_plan(
    *,
    rows: list[WorkbookRow],
    base_url: str,
    token: str,
    statuses: list[dict[str, Any]],
    types: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    initiative: dict[str, Any],
) -> dict[str, Any]:
    initiative_id = initiative["work_package_id"]
    descendants = fetch_descendants(base_url, token, initiative_id) if initiative_id else []
    versions_plan = build_version_plan(rows, versions)
    rows_by_key: dict[str, WorkbookRow] = {}
    for row in rows:
        key = story_key(row)
        if key in rows_by_key:
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                f"Duplicate Story ID in workbook: {key!r}",
            )
        rows_by_key[key] = row

    epics_by_subject = existing_epics_by_subject(descendants)
    stories_by_id = {
        op.work_package_id(work_package): work_package
        for work_package in descendants
        if op.work_package_type_name(work_package) == "Story"
    }
    stories_by_external_id = existing_stories_by_external_id(descendants)
    stories_by_subject = existing_stories_by_subject(descendants)
    epics: list[dict[str, Any]] = []
    for epic, epic_rows in unique_rows_by_epic(rows).items():
        existing = epics_by_subject.get(epic)
        if existing:
            epics.append(
                {
                    "key": epic,
                    "subject": epic,
                    "action": "reuse",
                    "work_package_id": op.work_package_id(existing),
                    "patch": None,
                    "warnings": ["Existing Epic matched by subject."],
                }
            )
            continue
        patch, warnings = build_epic_create_payload(
            epic=epic,
            rows=epic_rows,
            statuses=statuses,
            types=types,
            versions=versions,
            root_work_package_id=initiative_id,
        )
        epics.append(
            {
                "key": epic,
                "subject": epic,
                "action": "create",
                "work_package_id": None,
                "patch": patch,
                "warnings": warnings,
            }
        )

    epic_id_by_key = {epic["key"]: epic["work_package_id"] for epic in epics}
    stories: list[dict[str, Any]] = []
    for row in rows:
        key = story_key(row)
        epic_key = row.values["Epic"].strip()
        parent_id = epic_id_by_key.get(epic_key) if epic_key else initiative_id
        parent_ref = (
            f"/api/v3/work_packages/{parent_id}"
            if parent_id is not None
            else "planned:initiative"
            if not epic_key
            else f"planned:epic:{epic_key}"
        )
        existing_story, match_warnings = resolve_story_match(
            row=row,
            base_url=base_url,
            token=token,
            stories_by_id=stories_by_id,
            stories_by_external_id=stories_by_external_id,
            stories_by_subject=stories_by_subject,
        )
        action = "update" if existing_story is not None else "create"
        if existing_story is None:
            work_package_id = None
            patch, warnings = build_create_payload(
                values=row.values,
                statuses=statuses,
                types=types,
                versions=versions,
                parent_ref=parent_ref,
            )
        else:
            work_package_id = op.work_package_id(existing_story)
            patch, warnings = build_patch_payload(
                values=row.values,
                current=existing_story,
                statuses=statuses,
                types=types,
                versions=versions,
                parent_ref=parent_ref,
            )
            warnings = [*match_warnings, *warnings]
        stories.append(
            {
                "key": key,
                "action": action,
                "row_number": row.row_number,
                "work_package_id": work_package_id,
                "subject": row.values["Subject"],
                "epic_key": epic_key,
                "patch": patch,
                "warnings": warnings,
            }
        )

    relations: list[dict[str, Any]] = []
    existing_relations = existing_precedes_relations(base_url, token, descendants)
    for row in rows:
        current_key = story_key(row)
        for predecessor in split_predecessors(row.values["Predecessors"]):
            predecessor_row = rows_by_key.get(predecessor)
            if predecessor_row is None:
                raise op.ScriptError(
                    "WORKBOOK_SCHEMA_MISMATCH",
                    f"Row {row.row_number} predecessor {predecessor!r} does not match a Story ID.",
                )
            from_work_package_id = planned_story_work_package_id(stories, predecessor)
            to_work_package_id = planned_story_work_package_id(stories, current_key)
            relation_key = (from_work_package_id, to_work_package_id)
            action = (
                "reuse"
                if from_work_package_id is not None
                and to_work_package_id is not None
                and relation_key in existing_relations
                else "create"
            )
            relations.append(
                {
                    "action": action,
                    "from_story_key": predecessor,
                    "to_story_key": current_key,
                    "from_work_package_id": from_work_package_id,
                    "to_work_package_id": to_work_package_id,
                    "relation_type": "precedes",
                    "warnings": (
                        ["Existing predecessor relation matched."] if action == "reuse" else []
                    ),
                }
            )

    return {
        "initiative": initiative,
        "versions": versions_plan,
        "epics": epics,
        "stories": stories,
        "relations": relations,
    }


def planned_story_work_package_id(stories: list[dict[str, Any]], key: str) -> int | None:
    for story in stories:
        if story["key"] == key:
            return story["work_package_id"]
    return None


def existing_precedes_relations(
    base_url: str,
    token: str,
    descendants: list[dict[str, Any]],
) -> set[tuple[int, int]]:
    story_ids = [
        op.work_package_id(work_package)
        for work_package in descendants
        if op.work_package_type_name(work_package) == "Story"
    ]
    relations: set[tuple[int, int]] = set()
    for story_id in story_ids:
        document = op.api_get_json(
            base_url,
            token,
            f"/api/v3/work_packages/{story_id}/relations",
        )
        for relation in op.embedded_elements(document):
            if relation.get("type") != "precedes":
                continue
            to_href = str(((relation.get("_links") or {}).get("to") or {}).get("href") or "")
            match = re.search(r"/work_packages/(\d+)$", to_href)
            if match:
                relations.add((story_id, int(match.group(1))))
    return relations


def flat_plan(phases: dict[str, Any]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    plan.append({"kind": "initiative", **phases["initiative"]})
    for version in phases["versions"]:
        plan.append({"kind": "version", **version})
    for epic in phases["epics"]:
        plan.append({"kind": "epic", **epic})
    for story in phases["stories"]:
        plan.append({"kind": "story", **story})
    for relation in phases["relations"]:
        plan.append({"kind": "relation", **relation})
    return plan


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
    base_url: str,
    token: str,
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
        verified = op.api_get_json(base_url, token, f"/api/v3/work_packages/{initiative_id}")
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
        updated = op.api_request_json(
            "POST",
            base_url,
            token,
            f"/api/v3/projects/{project_id}/work_packages",
            body=initiative["patch"],
        )
        initiative_id = op.work_package_id(updated)
        verified = op.api_get_json(base_url, token, f"/api/v3/work_packages/{initiative_id}")
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
        updated = op.api_request_json(
            "POST",
            base_url,
            token,
            f"/api/v3/projects/{project_id}/versions",
            body=item["patch"],
        )
        href = str(((updated.get("_links") or {}).get("self") or {}).get("href") or "")
        if not href:
            raise op.ScriptError(
                "WORKBOOK_UPLOAD_FAILED",
                f"Created Version {item['name']!r} did not include a self href.",
            )
        verified = op.api_get_json(base_url, token, href)
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
        updated = op.api_request_json(
            "POST",
            base_url,
            token,
            f"/api/v3/projects/{project_id}/work_packages",
            body=patch,
        )
        epic_id = op.work_package_id(updated)
        verified = op.api_get_json(base_url, token, f"/api/v3/work_packages/{epic_id}")
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
            updated = op.api_request_json(
                "POST",
                base_url,
                token,
                f"/api/v3/projects/{project_id}/work_packages",
                body=patch,
            )
        else:
            work_package_id = item["work_package_id"]
            updated = op.api_patch_json(
                base_url,
                token,
                f"/api/v3/work_packages/{work_package_id}",
                patch,
            )
        story_id = op.work_package_id(updated)
        verified = op.api_get_json(base_url, token, f"/api/v3/work_packages/{story_id}")
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
        created = op.api_request_json(
            "POST",
            base_url,
            token,
            f"/api/v3/work_packages/{from_id}/relations",
            body={
                "type": item["relation_type"],
                "_links": {"to": {"href": f"/api/v3/work_packages/{to_id}"}},
            },
        )
        relation_id = created.get("id")
        verified = (
            op.api_get_json(base_url, token, f"/api/v3/relations/{relation_id}")
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        env = op.parse_env_file(Path(args.env_file))
        op.require_env(
            env,
            ["OPENPROJECT_URL", "OPENPROJECT_API_TOKEN"],
        )
        base_url = env["OPENPROJECT_URL"].rstrip("/")
        token = env["OPENPROJECT_API_TOKEN"]

        workbook = Path(args.workbook)
        rows = workbook_rows(workbook, args.sheet_name)
        metadata = workbook_metadata(workbook)
        workbook_project = unique_workbook_value(rows, "Project")
        project = resolve_project(
            base_url=base_url,
            token=token,
            configured_project_id=env.get("OPENPROJECT_PROJECT_ID", ""),
            workbook_project=workbook_project,
        )
        project_id = project_identifier(project)
        statuses = op.embedded_elements(op.api_get_json(base_url, token, "/api/v3/statuses"))
        types = op.embedded_elements(
            op.api_get_json(base_url, token, f"/api/v3/projects/{project_id}/types")
        )
        versions = op.embedded_elements(
            op.api_get_json(base_url, token, f"/api/v3/projects/{project_id}/versions")
        )
        initiative = resolve_initiative_plan(
            base_url=base_url,
            token=token,
            project=project,
            statuses=statuses,
            types=types,
            rows=rows,
            metadata=metadata,
            explicit_initiative_id=args.initiative_id,
            configured_initiative_id=env_initiative_id(env),
        )
        phases = build_implementation_plan(
            rows=rows,
            base_url=base_url,
            token=token,
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
        if args.apply:
            applied = apply_plan(base_url, token, project_id, phases)
            story_update = write_openproject_ids_to_workbook(
                workbook,
                sheet_name=args.sheet_name,
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
        payload = {
            "ok": True,
            "applied": args.apply,
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
    except op.ScriptError as err:
        payload = build_error_payload(err.code, str(err))

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 2
    return emit_non_json(payload)


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import json
import re
from typing import Any

from wood_project.openproject import OpenProjectClient, embedded_elements
from wood_project.planning_release import release_number, release_sort_key

from . import openproject as op
from . import workbook as workbook_module
from .packets import extract_labeled_value

IMPLEMENTATION_WORKBOOK_COLUMNS = workbook_module.IMPLEMENTATION_WORKBOOK_COLUMNS
WorkbookRow = workbook_module.WorkbookRow
workbook_metadata = workbook_module.workbook_metadata
workbook_rows = workbook_module.workbook_rows


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
        openproject_lines.append(f"Planning release: {values['Version'].strip()}")
    if values["Primary Repository"].strip():
        openproject_lines.append(f"Primary Repository: {values['Primary Repository'].strip()}")
    if values["Affected Repositories"].strip():
        openproject_lines.append(
            f"Affected Repositories: {values['Affected Repositories'].strip()}"
        )
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
    client: OpenProjectClient,
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
        document = client.get_json(path, query=page_query)
        page = embedded_elements(document)
        elements.extend(page)
        total = int(document.get("total") or len(elements))
        if len(elements) >= total or not page:
            break
        offset += len(page)
    return elements


def fetch_descendants(
    client: OpenProjectClient,
    root_work_package_id: int,
) -> list[dict[str, Any]]:
    filters = [{"ancestor": {"operator": "=", "values": [str(root_work_package_id)]}}]
    return fetch_collection(
        client,
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
    versions = required_version_names(rows)
    story_ids = [story_key(row) for row in rows]
    lines = ["Codex Implementation Packet", "", "OpenProject"]
    if versions:
        lines.append(f"Planning release: {versions[0]}")
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
    version_names = required_version_names(rows)
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
    existing_description = work_package_description_text(current)
    effective_values = dict(values)
    for column in ("Primary Repository", "Affected Repositories"):
        if not effective_values[column].strip():
            effective_values[column] = extract_labeled_value(existing_description, column)
    description = compose_description(effective_values)
    released_in = extract_labeled_value(existing_description, "Released In")
    if released_in:
        marker = "\nOpenProject\n"
        if marker in description:
            description = description.replace(marker, f"{marker}Released In: {released_in}\n", 1)
        else:
            description = f"{description}\n\nOpenProject\nReleased In: {released_in}"
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


def fetch_projects(client: OpenProjectClient) -> list[dict[str, Any]]:
    return fetch_collection(client, "/api/v3/projects", query={})


def fetch_project(client: OpenProjectClient, project_id: str) -> dict[str, Any]:
    return client.get_json(f"/api/v3/projects/{project_id}")


def resolve_project(
    *,
    client: OpenProjectClient,
    configured_project_id: str,
    workbook_project: str,
) -> dict[str, Any]:
    if configured_project_id:
        project = fetch_project(client, configured_project_id)
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
        for project in fetch_projects(client)
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
    client: OpenProjectClient,
    project_id: str,
) -> list[dict[str, Any]]:
    filters = [{"project": {"operator": "=", "values": [str(project_id)]}}]
    return fetch_collection(
        client,
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
    client: OpenProjectClient,
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
        existing = client.get_json(f"/api/v3/work_packages/{root_id}")
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
            client=client,
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
    names = {row.values["Version"].strip() for row in rows if row.values["Version"].strip()}
    for row in rows:
        name = row.values["Version"].strip()
        if name and release_number(name) is None:
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                f"Row {row.row_number} Version must be an R# planning release: {name!r}.",
            )
        if row.values["Released In"].strip():
            raise op.ScriptError(
                "WORKBOOK_SCHEMA_MISMATCH",
                f"Row {row.row_number} Released In must be blank during planning/import.",
            )
    return sorted(names, key=release_sort_key)


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
    client: OpenProjectClient,
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
        existing = client.get_json(f"/api/v3/work_packages/{work_package_id}")
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
    client: OpenProjectClient,
    statuses: list[dict[str, Any]],
    types: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    initiative: dict[str, Any],
) -> dict[str, Any]:
    initiative_id = initiative["work_package_id"]
    descendants = fetch_descendants(client, initiative_id) if initiative_id else []
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
            client=client,
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
    existing_relations = existing_precedes_relations(client, descendants)
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
    client: OpenProjectClient,
    descendants: list[dict[str, Any]],
) -> set[tuple[int, int]]:
    story_ids = [
        op.work_package_id(work_package)
        for work_package in descendants
        if op.work_package_type_name(work_package) == "Story"
    ]
    relations: set[tuple[int, int]] = set()
    for story_id in story_ids:
        document = client.get_json(f"/api/v3/work_packages/{story_id}/relations")
        for relation in embedded_elements(document):
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

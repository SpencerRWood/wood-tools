#!/usr/bin/env python3
"""Export a read-only OpenProject implementation workbook snapshot."""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

ROOT_ID_ENV_KEYS = (
    "OPENPROJECT_INITIATIVE_ID",
    "OPENPROJECT_ROOT_WORK_PACKAGE_ID",
    "OPENPROJECT_ROOT_ID",
)

IMPLEMENTATION_WORKBOOK_COLUMNS = [
    "Project",
    "Root Work Package",
    "Version",
    "Epic",
    "Parent",
    "Story ID",
    "Subject",
    "Goal",
    "Acceptance Criteria",
    "Non-Goals",
    "Implementation Notes",
    "Requirement IDs",
    "Predecessors",
    "Type",
    "Status",
    "Branch Name",
    "OpenProject ID",
    "Notes",
]

SYNC_METADATA_ROWS = [
    "Configured Root Work Package ID",
    "Verified Root Work Package ID",
    "Root Work Package Subject",
    "Root Work Package Type",
    "Root Work Package OpenProject URL",
    "Ancestry Validation Status",
    "Exported At",
    "Story Count",
    "Status Summary",
]


class ScriptError(RuntimeError):
    """Structured error for safe script output."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AncestryResult:
    verified_root_work_package_id: int | None
    immediate_parent_id: int | None
    epic_work_package_id: int | None
    warnings: tuple[str, ...]


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
        default=".env.resolved",
        help="Resolved environment file path. Default: .env.resolved.",
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


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            f"Environment file not found: {path}. Run wood-secrets resolve-env --apply.",
        )

    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def require_env(env: dict[str, str], keys: list[str]) -> None:
    missing = [key for key in keys if not env.get(key)]
    if missing:
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            f"Missing required OpenProject configuration: {', '.join(missing)}.",
        )


def env_value(env: dict[str, str], key: str) -> str:
    value = os.environ.get(key) or env.get(key) or ""
    return value.strip()


def resolve_root_work_package_id(args: argparse.Namespace, env: dict[str, str]) -> int:
    if args.root_work_package_id is not None:
        return args.root_work_package_id
    for key in ROOT_ID_ENV_KEYS:
        raw_value = env_value(env, key)
        if not raw_value:
            continue
        try:
            return int(raw_value)
        except ValueError as err:
            raise ScriptError("INVALID_ROOT_WORK_PACKAGE_ID", f"{key} must be an integer.") from err
    raise ScriptError(
        "MISSING_ROOT_WORK_PACKAGE_ID",
        "Pass <root_work_package_id> or set OPENPROJECT_INITIATIVE_ID in .env.resolved.",
    )


def api_get_json(
    base_url: str,
    token: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"

    credentials = base64.b64encode(f"apikey:{token}".encode()).decode("ascii")
    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/hal+json, application/json")

    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"OpenProject API error {err.code} for {path}: {body[:200]}",
        ) from err
    except URLError as err:
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            f"OpenProject connection failed for {path}: {err.reason}",
        ) from err
    except json.JSONDecodeError as err:
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"OpenProject returned invalid JSON for {path}: {err}",
        ) from err


def embedded_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((document.get("_embedded") or {}).get("elements")) or [])


def fetch_collection(
    base_url: str,
    token: str,
    path: str,
    *,
    query: dict[str, str],
    page_size: int,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    offset = 1
    while True:
        page_query = dict(query)
        page_query["pageSize"] = str(page_size)
        page_query["offset"] = str(offset)
        document = api_get_json(base_url, token, path, query=page_query)
        page_elements = embedded_elements(document)
        elements.extend(page_elements)
        total = int(document.get("total") or len(elements))
        count = int(document.get("count") or len(page_elements))
        if len(elements) >= total or count == 0:
            break
        offset += count
    return elements


def extract_id_from_href(href: str | None, resource_name: str) -> int | None:
    if not href:
        return None
    parsed_url = parse.urlparse(href)
    match = re.search(rf"/{re.escape(resource_name)}/(\d+)", parsed_url.path)
    if not match:
        return None
    return int(match.group(1))


def link_value(wp: dict[str, Any], link_name: str) -> dict[str, Any]:
    link = (wp.get("_links") or {}).get(link_name) or {}
    return link if isinstance(link, dict) else {}


def link_title(wp: dict[str, Any], link_name: str) -> str:
    return str(link_value(wp, link_name).get("title") or "")


def link_id(wp: dict[str, Any], link_name: str, resource_name: str) -> int | None:
    return extract_id_from_href(link_value(wp, link_name).get("href"), resource_name)


def work_package_id(wp: dict[str, Any]) -> int:
    return int(wp["id"])


def work_package_url(base_url: str, wp_id: int) -> str:
    return f"{base_url.rstrip('/')}/work_packages/{wp_id}"


def work_package_type_name(wp: dict[str, Any]) -> str:
    return link_title(wp, "type")


def work_package_status_name(wp: dict[str, Any]) -> str:
    return link_title(wp, "status")


def work_package_description_text(wp: dict[str, Any]) -> str:
    description = wp.get("description")
    if isinstance(description, dict):
        raw = description.get("raw")
        if isinstance(raw, str):
            return raw
    if isinstance(description, str):
        return description
    return ""


def custom_field_value(wp: dict[str, Any], field_name: str) -> str:
    if not field_name:
        return ""
    value = wp.get(field_name)
    if value is None:
        value = (wp.get("customFields") or {}).get(field_name)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return str(value.get("value") or value.get("title") or "")
    return str(value or "")


def normalize_packet_heading(line: str) -> str:
    heading = line.strip()
    heading = re.sub(r"^#+\s*", "", heading)
    heading = re.sub(r"^\s*(?:[-*]\s*)+", "", heading)
    heading = heading.strip("*_` ")
    heading = heading.removesuffix(":").strip()
    return re.sub(r"\s+", " ", heading).lower()


def append_section_text(existing: str, heading: str, body: str, *, include_heading: bool) -> str:
    body = body.strip()
    if not body:
        return existing
    addition = f"{heading}\n{body}" if include_heading else body
    return f"{existing}\n\n{addition}".strip() if existing else addition


def extract_labeled_value(description: str, label: str) -> str:
    pattern = re.compile(
        rf"^\s*(?:[-*]\s*)?{re.escape(label)}\s*:\s*(.+?)\s*$",
        flags=re.IGNORECASE,
    )
    for line in description.splitlines():
        match = pattern.match(line)
        if match:
            return strip_wrapping_backticks(clean_packet_text(match.group(1)))
    return ""


def clean_packet_text(value: str) -> str:
    cleaned = html.unescape(value)
    cleaned = cleaned.replace("\\_", "_")
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"(?m)^\s*(?:[-*]\s*)+", "* ", cleaned)
    cleaned = re.sub(r"(?m)^\s*#+\s*", "", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = cleaned.strip()
    return cleaned


def strip_wrapping_backticks(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`"):
        return stripped[1:-1].strip()
    return stripped


def is_packet_title(line: str) -> bool:
    return normalize_packet_heading(line) == "codex implementation packet"


def parse_description_packet(description: str) -> dict[str, str]:
    sections = {
        "story_id": extract_labeled_value(description, "External story ID"),
        "branch_name": extract_labeled_value(description, "Branch"),
        "requirement_ids": extract_labeled_value(description, "Requirement IDs"),
        "goal": "",
        "acceptance_criteria": "",
        "non_goals": "",
        "implementation_notes": "",
        "notes": "",
    }
    current: str | None = None
    current_heading = ""
    current_lines: list[str] = []
    heading_map = {
        "goal": "goal",
        "acceptance criteria": "acceptance_criteria",
        "acceptance": "acceptance_criteria",
        "non-goals": "non_goals",
        "non goals": "non_goals",
        "non-goal": "non_goals",
        "implementation notes": "implementation_notes",
        "implementation requirements": "implementation_notes",
        "expected commands": "implementation_notes",
        "expected files / modules": "implementation_notes",
        "expected files/modules": "implementation_notes",
        "expected files": "implementation_notes",
        "test requirements": "implementation_notes",
        "dependencies": "implementation_notes",
        "package": "implementation_notes",
        "requirement ids": "requirement_ids",
        "notes": "implementation_notes",
    }

    def flush_current() -> None:
        nonlocal current, current_heading, current_lines
        if current is None:
            current_lines = []
            return
        body = "\n".join(current_lines).strip()
        if current == "requirement_ids":
            if body and not sections["requirement_ids"]:
                sections["requirement_ids"] = body
        else:
            sections[current] = append_section_text(
                sections[current],
                current_heading,
                clean_packet_text(body),
                include_heading=current == "implementation_notes"
                and current_heading.lower() != "implementation notes",
            )
        current = None
        current_heading = ""
        current_lines = []

    for raw_line in description.splitlines():
        heading = normalize_packet_heading(raw_line)
        if heading in heading_map:
            flush_current()
            current = heading_map[heading]
            current_heading = raw_line.strip().strip("# ").removesuffix(":").strip()
            continue
        if current:
            current_lines.append(raw_line)
    flush_current()

    if not sections["goal"]:
        lines = [
            clean_packet_text(line)
            for line in description.splitlines()
            if line.strip() and not is_packet_title(line)
        ]
        sections["goal"] = lines[0] if lines else ""
    return sections


def fetch_root_and_descendants(
    base_url: str,
    token: str,
    root_work_package_id: int,
    page_size: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = api_get_json(base_url, token, f"/api/v3/work_packages/{root_work_package_id}")
    filters = [{"ancestor": {"operator": "=", "values": [str(root_work_package_id)]}}]
    descendants = fetch_collection(
        base_url,
        token,
        "/api/v3/work_packages",
        query={"filters": json.dumps(filters)},
        page_size=page_size,
    )
    descendants.sort(key=work_package_id)
    return root, descendants


def fetch_work_package(
    base_url: str,
    token: str,
    work_packages_by_id: dict[int, dict[str, Any]],
    wp_id: int,
) -> dict[str, Any]:
    existing = work_packages_by_id.get(wp_id)
    if existing is not None:
        return existing
    fetched = api_get_json(base_url, token, f"/api/v3/work_packages/{wp_id}")
    work_packages_by_id[wp_id] = fetched
    return fetched


def fetch_relations_for_work_package(
    base_url: str,
    token: str,
    work_package_id_value: int,
    page_size: int,
) -> list[dict[str, Any]]:
    filters = [{"involved": {"operator": "=", "values": [str(work_package_id_value)]}}]
    return fetch_collection(
        base_url,
        token,
        "/api/v3/relations",
        query={"filters": json.dumps(filters)},
        page_size=page_size,
    )


def fetch_all_relations(
    base_url: str,
    token: str,
    work_package_ids: set[int],
    page_size: int,
) -> list[dict[str, Any]]:
    relations_by_id: dict[int, dict[str, Any]] = {}
    for wp_id in sorted(work_package_ids):
        for relation in fetch_relations_for_work_package(base_url, token, wp_id, page_size):
            relations_by_id[int(relation["id"])] = relation
    return [relations_by_id[relation_id] for relation_id in sorted(relations_by_id)]


def normalize_relation(relation: dict[str, Any]) -> tuple[int, int, str] | None:
    links = relation.get("_links") or {}
    from_id = extract_id_from_href((links.get("from") or {}).get("href"), "work_packages")
    to_id = extract_id_from_href((links.get("to") or {}).get("href"), "work_packages")
    relation_type = str(relation.get("type") or "")
    if from_id is None or to_id is None or not relation_type:
        return None
    inverse_types = {"follows": "precedes", "blocked": "blocks", "required": "requires"}
    if relation_type in inverse_types:
        return (to_id, from_id, inverse_types[relation_type])
    return (from_id, to_id, relation_type)


def build_predecessor_map(relations: list[dict[str, Any]]) -> dict[int, set[int]]:
    predecessor_map: dict[int, set[int]] = {}
    for relation in relations:
        normalized = normalize_relation(relation)
        if normalized is None:
            continue
        from_id, to_id, relation_type = normalized
        if relation_type == "precedes":
            predecessor_map.setdefault(to_id, set()).add(from_id)
    return predecessor_map


def resolve_ancestry(
    *,
    base_url: str,
    token: str,
    story: dict[str, Any],
    root_work_package_id: int,
    epic_type_name: str,
    work_packages_by_id: dict[int, dict[str, Any]],
) -> AncestryResult:
    story_id = work_package_id(story)
    immediate_parent_id = link_id(story, "parent", "work_packages")
    if immediate_parent_id is None:
        return AncestryResult(None, None, None, ("missing parents",))

    warnings: list[str] = []
    visited = {story_id}
    current_id: int | None = immediate_parent_id
    epic_ids: list[int] = []

    while current_id is not None:
        if current_id in visited:
            warnings.append("circular ancestry")
            return AncestryResult(
                None,
                immediate_parent_id,
                first_or_none(epic_ids),
                tuple(warnings),
            )
        visited.add(current_id)

        try:
            current = fetch_work_package(base_url, token, work_packages_by_id, current_id)
        except ScriptError:
            warnings.append(f"inaccessible parent resource: WP-{current_id}")
            return AncestryResult(
                None,
                immediate_parent_id,
                first_or_none(epic_ids),
                tuple(warnings),
            )

        if work_package_type_name(current) == epic_type_name:
            epic_ids.append(current_id)
        if current_id == root_work_package_id:
            if len(epic_ids) > 1:
                warnings.append("multiple possible Epic ancestors")
            return AncestryResult(
                root_work_package_id,
                immediate_parent_id,
                first_or_none(epic_ids),
                tuple(warnings),
            )
        current_id = link_id(current, "parent", "work_packages")

    warnings.append("ancestry that does not reach the configured root")
    return AncestryResult(None, immediate_parent_id, first_or_none(epic_ids), tuple(warnings))


def first_or_none(values: list[int]) -> int | None:
    return values[0] if values else None


def branch_name(wp_id: int, subject: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug) or "story"
    return f"feature/op-{wp_id}-{slug}"


def build_story_records(
    *,
    base_url: str,
    root: dict[str, Any],
    stories: list[dict[str, Any]],
    predecessor_map: dict[int, set[int]],
    closed_status_names: set[str],
    epic_type_name: str,
    story_id_field: str,
    requirement_ids_field: str,
    work_packages_by_id: dict[int, dict[str, Any]],
    token: str,
) -> list[dict[str, Any]]:
    story_ids_by_wp_id: dict[int, str] = {}
    for story in stories:
        story_description = work_package_description_text(story)
        story_sections = parse_description_packet(story_description)
        story_ids_by_wp_id[work_package_id(story)] = (
            custom_field_value(story, story_id_field) or story_sections["story_id"]
        )
    records: list[dict[str, Any]] = []
    for story in stories:
        wp_id = work_package_id(story)
        ancestry = resolve_ancestry(
            base_url=base_url,
            token=token,
            story=story,
            root_work_package_id=work_package_id(root),
            epic_type_name=epic_type_name,
            work_packages_by_id=work_packages_by_id,
        )
        predecessor_ids = sorted(predecessor_map.get(wp_id, set()))
        for predecessor_id in predecessor_ids:
            fetch_work_package(base_url, token, work_packages_by_id, predecessor_id)
        predecessor_story_ids = [
            story_ids_by_wp_id[predecessor_id]
            for predecessor_id in predecessor_ids
            if story_ids_by_wp_id.get(predecessor_id)
        ]
        open_predecessors = [
            predecessor_id
            for predecessor_id in predecessor_ids
            if work_package_status_name(work_packages_by_id.get(predecessor_id, {}))
            not in closed_status_names
        ]
        description_sections = parse_description_packet(work_package_description_text(story))
        story_id = custom_field_value(story, story_id_field) or description_sections["story_id"]
        requirement_ids = (
            custom_field_value(story, requirement_ids_field)
            or description_sections["requirement_ids"]
        )
        branch = description_sections["branch_name"] or branch_name(
            wp_id,
            str(story.get("subject") or ""),
        )
        epic = work_packages_by_id.get(ancestry.epic_work_package_id or -1, {})
        parent = work_packages_by_id.get(ancestry.immediate_parent_id or -1, {})
        status_name = work_package_status_name(story)
        is_closed = status_name in closed_status_names
        sync_status = "Warning" if ancestry.warnings else "OK"
        record = {
            "Project": link_title(story, "project"),
            "OpenProject Project ID": link_id(story, "project", "projects"),
            "Root Work Package": str(root.get("subject") or ""),
            "Root Work Package ID": ancestry.verified_root_work_package_id,
            "Version": link_title(story, "version"),
            "OpenProject Version ID": link_id(story, "version", "versions"),
            "Epic": str(epic.get("subject") or ""),
            "Epic Work Package ID": ancestry.epic_work_package_id,
            "Parent": str(parent.get("subject") or link_title(story, "parent")),
            "Parent Work Package ID": ancestry.immediate_parent_id,
            "Story ID": story_id,
            "Subject": str(story.get("subject") or ""),
            "Goal": description_sections["goal"],
            "Acceptance Criteria": description_sections["acceptance_criteria"],
            "Non-Goals": description_sections["non_goals"],
            "Implementation Notes": description_sections["implementation_notes"],
            "Requirement IDs": requirement_ids,
            "Predecessors": ", ".join(
                predecessor_story_ids
                or [f"WP-{predecessor_id}" for predecessor_id in predecessor_ids]
            ),
            "Predecessor Story IDs": predecessor_story_ids,
            "Predecessor OpenProject IDs": predecessor_ids,
            "Type": work_package_type_name(story),
            "OpenProject Type ID": link_id(story, "type", "types"),
            "Status": status_name,
            "OpenProject Status ID": link_id(story, "status", "statuses"),
            "Is Closed": is_closed,
            "Progress": story.get("percentageDone") or story.get("percentDone") or "",
            "Blocked": bool(open_predecessors),
            "Open Predecessor Count": len(open_predecessors),
            "Open Predecessors": ", ".join(
                f"WP-{predecessor_id}" for predecessor_id in open_predecessors
            ),
            "Eligible Next Story": (not is_closed) and not open_predecessors,
            "Assignee": link_title(story, "assignee"),
            "OpenProject Assignee ID": link_id(story, "assignee", "users"),
            "Branch Name": branch,
            "Repository Evidence": "",
            "OpenProject ID": wp_id,
            "OpenProject Work Package ID": wp_id,
            "OpenProject Created At": str(story.get("createdAt") or ""),
            "OpenProject Updated At": str(story.get("updatedAt") or ""),
            "Sync Status": sync_status,
            "Sync Warning": "; ".join(ancestry.warnings),
            "Notes": "",
            "openproject_project_id": link_id(story, "project", "projects"),
            "root_work_package_id": ancestry.verified_root_work_package_id,
            "epic_work_package_id": ancestry.epic_work_package_id,
            "parent_work_package_id": ancestry.immediate_parent_id,
            "openproject_work_package_id": wp_id,
            "openproject_version_id": link_id(story, "version", "versions"),
            "openproject_type_id": link_id(story, "type", "types"),
            "openproject_status_id": link_id(story, "status", "statuses"),
            "openproject_assignee_id": link_id(story, "assignee", "users"),
            "predecessor_openproject_ids": predecessor_ids,
            "predecessor_story_ids": predecessor_story_ids,
        }
        records.append(record)
    return sorted(records, key=lambda record: int(record["OpenProject ID"]))


def cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def sheet_xml(rows: list[list[Any]]) -> str:
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row, start=1):
            text = html.escape(cell_value(value))
            ref = f"{column_name(col_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(row_xml)}</sheetData></worksheet>"
    )


def write_xlsx(path: Path, story_records: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    story_rows = [IMPLEMENTATION_WORKBOOK_COLUMNS]
    story_rows.extend(
        [
            [record.get(column) for column in IMPLEMENTATION_WORKBOOK_COLUMNS]
            for record in story_records
        ]
    )
    metadata_rows = [["Field", "Value"]]
    metadata_rows.extend([[row_name, metadata.get(row_name)] for row_name in SYNC_METADATA_ROWS])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml())
        workbook.writestr("_rels/.rels", package_relationships_xml())
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_relationships_xml())
        workbook.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Implementation" sheetId="1" r:id="rId1"/>'
            '<sheet name="Sync Metadata" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
        )
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml(story_rows))
        workbook.writestr("xl/worksheets/sheet2.xml", sheet_xml(metadata_rows))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def status_summary(story_records: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for record in story_records:
        status = str(record.get("Status") or "(blank)")
        summary[status] = summary.get(status, 0) + 1
    return dict(sorted(summary.items()))


def workbook_relationships_xml() -> str:
    worksheet_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{worksheet_type}" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{worksheet_type}" Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )


def content_types_xml() -> str:
    package_relationships = "application/vnd.openxmlformats-package.relationships+xml"
    workbook_content = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    worksheet_content = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Default Extension="rels" ContentType="{package_relationships}"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{workbook_content}"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{worksheet_content}"/>'
        f'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="{worksheet_content}"/>'
        "</Types>"
    )


def package_relationships_xml() -> str:
    office_document_type = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{office_document_type}" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        env = parse_env_file(Path(args.env_file))
        require_env(env, ["OPENPROJECT_URL", "OPENPROJECT_API_TOKEN"])
        base_url = env_value(env, "OPENPROJECT_URL").rstrip("/")
        token = env_value(env, "OPENPROJECT_API_TOKEN")
        root_work_package_id = resolve_root_work_package_id(args, env)
        closed_status_names = set(args.closed_status or ["Closed"])
        output_dir = (Path.cwd() / args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        root, descendants = fetch_root_and_descendants(
            base_url,
            token,
            root_work_package_id,
            args.page_size,
        )
        work_packages_by_id = {work_package_id(root): root}
        work_packages_by_id.update({work_package_id(wp): wp for wp in descendants})
        stories = [wp for wp in descendants if work_package_type_name(wp) == args.story_type]
        relation_ids = {work_package_id(wp) for wp in [root, *descendants]}
        relations = fetch_all_relations(base_url, token, relation_ids, args.page_size)
        predecessor_map = build_predecessor_map(relations)

        story_records = build_story_records(
            base_url=base_url,
            root=root,
            stories=stories,
            predecessor_map=predecessor_map,
            closed_status_names=closed_status_names,
            epic_type_name=args.epic_type,
            story_id_field=args.story_id_field,
            requirement_ids_field=args.requirement_ids_field,
            work_packages_by_id=work_packages_by_id,
            token=token,
        )
        validation_warnings = [
            record["Sync Warning"] for record in story_records if record["Sync Warning"]
        ]
        metadata = {
            "Configured Root Work Package ID": root_work_package_id,
            "Verified Root Work Package ID": (
                root_work_package_id if not validation_warnings else ""
            ),
            "Root Work Package Subject": str(root.get("subject") or ""),
            "Root Work Package Type": work_package_type_name(root),
            "Root Work Package OpenProject URL": work_package_url(base_url, root_work_package_id),
            "Ancestry Validation Status": "Warning" if validation_warnings else "OK",
            "Exported At": datetime.now(UTC).isoformat(),
            "Story Count": len(story_records),
            "Status Summary": status_summary(story_records),
        }
        snapshot = {
            "sync_metadata": metadata,
            "refresh_key": "openproject_work_package_id",
            "implementation_workbook_columns": IMPLEMENTATION_WORKBOOK_COLUMNS,
            "status_summary": metadata["Status Summary"],
            "stories": story_records,
        }
        json_path = output_dir / "implementation_workbook.json"
        xlsx_path = output_dir / "implementation_workbook.xlsx"
        write_json(json_path, snapshot)
        write_xlsx(xlsx_path, story_records, metadata)
    except ScriptError as err:
        payload = {"ok": False, "error": {"code": err.code, "message": str(err)}}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Error: {err}", file=sys.stderr)
        return 2

    payload = {
        "ok": True,
        "output_dir": str(output_dir),
        "json": str(json_path),
        "xlsx": str(xlsx_path),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wrote implementation workbook snapshot to {output_dir}")
        print(f"- {json_path.name}")
        print(f"- {xlsx_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

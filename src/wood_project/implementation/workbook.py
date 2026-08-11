from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import openproject as op

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

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


@dataclass(frozen=True)
class WorkbookRow:
    row_number: int
    values: dict[str, str]


def column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - 64)
    return index - 1


def xml_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


def read_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        raw = workbook.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    return [xml_text(item) for item in root.findall(f"{{{SHEET_NS}}}si")]


def workbook_sheet_target(workbook: zipfile.ZipFile, sheet_name: str) -> str:
    workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
    rels_xml = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    rels = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_xml.findall(f"{{{REL_NS}}}Relationship")
    }
    for sheet in workbook_xml.findall(f".//{{{SHEET_NS}}}sheet"):
        if sheet.attrib.get("name") != sheet_name:
            continue
        rel_id = sheet.attrib.get(f"{{{OFFICE_REL_NS}}}id")
        if not rel_id or rel_id not in rels:
            break
        target = rels[rel_id]
        return f"xl/{target}" if not target.startswith("/") else target.lstrip("/")
    raise op.ScriptError(
        "WORKBOOK_PARSE_FAILED",
        f"Sheet not found in workbook: {sheet_name}",
    )


def read_xlsx_rows(path: Path, sheet_name: str) -> list[list[str]]:
    if not path.is_file():
        raise op.ScriptError("WORKBOOK_PARSE_FAILED", f"Workbook not found: {path}")
    try:
        with zipfile.ZipFile(path) as workbook:
            shared_strings = read_shared_strings(workbook)
            sheet_path = workbook_sheet_target(workbook, sheet_name)
            sheet_xml = ET.fromstring(workbook.read(sheet_path))
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as err:
        raise op.ScriptError(
            "WORKBOOK_PARSE_FAILED",
            f"Unable to read workbook {path}: {err}",
        ) from err

    rows: list[list[str]] = []
    for row in sheet_xml.findall(f".//{{{SHEET_NS}}}row"):
        values_by_index: dict[int, str] = {}
        for cell in row.findall(f"{{{SHEET_NS}}}c"):
            ref = str(cell.attrib.get("r") or "")
            index = column_index(ref)
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = xml_text(cell.find(f"{{{SHEET_NS}}}is"))
            else:
                raw_value = xml_text(cell.find(f"{{{SHEET_NS}}}v"))
                if cell_type == "s" and raw_value:
                    value = shared_strings[int(raw_value)]
                else:
                    value = raw_value
            values_by_index[index] = value.strip()
        if values_by_index:
            rows.append(
                [values_by_index.get(index, "") for index in range(max(values_by_index) + 1)]
            )
    return rows


def workbook_rows(path: Path, sheet_name: str) -> list[WorkbookRow]:
    rows = read_xlsx_rows(path, sheet_name)
    if not rows:
        raise op.ScriptError("WORKBOOK_PARSE_FAILED", "Workbook sheet has no rows.")
    headers = rows[0]
    missing = [column for column in IMPLEMENTATION_WORKBOOK_COLUMNS if column not in headers]
    if missing:
        raise op.ScriptError(
            "WORKBOOK_SCHEMA_MISMATCH",
            f"Workbook is missing required columns: {', '.join(missing)}",
        )
    indexed_headers = {column: headers.index(column) for column in IMPLEMENTATION_WORKBOOK_COLUMNS}
    records: list[WorkbookRow] = []
    for row_number, row in enumerate(rows[1:], start=2):
        values = {
            column: row[indexed_headers[column]] if indexed_headers[column] < len(row) else ""
            for column in IMPLEMENTATION_WORKBOOK_COLUMNS
        }
        if any(values.values()):
            records.append(WorkbookRow(row_number=row_number, values=values))
    return records


def workbook_metadata(path: Path) -> dict[str, str]:
    try:
        rows = read_xlsx_rows(path, "Sync Metadata")
    except op.ScriptError:
        return {}
    metadata: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[0].strip():
            metadata[row[0].strip()] = row[1].strip()
    return metadata


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

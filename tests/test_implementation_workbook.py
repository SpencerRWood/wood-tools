from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wood_project.implementation import export as export_workbook
from wood_project.implementation import planner as implementation_workbook


def workbook_row(row_number: int, **values: str) -> implementation_workbook.WorkbookRow:
    row_values = {column: "" for column in implementation_workbook.IMPLEMENTATION_WORKBOOK_COLUMNS}
    row_values.update(values)
    return implementation_workbook.WorkbookRow(row_number=row_number, values=row_values)


def named_element(name: str, href: str, element_id: int = 1) -> dict[str, Any]:
    return {"id": element_id, "name": name, "_links": {"self": {"href": href}}}


def test_build_implementation_plan_matches_story_without_openproject_id(
    monkeypatch,
) -> None:
    row = workbook_row(
        2,
        **{
            "Story ID": "S1",
            "Subject": "Existing story",
            "Version": "V1",
            "Epic": "Existing epic",
            "Type": "Story",
            "Status": "New",
        },
    )
    descendants = [
        {
            "id": 10,
            "subject": "Existing epic",
            "_links": {"type": {"title": "Epic"}},
        },
        {
            "id": 11,
            "subject": "Existing story",
            "lockVersion": 3,
            "description": {"raw": "External story ID: S1"},
            "_links": {"type": {"title": "Story"}, "project": {"title": "Project"}},
        },
    ]

    def fake_get_json(base_url, token, path, *, query=None):
        if path == "/api/v3/work_packages":
            return {"total": len(descendants), "_embedded": {"elements": descendants}}
        if path == "/api/v3/work_packages/11/relations":
            return {"total": 0, "_embedded": {"elements": []}}
        raise AssertionError(path)

    monkeypatch.setattr(implementation_workbook.next_story, "api_get_json", fake_get_json)

    plan = implementation_workbook.build_implementation_plan(
        rows=[row],
        base_url="https://openproject.example.test",
        token="token",
        statuses=[named_element("New", "/api/v3/statuses/1")],
        types=[
            named_element("Story", "/api/v3/types/1"),
            named_element("Epic", "/api/v3/types/2"),
        ],
        versions=[],
        initiative={
            "key": "Root",
            "subject": "Root",
            "action": "reuse",
            "work_package_id": 208,
            "type": "Initiative",
            "patch": None,
            "warnings": [],
        },
    )

    assert plan["initiative"]["action"] == "reuse"
    assert plan["versions"][0]["action"] == "create"
    assert plan["epics"][0]["action"] == "reuse"
    assert plan["stories"][0]["action"] == "update"
    assert plan["stories"][0]["work_package_id"] == 11
    assert "Existing Story matched by Story ID." in plan["stories"][0]["warnings"]


def test_resolve_initiative_plan_creates_when_no_deterministic_match(
    monkeypatch,
) -> None:
    row = workbook_row(
        2,
        **{
            "Project": "Project",
            "Root Work Package": "V1 Initiative",
            "Story ID": "S1",
            "Subject": "New story",
        },
    )

    monkeypatch.setattr(implementation_workbook, "project_work_packages", lambda **kwargs: [])

    initiative = implementation_workbook.resolve_initiative_plan(
        base_url="https://openproject.example.test",
        token="token",
        project={"id": 7, "identifier": "project", "name": "Project"},
        statuses=[named_element("New", "/api/v3/statuses/1")],
        types=[named_element("Initiative", "/api/v3/types/9")],
        rows=[row],
        metadata={},
        explicit_initiative_id=None,
        configured_initiative_id=None,
    )

    assert initiative["action"] == "create"
    assert initiative["subject"] == "V1 Initiative"
    assert initiative["patch"]["subject"] == "V1 Initiative"
    assert initiative["patch"]["_links"]["type"]["href"] == "/api/v3/types/9"


def test_resolve_initiative_plan_blocks_conflicting_root_type(
    monkeypatch,
) -> None:
    row = workbook_row(
        2,
        **{
            "Project": "Project",
            "Root Work Package": "V1 Initiative",
            "Story ID": "S1",
            "Subject": "New story",
        },
    )
    monkeypatch.setattr(
        implementation_workbook,
        "project_work_packages",
        lambda **kwargs: [
            {
                "id": 22,
                "subject": "V1 Initiative",
                "_links": {"type": {"title": "Epic"}},
            }
        ],
    )

    with pytest.raises(
        implementation_workbook.next_story.ScriptError,
        match="conflicting types",
    ):
        implementation_workbook.resolve_initiative_plan(
            base_url="https://openproject.example.test",
            token="token",
            project={"id": 7, "identifier": "project", "name": "Project"},
            statuses=[named_element("New", "/api/v3/statuses/1")],
            types=[named_element("Initiative", "/api/v3/types/9")],
            rows=[row],
            metadata={},
            explicit_initiative_id=None,
            configured_initiative_id=None,
        )


def test_apply_writeback_records_openproject_ids(tmp_path: Path) -> None:
    workbook = tmp_path / "implementation_workbook.xlsx"
    story_record = {
        column: "" for column in implementation_workbook.IMPLEMENTATION_WORKBOOK_COLUMNS
    }
    story_record.update({"Story ID": "S1", "Subject": "Created story", "OpenProject ID": ""})
    export_workbook.write_xlsx(workbook, [story_record], {"Story Count": 1})

    rows = implementation_workbook.workbook_rows(workbook, "Implementation")
    result = implementation_workbook.write_openproject_ids_to_workbook(
        workbook,
        sheet_name="Implementation",
        rows=rows,
        applied={
            "versions": [],
            "epics": [],
            "stories": [{"key": "S1", "work_package_id": 123}],
            "relations": [],
        },
    )

    updated_rows = implementation_workbook.workbook_rows(workbook, "Implementation")
    assert result["updated"] is True
    assert result["updated_rows"] == [
        {
            "row_number": 2,
            "story_key": "S1",
            "openproject_id": 123,
            "previous_openproject_id": "",
        }
    ]
    assert updated_rows[0].values["OpenProject ID"] == "123"

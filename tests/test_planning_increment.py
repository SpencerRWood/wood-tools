from __future__ import annotations

from types import SimpleNamespace

import pytest

from wood_project.implementation import planning, workbook
from wood_project.story import discovery


def test_planning_increment_numeric_order() -> None:
    names = [f"R{number} — Increment {number}" for number in (10, 2, 1, 9)]
    assert sorted(names, key=discovery.version_rank) == [
        "R1 — Increment 1",
        "R2 — Increment 2",
        "R9 — Increment 9",
        "R10 — Increment 10",
    ]


def test_next_story_uses_earliest_active_increment_and_ready_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(settings=SimpleNamespace(project_id="1", base_url="https://op.test"))
    stories = [
        {
            "id": story_id,
            "subject": f"Story {story_id}",
            "_links": {
                "type": {"title": "Story"},
                "status": {"title": status},
                "version": {"title": increment},
            },
        }
        for story_id, status, increment in (
            (1, "New", "R1 — First"),
            (2, "New", "R1 — First"),
            (3, "New", "R2 — Second"),
            (10, "New", "R10 — Tenth"),
        )
    ]

    def fake_get(_client, path, **_kwargs):
        if path == "/api/v3/work_packages/99":
            return {"id": 99, "_links": {"project": {"href": "/api/v3/projects/1"}}}
        if path == "/api/v3/statuses":
            return {"_embedded": {"elements": [{"name": "Closed", "isClosed": True}]}}
        if path.endswith("/versions"):
            return {
                "_embedded": {
                    "elements": [
                        {"name": "R1 — First", "status": "open"},
                        {"name": "R2 — Second", "status": "open"},
                        {"name": "R10 — Tenth", "status": "open"},
                    ]
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(discovery, "api_get_json", fake_get)
    monkeypatch.setattr(discovery, "fetch_descendants", lambda *_args: stories)
    monkeypatch.setattr(discovery, "fetch_predecessor_map", lambda *_args: {1: {2}})
    result = discovery.discover_next_story(
        client=client,
        root_work_package_id=99,
        target_status="New",
        story_type="Story",
        page_size=100,
    )
    assert result["story"]["id"] == 2
    assert result["story"]["version"] == "R1 — First"


def test_workbook_traceability_and_released_in_lifecycle(tmp_path) -> None:
    path = tmp_path / "plan.xlsx"
    record = {column: "" for column in workbook.EXPORT_WORKBOOK_COLUMNS}
    record.update({"Version": "R1 — Foundations", "Primary Repository": "wood-tools"})
    workbook.write_xlsx(path, [record], {})
    values = workbook.workbook_rows(path, "Implementation")[0].values
    assert values["Primary Repository"] == "wood-tools"
    assert values["Released In"] == ""
    assert "Released In:" not in planning.compose_description(values)
    values.update({"Status": "Closed", "Released In": "v0.9.0"})
    assert "Released In: v0.9.0" in planning.compose_description(values)


def test_import_accepts_r_increment_without_semver() -> None:
    values = {column: "" for column in workbook.EXPORT_WORKBOOK_COLUMNS}
    values["Version"] = "R10 — Platform Foundations"
    row = workbook.WorkbookRow(2, values)
    assert planning.build_version_plan([row], [])[0]["name"] == values["Version"]
    with pytest.raises(Exception, match="R# Planning Increment"):
        planning.build_version_plan([workbook.WorkbookRow(2, {**values, "Version": "V0.2"})], [])

    rows = [
        workbook.WorkbookRow(index + 2, {**values, "Version": f"R{number}"})
        for index, number in enumerate((10, 2, 1, 9))
    ]
    assert planning.required_version_names(rows) == ["R1", "R2", "R9", "R10"]


def test_import_reuses_historical_version_without_migrating_it() -> None:
    values = {column: "" for column in workbook.EXPORT_WORKBOOK_COLUMNS}
    values["Version"] = "V0.2 Foundation"
    existing = [
        {
            "id": 7,
            "name": "V0.2 Foundation",
            "_links": {"self": {"href": "/api/v3/versions/7"}},
        }
    ]
    result = planning.build_version_plan([workbook.WorkbookRow(2, values)], existing)
    assert result[0]["action"] == "reuse"
    assert result[0]["version_id"] == 7


def test_released_in_requires_closed_story_and_actual_semver() -> None:
    values = {column: "" for column in workbook.EXPORT_WORKBOOK_COLUMNS}
    values.update({"Version": "R1", "Primary Repository": "wood-tools", "Status": "New"})
    row = workbook.WorkbookRow(2, values)
    planning.validate_story_traceability(row)
    values["Primary Repository"] = ""
    with pytest.raises(Exception, match="requires Primary Repository"):
        planning.validate_story_traceability(row)
    values["Primary Repository"] = "wood-tools"
    values["Released In"] = "v0.9.0"
    with pytest.raises(Exception, match="Released In requires a closed Story"):
        planning.validate_story_traceability(row)
    values["Status"] = "Closed"
    planning.validate_story_traceability(row)
    values["Released In"] = "R1"
    with pytest.raises(Exception, match="actual repository SemVer"):
        planning.validate_story_traceability(row)

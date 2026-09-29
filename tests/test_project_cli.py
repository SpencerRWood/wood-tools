from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from wood import project as project_module
from wood.cli import main
from wood_project.implementation import workbook


def project(id: int, identifier: str, name: str) -> dict[str, Any]:
    return {"id": id, "identifier": identifier, "name": name, "active": True}


def initiative(id: int, name: str, status: str = "New") -> dict[str, Any]:
    return {
        "id": id,
        "subject": name,
        "_links": {"type": {"title": "Initiative"}, "status": {"title": status}},
    }


def test_project_list_filters_by_initiative_id_and_reports_ambiguous_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(project_module, "_client", lambda: object())
    monkeypatch.setattr(
        project_module,
        "_projects",
        lambda client: [project(3, "wood", "Wood"), project(4, "other", "Other")],
    )
    monkeypatch.setattr(
        project_module,
        "_project_work_packages",
        lambda client, project_id: [initiative(project_id + 200, "Shared")],
    )

    assert main(["project", "list", "--initiative", "203", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["total"] == 1
    assert result["data"]["projects"][0]["id"] == 3
    assert result["data"]["projects"][0]["initiatives"][0]["id"] == 203

    assert main(["project", "list", "--initiative", "Shared", "--json"]) == 5
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ambiguous"
    assert {item["id"] for item in result["data"]["candidates"]} == {203, 204}


def test_project_status_resolves_identifier_and_scopes_story_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = initiative(208, "Tools")
    story = {"id": 398, "_links": {"type": {"title": "Story"}, "status": {"title": "In progress"}}}
    client = object()
    monkeypatch.setattr(project_module, "_client", lambda: client)
    monkeypatch.setattr(project_module, "_projects", lambda value: [project(3, "wood", "Wood")])
    monkeypatch.setattr(project_module, "_project_work_packages", lambda value, id: [root])
    monkeypatch.setattr(project_module.planning, "fetch_descendants", lambda value, id: [story])
    monkeypatch.setattr(
        project_module,
        "_collection",
        lambda value, path: [{"id": 20, "name": "R1", "status": "open"}],
    )

    assert main(["project", "status", "wood", "--initiative", "208", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["stories"] == {"total": 1, "by_status": {"In progress": 1}}
    assert result["data"]["project"]["initiatives"][0]["id"] == 208


def test_import_workbook_plan_hash_blocks_stale_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "implementation.xlsx"
    path.write_bytes(b"reviewed-workbook")
    values = {column: "" for column in workbook.IMPLEMENTATION_WORKBOOK_COLUMNS}
    values.update({"Project": "wood", "Root Work Package": "Tools", "Story ID": "S1"})
    row = workbook.WorkbookRow(row_number=2, values=values)
    client = SimpleNamespace(settings=SimpleNamespace(base_url="https://example.test"))
    client.get_json = lambda path, query=None: {"_embedded": {"elements": []}}
    phases = {
        "initiative": {
            "action": "reuse",
            "key": "Tools",
            "work_package_id": 208,
            "subject": "Tools",
        },
        "versions": [],
        "epics": [],
        "stories": [{"action": "create", "key": "S1", "row_number": 2, "work_package_id": None}],
        "relations": [],
    }
    writes: list[str] = []
    monkeypatch.setattr(project_module.workbook, "workbook_rows", lambda path, sheet: [row])
    monkeypatch.setattr(project_module.workbook, "workbook_metadata", lambda path: {})
    monkeypatch.setattr(project_module, "_projects", lambda value: [project(3, "wood", "Wood")])
    monkeypatch.setattr(
        project_module.planning, "resolve_project", lambda **kwargs: project(3, "wood", "Wood")
    )
    monkeypatch.setattr(
        project_module.planning, "resolve_initiative_plan", lambda **kwargs: phases["initiative"]
    )
    monkeypatch.setattr(
        project_module.planning, "build_implementation_plan", lambda **kwargs: phases
    )
    monkeypatch.setattr(
        project_module.workbook_apply,
        "apply_plan",
        lambda *args: (
            writes.append("apply")
            or {"initiative": [], "versions": [], "epics": [], "stories": [], "relations": []}
        ),
    )
    monkeypatch.setattr(
        project_module.workbook_apply,
        "write_openproject_ids_to_workbook",
        lambda *args, **kwargs: {"updated_rows": []},
    )
    monkeypatch.setattr(
        project_module.workbook_apply,
        "write_initiative_metadata_to_workbook",
        lambda *args, **kwargs: {"updated": False},
    )

    options = {"sheet_name": "Implementation", "project_ref": None, "initiative_ref": None}
    preview = project_module.import_workbook(client, path, apply=False, plan_hash=None, **options)
    assert preview["mutation"] == "preview"
    digest = preview["data"]["plan_hash"]
    path.write_bytes(b"changed-workbook")
    blocked = project_module.import_workbook(client, path, apply=True, plan_hash=digest, **options)
    assert blocked["status"] == "blocked"
    assert writes == []

    path.write_bytes(b"reviewed-workbook")
    applied = project_module.import_workbook(client, path, apply=True, plan_hash=digest, **options)
    assert applied["status"] == "success"
    assert writes == ["apply"]


def test_workbook_requires_all_21_columns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        workbook,
        "read_xlsx_rows",
        lambda path, sheet: [workbook.IMPLEMENTATION_WORKBOOK_COLUMNS[:18]],
    )
    with pytest.raises(Exception, match="21 distinct"):
        workbook.workbook_rows(tmp_path / "old.xlsx", "Implementation")


def test_project_collection_uses_openproject_page_offsets() -> None:
    offsets: list[str] = []

    def get_json(path: str, *, query: dict[str, str]) -> dict[str, Any]:
        offsets.append(query["offset"])
        ids = [1, 2] if query["offset"] == "1" else [3]
        return {"total": 3, "_embedded": {"elements": [{"id": id} for id in ids]}}

    client = SimpleNamespace(get_json=get_json)
    result = project_module.planning.fetch_collection(
        client, "/api/v3/projects", query={}, page_size=2
    )
    assert [item["id"] for item in result] == [1, 2, 3]
    assert offsets == ["1", "2"]

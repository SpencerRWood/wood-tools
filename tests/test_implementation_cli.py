from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wood_project.cli import main as project_main
from wood_project.commands import implementation as implementation_commands
from wood_project.implementation import workflows as implementation_workflows


@pytest.fixture(autouse=True)
def disable_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")


def test_project_implementation_export_returns_read_only_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_export_workbook(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "read_only": True,
            "output_dir": str(tmp_path),
            "json": str(tmp_path / "implementation_workbook.json"),
            "xlsx": str(tmp_path / "implementation_workbook.xlsx"),
        }

    monkeypatch.setattr(implementation_commands, "export_workbook", fake_export_workbook)

    assert (
        project_main(
            [
                "implementation",
                "export",
                "208",
                "--output-dir",
                str(tmp_path),
                "--closed-status",
                "Closed",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "implementation-export"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["requires_approval"] is False
    assert payload["data"]["xlsx"] == str(tmp_path / "implementation_workbook.xlsx")
    assert calls[0]["root_work_package_id"] == 208
    assert calls[0]["closed_statuses"] == ["Closed"]


def test_project_implementation_plan_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workbook = tmp_path / "implementation.xlsx"
    workbook.touch()
    calls: list[dict[str, Any]] = []

    def fake_plan_workbook(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "read_only": True,
            "applied": False,
            "row_count": 1,
            "phases": {"epics": [], "stories": [], "relations": []},
            "plan": [{"kind": "story", "action": "update"}],
            "applied_openproject": {"epics": [], "stories": [], "relations": []},
        }

    monkeypatch.setattr(implementation_commands, "plan_workbook", fake_plan_workbook)

    assert (
        project_main(
            [
                "implementation",
                "plan",
                str(workbook),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "implementation-plan"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["applied"] is False
    assert payload["data"]["row_count"] == 1
    assert calls[0]["workbook"] == workbook
    assert calls[0]["initiative_id"] is None


def test_project_implementation_apply_is_mutating(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workbook = tmp_path / "implementation.xlsx"
    workbook.touch()
    calls: list[dict[str, Any]] = []

    def fake_apply_workbook(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "read_only": False,
            "applied": True,
            "row_count": 1,
            "phases": {"epics": [], "stories": [], "relations": []},
            "plan": [{"kind": "story", "action": "update"}],
            "applied_openproject": {
                "epics": [],
                "stories": [{"action": "update", "work_package_id": 305}],
                "relations": [],
            },
        }

    monkeypatch.setattr(implementation_commands, "apply_workbook", fake_apply_workbook)

    assert (
        project_main(
            [
                "implementation",
                "apply",
                str(workbook),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "implementation-apply"
    assert payload["status"] == "success"
    assert payload["mutation"] == "mutating"
    assert payload["data"]["applied"] is True
    assert calls[0]["initiative_id"] is None


def test_implementation_workflow_uses_export_library_function(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_export_snapshot(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["root_work_package_id"] == 208
        return {
            "ok": True,
            "output_dir": str(tmp_path),
            "json": str(tmp_path / "implementation_workbook.json"),
            "xlsx": str(tmp_path / "implementation_workbook.xlsx"),
        }

    monkeypatch.setattr(
        implementation_workflows.export_snapshot_module,
        "export_snapshot",
        fake_export_snapshot,
    )

    payload = implementation_workflows.export_workbook(
        root_work_package_id=208,
        env_file=tmp_path / ".env.resolved",
        output_dir=tmp_path,
        story_type="Story",
        epic_type="Epic",
        closed_statuses=[],
        story_id_field="",
        requirement_ids_field="",
        page_size=500,
    )

    assert payload["read_only"] is True
    assert payload["xlsx"] == str(tmp_path / "implementation_workbook.xlsx")


def test_implementation_workflow_uses_planner_library_function(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_main(argv: list[str] | None = None) -> int:
        raise AssertionError("workflow should not call planner.main")

    def fake_plan_payload(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["apply"] is False
        return {
            "ok": True,
            "applied": False,
            "row_count": 1,
            "phases": {"epics": [], "stories": [], "relations": []},
            "plan": [],
            "applied_openproject": {"epics": [], "stories": [], "relations": []},
        }

    monkeypatch.setattr(implementation_workflows.planner_module, "main", fail_main)
    monkeypatch.setattr(
        implementation_workflows.planner_module,
        "implementation_plan_payload",
        fake_plan_payload,
    )

    payload = implementation_workflows.plan_workbook(
        workbook=tmp_path / "implementation_workbook.xlsx",
        env_file=tmp_path / ".env.resolved",
        sheet_name="Implementation",
        initiative_id=None,
    )

    assert payload["read_only"] is True
    assert payload["row_count"] == 1


def test_record_release_cli_previews_then_applies(monkeypatch, tmp_path, capsys) -> None:
    calls = []

    def fake_record(**kwargs):
        calls.append(kwargs)
        return {
            "work_package_id": 123,
            "released_in": "1.2.3",
            "changed": True,
            "applied": kwargs["apply"],
        }

    monkeypatch.setattr(implementation_commands, "record_released_in", fake_record)
    args = [
        "implementation",
        "record-release",
        str(tmp_path / "plan.xlsx"),
        "123",
        "1.2.3",
        "--json",
    ]
    assert project_main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "blocked" and calls[-1]["apply"] is False
    assert project_main([*args, "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "success" and calls[-1]["apply"] is True


@pytest.mark.parametrize("command", ["backlog", "story-backlog"])
def test_project_implementation_legacy_aliases_are_removed(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        project_main([command, "--help"])

    assert exc.value.code == 2

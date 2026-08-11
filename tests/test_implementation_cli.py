from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wood_project.cli import main as project_main
from wood_project.commands import implementation as implementation_commands


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


@pytest.mark.parametrize("command", ["backlog", "story-backlog"])
def test_project_implementation_legacy_aliases_are_removed(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        project_main([command, "--help"])

    assert exc.value.code == 2

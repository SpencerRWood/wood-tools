from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wood_project.cli import main as project_main
from wood_project.commands import story_backlog as story_backlog_commands


@pytest.fixture(autouse=True)
def disable_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")


def test_project_story_backlog_export_returns_read_only_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_export_snapshot(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "read_only": True,
            "output_dir": str(tmp_path),
            "json": str(tmp_path / "story_backlog.json"),
            "xlsx": str(tmp_path / "story_backlog.xlsx"),
        }

    monkeypatch.setattr(story_backlog_commands, "export_snapshot", fake_export_snapshot)

    assert (
        project_main(
            [
                "backlog",
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
    assert payload["command"] == "backlog-export"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["requires_approval"] is False
    assert payload["data"]["xlsx"] == str(tmp_path / "story_backlog.xlsx")
    assert calls[0]["root_work_package_id"] == 208
    assert calls[0]["closed_statuses"] == ["Closed"]


def test_project_story_backlog_upload_plan_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workbook = tmp_path / "implementation.xlsx"
    workbook.touch()
    calls: list[dict[str, Any]] = []

    def fake_upload_plan(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "ok": True,
            "read_only": True,
            "applied": False,
            "row_count": 1,
            "phases": {"epics": [], "stories": [], "relations": []},
            "plan": [{"kind": "story", "action": "update"}],
        }

    monkeypatch.setattr(story_backlog_commands, "upload_plan", fake_upload_plan)

    assert (
        project_main(
            [
                "backlog",
                "upload",
                str(workbook),
                "--initiative-id",
                "208",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "backlog-upload"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["applied"] is False
    assert payload["data"]["row_count"] == 1
    assert calls[0]["workbook"] == workbook
    assert calls[0]["parent_id"] == 208

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wood_project.cli import main as project_main
from wood_project.release import github, tag


@pytest.fixture(autouse=True)
def disable_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")


def write_pyproject(path: Path, version: str = "0.2.0") -> None:
    path.write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                f'version = "{version}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_project_release_bump_is_preview_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    assert project_main(["release", "bump", "patch", "--pyproject", str(pyproject), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "release-bump"
    assert payload["status"] == "blocked"
    assert payload["requires_approval"] is True
    assert payload["data"]["dry_run"] is True
    assert payload["data"]["old_version"] == "0.2.0"
    assert payload["data"]["new_version"] == "0.2.1"
    assert 'version = "0.2.0"' in pyproject.read_text(encoding="utf-8")


def test_project_release_bump_apply_updates_pyproject(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    assert (
        project_main(
            ["release", "bump", "minor", "--pyproject", str(pyproject), "--apply", "--json"]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["mutation"] == "mutating"
    assert payload["data"]["changed"] is True
    assert 'version = "0.3.0"' in pyproject.read_text(encoding="utf-8")


def test_project_release_tag_is_preview_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    monkeypatch.setattr(
        tag,
        "inspect_repo_state",
        lambda: (
            tmp_path,
            {
                "path": str(tmp_path),
                "current_branch": "main",
                "working_tree_clean": True,
                "modified_files": [],
                "untracked_files": [],
                "ahead": 0,
                "behind": 0,
                "head": "abc1234",
            },
        ),
    )
    monkeypatch.setattr(tag, "tag_exists", lambda repo_root, tag_name: False)

    assert project_main(["release", "tag", "--pyproject", str(pyproject), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "release-tag"
    assert payload["status"] == "blocked"
    assert payload["data"]["tag"] == {
        "name": "v0.2.0",
        "version": "0.2.0",
        "target_commit": "abc1234",
        "would_create": True,
    }


def test_project_release_github_create_preview_with_history_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    notes: dict[str, Any] = {
        "source": "branch-history",
        "body": "## Summary\nDemo",
        "commit_count": 1,
    }
    monkeypatch.setattr(github, "collect_history_notes", lambda version: notes)

    assert (
        project_main(
            [
                "release",
                "github-create",
                "--pyproject",
                str(pyproject),
                "--notes-from-history",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "release-github-create"
    assert payload["status"] == "blocked"
    assert payload["data"]["release"]["tag"] == "v0.2.0"
    assert payload["data"]["release"]["notes"] == notes
    assert payload["data"]["command"] == [
        "gh",
        "release",
        "create",
        "v0.2.0",
        "--title",
        "v0.2.0",
        "--notes-file",
        "-",
    ]

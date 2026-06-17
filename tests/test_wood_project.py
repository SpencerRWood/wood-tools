from __future__ import annotations

import json
from pathlib import Path

import pytest

from wood_project.cli import main


def test_init_show_validate_success_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "demo-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--project-id", "proj-123", "--apply"]) == 0

    project_file = project_root / "project.json"
    metadata_dir = project_root / ".wood"
    artifact_root = metadata_dir / "artifacts"
    artifact_dir = artifact_root / "demo-app"

    assert project_file.exists()
    assert metadata_dir.is_dir()
    assert artifact_root.is_dir()
    assert artifact_dir.is_dir()

    document = json.loads(project_file.read_text(encoding="utf-8"))
    assert document["project_id"] == "proj-123"
    assert document["project_slug"] == "demo-app"
    assert document["project_root"] == str(project_root.resolve())
    assert document["artifact_root"] == str(artifact_root.resolve())
    assert document["artifact_dir"] == str(artifact_dir.resolve())
    assert document["metadata_dir"] == str(metadata_dir.resolve())

    assert main(["show"]) == 0
    out = capsys.readouterr().out
    assert f"path: {project_file.resolve()}" in out
    assert "project_slug: demo-app" in out

    assert main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "valid: True" in out
    assert f"path: {project_file.resolve()}" in out


def test_init_without_apply_is_approval_gated_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "preview-only"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "init"
    assert payload["status"] == "blocked"
    assert payload["requires_approval"] is True
    assert payload["data"]["changed"] is False
    assert Path(payload["data"]["path"]).name == "project.json"
    assert not (project_root / "project.json").exists()


def test_init_with_apply_json_reports_success_and_custom_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "Client Portal"
    artifact_root = tmp_path / "artifacts-root"
    project_root.mkdir(parents=True)

    assert (
        main(
            [
                "--project-root",
                str(project_root),
                "init",
                "--project-id",
                "proj-456",
                "--project-slug",
                "client-portal",
                "--artifact-root",
                str(artifact_root),
                "--apply",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "init"
    assert payload["status"] == "success"
    assert payload["mutation"] == "mutating"
    assert payload["requires_approval"] is False
    assert payload["data"]["changed"] is True
    assert payload["data"]["project"]["project_id"] == "proj-456"
    assert payload["data"]["project"]["project_slug"] == "client-portal"
    assert payload["data"]["project"]["artifact_root"] == str(artifact_root.resolve())
    assert payload["data"]["project"]["artifact_dir"] == str(
        (artifact_root / "client-portal").resolve()
    )


def test_init_reuses_existing_project_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "existing-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--project-id", "proj-original", "--apply"]) == 0
    capsys.readouterr()

    assert main(["init", "--project-id", "proj-new", "--apply", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "init"
    assert payload["status"] == "success"
    assert payload["data"]["changed"] is False
    assert payload["data"]["project"]["project_id"] == "proj-original"


def test_validate_reports_invalid_project_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "broken-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    project_file = project_root / "project.json"
    project_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "proj-999",
                "project_slug": "broken-app",
                "project_root": str(project_root.resolve()),
                "artifact_root": str((project_root / ".wood" / "artifacts").resolve()),
                "artifact_dir": str((project_root / ".wood" / "artifacts" / "other-app").resolve()),
                "metadata_dir": str((project_root / ".wood").resolve()),
            }
        ),
        encoding="utf-8",
    )

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert "artifact_dir must end with project_slug." in payload["summary"]


def test_validate_json_success_reports_valid_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "valid-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    assert main(["validate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "validate"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["requires_approval"] is False
    assert payload["data"]["valid"] is True
    assert payload["data"]["project"]["project_slug"] == "valid-app"


def test_show_json_reports_generated_slug_and_custom_artifact_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "My Demo App"
    artifact_root = tmp_path / "shared-artifacts" / "projects"
    project_root.mkdir(parents=True)

    assert (
        main(
            [
                "--project-root",
                str(project_root),
                "init",
                "--artifact-root",
                str(artifact_root),
                "--apply",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["--project-root", str(project_root), "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "show"
    assert payload["status"] == "success"
    assert payload["data"]["project"]["project_slug"] == "my-demo-app"
    assert payload["data"]["project"]["artifact_root"] == str(artifact_root.resolve())
    assert payload["data"]["project"]["artifact_dir"] == str(
        (artifact_root / "my-demo-app").resolve()
    )


def test_init_reports_invalid_slug_in_json_error_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "slug-check"
    project_root.mkdir()

    code = main(
        [
            "--project-root",
            str(project_root),
            "init",
            "--project-slug",
            "Bad Slug",
            "--json",
        ]
    )

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "init"
    assert payload["status"] == "error"
    assert payload["mutation"] == "mutating"
    assert (
        "project_slug must use lowercase letters, numbers, and hyphens only." in payload["summary"]
    )

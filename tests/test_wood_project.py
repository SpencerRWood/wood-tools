from __future__ import annotations

import json
from pathlib import Path

import pytest

import wood_project.core as project_core
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
    assert payload["data"]["checked_paths"]["metadata_dir"] == str(
        (project_root / ".wood").resolve()
    )
    assert payload["data"]["mount_checks"]["project_root"] == {
        "path": str(project_root.resolve()),
        "readable": True,
        "writable": True,
    }
    assert payload["data"]["linked_repositories"] == []
    assert payload["data"]["project"]["project_slug"] == "valid-app"


def test_validate_fails_when_artifact_dir_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "missing-artifact-dir"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    artifact_dir = project_root / ".wood" / "artifacts" / "missing-artifact-dir"
    artifact_dir.rmdir()

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert f"artifact_dir does not exist: {artifact_dir.resolve()}" == payload["summary"]


def test_validate_fails_for_conflicting_metadata_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "conflicting-paths"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    metadata_dir = project_root / ".wood"
    metadata_dir.mkdir()
    project_file = project_root / "project.json"
    project_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "proj-237",
                "project_slug": "conflicting-paths",
                "project_root": str(project_root.resolve()),
                "artifact_root": str(metadata_dir.resolve()),
                "artifact_dir": str((metadata_dir / "conflicting-paths").resolve()),
                "metadata_dir": str(metadata_dir.resolve()),
            }
        ),
        encoding="utf-8",
    )
    (metadata_dir / "conflicting-paths").mkdir()

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert "artifact_root conflicts with metadata_dir" in payload["summary"]


def test_validate_checks_linked_repositories_when_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "linked-repos-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    linked_repo = tmp_path / "shared-lib"
    linked_repo.mkdir()
    project_file = project_root / "project.json"
    document = json.loads(project_file.read_text(encoding="utf-8"))
    document["linked_repositories"] = {"shared-lib": str(linked_repo.resolve())}
    project_file.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

    assert main(["validate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["linked_repositories"] == [
        {
            "name": "shared-lib",
            "path": str(linked_repo.resolve()),
            "access": {"readable": True, "writable": True},
        }
    ]


def test_validate_fails_when_linked_repository_path_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "broken-linked-repo"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    linked_repo = tmp_path / "missing-linked-repo"
    project_file = project_root / "project.json"
    document = json.loads(project_file.read_text(encoding="utf-8"))
    document["linked_repositories"] = [{"name": "missing-lib", "path": str(linked_repo.resolve())}]
    project_file.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert (
        f"linked_repositories['missing-lib'] does not exist: {linked_repo.resolve()}"
        == payload["summary"]
    )


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


def test_link_repo_apply_persists_repository_metadata_and_validate_reports_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "repo-linked-app"
    project_root.mkdir()
    linked_repo = tmp_path / "shared-lib"
    linked_repo.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "link",
                "repo",
                str(linked_repo),
                "--role",
                "library",
                "--apply",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "link-repo"
    assert payload["status"] == "success"
    assert payload["data"]["changed"] is True
    assert payload["data"]["repository"] == {
        "name": "shared-lib",
        "path": str(linked_repo.resolve()),
        "role": "library",
    }

    project_file = project_root / "project.json"
    document = json.loads(project_file.read_text(encoding="utf-8"))
    assert document["linked_repositories"] == [
        {
            "name": "shared-lib",
            "path": str(linked_repo.resolve()),
            "role": "library",
        }
    ]

    assert main(["validate", "--json"]) == 0
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["data"]["linked_repositories"] == [
        {
            "name": "shared-lib",
            "path": str(linked_repo.resolve()),
            "role": "library",
            "access": {"readable": True, "writable": True},
        }
    ]


def test_link_repo_without_apply_is_approval_gated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "link-preview-app"
    project_root.mkdir()
    linked_repo = tmp_path / "preview-repo"
    linked_repo.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    assert main(["link", "repo", str(linked_repo), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "link-repo"
    assert payload["status"] == "blocked"
    assert payload["requires_approval"] is True
    assert payload["data"]["changed"] is False

    project_file = project_root / "project.json"
    document = json.loads(project_file.read_text(encoding="utf-8"))
    assert "linked_repositories" not in document


def test_link_repo_rejects_missing_repository_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "missing-link-app"
    project_root.mkdir()
    missing_repo = tmp_path / "missing-repo"
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    code = main(["link", "repo", str(missing_repo), "--apply", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "link-repo"
    assert payload["status"] == "error"
    assert payload["mutation"] == "mutating"
    assert payload["summary"] == f"Repository path does not exist: {missing_repo.resolve()}"


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


def test_validate_reports_unavailable_mount_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "mount-check-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    project_file = project_root / "project.json"
    document = json.loads(project_file.read_text(encoding="utf-8"))
    missing_mount_root = tmp_path / "mnt" / "nas-share"
    document["artifact_root"] = str((missing_mount_root / "artifacts").resolve())
    document["artifact_dir"] = str((missing_mount_root / "artifacts" / "mount-check-app").resolve())
    project_file.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert "artifact_root is unavailable:" in payload["summary"]
    assert "nearest existing parent:" in payload["summary"]


def test_validate_fails_when_required_path_is_not_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "read-access-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--apply"]) == 0
    capsys.readouterr()

    artifact_dir = (project_root / ".wood" / "artifacts" / "read-access-app").resolve()
    original_access = project_core.os.access

    def fake_access(path: object, mode: int) -> bool:
        if Path(path) == artifact_dir and mode == (project_core.os.R_OK | project_core.os.X_OK):
            return False
        return original_access(path, mode)

    monkeypatch.setattr(project_core.os, "access", fake_access)

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert payload["summary"] == f"artifact_dir is not readable: {artifact_dir}"


def test_init_apply_fails_when_artifact_mount_parent_is_not_writable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "write-access-app"
    project_root.mkdir()
    artifact_root = tmp_path / "shared" / "artifacts"
    blocked_parent = (tmp_path / "shared").resolve()
    blocked_parent.mkdir()
    original_access = project_core.os.access

    def fake_access(path: object, mode: int) -> bool:
        if Path(path) == blocked_parent and mode == (project_core.os.W_OK | project_core.os.X_OK):
            return False
        return original_access(path, mode)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(project_core.os, "access", fake_access)
        code = main(
            [
                "--project-root",
                str(project_root),
                "init",
                "--artifact-root",
                str(artifact_root),
                "--apply",
                "--json",
            ]
        )

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "init"
    assert payload["status"] == "error"
    assert payload["summary"] == f"artifact_root is not writable: {blocked_parent}"

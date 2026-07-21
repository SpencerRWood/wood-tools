from __future__ import annotations

import json
from pathlib import Path

import pytest

import wood_project.core as project_core
from wood_project.cli import main

WOOD_HOME_DIRS = {
    "packs/templates",
    "packs/references",
    "packs/agents",
    "tools",
    "scripts",
    "cache",
    "state",
}


def test_init_show_validate_success_path_creates_global_wood_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "demo-app"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("WOOD_HOME", str(wood_home))

    assert main(["init", "--project-id", "proj-123", "--apply"]) == 0

    project_file = project_root / "project.json"
    assert project_file.exists()
    assert not (project_root / ".wood").exists()
    assert (wood_home / "config.toml").read_text(encoding="utf-8") == "version = 1\n"
    for directory in WOOD_HOME_DIRS:
        assert (wood_home / directory).is_dir()

    document = json.loads(project_file.read_text(encoding="utf-8"))
    assert document == {
        "schema_version": 1,
        "project_id": "proj-123",
        "project_slug": "demo-app",
        "project_root": str(project_root.resolve()),
        "wood_config_file": str((wood_home / "config.toml").resolve()),
        "wood_home": str(wood_home.resolve()),
    }

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
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "init"
    assert payload["status"] == "blocked"
    assert payload["requires_approval"] is True
    assert payload["data"]["changed"] is False
    assert Path(payload["data"]["path"]).name == "project.json"
    assert payload["data"]["project"]["wood_home"] == str(wood_home.resolve())
    assert not (project_root / "project.json").exists()
    assert not wood_home.exists()


def test_init_with_apply_json_reports_success_and_custom_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "Client Portal"
    wood_home = tmp_path / "global-wood"
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
                "--wood-home",
                str(wood_home),
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
    assert payload["data"]["project"]["wood_home"] == str(wood_home.resolve())
    assert payload["data"]["project"]["wood_config_file"] == str(
        (wood_home / "config.toml").resolve()
    )


def test_init_reuses_existing_project_document_and_preserves_wood_home_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "existing-app"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert (
        main(["init", "--wood-home", str(wood_home), "--project-id", "proj-original", "--apply"])
        == 0
    )
    (wood_home / "tools" / "custom.txt").write_text("keep me\n", encoding="utf-8")
    (wood_home / "config.toml").write_text("version = 1\ncustom = true\n", encoding="utf-8")
    capsys.readouterr()

    assert (
        main(
            [
                "init",
                "--wood-home",
                str(wood_home),
                "--project-id",
                "proj-new",
                "--apply",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "init"
    assert payload["status"] == "success"
    assert payload["data"]["changed"] is False
    assert payload["data"]["project"]["project_id"] == "proj-original"
    assert (wood_home / "tools" / "custom.txt").read_text(encoding="utf-8") == "keep me\n"
    assert (wood_home / "config.toml").read_text(encoding="utf-8") == "version = 1\ncustom = true\n"


def test_default_wood_home_uses_home_and_wood_home_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("WOOD_HOME", raising=False)

    assert project_core.resolve_wood_home() == (home / ".wood").resolve()

    override = tmp_path / "override"
    monkeypatch.setenv("WOOD_HOME", str(override))
    assert project_core.resolve_wood_home() == override.resolve()


def test_empty_wood_home_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOOD_HOME", " ")

    with pytest.raises(project_core.ProjectError, match="WOOD_HOME must be a non-empty path"):
        project_core.resolve_wood_home()


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
                "artifact_dir": str(
                    (project_root / ".wood" / "artifacts" / "broken-app").resolve()
                ),
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
    assert "artifact_root, artifact_dir, metadata_dir are obsolete" in payload["summary"]


def test_validate_json_success_reports_valid_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "valid-app"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    assert main(["validate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "validate"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["requires_approval"] is False
    assert payload["data"]["valid"] is True
    assert payload["data"]["checked_paths"]["wood_home"] == str(wood_home.resolve())
    assert (
        set(
            str(Path(path).relative_to(wood_home.resolve()))
            for path in payload["data"]["checked_paths"]["wood_home_dirs"]
        )
        == WOOD_HOME_DIRS
    )
    assert payload["data"]["mount_checks"]["project_root"] == {
        "path": str(project_root.resolve()),
        "readable": True,
        "writable": True,
    }
    assert payload["data"]["linked_repositories"] == []
    assert payload["data"]["project"]["project_slug"] == "valid-app"


def test_validate_fails_when_wood_home_dir_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "missing-wood-home-dir"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    missing_dir = wood_home / "packs" / "agents"
    missing_dir.rmdir()

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert (
        f"wood_home_dirs.packs/agents does not exist: {missing_dir.resolve()}" == payload["summary"]
    )


def test_init_rejects_project_local_wood_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "local-wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    code = main(["init", "--wood-home", str(project_root / ".wood"), "--apply", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "init"
    assert payload["status"] == "error"
    assert "must not be the project-local .wood directory" in payload["summary"]
    assert not (project_root / ".wood").exists()


def test_validate_checks_linked_repositories_when_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "linked-repos-app"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
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
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
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


def test_show_json_reports_generated_slug_and_custom_wood_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "My Demo App"
    wood_home = tmp_path / "shared-wood-home"
    project_root.mkdir(parents=True)

    assert (
        main(
            [
                "--project-root",
                str(project_root),
                "init",
                "--wood-home",
                str(wood_home),
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
    assert payload["data"]["project"]["wood_home"] == str(wood_home.resolve())
    assert payload["data"]["project"]["wood_config_file"] == str(
        (wood_home / "config.toml").resolve()
    )


def test_link_repo_apply_persists_repository_metadata_and_validate_reports_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "repo-linked-app"
    wood_home = tmp_path / "wood-home"
    linked_repo = tmp_path / "shared-lib"
    project_root.mkdir()
    linked_repo.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
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
    wood_home = tmp_path / "wood-home"
    linked_repo = tmp_path / "preview-repo"
    project_root.mkdir()
    linked_repo.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
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
    wood_home = tmp_path / "wood-home"
    missing_repo = tmp_path / "missing-repo"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
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


def test_validate_reports_unavailable_wood_home_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "mount-check-app"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    project_file = project_root / "project.json"
    document = json.loads(project_file.read_text(encoding="utf-8"))
    missing_mount_root = tmp_path / "mnt" / "nas-share"
    document["wood_home"] = str((missing_mount_root / "wood-home").resolve())
    document["wood_config_file"] = str((missing_mount_root / "wood-home" / "config.toml").resolve())
    project_file.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert "wood_home is unavailable:" in payload["summary"]
    assert "nearest existing parent:" in payload["summary"]


def test_validate_fails_when_required_path_is_not_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "read-access-app"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    tools_dir = (wood_home / "tools").resolve()
    original_access = project_core.os.access

    def fake_access(path: object, mode: int) -> bool:
        if Path(path) == tools_dir and mode == (project_core.os.R_OK | project_core.os.X_OK):
            return False
        return original_access(path, mode)

    monkeypatch.setattr(project_core.os, "access", fake_access)

    code = main(["validate", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "validate"
    assert payload["status"] == "error"
    assert payload["summary"] == f"wood_home_dirs.tools is not readable: {tools_dir}"


def test_init_apply_fails_when_wood_home_parent_is_not_writable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "write-access-app"
    project_root.mkdir()
    wood_home = tmp_path / "shared" / "wood-home"
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
                "--wood-home",
                str(wood_home),
                "--apply",
                "--json",
            ]
        )

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "init"
    assert payload["status"] == "error"
    assert payload["summary"] == f"wood_home is not writable: {blocked_parent}"


def test_init_rejects_obsolete_artifact_root_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_root = tmp_path / "obsolete-artifacts"
    project_root.mkdir()

    code = main(
        [
            "--project-root",
            str(project_root),
            "init",
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--apply",
            "--json",
        ]
    )

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "init"
    assert payload["status"] == "error"
    assert "--artifact-root is obsolete" in payload["summary"]

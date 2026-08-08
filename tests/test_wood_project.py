from __future__ import annotations

import json
from pathlib import Path

import pytest

import wood_project.core as project_core
import wood_templates
from wood_project.cli import main
from wood_project.template_cli import main as template_main

WOOD_HOME_DIRS = {
    "packs/templates",
    "packs/references",
    "packs/agents",
    "tools",
    "scripts",
    "cache",
    "state",
}


def _write_resource_manifest(
    source_dir: Path,
    *,
    kind: str = "script",
    name: str = "demo-helper",
    version: str = "1.0.0",
    digest: str | None = None,
    helper_contract: dict[str, object] | None = None,
    template_pack: dict[str, object] | None = None,
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": kind,
        "name": name,
        "version": version,
        "digest": digest or project_core.compute_resource_digest(source_dir),
        "compatibility": {"wood_tools": ">=0.1.1"},
    }
    if helper_contract is None and kind in {"tool", "script"}:
        helper_contract = {
            "deterministic": True,
            "input": "JSON object on stdin",
            "output": "JSON object on stdout",
            "errors": "Non-zero exit with JSON error envelope",
        }
    if helper_contract is not None:
        manifest["helper_contract"] = helper_contract
    if template_pack is not None:
        manifest["template_pack"] = template_pack
    (source_dir / "wood-resource.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _write_template_pack_manifest(
    source_dir: Path,
    *,
    name: str = "service-app",
    version: str = "1.0.0",
    digest: str | None = None,
    variables: dict[str, object] | None = None,
    operations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    template_pack = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "implementation_stack": "Python 3.11+, pytest, ruff",
        "variables": variables
        or {
            "project-name": {
                "type": "string",
                "required": True,
                "description": "Display name for the generated project",
            }
        },
        "operations": operations
        or [
            {
                "type": "render",
                "template": "templates/README.md.tmpl",
                "output": "README.md",
                "overwrite": "safe",
                "safe_overwrite": {"strategy": "if-unchanged"},
            }
        ],
        "expected_tree": ["README.md"],
        "features": {},
        "validation": [{"rule": "project-name", "message": "Project name is required."}],
    }
    return _write_resource_manifest(
        source_dir,
        kind="template",
        name=name,
        version=version,
        digest=digest,
        template_pack=template_pack,
    )


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
    assert (
        "Unexpected project metadata fields: artifact_dir, artifact_root, metadata_dir"
        in payload["summary"]
    )


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


def test_resource_install_preview_apply_reinstall_inspect_and_path_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "resource-app"
    wood_home = tmp_path / "wood-home"
    source_dir = tmp_path / "source-helper"
    project_root.mkdir()
    source_dir.mkdir()
    monkeypatch.chdir(project_root)

    (source_dir / "run.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = _write_resource_manifest(source_dir)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    assert main(["resource", "install", str(source_dir), "--json"]) == 0
    preview_payload = json.loads(capsys.readouterr().out)
    assert preview_payload["command"] == "resource-install"
    assert preview_payload["status"] == "blocked"
    assert preview_payload["requires_approval"] is True
    assert preview_payload["data"]["changed"] is False
    assert not (wood_home / "scripts" / "demo-helper" / "1.0.0").exists()

    assert main(["resource", "install", str(source_dir), "--apply", "--json"]) == 0
    install_payload = json.loads(capsys.readouterr().out)
    assert install_payload["command"] == "resource-install"
    assert install_payload["status"] == "success"
    assert install_payload["data"]["changed"] is True
    install_dir = wood_home / "scripts" / "demo-helper" / "1.0.0"
    assert (install_dir / "run.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert install_payload["data"]["resource"] == {
        "kind": "script",
        "name": "demo-helper",
        "version": "1.0.0",
        "digest": manifest["digest"],
        "compatibility": {"wood_tools": ">=0.1.1"},
        "installed_location": str(install_dir.resolve()),
        "helper_contract": {
            "deterministic": True,
            "input": "JSON object on stdin",
            "output": "JSON object on stdout",
            "errors": "Non-zero exit with JSON error envelope",
        },
    }

    assert main(["resource", "install", str(source_dir), "--apply", "--json"]) == 0
    reinstall_payload = json.loads(capsys.readouterr().out)
    assert reinstall_payload["data"]["changed"] is False

    assert (
        main(
            [
                "resource",
                "inspect",
                "script",
                "demo-helper",
                "--version",
                "1.0.0",
                "--json",
            ]
        )
        == 0
    )
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["command"] == "resource-inspect"
    assert inspect_payload["data"]["resource"]["digest"] == manifest["digest"]

    assert (
        main(
            [
                "resource",
                "path",
                "script",
                "demo-helper",
                "--version",
                "1.0.0",
                "--relative-path",
                "run.py",
                "--json",
            ]
        )
        == 0
    )
    path_payload = json.loads(capsys.readouterr().out)
    assert path_payload["command"] == "resource-path"
    assert path_payload["data"]["path"] == str((install_dir / "run.py").resolve())


def test_resource_install_rejects_digest_mismatch_before_activation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "digest-app"
    wood_home = tmp_path / "wood-home"
    source_dir = tmp_path / "bad-resource"
    project_root.mkdir()
    source_dir.mkdir()
    monkeypatch.chdir(project_root)

    (source_dir / "run.py").write_text("print('changed')\n", encoding="utf-8")
    _write_resource_manifest(source_dir, digest=f"sha256:{'0' * 64}")

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    code = main(["resource", "install", str(source_dir), "--apply", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "resource-install"
    assert payload["status"] == "error"
    assert "Resource digest mismatch" in payload["summary"]
    assert not (wood_home / "scripts" / "demo-helper" / "1.0.0").exists()


def test_resource_install_rejects_helper_without_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "contract-app"
    wood_home = tmp_path / "wood-home"
    source_dir = tmp_path / "source-tool"
    project_root.mkdir()
    source_dir.mkdir()
    monkeypatch.chdir(project_root)

    (source_dir / "helper.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _write_resource_manifest(source_dir, kind="tool", helper_contract={})

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    code = main(["resource", "install", str(source_dir), "--apply", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "resource-install"
    assert payload["status"] == "error"
    assert payload["summary"] == "helper_contract.deterministic must be true."


def test_resource_install_protects_existing_conflicting_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "conflict-app"
    wood_home = tmp_path / "wood-home"
    source_dir = tmp_path / "source-reference"
    project_root.mkdir()
    source_dir.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    conflict_dir = wood_home / "packs" / "references" / "demo-helper" / "1.0.0"
    conflict_dir.mkdir(parents=True)
    (conflict_dir / "notes.md").write_text("existing\n", encoding="utf-8")

    (source_dir / "notes.md").write_text("new\n", encoding="utf-8")
    _write_resource_manifest(source_dir, kind="reference")

    code = main(["resource", "install", str(source_dir), "--apply", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "resource-install"
    assert payload["status"] == "error"
    assert "Installed resource metadata not found" in payload["summary"]
    assert (conflict_dir / "notes.md").read_text(encoding="utf-8") == "existing\n"


def test_agent_resource_installs_as_distinct_pack_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "agent-app"
    wood_home = tmp_path / "wood-home"
    source_dir = tmp_path / "wood-agents-pack"
    project_root.mkdir()
    source_dir.mkdir()
    monkeypatch.chdir(project_root)

    (source_dir / "AGENTS.md").write_text("# Agent pack\n", encoding="utf-8")
    manifest = _write_resource_manifest(source_dir, kind="agent", name="wood-agents")

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    assert main(["resource", "install", str(source_dir), "--apply", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    install_dir = wood_home / "packs" / "agents" / "wood-agents" / "1.0.0"
    assert payload["data"]["resource"] == {
        "kind": "agent",
        "name": "wood-agents",
        "version": "1.0.0",
        "digest": manifest["digest"],
        "compatibility": {"wood_tools": ">=0.1.1"},
        "installed_location": str(install_dir.resolve()),
    }
    assert (install_dir / ".wood-resource-install.json").exists()


def test_template_pack_explicit_source_show_reports_contract_without_install_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "template-source-app"
    wood_home = tmp_path / "wood-home"
    source_dir = tmp_path / "service-app-pack"
    project_root.mkdir()
    (source_dir / "templates").mkdir(parents=True)
    monkeypatch.chdir(project_root)

    (source_dir / "templates" / "README.md.tmpl").write_text(
        "# {{project-name}}\n",
        encoding="utf-8",
    )
    manifest = _write_template_pack_manifest(source_dir)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    assert template_main(["show", "service-app", "--source-dir", str(source_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    template_pack = payload["data"]["template_pack"]
    assert payload["command"] == "show"
    assert template_pack["source"] == "explicit"
    assert template_pack["version"] == "1.0.0"
    assert template_pack["digest"] == manifest["digest"]
    assert template_pack["planned_outputs"] == ["README.md"]
    assert template_pack["variables"]["project-name"]["required"] is True
    assert "installed_location" not in json.dumps(payload)


def test_template_pack_installed_list_and_project_lock_precedence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "template-lock-app"
    wood_home = tmp_path / "wood-home"
    source_v1 = tmp_path / "service-app-v1"
    source_v2 = tmp_path / "service-app-v2"
    project_root.mkdir()
    for source_dir in (source_v1, source_v2):
        (source_dir / "templates").mkdir(parents=True)
        (source_dir / "templates" / "README.md.tmpl").write_text(
            f"# {source_dir.name}\n",
            encoding="utf-8",
        )
    monkeypatch.chdir(project_root)

    manifest_v1 = _write_template_pack_manifest(source_v1, version="1.0.0")
    _write_template_pack_manifest(source_v2, version="2.0.0")

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()
    assert main(["resource", "install", str(source_v1), "--apply", "--json"]) == 0
    capsys.readouterr()
    assert main(["resource", "install", str(source_v2), "--apply", "--json"]) == 0
    capsys.readouterr()

    assert template_main(["list", "--json"]) == 0
    installed_payload = json.loads(capsys.readouterr().out)
    assert installed_payload["data"]["precedence"] == ["locked", "installed", "built-in"]
    assert installed_payload["data"]["template_packs"][0]["source"] == "installed"
    assert installed_payload["data"]["template_packs"][0]["version"] == "2.0.0"

    project_file = project_root / "project.json"
    document = json.loads(project_file.read_text(encoding="utf-8"))
    document["template_packs"] = [
        {
            "name": "service-app",
            "version": "1.0.0",
            "digest": manifest_v1["digest"],
            "source": "user-pack",
        }
    ]
    project_file.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

    assert template_main(["show", "service-app", "--json"]) == 0
    locked_payload = json.loads(capsys.readouterr().out)
    template_pack = locked_payload["data"]["template_pack"]
    assert template_pack["source"] == "locked"
    assert template_pack["source_detail"] == "template_packs[service-app]"
    assert template_pack["version"] == "1.0.0"
    persisted = json.loads(project_file.read_text(encoding="utf-8"))
    assert str(wood_home.resolve()) not in json.dumps(persisted["template_packs"])


def test_template_pack_errors_for_missing_and_invalid_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "template-error-app"
    wood_home = tmp_path / "wood-home"
    source_dir = tmp_path / "bad-template"
    project_root.mkdir()
    source_dir.mkdir()
    monkeypatch.chdir(project_root)

    (source_dir / "README.md.tmpl").write_text("# hello\n", encoding="utf-8")
    _write_resource_manifest(source_dir, kind="template", name="bad-template")

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    code = template_main(["show", "missing-pack", "--json"])
    missing_payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert missing_payload["command"] == "show"
    assert missing_payload["status"] == "error"
    assert missing_payload["summary"] == "Template pack not found: missing-pack"

    code = template_main(["show", "bad-template", "--source-dir", str(source_dir), "--json"])
    invalid_payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert invalid_payload["command"] == "show"
    assert invalid_payload["status"] == "error"
    assert invalid_payload["summary"] == "template resources must define template_pack."


def test_builtin_first_party_template_pack_names_features_and_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "builtin-template-app"
    wood_home = tmp_path / "wood-home"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert main(["init", "--wood-home", str(wood_home), "--apply"]) == 0
    capsys.readouterr()

    assert template_main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    template_packs = payload["data"]["template_packs"]
    assert [template["name"] for template in template_packs] == [
        "python-api-service",
        "python-cli",
        "python-library",
        "python-web-app",
        "software-planning",
    ]
    for template_pack in template_packs:
        assert template_pack["source"] == "built-in"
        assert template_pack["version"] == "1.0.0"
        assert template_pack["digest"].startswith("sha256:")
        assert template_pack["implementation_stack"]
        assert template_pack["expected_tree"] == sorted(template_pack["expected_tree"])
        assert template_pack["features"] == {}
        assert "installed_location" not in json.dumps(template_pack)

    builtin_root = Path(wood_templates.__file__).parent / "builtin_template_packs"
    web_readme = (
        builtin_root / "python-web-app" / "1.0.0" / "templates" / "README.md.tmpl"
    ).read_text(encoding="utf-8")
    planning_story = (
        builtin_root / "software-planning" / "1.0.0" / "templates" / "story-description.md.tmpl"
    ).read_text(encoding="utf-8")
    assert "Architecture: full-stack Python web application." in web_readme
    assert "FastAPI backend with SQLAlchemy and Alembic" in web_readme
    assert "React, Vite, TypeScript, Tailwind" in web_readme
    assert "## Acceptance Criteria" in planning_story


def test_wood_template_renders_python_cli_in_current_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "my-tool"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert template_main(["generate", "python-cli", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "generate"
    assert payload["mutation"] == "mutating"
    assert payload["data"]["changed"] is True
    assert set(payload["data"]["template"]) == {"digest", "name", "source", "version"}
    assert payload["data"]["template"]["name"] == "python-cli"
    assert payload["data"]["template"]["source"] == "built-in"
    assert payload["data"]["template"]["version"] == "1.0.0"
    assert payload["data"]["template"]["digest"].startswith("sha256:")
    assert "template_pack" not in payload["data"]
    assert "operations" not in json.dumps(payload["data"])
    assert "source_detail" not in json.dumps(payload["data"])
    assert payload["data"]["variables"] == {
        "package-module": "my_tool",
        "package-name": "my-tool",
        "project-name": "my-tool",
    }
    assert [file["path"] for file in payload["data"]["files"]] == [
        ".pre-commit-config.yaml",
        "README.md",
        "pyproject.toml",
        "src/my_tool/__init__.py",
        "src/my_tool/__main__.py",
        "src/my_tool/cli.py",
        "tests/test_cli.py",
    ]
    assert "Architecture: Python command-line application." in (
        project_root / "README.md"
    ).read_text(encoding="utf-8")
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "my-tool"' in pyproject
    assert 'my-tool = "my_tool.cli:main"' in pyproject
    assert 'where = ["src"]' in pyproject
    assert 'pythonpath = ["src"]' in pyproject
    assert (project_root / "src" / "my_tool" / "cli.py").exists()
    assert "raise SystemExit(main())" in (
        project_root / "src" / "my_tool" / "__main__.py"
    ).read_text(encoding="utf-8")
    assert "ruff-pre-commit" in (project_root / ".pre-commit-config.yaml").read_text(
        encoding="utf-8"
    )
    assert "{{" not in (project_root / "tests" / "test_cli.py").read_text(encoding="utf-8")
    assert ".wood" not in json.dumps(payload)


def test_wood_template_renders_opinionated_python_api_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "orders-api"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert template_main(["generate", "python-api-service", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "generate"
    assert payload["data"]["template"]["name"] == "python-api-service"
    assert [file["path"] for file in payload["data"]["files"]] == [
        ".pre-commit-config.yaml",
        "README.md",
        "alembic.ini",
        "alembic/env.py",
        "alembic/script.py.mako",
        "pyproject.toml",
        "src/orders_api/__init__.py",
        "src/orders_api/config.py",
        "src/orders_api/db.py",
        "src/orders_api/main.py",
        "tests/test_health.py",
    ]
    assert "Pydantic Settings, SQLAlchemy, Alembic, SQLite" in (
        project_root / "README.md"
    ).read_text(encoding="utf-8")
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"alembic>=1.13"' in pyproject
    assert '"pydantic-settings>=2.2"' in pyproject
    assert '"sqlalchemy>=2.0"' in pyproject
    assert '"httpx>=0.28.0"' in pyproject
    assert '"pytest-asyncio>=1.0.0"' in pyproject
    assert 'where = ["src"]' in pyproject
    assert 'pythonpath = ["src"]' in pyproject
    assert "from alembic import context" in (project_root / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )
    assert "sqlite:///./app.db" in (project_root / "alembic.ini").read_text(encoding="utf-8")
    assert "FastAPI(title=settings.app_name)" in (
        project_root / "src" / "orders_api" / "main.py"
    ).read_text(encoding="utf-8")
    assert '@app.get("/api/health")' in (project_root / "src" / "orders_api" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "TestClient(app)" in (project_root / "tests" / "test_health.py").read_text(
        encoding="utf-8"
    )
    assert "ruff-pre-commit" in (project_root / ".pre-commit-config.yaml").read_text(
        encoding="utf-8"
    )
    assert "{{" not in "\n".join(
        [
            (project_root / "src" / "orders_api" / "main.py").read_text(encoding="utf-8"),
            (project_root / "src" / "orders_api" / "config.py").read_text(encoding="utf-8"),
            (project_root / "alembic" / "env.py").read_text(encoding="utf-8"),
        ]
    )


def test_wood_template_renders_python_library_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "shared-utils"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert template_main(["generate", "python-library", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["template"]["name"] == "python-library"
    assert [file["path"] for file in payload["data"]["files"]] == [
        ".pre-commit-config.yaml",
        "README.md",
        "pyproject.toml",
        "src/shared_utils/__init__.py",
        "tests/test_package.py",
    ]
    assert "uv, pyproject packaging, pytest, ruff, and pre-commit" in (
        project_root / "README.md"
    ).read_text(encoding="utf-8")
    assert 'where = ["src"]' in (project_root / "pyproject.toml").read_text(encoding="utf-8")
    assert "ruff-pre-commit" in (project_root / ".pre-commit-config.yaml").read_text(
        encoding="utf-8"
    )
    assert "{{" not in (project_root / "tests" / "test_package.py").read_text(encoding="utf-8")


def test_wood_template_renders_opinionated_python_web_app(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "client-portal"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    assert template_main(["generate", "python-web-app", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["command"] == "generate"
    assert payload["data"]["template"]["name"] == "python-web-app"
    assert [file["path"] for file in payload["data"]["files"]] == [
        "README.md",
        "backend/alembic.ini",
        "backend/alembic/env.py",
        "backend/alembic/script.py.mako",
        "backend/pyproject.toml",
        "backend/src/client_portal/__init__.py",
        "backend/src/client_portal/config.py",
        "backend/src/client_portal/db.py",
        "backend/src/client_portal/main.py",
        "backend/tests/test_health.py",
        "frontend/eslint.config.js",
        "frontend/index.html",
        "frontend/package.json",
        "frontend/src/App.tsx",
        "frontend/src/main.tsx",
        "frontend/src/styles.css",
        "frontend/tsconfig.json",
        "frontend/vite.config.ts",
    ]
    assert (
        "React, Vite, TypeScript, Tailwind, Radix UI, lucide-react, and react-router-dom frontend"
        in (project_root / "README.md").read_text(encoding="utf-8")
    )
    frontend_package = (project_root / "frontend" / "package.json").read_text(encoding="utf-8")
    assert '"react-router-dom":' in frontend_package
    assert '"tailwindcss":' in frontend_package
    assert '"@radix-ui/react-tabs":' in frontend_package
    assert '"lucide-react":' in frontend_package
    assert '"lint": "eslint ."' in frontend_package
    assert '"globals":' in frontend_package
    assert "from alembic import context" in (
        project_root / "backend" / "alembic" / "env.py"
    ).read_text(encoding="utf-8")
    assert "TestClient(app)" in (project_root / "backend" / "tests" / "test_health.py").read_text(
        encoding="utf-8"
    )
    assert 'pythonpath = ["src"]' in (project_root / "backend" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "FastAPI(title=settings.app_name)" in (
        project_root / "backend" / "src" / "client_portal" / "main.py"
    ).read_text(encoding="utf-8")
    assert '"/api": "http://localhost:8000"' in (
        project_root / "frontend" / "vite.config.ts"
    ).read_text(encoding="utf-8")
    assert '@import "tailwindcss";' in (project_root / "frontend" / "src" / "styles.css").read_text(
        encoding="utf-8"
    )
    assert "<BrowserRouter>" in (project_root / "frontend" / "src" / "main.tsx").read_text(
        encoding="utf-8"
    )
    assert 'fetch("/api/health")' in (project_root / "frontend" / "src" / "App.tsx").read_text(
        encoding="utf-8"
    )
    assert "{{" not in "\n".join(
        [
            (project_root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"),
            (project_root / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8"),
            (project_root / "frontend" / "vite.config.ts").read_text(encoding="utf-8"),
        ]
    )


def test_wood_template_fails_before_mutation_when_output_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "conflict-app"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    (project_root / "README.md").write_text("keep\n", encoding="utf-8")

    code = template_main(["generate", "python-cli", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["command"] == "generate"
    assert payload["mutation"] == "mutating"
    assert payload["summary"] == "Template output already exists: README.md"
    assert (project_root / "README.md").read_text(encoding="utf-8") == "keep\n"
    assert not (project_root / "pyproject.toml").exists()


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

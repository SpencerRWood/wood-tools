from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1] / "scripts" / "release_loop"


def load_module(name: str, relative_path: str):
    module_path = MODULE_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUMP_VERSION = load_module("bump_version", "bump_version.py")
CREATE_TAG = load_module("create_tag", "create_tag.py")
CREATE_GITHUB_RELEASE = load_module("create_github_release", "create_github_release.py")


def test_bump_version_dry_run_json_shape(capsys, tmp_path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    exit_code = BUMP_VERSION.main(["patch", "--dry-run", "--json", "--pyproject", str(pyproject)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "ok": True,
        "dry_run": True,
        "file": str(pyproject),
        "old_version": "0.1.0",
        "new_version": "0.1.1",
        "changed": False,
        "changed_files": [],
    }
    assert pyproject.read_text(encoding="utf-8") == '[project]\nname = "demo"\nversion = "0.1.0"\n'


def test_bump_version_rejects_dynamic_version(capsys, tmp_path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\ndynamic = ["version"]\n',
        encoding="utf-8",
    )

    exit_code = BUMP_VERSION.main(["patch", "--json", "--pyproject", str(pyproject)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["error"]["code"] == "VERSION_BUMP_FAILED"


def test_create_tag_dry_run_json_shape(monkeypatch, capsys) -> None:
    monkeypatch.setattr(CREATE_TAG, "resolve_version", lambda _version, _path: "0.2.0")
    monkeypatch.setattr(
        CREATE_TAG,
        "inspect_repo_state",
        lambda: (
            Path("/tmp/repo"),
            {
                "path": "/tmp/repo",
                "current_branch": "main",
                "working_tree_clean": True,
                "modified_files": [],
                "untracked_files": [],
                "ahead": 0,
                "behind": 0,
                "head": "abcdef1",
            },
        ),
    )
    monkeypatch.setattr(CREATE_TAG, "tag_exists", lambda _root, _tag: False)

    exit_code = CREATE_TAG.main(["--version", "0.2.0", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "ok": True,
        "dry_run": True,
        "repo": {
            "path": "/tmp/repo",
            "current_branch": "main",
            "working_tree_clean": True,
            "modified_files": [],
            "untracked_files": [],
            "ahead": 0,
            "behind": 0,
            "head": "abcdef1",
        },
        "tag": {
            "name": "v0.2.0",
            "version": "0.2.0",
            "target_commit": "abcdef1",
            "would_create": True,
        },
    }


def test_create_github_release_dry_run_json_shape(capsys) -> None:
    exit_code = CREATE_GITHUB_RELEASE.main(
        ["--version", "0.2.0", "--generate-notes", "--dry-run", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "ok": True,
        "dry_run": True,
        "release": {
            "tag": "v0.2.0",
            "version": "0.2.0",
            "title": "v0.2.0",
            "generate_notes": True,
            "would_create": True,
        },
        "command": [
            "gh",
            "release",
            "create",
            "v0.2.0",
            "--title",
            "v0.2.0",
            "--generate-notes",
        ],
    }

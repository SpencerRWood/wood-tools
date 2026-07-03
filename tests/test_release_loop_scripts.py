from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1] / "scripts" / "release_loop"
REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_package_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = pyproject["project"]["version"]

    module_path = REPO_ROOT / "src" / "wood_project" / "__init__.py"
    spec = importlib.util.spec_from_file_location("wood_project_package", module_path)
    assert spec and spec.loader
    wood_project_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wood_project_module
    spec.loader.exec_module(wood_project_module)

    assert wood_project_module.__version__ == project_version


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


def test_collect_history_notes_builds_markdown_for_first_release(monkeypatch) -> None:
    monkeypatch.setattr(
        CREATE_TAG,
        "inspect_repo_state",
        lambda: (
            Path("/tmp/repo"),
            {
                "path": "/tmp/repo",
                "current_branch": "release/v1-foundation-prep",
                "working_tree_clean": True,
                "modified_files": [],
                "untracked_files": [],
                "ahead": 0,
                "behind": 0,
                "head": "abcdef1",
            },
        ),
    )

    def fake_run_git(args: list[str], *, cwd=None, check: bool = True):
        if args == ["tag", "--list", "v*"]:
            return CREATE_TAG.subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args == ["log", "--reverse", "--format=%s", "HEAD"]:
            return CREATE_TAG.subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "organize release helpers and add safe release scripts for V1 Foundation\n"
                    "implement wood-secrets checks and redacted integration readiness for op-266\n"
                ),
                stderr="",
            )
        raise AssertionError(f"Unexpected git args: {args}")

    monkeypatch.setattr(CREATE_TAG, "run_git", fake_run_git)

    notes = CREATE_GITHUB_RELEASE.collect_history_notes("0.1.1")

    assert notes == {
        "source": "branch-history",
        "history_boundary": "first-release",
        "previous_tag": None,
        "boundary_label": "repository start",
        "head_commit": "abcdef1",
        "revision_range": "HEAD",
        "commit_count": 2,
        "commits": [
            "organize release helpers and add safe release scripts for V1 Foundation",
            "implement wood-secrets checks and redacted integration readiness for op-266",
        ],
        "grouped_changes": [
            {
                "title": "Release Tooling",
                "items": [
                    "Organize release helpers and add safe release scripts for V1 Foundation"
                ],
            },
            {
                "title": "Secrets and Integrations",
                "items": ["Implement wood-secrets checks and redacted integration readiness"],
            },
        ],
        "body": (
            "## Summary\n"
            "Release `v0.1.1` is the first release and includes 2 commit(s) from the "
            "repository history.\n\n"
            "## Changes\n"
            "\n### Release Tooling\n"
            "- Organize release helpers and add safe release scripts for V1 Foundation\n"
            "\n### Secrets and Integrations\n"
            "- Implement wood-secrets checks and redacted integration readiness"
        ),
    }


def test_collect_history_notes_uses_previous_release_tag(monkeypatch) -> None:
    monkeypatch.setattr(
        CREATE_TAG,
        "inspect_repo_state",
        lambda: (
            Path("/tmp/repo"),
            {
                "path": "/tmp/repo",
                "current_branch": "release/v1-foundation-prep",
                "working_tree_clean": True,
                "modified_files": [],
                "untracked_files": [],
                "ahead": 0,
                "behind": 0,
                "head": "abcdef1",
            },
        ),
    )

    def fake_run_git(args: list[str], *, cwd=None, check: bool = True):
        if args == ["tag", "--list", "v*"]:
            return CREATE_TAG.subprocess.CompletedProcess(
                args,
                0,
                stdout="v0.1.0\nv0.1.1\n",
                stderr="",
            )
        if args == ["log", "--reverse", "--format=%s", "v0.1.0..HEAD"]:
            return CREATE_TAG.subprocess.CompletedProcess(
                args,
                0,
                stdout="add release notes helper\n",
                stderr="",
            )
        raise AssertionError(f"Unexpected git args: {args}")

    monkeypatch.setattr(CREATE_TAG, "run_git", fake_run_git)

    notes = CREATE_GITHUB_RELEASE.collect_history_notes("0.1.1")

    assert notes == {
        "source": "branch-history",
        "history_boundary": "previous-release",
        "previous_tag": "v0.1.0",
        "boundary_label": "v0.1.0",
        "head_commit": "abcdef1",
        "revision_range": "v0.1.0..HEAD",
        "commit_count": 1,
        "commits": ["add release notes helper"],
        "grouped_changes": [
            {"title": "Release Tooling", "items": ["Add release notes helper"]},
        ],
        "body": (
            "## Summary\n"
            "Release `v0.1.1` includes 1 commit(s) since the previous release `v0.1.0`.\n\n"
            "## Changes\n"
            "\n### Release Tooling\n"
            "- Add release notes helper"
        ),
    }


def test_create_github_release_dry_run_with_history_notes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        CREATE_GITHUB_RELEASE,
        "collect_history_notes",
        lambda version: {
            "source": "branch-history",
            "history_boundary": "previous-release",
            "previous_tag": "v0.1.0",
            "boundary_label": "v0.1.0",
            "head_commit": "abcdef1",
            "revision_range": "v0.1.0..HEAD",
            "commit_count": 1,
            "commits": ["add release notes helper"],
            "grouped_changes": [
                {"title": "Release Tooling", "items": ["Add release notes helper"]},
            ],
            "body": "## Summary\nRelease `v0.2.0` includes 1 commit.",
        },
    )

    exit_code = CREATE_GITHUB_RELEASE.main(
        ["--version", "0.2.0", "--notes-from-history", "--dry-run", "--json"]
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
            "generate_notes": False,
            "would_create": True,
            "notes": {
                "source": "branch-history",
                "history_boundary": "previous-release",
                "previous_tag": "v0.1.0",
                "boundary_label": "v0.1.0",
                "head_commit": "abcdef1",
                "revision_range": "v0.1.0..HEAD",
                "commit_count": 1,
                "commits": ["add release notes helper"],
                "grouped_changes": [
                    {"title": "Release Tooling", "items": ["Add release notes helper"]},
                ],
                "body": "## Summary\nRelease `v0.2.0` includes 1 commit.",
            },
        },
        "command": [
            "gh",
            "release",
            "create",
            "v0.2.0",
            "--title",
            "v0.2.0",
            "--notes-file",
            "-",
        ],
    }

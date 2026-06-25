from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load_module(name: str):
    module_path = MODULE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NEXT_STORY = load_module("openproject_next_story")
SET_STATUS = load_module("openproject_set_status")
CREATE_BRANCH = load_module("create_story_branch")


def make_story(
    story_id: int,
    subject: str,
    status: str,
    version: str,
    parent_id: int = 216,
) -> dict[str, object]:
    return {
        "id": story_id,
        "subject": subject,
        "_links": {
            "type": {"title": "Story"},
            "status": {"title": status},
            "version": {"title": version},
            "parent": {"href": f"/api/v3/work_packages/{parent_id}", "title": "Epic"},
            "self": {"href": f"https://projects.example.test/api/v3/work_packages/{story_id}"},
        },
    }


def test_parse_milestone_rank_supports_v_prefix() -> None:
    assert NEXT_STORY.parse_milestone_rank("V2 Templates") < NEXT_STORY.parse_milestone_rank(
        "V10 Later"
    )
    assert (
        NEXT_STORY.parse_milestone_rank("V2 Templates")[0]
        == NEXT_STORY.parse_milestone_rank("M2 Legacy")[0]
    )


def test_choose_candidate_reports_release_ready_for_completed_open_version() -> None:
    stories = [
        make_story(201, "Done one", "Closed", "V1 Foundation"),
        make_story(202, "Done two", "Closed", "V1 Foundation"),
        make_story(301, "Future work", "New", "V2 Templates and Artifacts", parent_id=219),
    ]

    candidate, blocked, active_version, release_ready = NEXT_STORY.choose_candidate(
        base_url="https://example.test",
        token="token",
        stories=stories,
        target_status="New",
        closed_status_names={"Closed"},
        predecessor_map={},
        work_packages_by_id={},
        root_work_package_id=208,
        version_status_by_name={
            "V1 Foundation": "open",
            "V2 Templates and Artifacts": "open",
        },
    )

    assert candidate is None
    assert blocked == []
    assert active_version == "V1 Foundation"
    assert release_ready is not None
    assert release_ready.name == "V1 Foundation"
    assert release_ready.closed_story_count == 2


def test_choose_candidate_skips_closed_release_versions() -> None:
    stories = [
        make_story(201, "Done one", "Closed", "V1 Foundation"),
        make_story(301, "Future work", "New", "V2 Templates and Artifacts", parent_id=219),
    ]

    candidate, blocked, active_version, release_ready = NEXT_STORY.choose_candidate(
        base_url="https://example.test",
        token="token",
        stories=stories,
        target_status="New",
        closed_status_names={"Closed"},
        predecessor_map={},
        work_packages_by_id={},
        root_work_package_id=208,
        version_status_by_name={
            "V1 Foundation": "closed",
            "V2 Templates and Artifacts": "open",
        },
    )

    assert release_ready is None
    assert blocked == []
    assert active_version == "V2 Templates and Artifacts"
    assert candidate is not None
    assert candidate.story_id == 301


def test_resolve_root_work_package_id_uses_env_fallback() -> None:
    args = NEXT_STORY.parse_args(["--json"])
    assert (
        NEXT_STORY.resolve_root_work_package_id(
            args,
            {"OPENPROJECT_INITIATIVE_ID": "208"},
        )
        == 208
    )


def test_openproject_next_story_json_error_for_missing_root(capsys) -> None:
    exit_code = NEXT_STORY.main(["--json", "--env-file", "/tmp/does-not-exist.env"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["error"]["code"] == "OPENPROJECT_ACCESS_UNAVAILABLE"


def test_set_status_dry_run_json_shape(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        SET_STATUS.next_story,
        "parse_env_file",
        lambda _path: {
            "OPENPROJECT_URL": "https://projects.example.test",
            "OPENPROJECT_PROJECT_ID": "wood",
            "OPENPROJECT_API_TOKEN": "secret",
        },
    )
    monkeypatch.setattr(SET_STATUS.next_story, "require_env", lambda _env, _keys: None)
    monkeypatch.setattr(
        SET_STATUS.next_story,
        "api_get_json",
        lambda _base_url, _token, path: (
            make_story(278, "Example story title", "New", "V1 Foundation")
            if path.endswith("/work_packages/278")
            else {
                "_embedded": {
                    "elements": [
                        {"name": "In Progress", "_links": {"self": {"href": "/api/v3/statuses/2"}}}
                    ]
                }
            }
        ),
    )

    exit_code = SET_STATUS.main(["278", "In Progress", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "ok": True,
        "dry_run": True,
        "mutation": {
            "system": "openproject",
            "work_package_id": 278,
            "action": "set_status",
            "target_status": "In Progress",
        },
    }


def test_create_story_branch_builds_expected_name() -> None:
    assert (
        CREATE_BRANCH.build_branch_name(278, "Example story title")
        == "feature/op-278-example-story-title"
    )
    assert CREATE_BRANCH.build_branch_name(278, None) == "feature/op-278"


def test_create_story_branch_refuses_dirty_tree(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        CREATE_BRANCH,
        "inspect_repo_state",
        lambda: {
            "path": "/tmp/repo",
            "current_branch": "main",
            "working_tree_clean": False,
            "untracked_files": [],
            "ahead": 0,
            "behind": 0,
        },
    )
    monkeypatch.setattr(CREATE_BRANCH, "branch_exists", lambda _root, _branch: False)

    exit_code = CREATE_BRANCH.main(["278", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["error"]["code"] == "DIRTY_WORKTREE"

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "story_loop" / "next_story.py"
SPEC = importlib.util.spec_from_file_location("next_story", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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
        },
    }


def test_parse_milestone_rank_supports_v_prefix() -> None:
    assert MODULE.parse_milestone_rank("V2 Templates") < MODULE.parse_milestone_rank("V10 Later")
    assert (
        MODULE.parse_milestone_rank("V2 Templates")[0]
        == (MODULE.parse_milestone_rank("M2 Legacy")[0])
    )


def test_choose_candidate_reports_release_ready_for_completed_open_version() -> None:
    stories = [
        make_story(201, "Done one", "Closed", "V1 Foundation"),
        make_story(202, "Done two", "Closed", "V1 Foundation"),
        make_story(301, "Future work", "New", "V2 Templates and Artifacts", parent_id=219),
    ]

    candidate, blocked, active_version, release_ready = MODULE.choose_candidate(
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

    candidate, blocked, active_version, release_ready = MODULE.choose_candidate(
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

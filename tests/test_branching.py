"""Repository adoption metadata must be explicit and cannot waive release scope."""

from __future__ import annotations

import json

import pytest

from wood.cli import main
from wood_project.story import branching, discovery, session
from wood_project.story.models import StoryWorkflowError


def test_story_selection_stays_in_mapped_release(monkeypatch):
    from test_story_cli import settings

    from wood_project.openproject import OpenProjectClient

    def story(identifier, release_id, name):
        return {
            "id": identifier,
            "subject": "Story",
            "_links": {
                "type": {"title": "Story"},
                "status": {"title": "New"},
                "version": {"title": name, "href": f"/api/v3/versions/{release_id}"},
            },
        }

    documents = {
        "/api/v3/work_packages/10": {
            "id": 10,
            "_links": {"project": {"href": "/api/v3/projects/3"}},
        },
        "/api/v3/statuses": {"_embedded": {"elements": [{"name": "Closed", "isClosed": True}]}},
        "/api/v3/projects/3/versions": {
            "_embedded": {
                "elements": [
                    {"id": 20, "name": "R1", "status": "open"},
                    {"id": 21, "name": "R2", "status": "open"},
                ]
            }
        },
    }
    monkeypatch.setattr(discovery, "api_get_json", lambda _client, path: documents[path])
    monkeypatch.setattr(
        discovery, "fetch_descendants", lambda *_args: [story(1, 20, "R1"), story(2, 21, "R2")]
    )
    monkeypatch.setattr(discovery, "fetch_predecessor_map", lambda *_args: {})
    result = discovery.discover_next_story(
        client=OpenProjectClient(settings()),
        root_work_package_id=10,
        target_status="New",
        story_type="Story",
        page_size=100,
        release_id=21,
    )
    assert result["story"]["id"] == 2
    documents["/api/v3/projects/3/versions"]["_embedded"]["elements"][1]["status"] = "closed"
    with pytest.raises(StoryWorkflowError, match="No eligible Story"):
        discovery.discover_next_story(
            client=OpenProjectClient(settings()),
            root_work_package_id=10,
            target_status="New",
            story_type="Story",
            page_size=100,
            release_id=21,
        )


@pytest.mark.parametrize(
    "text",
    [
        '[tool.wood.branching]\nstrategy = "develop"',
        '[tool.wood.branching]\nstrategy = "release"',
        '[tool.wood.branching]\nstrategy = "trunk"\nrelease_id = 1',
        '[tool.wood.branching]\nstrategy = "release"\nrelease_id = true\n'
        'release_branch = "release/v1.0.0"\nscope_reference = "approved"',
        '[tool.wood.branching]\nstrategy = "release"\nrelease_id = 1\n'
        'release_branch = "release/v1.0.0; bad"\nscope_reference = "approved"',
        '[tool.wood.branching]\nstrategy = "trunk"\nunknown = "setting"',
        '[tool.wood.branching]\nstrategy = "integration"\nrelease_id = 1\n'
        'integration_branch = "main"\nscope_reference = "approved"',
        '[tool.wood.branching]\nstrategy = "integration"\nrelease_id = 1\n'
        'integration_branch = "bad..branch"\nscope_reference = "approved"',
    ],
)
def test_invalid_adoption_fails_closed(tmp_path, text):
    with pytest.raises(StoryWorkflowError, match="valid .*branching"):
        branching.configuration(tmp_path, source_text=text)


def test_discovery_cli_needs_no_release_contract_or_credentials(tmp_path, monkeypatch, capsys):
    session.git(tmp_path, "init", "-q", "-b", "main")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    assert main(["repo", "branching", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["strategy"] == "trunk" and not data["adopted"]
    assert data["base_branch"] == data["target_branch"] == "main"
    assert not data["remote_verified"]
    with pytest.raises(StoryWorkflowError, match="Explicit branches"):
        branching.select(tmp_path, base_branch="release/v1.0.0")


def test_integration_mapping_keeps_release_and_target_guards(tmp_path):
    session.git(tmp_path, "init", "-q", "-b", "main")
    text = (
        '[tool.wood.branching]\nstrategy = "integration"\nrelease_id = 25\n'
        'integration_branch = "development"\nscope_reference = "approved workbook"'
    )
    story = {"_links": {"version": {"href": "/api/v3/versions/25", "title": "R1"}}}
    plan = branching.select(tmp_path, story=story, source_text=text)
    assert plan["base_branch"] == plan["target_branch"] == "development"
    with pytest.raises(StoryWorkflowError, match="Explicit branches"):
        branching.select(tmp_path, base_branch="main", source_text=text)
    story["_links"]["version"]["href"] = "/api/v3/versions/26"
    with pytest.raises(StoryWorkflowError, match="planning release differs"):
        branching.select(tmp_path, story=story, source_text=text)

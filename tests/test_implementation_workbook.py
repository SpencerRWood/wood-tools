from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wood_project.implementation import apply as implementation_apply
from wood_project.implementation import openproject as implementation_openproject
from wood_project.implementation import planning as implementation_planning
from wood_project.implementation import workbook as implementation_workbook
from wood_project.implementation.packets import parse_description_packet


def workbook_row(row_number: int, **values: str) -> implementation_planning.WorkbookRow:
    row_values = {column: "" for column in implementation_planning.IMPLEMENTATION_WORKBOOK_COLUMNS}
    row_values.update(values)
    return implementation_planning.WorkbookRow(row_number=row_number, values=row_values)


def named_element(name: str, href: str, element_id: int = 1) -> dict[str, Any]:
    return {"id": element_id, "name": name, "_links": {"self": {"href": href}}}


class FakeOpenProjectClient:
    def __init__(
        self,
        *,
        get_json: Any | None = None,
        request_json: Any | None = None,
    ) -> None:
        self._get_json = get_json
        self._request_json = request_json

    def get_json(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._get_json is None:
            raise AssertionError(path)
        return self._get_json(path, query=query)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._request_json is None:
            raise AssertionError((method, path))
        return self._request_json(method, path, query=query, body=body)


def test_build_implementation_plan_matches_story_without_openproject_id(
    monkeypatch,
) -> None:
    row = workbook_row(
        2,
        **{
            "Story ID": "S1",
            "Subject": "Existing story",
            "Version": "R1",
            "Epic": "Existing epic",
            "Type": "Story",
            "Status": "New",
        },
    )
    descendants = [
        {
            "id": 10,
            "subject": "Existing epic",
            "_links": {"type": {"title": "Epic"}},
        },
        {
            "id": 11,
            "subject": "Existing story",
            "lockVersion": 3,
            "description": {"raw": "External story ID: S1"},
            "_links": {"type": {"title": "Story"}, "project": {"title": "Project"}},
        },
    ]

    def fake_get_json(path, *, query=None):
        if path == "/api/v3/work_packages":
            return {"total": len(descendants), "_embedded": {"elements": descendants}}
        if path == "/api/v3/work_packages/11/relations":
            return {"total": 0, "_embedded": {"elements": []}}
        raise AssertionError(path)

    plan = implementation_planning.build_implementation_plan(
        rows=[row],
        client=FakeOpenProjectClient(get_json=fake_get_json),
        statuses=[named_element("New", "/api/v3/statuses/1")],
        types=[
            named_element("Story", "/api/v3/types/1"),
            named_element("Epic", "/api/v3/types/2"),
        ],
        versions=[],
        initiative={
            "key": "Root",
            "subject": "Root",
            "action": "reuse",
            "work_package_id": 208,
            "type": "Initiative",
            "patch": None,
            "warnings": [],
        },
    )

    assert plan["initiative"]["action"] == "reuse"
    assert plan["versions"][0]["action"] == "create"
    assert plan["epics"][0]["action"] == "reuse"
    assert plan["stories"][0]["action"] == "update"
    assert plan["stories"][0]["work_package_id"] == 11
    assert "Existing Story matched by Story ID." in plan["stories"][0]["warnings"]


def test_resolve_initiative_plan_creates_when_no_deterministic_match(
    monkeypatch,
) -> None:
    row = workbook_row(
        2,
        **{
            "Project": "Project",
            "Root Work Package": "R1 Initiative",
            "Story ID": "S1",
            "Subject": "New story",
        },
    )

    monkeypatch.setattr(implementation_planning, "project_work_packages", lambda **kwargs: [])

    initiative = implementation_planning.resolve_initiative_plan(
        client=FakeOpenProjectClient(),
        project={"id": 7, "identifier": "project", "name": "Project"},
        statuses=[named_element("New", "/api/v3/statuses/1")],
        types=[named_element("Initiative", "/api/v3/types/9")],
        rows=[row],
        metadata={},
        explicit_initiative_id=None,
        configured_initiative_id=None,
    )

    assert initiative["action"] == "create"
    assert initiative["subject"] == "R1 Initiative"
    assert initiative["patch"]["subject"] == "R1 Initiative"
    assert initiative["patch"]["_links"]["type"]["href"] == "/api/v3/types/9"


def test_resolve_initiative_plan_blocks_conflicting_root_type(
    monkeypatch,
) -> None:
    row = workbook_row(
        2,
        **{
            "Project": "Project",
            "Root Work Package": "R1 Initiative",
            "Story ID": "S1",
            "Subject": "New story",
        },
    )
    monkeypatch.setattr(
        implementation_planning,
        "project_work_packages",
        lambda **kwargs: [
            {
                "id": 22,
                "subject": "R1 Initiative",
                "_links": {"type": {"title": "Epic"}},
            }
        ],
    )

    with pytest.raises(
        implementation_openproject.ScriptError,
        match="conflicting types",
    ):
        implementation_planning.resolve_initiative_plan(
            client=FakeOpenProjectClient(),
            project={"id": 7, "identifier": "project", "name": "Project"},
            statuses=[named_element("New", "/api/v3/statuses/1")],
            types=[named_element("Initiative", "/api/v3/types/9")],
            rows=[row],
            metadata={},
            explicit_initiative_id=None,
            configured_initiative_id=None,
        )


def test_apply_writeback_records_openproject_ids(tmp_path: Path) -> None:
    workbook = tmp_path / "implementation_workbook.xlsx"
    story_record = {
        column: "" for column in implementation_planning.IMPLEMENTATION_WORKBOOK_COLUMNS
    }
    story_record.update({"Story ID": "S1", "Subject": "Created story", "OpenProject ID": ""})
    implementation_workbook.write_xlsx(workbook, [story_record], {"Story Count": 1})

    rows = implementation_workbook.workbook_rows(workbook, "Implementation")
    result = implementation_apply.write_openproject_ids_to_workbook(
        workbook,
        sheet_name="Implementation",
        rows=rows,
        applied={
            "versions": [],
            "epics": [],
            "stories": [{"key": "S1", "work_package_id": 123}],
            "relations": [],
        },
    )

    updated_rows = implementation_workbook.workbook_rows(workbook, "Implementation")
    assert result["updated"] is True
    assert result["updated_rows"] == [
        {
            "row_number": 2,
            "story_key": "S1",
            "openproject_id": 123,
            "previous_openproject_id": "",
        }
    ]
    assert updated_rows[0].values["OpenProject ID"] == "123"


def test_traceability_round_trip_and_planning_guards(tmp_path: Path) -> None:
    path = tmp_path / "implementation.xlsx"
    values = workbook_row(
        2,
        **{
            "Project": "Platform",
            "Version": "R10 — Deployment",
            "Story ID": "S1",
            "Subject": "Ship",
            "Primary Repository": "wood-tools",
            "Affected Repositories": "codex-config, rag-service",
            "Predecessors": "S0",
        },
    ).values
    implementation_workbook.write_xlsx(path, [values], {})
    parsed = implementation_workbook.workbook_rows(path, "Implementation")[0]
    assert parsed.values == values
    assert implementation_planning.required_version_names([parsed]) == ["R10 — Deployment"]
    packet = parse_description_packet(implementation_planning.compose_description(parsed.values))
    assert packet["primary_repository"] == "wood-tools"
    assert packet["affected_repositories"] == "codex-config, rag-service"
    assert packet["released_in"] == ""
    parsed.values["Released In"] = "1.2.3"
    with pytest.raises(implementation_openproject.ScriptError, match="must be blank"):
        implementation_planning.required_version_names([parsed])
    parsed.values["Released In"] = ""
    parsed.values["Version"] = "V1"
    with pytest.raises(implementation_openproject.ScriptError, match="R#"):
        implementation_planning.required_version_names([parsed])


def test_planning_update_preserves_shipped_traceability() -> None:
    values = workbook_row(2, **{"Subject": "Shipped", "Version": "R1"}).values
    existing = {
        "id": 123,
        "lockVersion": 2,
        "description": {
            "raw": (
                "Codex Implementation Packet\n\nOpenProject\n"
                "Primary Repository: wood-tools\nAffected Repositories: codex-config\n"
                "Released In: 1.2.3\n\nGoal\nShipped"
            )
        },
        "_links": {},
    }
    patch, _ = implementation_planning.build_patch_payload(
        values=values,
        current=existing,
        statuses=[],
        types=[],
        versions=[],
        parent_ref="/api/v3/work_packages/12",
    )
    packet = parse_description_packet(patch["description"]["raw"])
    assert packet["primary_repository"] == "wood-tools"
    assert packet["affected_repositories"] == "codex-config"
    assert packet["released_in"] == "1.2.3"


def test_build_implementation_plan_blocks_stale_openproject_id(
    monkeypatch,
) -> None:
    row = workbook_row(
        2,
        **{
            "OpenProject ID": "99",
            "Story ID": "S1",
            "Subject": "Stale story",
            "Version": "R1",
            "Type": "Story",
            "Status": "New",
        },
    )

    def fake_get_json(path, *, query=None):
        if path == "/api/v3/work_packages":
            return {"total": 0, "_embedded": {"elements": []}}
        if path == "/api/v3/work_packages/99":
            return {
                "id": 99,
                "subject": "Story from another root",
                "lockVersion": 3,
                "_links": {"type": {"title": "Story"}, "project": {"title": "Project"}},
            }
        raise AssertionError(path)

    with pytest.raises(
        implementation_openproject.ScriptError,
        match="is not a Story beneath the resolved Root Work Package",
    ):
        implementation_planning.build_implementation_plan(
            rows=[row],
            client=FakeOpenProjectClient(get_json=fake_get_json),
            statuses=[named_element("New", "/api/v3/statuses/1")],
            types=[named_element("Story", "/api/v3/types/1")],
            versions=[],
            initiative={
                "key": "Root",
                "subject": "Root",
                "action": "reuse",
                "work_package_id": 208,
                "type": "Initiative",
                "patch": None,
                "warnings": [],
            },
        )


@pytest.mark.parametrize("inverse_relation", [False, True])
def test_apply_plan_verifies_created_and_updated_openproject_writes(
    monkeypatch, inverse_relation: bool
) -> None:
    requested: list[tuple[str, str]] = []

    def work_package(
        work_package_id: int,
        subject: str,
        type_name: str,
        status_name: str = "New",
        links: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": work_package_id,
            "subject": subject,
            "_links": {
                "type": {"title": type_name},
                "status": {"title": status_name},
                **(links or {}),
            },
        }

    def fake_request_json(
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested.append((method, path))
        if method == "POST" and path == "/api/v3/versions":
            assert body == {
                "name": "R1",
                "_links": {"definingProject": {"href": "/api/v3/projects/7"}},
            }
            return {
                "id": 12,
                "name": "R1",
                "_links": {"self": {"href": "/api/v3/versions/12"}},
            }
        if method == "POST" and path == "/api/v3/projects/7/work_packages":
            subject = str((body or {}).get("subject") or "")
            if subject == "Epic":
                return work_package(22, subject, "Epic")
            return work_package(33, subject, "Story")
        if method == "PATCH" and path == "/api/v3/work_packages/34":
            return work_package(34, "Updated story", "Story", "In progress")
        if method == "POST" and path == "/api/v3/work_packages/33/relations":
            return {
                "id": 44,
                "type": "precedes",
                "_links": {
                    "from": {"href": "/api/v3/work_packages/33"},
                    "to": {"href": "/api/v3/work_packages/34"},
                },
            }
        raise AssertionError((method, path))

    def fake_get_json(path, *, query=None):
        requested.append(("GET", path))
        if path == "/api/v3/work_packages/208":
            return work_package(208, "Root", "Initiative")
        if path == "/api/v3/versions/12":
            return {"id": 12, "name": "R1"}
        if path == "/api/v3/work_packages/22":
            return work_package(
                22,
                "Epic",
                "Epic",
                links={
                    "parent": {"href": "/api/v3/work_packages/208"},
                    "version": {"href": "/api/v3/versions/12"},
                },
            )
        if path == "/api/v3/work_packages/33":
            return work_package(
                33,
                "Created story",
                "Story",
                links={
                    "parent": {"href": "/api/v3/work_packages/22"},
                },
            )
        if path == "/api/v3/work_packages/34":
            return work_package(34, "Updated story", "Story", "In progress")
        if path == "/api/v3/relations/44":
            return {
                "id": 44,
                "type": "follows" if inverse_relation else "precedes",
                "_links": {
                    "from": {"href": f"/api/v3/work_packages/{34 if inverse_relation else 33}"},
                    "to": {"href": f"/api/v3/work_packages/{33 if inverse_relation else 34}"},
                },
            }
        raise AssertionError(path)

    client = FakeOpenProjectClient(
        get_json=fake_get_json,
        request_json=fake_request_json,
    )

    applied = implementation_apply.apply_plan(
        client,
        {"id": 7, "identifier": "project", "name": "Project"},
        {
            "initiative": {
                "key": "Root",
                "action": "reuse",
                "work_package_id": 208,
            },
            "versions": [{"key": "R1", "name": "R1", "action": "create", "patch": {"name": "R1"}}],
            "epics": [
                {
                    "key": "Epic",
                    "action": "create",
                    "patch": {
                        "subject": "Epic",
                        "_links": {
                            "parent": {"href": "planned:initiative"},
                            "version": {"href": "planned:version:R1"},
                        },
                    },
                }
            ],
            "stories": [
                {
                    "key": "S1",
                    "action": "create",
                    "patch": {
                        "subject": "Created story",
                        "_links": {"parent": {"href": "planned:epic:Epic"}},
                    },
                },
                {
                    "key": "S2",
                    "action": "update",
                    "work_package_id": 34,
                    "patch": {"lockVersion": 1, "subject": "Updated story"},
                },
            ],
            "relations": [
                {
                    "action": "create",
                    "from_story_key": "S1",
                    "to_story_key": "S2",
                    "from_work_package_id": None,
                    "to_work_package_id": 34,
                    "relation_type": "precedes",
                }
            ],
        },
    )

    assert applied["versions"][0]["verified"] is True
    assert applied["epics"][0]["verified"] is True
    assert applied["stories"][0]["verified"] is True
    assert applied["stories"][1]["verified"] is True
    assert applied["relations"][0]["verified"] is True
    assert applied["relations"][0]["from_work_package_id"] == 33
    assert applied["relations"][0]["to_work_package_id"] == 34
    assert applied["relations"][0]["relation_type"] == "precedes"
    assert ("GET", "/api/v3/versions/12") in requested
    assert ("GET", "/api/v3/work_packages/33") in requested
    assert ("GET", "/api/v3/relations/44") in requested


@pytest.mark.parametrize(
    ("relation_type", "from_id", "to_id", "expected"),
    [
        ("precedes", 33, 34, (33, 34)),
        ("follows", 34, 33, (33, 34)),
        ("follows", 33, 34, (34, 33)),
        ("relates", 33, 34, None),
        ("follows", None, 33, None),
    ],
)
def test_existing_predecessors_preserve_direction_and_reuse_inverse_relations(
    relation_type, from_id, to_id, expected
) -> None:
    relation = {
        "type": relation_type,
        "_links": {
            "from": {"href": f"/api/v3/work_packages/{from_id}"},
            "to": {"href": f"/api/v3/work_packages/{to_id}"},
        },
    }

    def get_json(path, *, query=None):
        assert path in {"/api/v3/work_packages/33/relations", "/api/v3/work_packages/34/relations"}
        return {"_embedded": {"elements": [relation]}}

    found = implementation_planning.existing_precedes_relations(
        FakeOpenProjectClient(get_json=get_json),
        [{"id": i, "_links": {"type": {"title": "Story"}}} for i in (33, 34)],
    )
    assert found == ({expected} if expected is not None else set())


def test_apply_plan_stops_dependent_actions_after_story_failure(monkeypatch) -> None:
    requested: list[tuple[str, str]] = []

    def fake_request_json(
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested.append((method, path))
        if method == "PATCH" and path == "/api/v3/work_packages/33":
            raise implementation_openproject.ScriptError(
                "WORKBOOK_UPLOAD_FAILED",
                "Story update failed.",
            )
        raise AssertionError("Dependent relation creation should not run.")

    def fake_get_json(path, *, query=None):
        requested.append(("GET", path))
        if path == "/api/v3/work_packages/208":
            return {
                "id": 208,
                "subject": "Root",
                "_links": {"type": {"title": "Initiative"}},
            }
        raise AssertionError(path)

    client = FakeOpenProjectClient(
        get_json=fake_get_json,
        request_json=fake_request_json,
    )

    with pytest.raises(implementation_openproject.ScriptError, match="Story update failed"):
        implementation_apply.apply_plan(
            client,
            {"id": 7, "identifier": "project", "name": "Project"},
            {
                "initiative": {
                    "key": "Root",
                    "action": "reuse",
                    "work_package_id": 208,
                },
                "versions": [],
                "epics": [],
                "stories": [
                    {
                        "key": "S1",
                        "action": "update",
                        "work_package_id": 33,
                        "patch": {"lockVersion": 1, "subject": "Updated story"},
                    }
                ],
                "relations": [
                    {
                        "action": "create",
                        "from_story_key": "S1",
                        "to_story_key": "S2",
                        "from_work_package_id": 33,
                        "to_work_package_id": 34,
                        "relation_type": "precedes",
                    }
                ],
            },
        )

    assert ("PATCH", "/api/v3/work_packages/33") in requested
    assert ("POST", "/api/v3/work_packages/33/relations") not in requested


def test_repeated_story_import_reuses_matching_work_package_without_write() -> None:
    current = {
        "id": 33,
        "subject": "Story",
        "description": {"raw": "Goal\nDone"},
        "_links": {"parent": {"href": "/api/v3/work_packages/208"}},
    }
    patch = {
        "lockVersion": 4,
        "subject": "Story",
        "description": {"raw": "Goal\nDone"},
        "_links": {"parent": {"href": "/api/v3/work_packages/208"}},
    }
    assert not implementation_planning.patch_has_changes(patch, current)

    def get_json(path: str, *, query=None):
        if path == "/api/v3/work_packages/208":
            return {"id": 208, "subject": "Root", "_links": {"type": {"title": "Initiative"}}}
        if path == "/api/v3/work_packages/33":
            return current
        raise AssertionError(path)

    def request_json(method: str, path: str, *, body=None, query=None):
        raise AssertionError(f"Unexpected mutation: {method} {path}")

    applied = implementation_apply.apply_plan(
        FakeOpenProjectClient(get_json=get_json, request_json=request_json),
        {"id": 7, "name": "Project"},
        {
            "initiative": {"key": "Root", "action": "reuse", "work_package_id": 208},
            "versions": [],
            "epics": [],
            "stories": [{"key": "S1", "action": "reuse", "work_package_id": 33, "patch": patch}],
            "relations": [],
        },
    )
    assert applied["stories"][0]["action"] == "reuse"


def test_import_rejects_conflicting_initiative_selector_and_workbook_metadata() -> None:
    row = workbook_row(2, **{"Root Work Package": "Tools"})
    with pytest.raises(implementation_openproject.ScriptError, match="conflicts"):
        implementation_planning.resolve_initiative_plan(
            client=FakeOpenProjectClient(),
            project={"id": 3, "name": "Wood"},
            statuses=[],
            types=[],
            rows=[row],
            metadata={"Verified Root Work Package ID": "999"},
            explicit_initiative_id=208,
            configured_initiative_id=None,
        )

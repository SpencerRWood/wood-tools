from __future__ import annotations

import copy
import json
import tomllib

import pytest
from test_story_cli import settings

from wood import hierarchy
from wood.cli import main
from wood_project.openproject import OpenProjectClient, OpenProjectError

SELECTORS = ["--project", "3", "--initiative", "Tools", "--release", "R1", "--epic", "Delivery"]


def test_legacy_context_fallback_and_repository_precedence(remote, monkeypatch, capsys):
    monkeypatch.setenv("OPENPROJECT_PROJECT_ID", "3")
    monkeypatch.setenv("OPENPROJECT_INITIATIVE_ID", "208")
    args = ["--release", "20", "--epic", "412"]
    code, result = invoke(capsys, args=args)
    assert code == 0
    assert result["data"]["mapping"]["project_id"] == 3
    assert result["data"]["mapping"]["initiative_id"] == 208
    remote["path"].write_text("[tool.wood.openproject]\nproject_id = 3\ninitiative_id = 208\n")
    monkeypatch.setenv("OPENPROJECT_PROJECT_ID", "99")
    monkeypatch.setenv("OPENPROJECT_INITIATIVE_ID", "999")
    code, result = invoke(capsys, args=args)
    assert code == 0
    assert result["data"]["mapping"]["project_id"] == 3
    assert result["data"]["mapping"]["initiative_id"] == 208


def package(identifier, name, kind, parent=None, release=None):
    links = {
        "project": {"href": "/api/v3/projects/3"},
        "type": {"href": f"/api/v3/types/{1 if kind == 'Initiative' else 2}", "title": kind},
        "status": {"href": "/api/v3/statuses/1", "title": "New"},
    }
    if parent is not None:
        links["parent"] = {"href": f"/api/v3/work_packages/{parent}"}
    if release is not None:
        links["version"] = {"href": f"/api/v3/versions/{release}"}
    return {"id": identifier, "subject": name, "_links": links}


def release(identifier=20, name="R1"):
    return {
        "id": identifier,
        "name": name,
        "status": "open",
        "_links": {"definingProject": {"href": "/api/v3/projects/3"}},
    }


@pytest.fixture
def remote(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "pyproject.toml"
    path.write_text('[project]\nname = "example" # preserved\n')
    state = {
        "root": root,
        "path": path,
        "projects": [{"id": 3, "identifier": "platform", "name": "Platform", "active": True}],
        "packages": [
            package(208, "Tools", "Initiative"),
            package(412, "Delivery", "Epic", 208, 20),
        ],
        "releases": [release()],
        "types": [{"id": 1, "name": "Initiative"}, {"id": 2, "name": "Epic"}],
        "statuses": [
            {"id": 1, "name": "New", "isClosed": False},
            {"id": 2, "name": "Closed", "isClosed": True},
        ],
        "calls": [],
        "fail": False,
        "posts": [],
        "after_post": None,
        "before_get": None,
        "incomplete": False,
        "post_error": None,
    }

    def request(self, method, endpoint, *, query=None, body=None):
        state["calls"].append((method, endpoint, copy.deepcopy(body)))
        if state["fail"]:
            raise OpenProjectError("NETWORK_ERROR", "secret-transport-text")
        if method == "POST":
            state["posts"].append(copy.deepcopy(body))
            item = copy.deepcopy(body)
            item["id"] = 500 + len(state["posts"])
            if endpoint == "/api/v3/versions":
                state["releases"].append(item)
            else:
                kind = "Initiative" if item["_links"]["type"]["href"].endswith("/1") else "Epic"
                item["_links"]["type"]["title"] = kind
                item["_links"]["status"]["title"] = "New"
                state["packages"].append(item)
            if state["after_post"]:
                state["after_post"](item)
            if state["post_error"]:
                raise OpenProjectError("NETWORK_ERROR", "uncertain response")
            return copy.deepcopy(item)
        assert method == "GET" and body is None
        if state["before_get"]:
            state["before_get"](endpoint)
        collections = {
            "/api/v3/projects": state["projects"],
            "/api/v3/work_packages": state["packages"],
            "/api/v3/projects/3/versions": state["releases"],
            "/api/v3/projects/3/types": state["types"],
            "/api/v3/statuses": state["statuses"],
        }
        if endpoint in collections:
            items = collections[endpoint]
            start = (int(query["offset"]) - 1) * int(query["pageSize"])
            result = {
                "total": len(items),
                "_embedded": {"elements": copy.deepcopy(items[start : start + 100])},
            }
            if state["incomplete"]:
                result.pop("total")
            return result
        for item in state["packages"] + state["releases"]:
            resource = "work_packages" if "subject" in item else "versions"
            if endpoint == f"/api/v3/{resource}/{item['id']}":
                return copy.deepcopy(item)
        raise AssertionError(endpoint)

    monkeypatch.setattr(OpenProjectClient, "request_json", request)
    monkeypatch.setattr(hierarchy, "load_settings", settings)
    monkeypatch.setattr(hierarchy, "repository_root", lambda _cwd: root)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    return state


def invoke(capsys, action="plan", args=None):
    code = main(["hierarchy", action, *(SELECTORS if args is None else args), "--json"])
    return code, json.loads(capsys.readouterr().out)


def apply(capsys, args=None):
    _, plan = invoke(capsys, args=args)
    return invoke(
        capsys,
        "ensure",
        [
            *(SELECTORS if args is None else args),
            "--apply",
            "--plan-hash",
            plan["data"]["plan_hash"],
        ],
    )


@pytest.mark.parametrize("action", ["plan", "ensure"])
def test_preview_is_read_only_and_deterministic(remote, capsys, action):
    original = remote["path"].read_bytes()
    code, result = invoke(capsys, action)
    assert code == 0 and result["data"]["dry_run"] is True
    assert result["mutation"] == ("read-only" if action == "plan" else "preview")
    assert [op["action"] for op in result["data"]["operations"]] == ["reuse"] * 3
    assert result["data"]["mapping"] == {
        "project_id": 3,
        "initiative_id": 208,
        "release_id": 20,
        "epic_id": 412,
    }
    assert remote["path"].read_bytes() == original
    assert not remote["posts"]
    _, again = invoke(capsys, action)
    assert result == again


def test_reuse_persists_mapping_preserves_toml_and_converges(remote, capsys):
    code, result = apply(capsys)
    assert code == 0 and result["data"]["mapping_written"]
    assert len(result["data"]["applied"]) == 3
    assert all(item["verified"] for item in result["data"]["applied"])
    assert '[project]\nname = "example" # preserved\n' in remote["path"].read_text()
    assert not remote["posts"]
    original = remote["path"].read_bytes()
    code, result = apply(capsys, args=[])
    assert code == 0 and not result["data"]["mapping_written"]
    assert original == remote["path"].read_bytes()


def test_create_exact_preview_requests_verify_links_and_repeat(remote, capsys):
    remote["packages"] = []
    remote["releases"] = []
    _, plan = invoke(capsys)
    operations = plan["data"]["operations"]
    assert [op["action"] for op in operations] == ["create"] * 3
    assert len(json.dumps(plan)) < 5000 and "<depth limit>" not in json.dumps(plan)
    assert all(json.loads(op["body_json"]) for op in operations)
    code, result = apply(capsys)
    assert code == 0
    ids = result["data"]["mapping"]
    epic = remote["packages"][-1]
    assert epic["_links"]["parent"]["href"] == f"/api/v3/work_packages/{ids['initiative_id']}"
    assert epic["_links"]["version"]["href"] == f"/api/v3/versions/{ids['release_id']}"
    assert remote["posts"][0] == json.loads(operations[0]["body_json"])
    assert remote["posts"][1] == json.loads(operations[1]["body_json"])
    assert tomllib.loads(remote["path"].read_text())["tool"]["wood"]["openproject"] == ids
    code, again = apply(capsys, args=[])
    assert code == 0 and not again["data"]["mapping_written"]
    assert len(remote["posts"]) == 3


@pytest.mark.parametrize("kind", ["initiative", "release", "epic"])
def test_missing_objects_are_created_independently(remote, capsys, kind):
    if kind == "initiative":
        remote["packages"] = []
    elif kind == "release":
        remote["releases"] = []
        remote["packages"] = remote["packages"][:1]
    else:
        remote["packages"] = remote["packages"][:1]
    code, _ = apply(capsys)
    assert code == 0
    assert len(remote["posts"]) == (1 if kind == "epic" else 2)


@pytest.mark.parametrize("kind", ["initiative", "release", "epic"])
def test_duplicate_names_fail_before_writes(remote, capsys, kind):
    if kind == "release":
        remote["releases"].append(release(21))
    else:
        remote["packages"].append(
            package(999, "Tools" if kind == "initiative" else "Delivery", kind.title(), 208, 20)
        )
    code, result = invoke(capsys)
    assert code == 5 and result["status"] == "ambiguous"
    assert len(result["data"]["candidates"]) == 2
    assert not remote["posts"]


def test_late_page_duplicate_is_not_missed(remote, capsys):
    remote["packages"].extend(package(i, f"Other {i}", "Epic", 208, 20) for i in range(600, 701))
    remote["packages"].append(package(999, "Delivery", "Epic", 208, 20))
    code, _ = invoke(capsys)
    assert code == 5


@pytest.mark.parametrize("field", ["parent", "version", "project", "status"])
def test_epic_conflicts_and_closed_status_rejected(remote, capsys, field):
    remote["packages"][-1]["_links"][field]["href"] = {
        "parent": "/api/v3/work_packages/999",
        "version": "/api/v3/versions/999",
        "project": "/api/v3/projects/999",
        "status": "/api/v3/statuses/2",
    }[field]
    code, result = invoke(capsys)
    assert code == 2
    assert result["errors"][0]["code"] in {"HIERARCHY_CONFLICT", "HIERARCHY_INACTIVE"}
    assert not remote["posts"]


@pytest.mark.parametrize(
    "target", ["project", "release", "initiative", "new-status", "shared-release"]
)
def test_inactive_or_wrong_project_objects_are_not_reused(remote, capsys, target):
    if target == "project":
        remote["projects"][0]["active"] = False
    elif target == "release":
        remote["releases"][0]["status"] = "closed"
    elif target == "initiative":
        remote["packages"][0]["_links"]["status"]["href"] = "/api/v3/statuses/2"
    elif target == "new-status":
        remote["packages"] = []
        remote["statuses"][0]["isClosed"] = True
    else:
        remote["releases"][0]["_links"]["definingProject"]["href"] = "/api/v3/projects/4"
    code, _ = invoke(capsys)
    assert code == 2 and not remote["posts"]


@pytest.mark.parametrize("extra", [[], ["--plan-hash", "wrong"]])
def test_apply_requires_matching_reviewed_hash(remote, capsys, extra):
    code, result = invoke(capsys, "ensure", [*SELECTORS, "--apply", *extra])
    assert code == 6 and result["errors"][0]["code"] == "HIERARCHY_PLAN_STALE"
    assert not remote["posts"]


@pytest.mark.parametrize("target", ["remote", "file"])
def test_changed_reviewed_plan_rejected(remote, capsys, target):
    _, plan = invoke(capsys)
    if target == "remote":
        remote["packages"][-1]["subject"] = "Renamed"
    else:
        remote["path"].write_text(remote["path"].read_text() + "# change\n")
    code, result = invoke(
        capsys, "ensure", [*SELECTORS, "--apply", "--plan-hash", plan["data"]["plan_hash"]]
    )
    assert code == 6 and not remote["posts"]
    assert result["mutation"] == "mutating"


def test_uncertain_create_response_reuses_object_on_new_plan(remote, capsys):
    remote["packages"] = []
    original = remote["path"].read_bytes()
    remote["post_error"] = True
    code, result = apply(capsys)
    assert code == 4 and len(remote["posts"]) == 1
    assert remote["path"].read_bytes() == original
    assert "uncertain response" not in json.dumps(result)
    remote["post_error"] = False
    code, _ = apply(capsys)
    assert code == 0 and len(remote["posts"]) == 2


@pytest.mark.parametrize("kind", ["initiative", "release", "epic"])
def test_wrong_created_result_blocks_mapping_write(remote, capsys, kind):
    remote["packages"] = []
    remote["releases"] = []
    original = remote["path"].read_bytes()

    def tamper(item):
        item_kind = "release" if "name" in item else item["_links"]["type"]["title"].lower()
        if item_kind == kind:
            item["_links"]["definingProject" if kind == "release" else "project"]["href"] = (
                "/api/v3/projects/4"
            )

    remote["after_post"] = tamper
    code, result = apply(capsys)
    assert code == 1 and result["errors"][0]["code"] == "HIERARCHY_VERIFICATION_FAILED"
    assert remote["path"].read_bytes() == original
    assert len(result["data"]["applied"]) == (
        1 if kind == "initiative" else 2 if kind == "release" else 3
    )
    assert result["data"]["applied"][-1]["verified"] is False
    assert result["data"]["applied"][-1]["id"] > 0


def test_mapping_changed_during_apply_is_retained(remote, capsys):
    changed = remote["path"].read_text() + "# concurrent edit\n"

    def edit(endpoint):
        if endpoint == "/api/v3/work_packages/412":
            remote["path"].write_text(changed)

    remote["before_get"] = edit
    code, result = apply(capsys)
    assert code == 6 and remote["path"].read_text() == changed
    assert len(result["data"]["applied"]) == 3


@pytest.mark.parametrize(
    "text",
    [
        "malformed [",
        '[tool.wood]\nopenproject = "bad"\n',
        "[tool.wood.openproject]\ninitiative_id = true\n",
        "[tool.wood.openproject]\nproject_id = -1\n",
        "[tool.wood]\nopenproject = {initiative_id = 208}\n",
        '[tool.wood.openproject]\n"initiative_id" = 208\n',
    ],
)
def test_invalid_or_unsupported_mapping_fails_before_remote_requests(remote, capsys, text):
    remote["path"].write_text(text)
    code, result = invoke(capsys)
    assert code == 2 and result["errors"][0]["code"] == "HIERARCHY_MAPPING_INVALID"
    assert not remote["calls"]


def test_mapping_patch_preserves_comments_other_keys_and_tables(remote, capsys):
    remote["path"].write_text(
        "[tool.wood.openproject] # context\ninitiative_id = 208 # keep\n"
        'custom = "value"\n\n[project]\nname = "sample"\n'
    )
    code, _ = apply(capsys)
    assert code == 0
    text = remote["path"].read_text()
    assert "initiative_id = 208 # keep" in text
    assert 'custom = "value"' in text
    assert '[project]\nname = "sample"\n' in text


def test_missing_pyproject_created_only_on_apply(remote, capsys):
    remote["path"].unlink()
    code, _ = invoke(capsys)
    assert code == 0 and not remote["path"].exists()
    code, _ = apply(capsys)
    assert code == 0 and remote["path"].exists()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--project", "3"],
        ["--project", "unknown"],
        [*SELECTORS, "--initiative", "999"],
        [*SELECTORS, "--epic", " "],
        [*SELECTORS, "--epic", "x" * 121],
    ],
)
def test_missing_context_unknown_ids_and_bad_selectors(remote, capsys, args):
    code, _ = invoke(capsys, args=args)
    assert code == 2 and not remote["posts"]


def test_connection_failure_and_incomplete_discovery_are_bounded(remote, capsys):
    remote["fail"] = True
    code, result = invoke(capsys)
    assert code == 4 and "secret-transport-text" not in json.dumps(result)
    remote["fail"] = False
    remote["incomplete"] = True
    code, result = invoke(capsys)
    assert code == 4 and result["errors"][0]["code"] == "INCOMPLETE_COLLECTION"


def test_explicit_ids_resolve_duplicate_names(remote, capsys):
    remote["packages"].append(package(999, "Delivery", "Epic", 208, 20))
    code, result = apply(capsys, args=[*SELECTORS, "--epic", "412"])
    assert code == 0 and result["data"]["mapping"]["epic_id"] == 412


def test_cooperating_writers_do_not_overlap(remote, capsys, monkeypatch):
    def busy(*_args):
        raise BlockingIOError()

    monkeypatch.setattr(hierarchy.fcntl, "flock", busy)
    code, result = apply(capsys)
    assert code == 3 and result["errors"][0]["code"] == "HIERARCHY_BUSY"
    assert not remote["posts"]


def test_plan_cannot_apply_and_commands_are_advertised(remote, capsys):
    code, _ = invoke(capsys, "plan", [*SELECTORS, "--apply"])
    assert code == 2
    assert main(["contract", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {"hierarchy plan", "hierarchy ensure"} <= set(result["data"]["capabilities"])


def test_new_duplicate_during_apply_prevents_following_creation(remote, capsys):
    remote["packages"] = []
    original = remote["path"].read_bytes()

    def duplicate(item):
        if item.get("subject") == "Tools":
            remote["releases"].append(release(21))

    remote["after_post"] = duplicate
    code, result = apply(capsys)
    assert code == 5 and len(remote["posts"]) == 1
    assert remote["path"].read_bytes() == original
    assert result["data"]["applied"][0]["verified"] is True


def test_creation_metadata_changed_during_apply_requires_fresh_plan(remote, capsys):
    remote["packages"] = []

    def change_status(item):
        if item.get("subject") == "Tools":
            remote["statuses"][0]["id"] = 3
            # Keep the already-created Initiative open with its original status.
            remote["statuses"].append({"id": 1, "name": "Active", "isClosed": False})

    remote["after_post"] = change_status
    code, result = apply(capsys)
    assert code == 6 and len(remote["posts"]) == 1
    assert result["errors"][0]["code"] == "HIERARCHY_PLAN_STALE"


def test_atomic_mapping_write_failure_retains_original_and_known_ids(remote, capsys, monkeypatch):
    original = remote["path"].read_bytes()

    def fail_replace(*_args):
        raise OSError("secret-file-error")

    monkeypatch.setattr(hierarchy.os, "replace", fail_replace)
    code, result = apply(capsys)
    assert code == 4
    assert remote["path"].read_bytes() == original
    assert list(remote["root"].iterdir()) == [remote["path"]]
    assert len(result["data"]["applied"]) == 3
    assert "secret-file-error" not in json.dumps(result)


def test_symlink_mapping_is_rejected_without_remote_requests(remote, capsys, tmp_path):
    target = tmp_path / "shared.toml"
    remote["path"].rename(target)
    remote["path"].symlink_to(target)
    code, result = invoke(capsys)
    assert code == 2 and result["errors"][0]["code"] == "HIERARCHY_MAPPING_INVALID"
    assert not remote["calls"]

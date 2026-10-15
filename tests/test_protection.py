"""Mocked GitHub writes and effective readback; no live branch enforcement changes."""

from __future__ import annotations

import base64
import copy
import json
import subprocess

import pytest
from test_initialization import specification_fixture

from wood import initialization, operations, protection
from wood.cli import main
from wood.workflow_files import WorkflowFilesError
from wood_project.story import session


@pytest.fixture
def github(tmp_path, monkeypatch):
    spec = specification_fixture()
    root = tmp_path / "repo"
    root.mkdir()
    session.git(root, "init", "-q", "-b", "main")
    (root / "pyproject.toml").write_text(
        f'[tool.wood.workflow]\noutput_directory = "{tmp_path / "workflow-output"}"\n'
    )
    state = {
        "rulesets": [],
        "created": [],
        "extra_effective": [],
        "workflows": initialization.workflows(initialization.specification(spec)),
        "app_id": 15368,
        "fail_create": 0,
        "calls": [],
    }
    monkeypatch.setattr(operations, "_slug", lambda _root: spec["repository"])

    def api(_root, path):
        state["calls"].append(path)
        if path == f"repos/{spec['repository']}":
            return {"archived": state.get("archived", False), "default_branch": "main"}
        if path == "apps/github-actions":
            return {"id": state["app_id"]}
        if "rulesets?" in path:
            return [
                {"id": rule["id"], "name": rule["name"], "source_type": "Repository"}
                for rule in state["rulesets"]
            ]
        if "/rulesets/" in path:
            return next(
                copy.deepcopy(r)
                for r in state["rulesets"]
                if str(r["id"]) == path.rsplit("/", 1)[1]
            )
        if "/rules/branches/" in path:
            branch = path.rsplit("/", 1)[1].split("?")[0]
            return [
                dict(rule, ruleset_id=r["id"])
                for r in state["rulesets"]
                if r["enforcement"] == "active"
                and f"refs/heads/{branch}" in r["conditions"]["ref_name"]["include"]
                for rule in r["rules"]
            ] + state["extra_effective"]
        if "/branches/" in path:
            return {"commit": {"sha": "c" * 40}}
        if "/contents/.github/workflows?" in path:
            return [{"name": name, "type": "file"} for name in state["workflows"]]
        if "/contents/.github/workflows/" in path:
            name = path.rsplit("/", 1)[1].split("?")[0]
            return {
                "encoding": "base64",
                "content": base64.b64encode(state["workflows"][name].encode()).decode(),
            }
        raise AssertionError(path)

    def create(_root, repository, payload):
        assert repository == spec["repository"]
        if state["fail_create"] == len(state["created"]) + 1:
            raise WorkflowFilesError("FIXTURE_FAILURE", "fixture failure")
        value = {**copy.deepcopy(payload), "id": len(state["rulesets"]) + 1}
        state["rulesets"].append(value)
        state["created"].append(value)
        return value

    monkeypatch.setattr(operations, "_gh", api)
    monkeypatch.setattr(protection, "_create", create)
    return root, spec, state


def test_plan_apply_readback_and_idempotent_retry(github):
    root, spec, state = github
    record = protection.plan(root, spec)
    assert not state["created"]
    assert {op["payload"]["name"] for op in record["operations"]} == {
        "wood-main",
        "wood-development",
    }
    assert all(op["payload"]["bypass_actors"] == [] for op in record["operations"])
    result = protection.apply(root, spec, record["plan_hash"], "approval:separate-enforcement")
    assert len(state["created"]) == 2 and result["readback"]["passed"]
    main = state["rulesets"][0]
    contexts = main["rules"][-1]["parameters"]["required_status_checks"]
    assert {check["context"] for check in contexts} == {
        "validation / validation",
        "pr-routing",
        "readiness / release-readiness",
    }
    assert all(check["integration_id"] == 15368 for check in contexts)
    retry = protection.plan(root, spec)
    assert {operation["action"] for operation in retry["operations"]} == {"verify"}
    assert protection.apply(root, spec, retry["plan_hash"], "approval:retry")["readback"]["passed"]
    assert len(state["created"]) == 2


def test_missing_approval_and_stale_plan_never_write(github):
    root, spec, state = github
    plan = protection.plan(root, spec)
    with pytest.raises(WorkflowFilesError, match="approval"):
        protection.apply(root, spec, plan["plan_hash"], "")
    with pytest.raises(WorkflowFilesError, match="stale"):
        protection.apply(root, spec, "outdated", "approval")
    assert not state["created"]


def test_partial_failure_replans_without_overwriting_or_recreating(github):
    root, spec, state = github
    plan = protection.plan(root, spec)
    state["fail_create"] = 2
    with pytest.raises(WorkflowFilesError, match="fixture failure"):
        protection.apply(root, spec, plan["plan_hash"], "approval")
    assert len(state["created"]) == 1
    with pytest.raises(WorkflowFilesError, match="stale"):
        protection.apply(root, spec, plan["plan_hash"], "approval")
    retry = protection.plan(root, spec)
    assert [operation["action"] for operation in retry["operations"]] == ["verify", "create"]
    state["fail_create"] = 0
    assert protection.apply(root, spec, retry["plan_hash"], "approval:retry")["readback"]["passed"]
    assert len(state["created"]) == 2


@pytest.mark.parametrize(
    "failure",
    [
        "disabled",
        "bypass",
        "missing-check",
        "wrong-app",
        "routing",
        "extra-workflow",
        "unknown-inherited",
        "weak-pr",
    ],
)
def test_readback_rejects_effective_gaps(github, failure):
    root, spec, state = github
    plan = protection.plan(root, spec)
    protection.apply(root, spec, plan["plan_hash"], "approval")
    if failure == "disabled":
        state["rulesets"][0]["enforcement"] = "disabled"
    elif failure == "bypass":
        state["rulesets"][0]["bypass_actors"] = [{"actor_type": "RepositoryRole", "actor_id": 5}]
    elif failure in {"missing-check", "wrong-app"}:
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        if failure == "missing-check":
            checks.pop()
        else:
            checks[0]["integration_id"] = 123
    elif failure == "routing":
        state["workflows"]["routing.yml"] = '{"jobs": {"routing": {"run": "true"}}}'
    elif failure == "extra-workflow":
        state["workflows"]["unsafe-development-publication.yml"] = "{}"
    elif failure == "unknown-inherited":
        state["extra_effective"] = [{"type": "deletion", "ruleset_id": 999}]
    elif failure == "weak-pr":
        state["rulesets"][0]["rules"][2]["parameters"]["required_review_thread_resolution"] = False
    result = protection.readback(root, spec)
    assert result["passed"] is False and result["authorization_granted"] is False


def test_workflow_compatibility_is_required_before_writes(github):
    root, spec, state = github
    plan = protection.plan(root, spec)
    state["workflows"]["validate.yml"] = "incompatible check name"
    with pytest.raises(WorkflowFilesError, match="workflows first"):
        protection.apply(root, spec, plan["plan_hash"], "approval")
    assert not state["created"]


def test_conflicting_policy_and_app_are_not_overwritten(github):
    root, spec, state = github
    state["archived"] = True
    with pytest.raises(WorkflowFilesError, match="active repository"):
        protection.plan(root, spec)
    state["archived"] = False
    state["app_id"] = 99
    with pytest.raises(WorkflowFilesError, match="integration ID"):
        protection.plan(root, spec)
    state["app_id"] = 15368
    state["rulesets"] = [{**protection.rulesets(initialization.specification(spec))[0], "id": 1}]
    state["rulesets"][0]["enforcement"] = "disabled"
    with pytest.raises(WorkflowFilesError, match="resolve its policy separately"):
        protection.plan(root, spec)
    assert not state["created"]


def test_boundaries_fail_closed(github, monkeypatch):
    root, spec, state = github
    with pytest.raises(WorkflowFilesError, match="repository differs"):
        protection.plan(root, {**spec, "repository": "other/repo"})
    monkeypatch.setattr(operations, "_gh", lambda *_args: [{"id": 1}] * 100)
    with pytest.raises(WorkflowFilesError, match="truncated"):
        protection._list(root, spec["repository"])
    with pytest.raises(WorkflowFilesError, match="administrator review"):
        protection._details(root, spec["repository"], [{"id": 1, "source_type": "Organization"}])


def test_real_write_transport_reports_only_safe_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "secret")
    )
    with pytest.raises(WorkflowFilesError, match="read back") as error:
        protection._create(tmp_path, "fixture/repo", {})
    assert "secret" not in str(error.value)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '{"id":9}', "")
    )
    assert protection._create(tmp_path, "fixture/repo", {}) == {"id": 9}


def test_protection_cli_records_and_blocked_readback(github, tmp_path, monkeypatch, capsys):
    root, spec, state = github
    monkeypatch.chdir(root)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    args = ["repo", "protection", "plan", "--spec", str(path), "--json"]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mutation"] == "preview"
    assert (
        main(
            [
                "repo",
                "protection",
                "apply",
                "--spec",
                str(path),
                "--plan-hash",
                preview["data"]["plan_hash"],
                "--approval-reference",
                "fixture-approval",
                "--json",
            ]
        )
        == 0
    )
    value = json.loads(capsys.readouterr().out)
    assert value["mutation"] == "mutating" and value["data"]["readback"]["passed"]
    state["rulesets"][0]["enforcement"] = "disabled"
    assert main(["repo", "protection", "readback", "--spec", str(path), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["passed"] is False


def test_github_added_nonweakening_defaults_and_rule_order_read_back_and_retry(github):
    root, spec, state = github
    plan = protection.plan(root, spec)
    protection.apply(root, spec, plan["plan_hash"], "approved fixture")
    for rule in state["rulesets"]:
        parameters = next(
            item["parameters"] for item in rule["rules"] if item["type"] == "pull_request"
        )
        parameters["required_reviewers"] = []
        parameters["require_extra_approval_for_unattributed_changes"] = True
        parameters["allowed_merge_methods"].reverse()
        rule["rules"].reverse()
    assert protection.readback(root, spec)["passed"]
    assert {item["action"] for item in protection.plan(root, spec)["operations"]} == {"verify"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("required_reviewers", [{"id": 42}]),
        ("require_extra_approval_for_unattributed_changes", False),
        ("dismiss_stale_reviews_on_push", False),
        ("required_approving_review_count", 2),
        ("unknown_policy", True),
    ],
)
def test_normalization_never_ignores_unreviewed_or_weak_policy(github, field, value):
    root, spec, state = github
    plan = protection.plan(root, spec)
    protection.apply(root, spec, plan["plan_hash"], "approved fixture")
    state["rulesets"][0]["rules"][2]["parameters"][field] = value
    assert not protection.readback(root, spec)["passed"]
    with pytest.raises(WorkflowFilesError, match="resolve its policy separately"):
        protection.plan(root, spec)


def test_semantic_yaml_and_preserved_reusable_interfaces_are_not_false_drift(github):
    import yaml

    root, spec, state = github
    plan = protection.plan(root, spec)
    protection.apply(root, spec, plan["plan_hash"], "approved fixture")
    state["workflows"] = {
        name: yaml.safe_dump(json.loads(text), sort_keys=False)
        for name, text in state["workflows"].items()
    }
    state["workflows"]["shared-contract.yml"] = json.dumps(
        {"on": {"workflow_call": {}}, "jobs": {"publish": {"uses": "./reusable.yml"}}}
    )
    assert protection.readback(root, spec)["passed"]
    state["workflows"]["shared-contract.yml"] = json.dumps(
        {
            "on": {"push": {"branches": ["development"]}},
            "permissions": {"contents": "write"},
            "jobs": {"publish": {"runs-on": "ubuntu-latest", "steps": [{"run": "publish"}]}},
        }
    )
    assert not protection.readback(root, spec)["passed"]

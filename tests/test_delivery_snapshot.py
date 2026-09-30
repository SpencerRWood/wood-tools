"""Optional delivery facts must be attributable to the merged Story revision."""

from __future__ import annotations

from pathlib import Path

import pytest

from wood import delivery, operations


@pytest.fixture
def providers(monkeypatch):
    merged = "a" * 40
    state = {
        "release_enabled": True,
        "deployment_enabled": True,
        "release": {"tag_name": "v1.0.0", "draft": False},
        "commit": {"sha": "b" * 40},
        "comparison": {"status": "ahead"},
        "deployment_status": "success",
        "deployment": {
            "commit_sha": merged,
            "environment": "production",
            "digest": "sha256:" + "c" * 64,
        },
    }
    monkeypatch.setattr(
        operations,
        "repo_info",
        lambda _root: {
            "release": {"semantic_release": state["release_enabled"]},
            "deployment": {"applicable": state["deployment_enabled"]},
        },
    )

    def gh(_root, path):
        if "/releases/" in path:
            return state["release"]
        if "/commits/" in path:
            return state["commit"]
        return state["comparison"]

    monkeypatch.setattr(operations, "_gh", gh)
    monkeypatch.setattr(
        operations, "deploy_status", lambda _root: (state["deployment_status"], state["deployment"])
    )
    state["evidence"] = {
        "repository": "owner/repo",
        "validation_file": "/validation.json",
        "repository_checks": [{"name": "ruff", "status": "passed"}],
        "ci": {"run_id": 1, "url": "https://github.com/owner/repo/actions/runs/1"},
        "pull_request": {
            "number": 2,
            "url": "https://github.com/owner/repo/pull/2",
            "source_sha": "d" * 40,
            "merge_sha": merged,
        },
    }
    return state


def collect(state):
    return delivery.collect_delivery(Path("."), state["evidence"], "feature/op-413-test")


def test_all_fields_and_revision_bound_attestations(providers):
    for field in ("infrastructure_promotion", "runtime_verification"):
        providers["deployment"][field] = {
            "revision": "a" * 40,
            "environment": "production",
            "status": "passed",
            "url": "https://example.test/evidence",
        }
    result = collect(providers)
    assert set(result) == {
        "repository",
        "branch",
        "source_commit",
        "pull_request",
        "validation",
        "ci",
        "merged_revision",
        "semantic_release",
        "container_image_digest",
        "infrastructure_promotion",
        "deployed_revision",
        "runtime_verification",
    }
    assert all(item["state"] == "available" for item in result.values())
    assert result["deployed_revision"]["value"]["environment"] == "production"


def test_not_applicable_is_distinct_from_unavailable(providers):
    providers["release_enabled"] = False
    providers["deployment_enabled"] = False
    result = collect(providers)
    for field in (
        "semantic_release",
        "deployed_revision",
        "container_image_digest",
        "infrastructure_promotion",
        "runtime_verification",
    ):
        assert result[field]["state"] == "not_applicable"


@pytest.mark.parametrize("status", ["stale", "error", "unavailable"])
def test_failed_or_stale_deployment_is_not_delivery(providers, status):
    providers["deployment_status"] = status
    assert collect(providers)["deployed_revision"]["state"] == "unavailable"


def test_unrelated_release_and_deployment(providers):
    providers["comparison"] = {"status": "diverged"}
    providers["deployment"]["commit_sha"] = "e" * 40
    result = collect(providers)
    assert result["semantic_release"]["state"] == "unavailable"
    assert result["deployed_revision"]["state"] == "unavailable"


def test_invalid_digest_and_unbound_runtime_are_unavailable(providers):
    providers["deployment"]["digest"] = "tag:latest"
    providers["deployment"]["runtime_verification"] = {"revision": "wrong", "status": "passed"}
    result = collect(providers)
    assert result["container_image_digest"]["state"] == "unavailable"
    assert result["runtime_verification"]["state"] == "unavailable"


def test_optional_provider_failure_does_not_erase_core(providers, monkeypatch):
    def unavailable(*_args):
        raise operations.OperationsError("UNAVAILABLE", "provider unavailable")

    monkeypatch.setattr(operations, "_gh", unavailable)
    monkeypatch.setattr(operations, "deploy_status", unavailable)
    result = collect(providers)
    assert result["merged_revision"]["state"] == "available"
    assert result["semantic_release"]["state"] == "unavailable"
    assert result["deployed_revision"]["state"] == "unavailable"

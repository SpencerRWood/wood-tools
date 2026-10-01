"""Actions run/attempt evidence diagnoses delivery without inventing runtime facts."""

from __future__ import annotations

from pathlib import Path

import pytest

from wood import operations
from wood.release_pipeline import collect_release_pipeline


@pytest.fixture
def pipeline(monkeypatch):
    state = {
        "run": {
            "id": 73,
            "run_attempt": 6,
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "failure",
        },
        "jobs": [
            {
                "id": 101,
                "run_id": 73,
                "name": "release / release",
                "status": "completed",
                "conclusion": "success",
                "steps": [],
            },
            {
                "id": 102,
                "run_id": 73,
                "name": "promotion / Validate and merge exact dev image PR",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {"name": "Create or reuse exact infrastructure PR", "conclusion": "failure"}
                ],
            },
        ],
        "calls": [],
    }

    def gh(_root, path):
        state["calls"].append(path)
        if "workflows/release.yml/runs" in path:
            return {"workflow_runs": [state["run"]] if state["run"] else []}
        return {"total_count": state.get("total", len(state["jobs"])), "jobs": state["jobs"]}

    monkeypatch.setattr(operations, "_gh", gh)
    return state


def collect():
    return collect_release_pipeline(Path("."), "owner/service", ["a" * 40])


def test_rag_promotion_failure_is_bound_to_exact_rerun(pipeline):
    result = collect()
    assert result["field"]["value"]["attempt"] == 6
    assert result["jobs"][0]["failed_steps"] == ["Create or reuse exact infrastructure PR"]
    assert result["blocker"]["stage"] == "infrastructure_promotion"
    assert result["blocker"]["action"] == (
        "Inspect gh run view 73 --repo owner/service --attempt 6 --job 102 --log-failed."
    )
    assert pipeline["calls"][1].endswith("/runs/73/attempts/6/jobs?per_page=100")
    assert len(pipeline["calls"]) == 2


@pytest.mark.parametrize(
    "name,stage",
    [("release / Publish", "release"), ("deploy-dev / Deploy", "deployment")],
)
def test_failure_phase(pipeline, name, stage):
    pipeline["jobs"][1]["name"] = name
    assert collect()["blocker"]["stage"] == stage


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "action_required"])
def test_unsuccessful_attempts(pipeline, conclusion):
    pipeline["run"]["conclusion"] = conclusion
    pipeline["jobs"][1]["conclusion"] = conclusion
    assert collect()["blocker"]["stage"] == "infrastructure_promotion"


def test_success_does_not_claim_runtime_or_artifact_evidence(pipeline):
    pipeline["run"]["conclusion"] = "success"
    pipeline["jobs"][1]["conclusion"] = "success"
    result = collect()
    assert result["blocker"] is None
    assert result["jobs"] == []
    assert set(result["field"]["value"]) == {
        "run_id",
        "attempt",
        "revision",
        "status",
        "conclusion",
    }


def test_pending_promotion_returns_a_single_wait_action(pipeline):
    pipeline["run"].update(status="in_progress", conclusion=None)
    pipeline["jobs"][1].update(status="queued", conclusion=None, steps=[])
    result = collect()
    assert result["blocker"]["stage"] == "infrastructure_promotion"
    assert "refresh delivery status after it completes" in result["blocker"]["action"]


def test_skipped_dependent_job_does_not_mask_earlier_failure(pipeline):
    pipeline["jobs"][0]["conclusion"] = "failure"
    pipeline["jobs"][1]["conclusion"] = "skipped"
    assert collect()["blocker"]["stage"] == "release"


@pytest.mark.parametrize(
    "field,value",
    [("head_sha", "b" * 40), ("id", True), ("run_attempt", 0), ("status", []), ("conclusion", {})],
)
def test_unrelated_or_malformed_run_is_rejected(pipeline, field, value):
    pipeline["run"][field] = value
    result = collect()
    assert result["field"]["state"] == "unavailable"
    assert result["blocker"]["stage"] == "release"
    assert len(pipeline["calls"]) == 1


@pytest.mark.parametrize("field,value", [("run_id", 74), ("run_attempt", 5), ("steps", None)])
def test_jobs_from_another_run_or_attempt_are_rejected(pipeline, field, value):
    pipeline["jobs"][1][field] = value
    result = collect()
    assert result["field"]["state"] == "available"
    assert result["blocker"]["reason"] == "Release attempt contains an invalid job."


def test_job_pagination_is_explicit_and_never_infers_success(pipeline):
    pipeline["run"]["conclusion"] = "success"
    pipeline["jobs"][1]["conclusion"] = "success"
    pipeline["total"] = 101
    result = collect()
    assert result["jobs_truncated"] is True
    assert "incomplete" in result["blocker"]["reason"]


def test_missing_run_and_verified_release_revision_fallback(pipeline, monkeypatch):
    def gh(_root, path):
        pipeline["calls"].append(path)
        if "head_sha=" + "b" * 40 in path:
            return {"workflow_runs": [{**pipeline["run"], "head_sha": "b" * 40}]}
        if "workflows/release.yml" in path:
            return {"workflow_runs": []}
        return {"total_count": len(pipeline["jobs"]), "jobs": pipeline["jobs"]}

    monkeypatch.setattr(operations, "_gh", gh)
    assert collect()["field"]["state"] == "unavailable"
    result = collect_release_pipeline(Path("."), "owner/service", ["a" * 40, "b" * 40])
    assert result["field"]["value"]["revision"] == "b" * 40
    assert result["blocker"]["stage"] == "infrastructure_promotion"


def test_inaccessible_jobs_preserve_verified_run(pipeline, monkeypatch):
    def gh(_root, path):
        if "workflows/release.yml" in path:
            return {"workflow_runs": [pipeline["run"]]}
        raise operations.OperationsError("UNAVAILABLE", "GitHub API request failed.")

    monkeypatch.setattr(operations, "_gh", gh)
    result = collect()
    assert result["field"]["state"] == "available"
    assert result["blocker"]["reason"] == "GitHub API request failed."


def test_empty_duplicate_and_invalid_job_responses(pipeline):
    pipeline["jobs"] = []
    assert "incomplete" in collect()["blocker"]["reason"]
    pipeline["jobs"] = [{"id": 1}]
    assert "invalid job" in collect()["blocker"]["reason"]


def test_failed_step_output_is_bounded(pipeline):
    pipeline["jobs"][1]["steps"] *= 20
    result = collect()
    assert len(result["jobs"][0]["failed_steps"]) == 3
    assert result["jobs"][0]["steps_truncated"] is True


@pytest.mark.parametrize("response", [[], {}, {"workflow_runs": [{}, {}]}])
def test_invalid_release_discovery_response(pipeline, monkeypatch, response):
    monkeypatch.setattr(operations, "_gh", lambda *_args: response)
    assert collect()["blocker"]["reason"] == "Release workflow response is invalid."


@pytest.mark.parametrize("total", [True, -1, None])
def test_invalid_attempt_job_count(pipeline, total):
    pipeline["total"] = total
    assert collect()["blocker"]["reason"] == "Release attempt jobs response is invalid."


def test_duplicate_job_ids_and_unknown_job_state(pipeline):
    pipeline["jobs"][1]["id"] = pipeline["jobs"][0]["id"]
    assert collect()["blocker"]["reason"] == "Release attempt contains an invalid job."
    pipeline["jobs"][1]["id"] = 103
    pipeline["jobs"][1]["status"] = []
    assert collect()["blocker"]["reason"] == "Release attempt contains an invalid job."


def test_neutral_attempt_is_not_success(pipeline):
    pipeline["run"]["conclusion"] = "neutral"
    pipeline["jobs"][1]["conclusion"] = "neutral"
    assert collect()["blocker"]["reason"] == "Release attempt has not completed successfully."


def test_returned_unsuccessful_jobs_are_bounded(pipeline):
    job = pipeline["jobs"][1]
    pipeline["jobs"] = [{**job, "id": index + 200} for index in range(21)]
    result = collect()
    assert len(result["jobs"]) == 5
    assert result["jobs_truncated"] is True

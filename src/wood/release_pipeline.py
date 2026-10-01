"""Revision-bound, bounded inspection of the current release workflow attempt."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from . import operations
from .delivery import _available, _missing

_PENDING = {"queued", "in_progress", "waiting", "pending", "requested"}
_FAILED = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
_CONCLUSIONS = _FAILED | {"success", "neutral", "skipped", "stale"}
_MAX_JOBS = 5
_MAX_STEPS = 3
_MAX_NAME = 160


def _valid_state(document: dict[str, Any]) -> bool:
    status, conclusion = document.get("status"), document.get("conclusion")
    return (
        isinstance(status, str)
        and status in _PENDING | {"completed"}
        and (
            isinstance(conclusion, str) and conclusion in _CONCLUSIONS
            if status == "completed"
            else conclusion is None
        )
    )


def _stage(name: str) -> str:
    if re.search(r"\bpromot(?:ion|e)\b", name, re.IGNORECASE):
        return "infrastructure_promotion"
    if re.search(r"\bdeploy(?:ment)?\b", name, re.IGNORECASE):
        return "deployment"
    return "release"


def collect_release_pipeline(root: Path, slug: str, revisions: list[str]) -> dict[str, Any]:
    """Observe one exact run/attempt; Actions success is not a runtime attestation."""
    result: dict[str, Any] = {
        "field": _missing("No revision-bound release workflow run is available."),
        "jobs": [],
        "jobs_truncated": False,
        "blocker": None,
    }

    def stop(stage: str, reason: str, action: str) -> dict[str, Any]:
        result["blocker"] = {"stage": stage, "reason": reason, "action": action}
        return result

    try:
        run = None
        for revision in dict.fromkeys(revisions):
            data = operations._gh(
                root,
                f"repos/{slug}/actions/workflows/release.yml/runs?"
                + urlencode({"head_sha": revision, "per_page": 1}),
            )
            runs = data.get("workflow_runs") if isinstance(data, dict) else None
            if not isinstance(runs, list) or len(runs) > 1:
                raise operations.OperationsError(
                    "RELEASE_EVIDENCE_INVALID", "Release workflow response is invalid."
                )
            if not runs:
                continue
            candidate = runs[0]
            if (
                not isinstance(candidate, dict)
                or candidate.get("head_sha") != revision
                or type(candidate.get("id")) is not int
                or candidate["id"] <= 0
                or type(candidate.get("run_attempt")) is not int
                or candidate["run_attempt"] <= 0
                or not _valid_state(candidate)
            ):
                raise operations.OperationsError(
                    "RELEASE_EVIDENCE_INVALID",
                    "Release run is not bound to the requested revision.",
                )
            run = candidate
            break
        if run is None:
            return result
        run_id, attempt = run["id"], run["run_attempt"]
        url = f"https://github.com/{slug}/actions/runs/{run_id}/attempts/{attempt}"
        result["field"] = _available(
            {
                "run_id": run_id,
                "attempt": attempt,
                "revision": run["head_sha"],
                "status": run["status"],
                "conclusion": run.get("conclusion"),
            },
            url,
        )
        data = operations._gh(
            root, f"repos/{slug}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        jobs = data.get("jobs") if isinstance(data, dict) else None
        total = data.get("total_count") if isinstance(data, dict) else None
        if (
            not isinstance(jobs, list)
            or type(total) is not int
            or total < len(jobs)
            or len(jobs) > 100
        ):
            raise operations.OperationsError(
                "RELEASE_EVIDENCE_INVALID", "Release attempt jobs response is invalid."
            )
        relevant = []
        job_ids = set()
        for job in jobs:
            if (
                not isinstance(job, dict)
                or type(job.get("id")) is not int
                or job["id"] <= 0
                or job["id"] in job_ids
                or type(job.get("run_id")) is not int
                or job["run_id"] != run_id
                or (
                    "run_attempt" in job
                    and (type(job["run_attempt"]) is not int or job["run_attempt"] != attempt)
                )
                or not isinstance(job.get("name"), str)
                or not isinstance(job.get("steps"), list)
                or not _valid_state(job)
            ):
                raise operations.OperationsError(
                    "RELEASE_EVIDENCE_INVALID", "Release attempt contains an invalid job."
                )
            job_ids.add(job["id"])
            conclusion = job.get("conclusion")
            if job["status"] != "completed" or conclusion != "success":
                steps = [
                    step["name"][:_MAX_NAME]
                    for step in job["steps"]
                    if isinstance(step, dict)
                    and isinstance(step.get("conclusion"), str)
                    and step.get("conclusion") in _FAILED
                    and isinstance(step.get("name"), str)
                ]
                relevant.append(
                    {
                        "job_id": job["id"],
                        "name": job["name"][:_MAX_NAME],
                        "stage": _stage(job["name"]),
                        "status": job["status"],
                        "conclusion": conclusion if isinstance(conclusion, str) else None,
                        "failed_steps": steps[:_MAX_STEPS],
                        "steps_truncated": len(steps) > _MAX_STEPS,
                        "url": f"https://github.com/{slug}/actions/runs/{run_id}/job/{job['id']}",
                    }
                )
        relevant.sort(key=lambda job: job["job_id"])
        result["jobs"] = relevant[:_MAX_JOBS]
        result["jobs_truncated"] = total > len(jobs) or len(relevant) > _MAX_JOBS
        failures = [job for job in relevant if job["conclusion"] in _FAILED]
        if failures:
            failure = failures[0]
            return stop(
                failure["stage"],
                f"Release attempt {attempt} job {failure['job_id']} concluded "
                f"{failure['conclusion']}.",
                f"Inspect gh run view {run_id} --repo {slug} "
                f"--attempt {attempt} --job {failure['job_id']} --log-failed.",
            )
        if result["jobs_truncated"] or not jobs:
            return stop(
                "release",
                "Release attempt jobs evidence is incomplete.",
                f"Inspect all jobs for release run {run_id}, attempt {attempt}, at {url}.",
            )
        if run["status"] in _PENDING or any(job["status"] in _PENDING for job in relevant):
            pending = next((job for job in relevant if job["status"] in _PENDING), None)
            return stop(
                pending["stage"] if pending else "release",
                f"Release run {run_id}, attempt {attempt}, is pending.",
                f"Await release run {run_id}, attempt {attempt}, at {url}; "
                "refresh delivery status after it completes.",
            )
        if run.get("conclusion") != "success" or any(
            job["conclusion"] not in {"success", "skipped"} for job in relevant
        ):
            return stop(
                "release",
                "Release attempt has not completed successfully.",
                f"Inspect gh run view {run_id} --repo {slug} --attempt {attempt}.",
            )
        return result
    except operations.OperationsError as exc:
        if result["field"]["state"] == "unavailable":
            result["field"] = _missing(str(exc))
        return stop(
            "release",
            str(exc),
            "Restore release workflow authority access or repair its response; "
            "then rerun delivery status.",
        )

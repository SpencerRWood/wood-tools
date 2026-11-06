from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wood import operations, reviews


def item(identifier=1, state="APPROVED", revision="a" * 40, user=2):
    return {
        "id": identifier,
        "state": state,
        "commit_id": revision,
        "user": {"id": user},
        "submitted_at": (
            datetime(2026, 10, 19, tzinfo=UTC) + timedelta(seconds=identifier)
        ).isoformat(),
    }


def observe(monkeypatch, values):
    monkeypatch.setattr(operations, "_gh", lambda *_: values)
    return reviews.collect_review(
        Path("."), "owner/repo", 7, {"user": {"id": 1}, "head": {"sha": "a" * 40}}
    )


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], "unreviewed"),
        ([item()], "approved"),
        ([item(user=1)], "unreviewed"),
        ([item(revision="b" * 40)], "unreviewed"),
        ([item(), item(2, "CHANGES_REQUESTED")], "changes_requested"),
        ([item(1, "CHANGES_REQUESTED"), item(2)], "approved"),
        ([item(), item(2, "COMMENTED")], "approved"),
        ([item(), item(2, "DISMISSED")], "unreviewed"),
    ],
)
def test_current_independent_review_and_latest_action(monkeypatch, values, expected):
    result = observe(monkeypatch, values)
    assert result["value"]["state"] == expected
    assert result["source"].startswith("https://github.com/owner/repo/pull/7")


@pytest.mark.parametrize(
    "values", [{}, [None], [item(), item()], [{"id": True}], [item(state="UNKNOWN")]]
)
def test_malformed_collections_fail_closed(monkeypatch, values):
    assert observe(monkeypatch, values)["state"] == "unavailable"


def test_latest_submitted_review_wins_over_creation_id(monkeypatch):
    later_request = {**item(1, "CHANGES_REQUESTED"), "submitted_at": "2026-10-20T00:00:00Z"}
    assert observe(monkeypatch, [item(2), later_request])["value"]["state"] == "changes_requested"
    assert observe(monkeypatch, [{**item(), "submitted_at": None}])["state"] == "unavailable"


def test_pagination_and_authority_failure(monkeypatch):
    pages = [[item(i, "COMMENTED") for i in range(1, 101)], [item(101)]]
    monkeypatch.setattr(operations, "_gh", lambda *_: pages.pop(0))
    result = reviews.collect_review(
        Path("."), "owner/repo", 7, {"user": {"id": 1}, "head": {"sha": "a" * 40}}
    )
    assert result["value"]["approval_ids"] == [101]

    def fail(*_):
        raise operations.OperationsError("GITHUB_UNAVAILABLE", "private response")

    monkeypatch.setattr(operations, "_gh", fail)
    result = reviews.collect_review(
        Path("."), "owner/repo", 7, {"user": {"id": 1}, "head": {"sha": "a" * 40}}
    )
    assert result["state"] == "unavailable"
    assert "private response" not in str(result)


def test_operator_approval_is_explicit_and_does_not_override_change_requests(monkeypatch):
    pr = {"user": {"id": 1}, "head": {"sha": "a" * 40}}
    approval = {
        "reference": "User approved reviewed source in session 2026-10-19",
        "revision": "a" * 40,
    }
    monkeypatch.setattr(operations, "_gh", lambda *_: [])
    result = reviews.collect_review(Path("."), "owner/repo", 7, pr, operator_approval=approval)
    assert result["value"]["basis"] == "operator_attestation"
    assert result["value"]["state"] == "approved"
    assert "cannot verify" in result["value"]["limitation"]
    result = reviews.collect_review(
        Path("."), "owner/repo", 7, pr, operator_approval={**approval, "revision": "b" * 40}
    )
    assert result["state"] == "unavailable"
    monkeypatch.setattr(operations, "_gh", lambda *_: [item(state="CHANGES_REQUESTED")])
    result = reviews.collect_review(Path("."), "owner/repo", 7, pr, operator_approval=approval)
    assert result["value"]["state"] == "changes_requested"


def test_review_bound_exhaustion_and_missing_author(monkeypatch):
    count = 0

    def page(*_):
        nonlocal count
        values = [item(count * 100 + index, "COMMENTED") for index in range(1, 101)]
        count += 1
        return values

    monkeypatch.setattr(operations, "_gh", page)
    pr = {"user": {"id": 1}, "head": {"sha": "a" * 40}}
    assert reviews.collect_review(Path("."), "owner/repo", 7, pr)["state"] == "unavailable"
    assert count == 10
    assert (
        reviews.collect_review(Path("."), "owner/repo", 7, {"user": None})["state"] == "unavailable"
    )


def self_review_case(monkeypatch, *, values=None, **kwargs):
    monkeypatch.setattr(operations, "_gh", lambda *_: values or [])
    return reviews.collect_review(
        Path("."),
        "owner/repo",
        7,
        kwargs.pop(
            "pr",
            {
                "user": {"id": 1},
                "head": {"sha": "a" * 40},
                "base": {"ref": "development"},
                "merged": True,
            },
        ),
        self_review=kwargs.pop(
            "self_review",
            {
                "reference": "PR7 source-bound agent review",
                "authorization": "User explicitly authorized autonomous self-review",
                "revision": "a" * 40,
            },
        ),
        integration_branch=kwargs.pop("integration_branch", "development"),
        **kwargs,
    )


def test_authorized_self_review_is_distinct_from_human_approval(monkeypatch):
    result = self_review_case(monkeypatch)
    assert result["value"]["state"] == "self_reviewed"
    assert result["value"]["basis"] == "authorized_self_review"
    assert result["value"]["approval_ids"] == []
    assert "no independent human approval" in result["value"]["limitation"]
    assert reviews.review_satisfied(result)


@pytest.mark.parametrize(
    "change",
    [
        {"authorization": ""},
        {"authorization": None},
        {"authorization": "x\ny"},
        {"reference": " "},
        {"reference": "x" * 501},
        {"revision": "b" * 40},
        {"extra": "unexpected"},
    ],
)
def test_partial_stale_and_malformed_self_review_fail_closed(monkeypatch, change):
    value = {"reference": "review", "authorization": "user authorization", "revision": "a" * 40}
    assert self_review_case(monkeypatch, self_review={**value, **change})["state"] == "unavailable"


@pytest.mark.parametrize(
    "branch,merged,target",
    [
        ("main", True, "main"),
        ("development", False, "development"),
        ("development", True, None),
        ("release/v1", True, "development"),
    ],
)
def test_self_review_cannot_authorize_merging_or_other_targets(monkeypatch, branch, merged, target):
    pr = {"user": {"id": 1}, "head": {"sha": "a" * 40}, "base": {"ref": branch}, "merged": merged}
    assert self_review_case(monkeypatch, pr=pr, integration_branch=target)["state"] == "unavailable"


def test_self_review_keeps_review_authority_and_changes_requests(monkeypatch):
    requested = self_review_case(monkeypatch, values=[item(state="CHANGES_REQUESTED")])
    assert requested["value"]["state"] == "changes_requested"
    assert not reviews.review_satisfied(requested)
    approved = self_review_case(monkeypatch, values=[item()])
    assert approved["value"]["basis"] == "github_reviews"
    mixed = self_review_case(
        monkeypatch, operator_approval={"reference": "human", "revision": "a" * 40}
    )
    assert mixed["state"] == "unavailable"

    def unavailable(*_):
        raise operations.OperationsError("GITHUB_UNAVAILABLE", "private diagnostic")

    monkeypatch.setattr(operations, "_gh", unavailable)
    result = reviews.collect_review(
        Path("."),
        "owner/repo",
        7,
        {
            "user": {"id": 1},
            "head": {"sha": "a" * 40},
            "base": {"ref": "development"},
            "merged": True,
        },
        self_review={"reference": "review", "authorization": "user", "revision": "a" * 40},
        integration_branch="development",
    )
    assert result["state"] == "unavailable"
    assert not reviews.review_satisfied(result)
    assert "private diagnostic" not in str(result)


@pytest.mark.parametrize(
    "plan",
    [
        {"strategy": "trunk", "target_branch": "main"},
        {"adopted": True, "strategy": "release", "target_branch": "release/v1"},
        {"adopted": True, "strategy": "integration", "mode": "hotfix", "target_branch": "main"},
        {"strategy": "integration", "target_branch": "development"},
    ],
)
def test_nonintegration_or_unadopted_scope_cannot_enable_self_review(plan):
    assert reviews.integration_target(plan) is None

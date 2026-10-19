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

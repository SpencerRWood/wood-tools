"""Bounded, revision-bound GitHub review observations shared by delivery consumers."""

from datetime import datetime
from pathlib import Path
from typing import Any

from . import operations
from .delivery import _available, _missing


def integration_target(plan: dict[str, Any]) -> str | None:
    """Self-review evidence applies only to adopted ordinary integration Stories."""
    target = plan.get("target_branch")
    return (
        target
        if plan.get("adopted") is True
        and plan.get("strategy") == "integration"
        and plan.get("mode") != "hotfix"
        and isinstance(target, str)
        and target != "main"
        else None
    )


def review_satisfied(review: dict[str, Any]) -> bool:
    """A self-review satisfies its own criterion without becoming human approval."""
    if review.get("state") != "available":
        return False
    value = review.get("value")
    if not isinstance(value, dict):
        return False
    return bool(
        value.get("state") == "approved"
        or (
            value.get("state") == "self_reviewed" and value.get("basis") == "authorized_self_review"
        )
    )


def collect_review(
    root: Path,
    slug: str,
    number: int,
    pr: dict[str, Any],
    *,
    operator_approval: dict[str, str] | None = None,
    self_review: dict[str, str] | None = None,
    integration_branch: str | None = None,
) -> dict[str, Any]:
    source = f"https://github.com/{slug}/pull/{number}#pullrequestreview"
    user, head = pr.get("user"), pr.get("head")
    author = user.get("id") if isinstance(user, dict) else None
    revision = head.get("sha") if isinstance(head, dict) else None
    if type(author) is not int or not isinstance(revision, str):
        return _missing("PR author or source revision is unavailable.")
    if self_review is not None:
        base = pr.get("base")
        if (
            not isinstance(self_review, dict)
            or operator_approval is not None
            or set(self_review) != {"reference", "authorization", "revision"}
            or self_review.get("revision") != revision
            or any(
                not isinstance(self_review.get(key), str)
                or not self_review[key].strip()
                or len(self_review[key]) > 500
                or any(character in self_review[key] for character in "\r\n")
                for key in ("reference", "authorization")
            )
        ):
            return _missing(
                "Self-review requires separate review and authorization references bound to "
                "the source revision; do not combine it with human approval."
            )
        if (
            integration_branch is None
            or integration_branch == "main"
            or pr.get("merged") is not True
            or not isinstance(base, dict)
            or base.get("ref") != integration_branch
        ):
            return _missing(
                "Authorized self-review evidence requires a merged ordinary integration Story; "
                "it cannot authorize a merge, release or hotfix."
            )
    if operator_approval is not None and (
        set(operator_approval) != {"reference", "revision"}
        or operator_approval.get("revision") != revision
        or not isinstance(operator_approval.get("reference"), str)
        or not operator_approval["reference"].strip()
        or len(operator_approval["reference"]) > 500
        or any(character in operator_approval["reference"] for character in "\r\n")
    ):
        return _missing("Operator approval must bind a reference to the PR source revision.")
    latest: dict[int, dict[str, Any]] = {}
    seen: set[int] = set()
    try:
        for page in range(1, 11):
            reviews = operations._gh(
                root, f"repos/{slug}/pulls/{number}/reviews?per_page=100&page={page}"
            )
            if not isinstance(reviews, list) or len(reviews) > 100:
                return _missing("GitHub review collection is invalid.")
            for item in reviews:
                if not isinstance(item, dict):
                    return _missing("GitHub review entry is invalid.")
                identifier, user, state = item.get("id"), item.get("user"), item.get("state")
                if (
                    type(identifier) is not int
                    or identifier <= 0
                    or identifier in seen
                    or not isinstance(user, dict)
                    or type(user.get("id")) is not int
                    or state
                    not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED", "PENDING"}
                ):
                    return _missing("GitHub review entry is invalid or duplicated.")
                seen.add(identifier)
                if user["id"] == author or state in {"COMMENTED", "PENDING"}:
                    continue
                try:
                    submitted = datetime.fromisoformat(item["submitted_at"])
                    if submitted.tzinfo is None:
                        raise ValueError
                except KeyError, TypeError, ValueError:
                    return _missing("GitHub review submission time is unavailable.")
                previous = latest.get(user["id"])
                if previous is None or (submitted, identifier) > (
                    datetime.fromisoformat(previous["submitted_at"]),
                    previous["id"],
                ):
                    latest[user["id"]] = item
            if len(reviews) < 100:
                break
        else:
            return _missing(
                "Review collection exceeds the supported bound; approval is unverified."
            )
    except operations.OperationsError:
        return _missing("GitHub review evidence is unavailable.")
    approved = sorted(
        item["id"]
        for item in latest.values()
        if item["state"] == "APPROVED" and item.get("commit_id") == revision
    )
    changes = sorted(item["id"] for item in latest.values() if item["state"] == "CHANGES_REQUESTED")
    if not approved and not changes and self_review is not None:
        return _available(
            {
                "state": "self_reviewed",
                "revision": revision,
                "approval_ids": [],
                "change_request_ids": [],
                "basis": "authorized_self_review",
                "self_review": self_review,
                "limitation": "Operator attested authorized agent self-review; no independent "
                "human approval is asserted. Wood cannot verify the authorization reference.",
            },
            source,
        )
    if not approved and not changes and operator_approval is not None:
        return _available(
            {
                "state": "approved",
                "revision": revision,
                "approval_ids": [],
                "change_request_ids": [],
                "basis": "operator_attestation",
                "operator_approval": operator_approval,
                "limitation": "Operator asserted human approval; Wood cannot verify the reference.",
            },
            source,
        )
    return _available(
        {
            "state": "changes_requested" if changes else "approved" if approved else "unreviewed",
            "revision": revision,
            "approval_ids": approved,
            "change_request_ids": changes,
            "basis": "github_reviews",
        },
        source,
    )

"""Verified, retry-safe Story activity posting."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from wood_project.openproject import (
    OpenProjectClient,
    OpenProjectError,
    embedded_elements,
    link_href,
)

from .models import StoryWorkflowError
from .openproject import extract_id_from_href, work_package_type_name

MAX_COMMENT_BYTES = 4096
PAGE_SIZE = 100
UPDATE_HEADING = re.compile(r"^#{0,6}\s*Implementation update \(WP-(\d+)\)\s*$")
SUMMARY_HEADING = re.compile(r"^#{0,6}\s*Final summary \(WP-(\d+)\)\s*$")


def comment_hash(comment: str) -> str:
    return hashlib.sha256(comment.encode("utf-8")).hexdigest()


def _summary_heading(comment: str) -> re.Match[str] | None:
    first = comment.splitlines()[0] if comment.splitlines() else ""
    return UPDATE_HEADING.fullmatch(first) or SUMMARY_HEADING.fullmatch(first)


def read_comment(path: Path) -> str:
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_COMMENT_BYTES + 1)
    except OSError as exc:
        raise StoryWorkflowError("INVALID_COMMENT", "Comment file must be readable.") from exc
    if not raw or len(raw) > MAX_COMMENT_BYTES:
        raise StoryWorkflowError(
            "INVALID_COMMENT", f"Comment must contain 1 to {MAX_COMMENT_BYTES} UTF-8 bytes."
        )
    try:
        comment = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StoryWorkflowError("INVALID_COMMENT", "Comment file must be UTF-8.") from exc
    if not comment.strip():
        raise StoryWorkflowError("INVALID_COMMENT", "Comment must not be blank.")
    return comment


def _request(
    client: OpenProjectClient,
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return client.request_json(method, path, query=query, body=body)
    except OpenProjectError as exc:
        raise StoryWorkflowError(
            "ACTIVITY_UNAVAILABLE", "OpenProject activity request failed."
        ) from exc


def _activity_comment(activity: dict[str, Any]) -> str | None:
    value = activity.get("comment")
    if isinstance(value, dict) and isinstance(value.get("raw"), str):
        return value["raw"]
    return None


def _verify_activity(activity: dict[str, Any], story_id: int, comment: str) -> int:
    activity_id = activity.get("id")
    owner = extract_id_from_href(link_href(activity, "workPackage"), "work_packages")
    if (
        not isinstance(activity_id, int)
        or owner != story_id
        or _activity_comment(activity) != comment
    ):
        raise StoryWorkflowError(
            "ACTIVITY_READBACK_FAILED", "Activity readback did not match the Story and comment."
        )
    return activity_id


def _activities(client: OpenProjectClient, story_id: int) -> list[dict[str, Any]]:
    path = f"/api/v3/work_packages/{story_id}/activities"
    offset = 1
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    total = None
    while True:
        page = _request(
            client,
            "GET",
            path,
            query={"pageSize": str(PAGE_SIZE), "offset": str(offset)},
        )
        elements = embedded_elements(page)
        if (
            type(page.get("total")) is not int
            or not 0 <= page["total"] <= 10000
            or (total is not None and total != page["total"])
        ):
            raise StoryWorkflowError(
                "ACTIVITY_UNAVAILABLE", "OpenProject activity page is invalid."
            )
        total = page["total"]
        for activity in elements:
            activity_id = activity.get("id")
            owner = extract_id_from_href(link_href(activity, "workPackage"), "work_packages")
            if type(activity_id) is not int or activity_id in seen or owner != story_id:
                raise StoryWorkflowError(
                    "ACTIVITY_UNAVAILABLE", "OpenProject activity collection is invalid."
                )
            seen.add(activity_id)
            result.append(activity)
        offset += 1
        if len(result) > total:
            raise StoryWorkflowError("ACTIVITY_UNAVAILABLE", "Activity total is inconsistent.")
        if len(result) == total:
            return result
        if not elements:
            raise StoryWorkflowError(
                "ACTIVITY_UNAVAILABLE", "OpenProject activity pagination stopped early."
            )


def _summaries(activities: list[dict[str, Any]], story_id: int) -> list[dict[str, Any]]:
    return [
        activity
        for activity in activities
        if (match := _summary_heading(_activity_comment(activity) or ""))
        and int(match.group(1)) == story_id
    ]


def _existing_update(client: OpenProjectClient, story_id: int, comment: str) -> int | None:
    summaries = _summaries(_activities(client, story_id), story_id)
    if len(summaries) > 1 or any(_activity_comment(item) != comment for item in summaries):
        raise StoryWorkflowError(
            "ACTIVITY_CONFLICT", f"WP-{story_id} already has a different or ambiguous summary."
        )
    return summaries[0]["id"] if summaries else None


def require_summary(client: OpenProjectClient, story_id: int, comment: str) -> None:
    activity_id = _existing_update(client, story_id, comment)
    if activity_id is None:
        raise StoryWorkflowError(
            "VALIDATION_REQUIRED", "Post the verified implementation summary first."
        )
    _verify_activity(
        _request(client, "GET", f"/api/v3/activities/{activity_id}"), story_id, comment
    )


def inspect_activities(
    client: OpenProjectClient, story_id: int, *, offset: int = 0
) -> dict[str, Any]:
    if offset < 0:
        raise StoryWorkflowError("INVALID_OFFSET", "Offset must be nonnegative.")
    story = _request(client, "GET", f"/api/v3/work_packages/{story_id}")
    if work_package_type_name(story) != "Story":
        raise StoryWorkflowError("NOT_A_STORY", f"WP-{story_id} is not a Story.")
    activities = _activities(client, story_id)
    summary_ids = [item["id"] for item in _summaries(activities, story_id)]
    items = []
    for activity in activities[offset : offset + 20]:
        comment = _activity_comment(activity) or ""
        items.append(
            {
                "id": activity["id"],
                "role": "implementation_summary"
                if activity["id"] in summary_ids
                else "progress"
                if comment
                else "system",
                "comment": comment[:500],
                "comment_truncated": len(comment) > 500,
                "sha256": comment_hash(comment),
                "created_at": activity.get("createdAt"),
            }
        )
    return {
        "story_id": story_id,
        "activities": items,
        "total": len(activities),
        "offset": offset,
        "next_offset": offset + 20 if offset + 20 < len(activities) else None,
        "summary_activity_id": summary_ids[0] if len(summary_ids) == 1 else None,
        "summary_count": len(summary_ids),
        "summary_ambiguous": len(summary_ids) > 1,
    }


def upsert_summary(
    client: OpenProjectClient,
    story_id: int,
    comment: str,
    *,
    expected_sha256: str,
    apply: bool,
) -> dict[str, Any]:
    heading = _summary_heading(comment)
    if heading is None or int(heading.group(1)) != story_id:
        raise StoryWorkflowError("INVALID_COMMENT", "Summary needs a heading for this Story.")
    if expected_sha256 != "absent" and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise StoryWorkflowError("INVALID_COMMENT", "Expected SHA must be a hash or 'absent'.")
    inspection = inspect_activities(client, story_id)
    if inspection["summary_ambiguous"]:
        raise StoryWorkflowError("ACTIVITY_CONFLICT", "Multiple summary activities exist.")
    activity_id = inspection["summary_activity_id"]
    if activity_id is None:
        if expected_sha256 != "absent":
            raise StoryWorkflowError("ACTIVITY_CONFLICT", "Expected summary no longer exists.")
        return {"action": "create", **add_activity(client, story_id, comment, apply=apply)}
    path = f"/api/v3/activities/{activity_id}"
    activity = _request(client, "GET", path)
    existing = _activity_comment(activity)
    if existing is None or _summary_heading(existing) is None:
        raise StoryWorkflowError("ACTIVITY_CONFLICT", "Summary changed during inspection.")
    _verify_activity(activity, story_id, existing)
    if existing == comment:
        return {
            "action": "reuse",
            "story_id": story_id,
            "activity_id": activity_id,
            "reused": True,
            "dry_run": not apply,
        }
    if comment_hash(existing) != expected_sha256:
        raise StoryWorkflowError("ACTIVITY_CONFLICT", "Summary changed; inspect before retrying.")
    if link_href(activity, "update") != path:
        raise StoryWorkflowError("ACTIVITY_NOT_EDITABLE", "Summary cannot be edited by this user.")
    result = {
        "action": "update",
        "story_id": story_id,
        "activity_id": activity_id,
        "expected_sha256": expected_sha256,
        "sha256": comment_hash(comment),
        "dry_run": not apply,
        "reused": False,
    }
    if not apply:
        return {**result, "comment": comment}
    # The API has no comment CAS/lockVersion. Recheck immediately before PATCH.
    fresh = _request(client, "GET", path)
    _verify_activity(fresh, story_id, existing)
    _request(client, "PATCH", path, body={"comment": {"raw": comment}})
    _verify_activity(_request(client, "GET", path), story_id, comment)
    return result


def add_activity(
    client: OpenProjectClient, story_id: int, comment: str, *, apply: bool
) -> dict[str, Any]:
    story = _request(client, "GET", f"/api/v3/work_packages/{story_id}")
    if work_package_type_name(story) != "Story":
        raise StoryWorkflowError("NOT_A_STORY", f"WP-{story_id} is not a Story.")
    heading = _summary_heading(comment)
    if heading and int(heading.group(1)) != story_id:
        raise StoryWorkflowError(
            "INVALID_COMMENT", "Implementation update heading has another WP ID."
        )
    existing_id = _existing_update(client, story_id, comment) if heading else None
    if existing_id is not None:
        activity = _request(client, "GET", f"/api/v3/activities/{existing_id}")
        _verify_activity(activity, story_id, comment)
        return {
            "story_id": story_id,
            "activity_id": existing_id,
            "reused": True,
            "dry_run": not apply,
        }
    if not apply:
        return {"story_id": story_id, "comment": comment, "dry_run": True, "reused": False}
    created = _request(
        client,
        "POST",
        f"/api/v3/work_packages/{story_id}/activities",
        body={"comment": {"raw": comment}},
    )
    activity_id = created.get("id")
    if not isinstance(activity_id, int):
        raise StoryWorkflowError("ACTIVITY_READBACK_FAILED", "Posted activity has no ID.")
    activity = _request(client, "GET", f"/api/v3/activities/{activity_id}")
    _verify_activity(activity, story_id, comment)
    return {"story_id": story_id, "activity_id": activity_id, "reused": False, "dry_run": False}

"""Verified, retry-safe Story activity posting."""

from __future__ import annotations

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


def _existing_update(client: OpenProjectClient, story_id: int, comment: str) -> int | None:
    path = f"/api/v3/work_packages/{story_id}/activities"
    offset = 1
    matching_id = None
    while True:
        page = _request(
            client,
            "GET",
            path,
            query={"pageSize": str(PAGE_SIZE), "offset": str(offset)},
        )
        elements = embedded_elements(page)
        if not isinstance(page.get("total"), int) or page["total"] < 0:
            raise StoryWorkflowError(
                "ACTIVITY_UNAVAILABLE", "OpenProject activity page is invalid."
            )
        for activity in elements:
            existing = _activity_comment(activity)
            if not existing:
                continue
            match = UPDATE_HEADING.fullmatch(existing.splitlines()[0])
            if match is None or int(match.group(1)) != story_id:
                continue
            activity_id = activity.get("id")
            if not isinstance(activity_id, int):
                raise StoryWorkflowError("ACTIVITY_UNAVAILABLE", "OpenProject activity has no ID.")
            if existing != comment:
                raise StoryWorkflowError(
                    "ACTIVITY_CONFLICT",
                    f"WP-{story_id} already has a different implementation update.",
                )
            matching_id = activity_id
        offset += len(elements)
        if offset > page["total"]:
            return matching_id
        if not elements:
            raise StoryWorkflowError(
                "ACTIVITY_UNAVAILABLE", "OpenProject activity pagination stopped early."
            )


def add_activity(
    client: OpenProjectClient, story_id: int, comment: str, *, apply: bool
) -> dict[str, Any]:
    story = _request(client, "GET", f"/api/v3/work_packages/{story_id}")
    if work_package_type_name(story) != "Story":
        raise StoryWorkflowError("NOT_A_STORY", f"WP-{story_id} is not a Story.")
    heading = UPDATE_HEADING.fullmatch(comment.splitlines()[0])
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

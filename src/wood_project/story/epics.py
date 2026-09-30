"""Live Epic child discovery and shared completion semantics."""

from __future__ import annotations

import json
from typing import Any

from wood_project.openproject import OpenProjectClient

from .discovery import fetch_collection
from .openproject import work_package_status_name, work_package_type_name


def completed_status_names(statuses: list[dict[str, Any]]) -> set[str]:
    return {
        str(status.get("name") or "").casefold()
        for status in statuses
        if status.get("isClosed") and str(status.get("name") or "").casefold() != "rejected"
    }


def epic_stories(client: OpenProjectClient, epic_id: int) -> list[dict[str, Any]]:
    filters = [
        {"ancestor": {"operator": "=", "values": [str(epic_id)]}},
        {"status": {"operator": "*", "values": []}},
    ]
    descendants = fetch_collection(
        client,
        "/api/v3/work_packages",
        query={"filters": json.dumps(filters)},
        page_size=100,
        require_total=True,
    )
    return [child for child in descendants if work_package_type_name(child) == "Story"]


def incomplete_stories(stories: list[dict[str, Any]], completed: set[str]) -> list[dict[str, Any]]:
    return [
        child
        for child in stories
        if work_package_status_name(child).casefold() not in completed | {"rejected"}
    ]

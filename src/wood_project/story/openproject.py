from __future__ import annotations

from typing import Any

from wood_project.openproject import (
    OpenProjectClient,
    OpenProjectError,
    link_title,
)
from wood_project.openproject import (
    embedded_elements as embedded_elements,
)
from wood_project.openproject import (
    extract_id_from_href as extract_id_from_href,
)
from wood_project.openproject import (
    link_href as link_href,
)

from .models import StoryWorkflowError


def api_request_json(
    method: str,
    client: OpenProjectClient,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return client.request_json(method, path, query=query, body=body)
    except OpenProjectError as exc:
        raise StoryWorkflowError(exc.code, str(exc)) from exc


def api_get_json(
    client: OpenProjectClient,
    path: str,
    *,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    return api_request_json("GET", client, path, query=query)


def find_named_element(elements: list[dict[str, Any]], expected_name: str) -> dict[str, Any]:
    for element in elements:
        if str(element.get("name") or "") == expected_name:
            return element
    available = sorted(str(element.get("name")) for element in elements if element.get("name"))
    raise StoryWorkflowError(
        "OPENPROJECT_LOOKUP_FAILED",
        f"OpenProject value not found: {expected_name}. Available values: {available}",
    )


def work_package_id(work_package: dict[str, Any]) -> int:
    return int(work_package["id"])


def work_package_status_name(work_package: dict[str, Any]) -> str:
    return link_title(work_package, "status")


def work_package_type_name(work_package: dict[str, Any]) -> str:
    return link_title(work_package, "type")


def work_package_version_name(work_package: dict[str, Any]) -> str:
    return link_title(work_package, "version")


def work_package_description_text(work_package: dict[str, Any]) -> str:
    description = work_package.get("description")
    if isinstance(description, dict) and isinstance(description.get("raw"), str):
        return str(description["raw"])
    if isinstance(description, str):
        return description
    return ""

from __future__ import annotations

import re
from typing import Any
from urllib import parse

from wood_project.openproject import OpenProjectClient, OpenProjectError

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


def embedded_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((document.get("_embedded") or {}).get("elements")) or [])


def find_named_element(elements: list[dict[str, Any]], expected_name: str) -> dict[str, Any]:
    for element in elements:
        if str(element.get("name") or "") == expected_name:
            return element
    available = sorted(str(element.get("name")) for element in elements if element.get("name"))
    raise StoryWorkflowError(
        "OPENPROJECT_LOOKUP_FAILED",
        f"OpenProject value not found: {expected_name}. Available values: {available}",
    )


def link_title(document: dict[str, Any], name: str) -> str:
    return str(((document.get("_links") or {}).get(name) or {}).get("title") or "")


def link_href(document: dict[str, Any], name: str) -> str | None:
    return ((document.get("_links") or {}).get(name) or {}).get("href")


def extract_id_from_href(href: str | None, resource_name: str) -> int | None:
    if not href:
        return None
    parsed_url = parse.urlparse(href)
    match = re.search(rf"/{re.escape(resource_name)}/(\d+)", parsed_url.path)
    return int(match.group(1)) if match else None


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

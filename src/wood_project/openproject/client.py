from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

from .models import OpenProjectError, OpenProjectSettings, Transport


def embedded_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((document.get("_embedded") or {}).get("elements")) or [])


def link_href(document: dict[str, Any], name: str) -> str | None:
    link = (document.get("_links") or {}).get(name)
    if isinstance(link, dict):
        href = link.get("href")
        return href if isinstance(href, str) else None
    return None


def link_title(document: dict[str, Any], name: str) -> str:
    link = (document.get("_links") or {}).get(name)
    if isinstance(link, dict):
        title = link.get("title")
        return title if isinstance(title, str) else ""
    return ""


def extract_id_from_href(href: str | None, resource_name: str) -> int | None:
    if not href:
        return None
    parsed_url = parse.urlparse(href)
    match = re.search(rf"/{re.escape(resource_name)}/(\d+)", parsed_url.path)
    return int(match.group(1)) if match else None


def _id_from_link(document: dict[str, Any], name: str, resource_name: str) -> int | None:
    return extract_id_from_href(link_href(document, name), resource_name)


def summarize_user(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": document.get("id"),
        "login": document.get("login"),
        "name": document.get("name"),
        "email": document.get("email"),
        "status": document.get("status"),
    }


def summarize_project(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": document.get("id"),
        "identifier": document.get("identifier"),
        "name": document.get("name"),
        "active": document.get("active"),
        "public": document.get("public"),
        "created_at": document.get("createdAt"),
        "updated_at": document.get("updatedAt"),
    }


def summarize_description(document: dict[str, Any]) -> dict[str, Any]:
    description = document.get("description")
    if isinstance(description, dict):
        raw = description.get("raw")
        return {
            "format": description.get("format"),
            "raw": raw if isinstance(raw, str) else "",
        }
    if isinstance(description, str):
        return {"format": None, "raw": description}
    return {"format": None, "raw": ""}


def summarize_work_package(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": document.get("id"),
        "subject": document.get("subject"),
        "description": summarize_description(document),
        "type": link_title(document, "type"),
        "status": link_title(document, "status"),
        "priority": link_title(document, "priority"),
        "project": {
            "id": _id_from_link(document, "project", "projects"),
            "title": link_title(document, "project"),
        },
        "version": {
            "id": _id_from_link(document, "version", "versions"),
            "title": link_title(document, "version"),
        },
        "parent": {
            "id": _id_from_link(document, "parent", "work_packages"),
            "title": link_title(document, "parent"),
        },
        "assignee": {
            "id": _id_from_link(document, "assignee", "users"),
            "title": link_title(document, "assignee"),
        },
        "created_at": document.get("createdAt"),
        "updated_at": document.get("updatedAt"),
    }


class OpenProjectClient:
    def __init__(
        self,
        settings: OpenProjectSettings,
        *,
        transport: Transport | None = None,
        timeout: int = 30,
    ) -> None:
        self.settings = settings
        self._transport = transport or request.urlopen
        self._timeout = timeout

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{parse.urlencode(query)}"

        credentials = base64.b64encode(f"apikey:{self.settings.token}".encode()).decode("ascii")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Basic {credentials}")
        req.add_header("Accept", "application/hal+json")
        req.add_header("User-Agent", self.settings.user_agent)
        if body is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with self._transport(req, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OpenProjectError(
                "OPENPROJECT_LOOKUP_FAILED",
                f"OpenProject API error {exc.code} for {path}: {body[:200]}",
            ) from exc
        except URLError as exc:
            raise OpenProjectError(
                "OPENPROJECT_ACCESS_UNAVAILABLE",
                f"OpenProject connection failed for {path}: {exc.reason}",
            ) from exc

        if not raw:
            raise OpenProjectError(
                "OPENPROJECT_LOOKUP_FAILED",
                f"OpenProject returned an empty response for {path}.",
            )
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise OpenProjectError(
                "OPENPROJECT_LOOKUP_FAILED",
                f"OpenProject returned invalid JSON for {path}: {exc}",
            ) from exc

    def get_json(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.request_json("GET", path, query=query)

    def me(self) -> dict[str, Any]:
        return summarize_user(self.get_json("/api/v3/users/me"))

    def project(self, project_id: str | None = None) -> dict[str, Any]:
        selected = project_id or self.settings.project_id
        if selected is None and self.settings.initiative_id is not None:
            root = self.get_json(f"/api/v3/work_packages/{self.settings.initiative_id}")
            selected = extract_id_from_href(link_href(root, "project"), "projects")
        if selected is None:
            raise OpenProjectError(
                "OPENPROJECT_CONFIG_UNAVAILABLE",
                (
                    "Pass a project ID or set initiative_id in the selected "
                    "integrations.openproject.projects entry."
                ),
            )
        return summarize_project(self.get_json(f"/api/v3/projects/{parse.quote(str(selected))}"))

    def work_package(self, work_package_id: int) -> dict[str, Any]:
        return summarize_work_package(self.get_json(f"/api/v3/work_packages/{work_package_id}"))

    def story_context(self, work_package_id: int) -> dict[str, Any]:
        work_package = self.get_json(f"/api/v3/work_packages/{work_package_id}")
        relations = self.get_json(
            "/api/v3/relations",
            query={
                "filters": json.dumps(
                    [{"involved": {"operator": "=", "values": [str(work_package_id)]}}]
                )
            },
        )
        return {
            "work_package": summarize_work_package(work_package),
            "relations": [
                {
                    "id": relation.get("id"),
                    "type": relation.get("type"),
                    "from": {
                        "id": _id_from_link(relation, "from", "work_packages"),
                        "title": link_title(relation, "from"),
                    },
                    "to": {
                        "id": _id_from_link(relation, "to", "work_packages"),
                        "title": link_title(relation, "to"),
                    },
                }
                for relation in embedded_elements(relations)
            ],
        }

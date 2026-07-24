from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib import parse, request
from urllib.error import HTTPError, URLError

from wood_config.core import ConfigError, build_paths, load_config
from wood_secrets.core import SecretResolver
from wood_secrets.providers import SecretProviderError


class OpenProjectError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OpenProjectSettings:
    base_url: str
    project_id: str
    token: str = field(repr=False)
    token_provider: str
    user_agent: str


class Transport(Protocol):
    def __call__(
        self,
        req: request.Request,
        *,
        timeout: int,
    ) -> Any: ...


def _active_profile(document: dict[str, Any], profile: str | None) -> tuple[str, dict[str, Any]]:
    selected = profile or str(document["active_profile"])
    profiles = document.get("profiles", {})
    values = profiles.get(selected)
    if not isinstance(values, dict):
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            f"Config profile {selected!r} is not available.",
        )
    return selected, values


def load_settings(
    *,
    config_path: Path | None = None,
    profile: str | None = None,
    resolver: SecretResolver | None = None,
) -> OpenProjectSettings:
    try:
        document = load_config(build_paths(config_path))
        _, values = _active_profile(document, profile)
    except (ConfigError, OSError, KeyError, TypeError, ValueError) as exc:
        raise OpenProjectError("OPENPROJECT_CONFIG_UNAVAILABLE", str(exc)) from exc

    integrations = values.get("integrations")
    openproject = integrations.get("openproject") if isinstance(integrations, dict) else None
    openproject = openproject if isinstance(openproject, dict) else {}

    base_url = openproject.get("url")
    project_id = openproject.get("project_id")
    token_ref = openproject.get("token_ref")
    user_agent = openproject.get("user_agent") or "wood-tools/0.1"
    missing = [
        field
        for field, value in (
            ("integrations.openproject.url", base_url),
            ("integrations.openproject.project_id", project_id),
            ("integrations.openproject.token_ref", token_ref),
        )
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise OpenProjectError(
            "OPENPROJECT_CONFIG_UNAVAILABLE",
            "Missing required OpenProject configuration: " + ", ".join(missing) + ".",
        )

    secret_resolver = resolver or SecretResolver()
    try:
        token = secret_resolver.resolve(str(token_ref).strip())
    except SecretProviderError as exc:
        raise OpenProjectError("OPENPROJECT_ACCESS_UNAVAILABLE", str(exc)) from exc

    return OpenProjectSettings(
        base_url=base_url.strip(),
        project_id=project_id.strip(),
        token=token.value,
        token_provider=token.provider,
        user_agent=str(user_agent).strip() or "wood-tools/0.1",
    )


def embedded_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((document.get("_embedded") or {}).get("elements")) or [])


def _link_href(document: dict[str, Any], name: str) -> str | None:
    link = (document.get("_links") or {}).get(name)
    if isinstance(link, dict):
        href = link.get("href")
        return href if isinstance(href, str) else None
    return None


def _link_title(document: dict[str, Any], name: str) -> str | None:
    link = (document.get("_links") or {}).get(name)
    if isinstance(link, dict):
        title = link.get("title")
        return title if isinstance(title, str) else None
    return None


def _id_from_href(href: str | None) -> int | None:
    if not href:
        return None
    tail = href.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


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
        "type": _link_title(document, "type"),
        "status": _link_title(document, "status"),
        "priority": _link_title(document, "priority"),
        "project": {
            "id": _id_from_href(_link_href(document, "project")),
            "title": _link_title(document, "project"),
        },
        "version": {
            "id": _id_from_href(_link_href(document, "version")),
            "title": _link_title(document, "version"),
        },
        "parent": {
            "id": _id_from_href(_link_href(document, "parent")),
            "title": _link_title(document, "parent"),
        },
        "assignee": {
            "id": _id_from_href(_link_href(document, "assignee")),
            "title": _link_title(document, "assignee"),
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

    def get_json(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{parse.urlencode(query)}"

        credentials = base64.b64encode(f"apikey:{self.settings.token}".encode()).decode("ascii")
        req = request.Request(url, method="GET")
        req.add_header("Authorization", f"Basic {credentials}")
        req.add_header("Accept", "application/hal+json")
        req.add_header("User-Agent", self.settings.user_agent)

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

    def me(self) -> dict[str, Any]:
        return summarize_user(self.get_json("/api/v3/users/me"))

    def project(self, project_id: str | None = None) -> dict[str, Any]:
        selected = project_id or self.settings.project_id
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
                        "id": _id_from_href(_link_href(relation, "from")),
                        "title": _link_title(relation, "from"),
                    },
                    "to": {
                        "id": _id_from_href(_link_href(relation, "to")),
                        "title": _link_title(relation, "to"),
                    },
                }
                for relation in embedded_elements(relations)
            ],
        }

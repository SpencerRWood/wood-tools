from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

ROOT_ID_ENV_KEYS = (
    "OPENPROJECT_INITIATIVE_ID",
    "OPENPROJECT_ROOT_WORK_PACKAGE_ID",
    "OPENPROJECT_ROOT_ID",
)


class ScriptError(RuntimeError):
    """Structured error for implementation workbook workflows."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            f"Environment file not found: {path}. Run wood-secrets resolve-env --apply "
            "or execute the workflow through the existing secrets workflow.",
        )

    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def require_env(env: dict[str, str], keys: list[str]) -> None:
    missing = [key for key in keys if not env.get(key)]
    if missing:
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            "Missing required OpenProject configuration. Resolve .env.resolved and run the "
            "workflow through the secrets execution path so OPENPROJECT_API_TOKEN is available.",
        )


def env_value(env: dict[str, str], key: str) -> str:
    value = os.environ.get(key) or env.get(key) or ""
    return value.strip()


def api_request_json(
    method: str,
    base_url: str,
    token: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"

    credentials = base64.b64encode(f"apikey:{token}".encode()).decode("ascii")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/hal+json")
    if body is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()
            if not raw:
                raise ScriptError(
                    "OPENPROJECT_LOOKUP_FAILED",
                    f"OpenProject returned an empty response for {path}.",
                )
            return json.loads(raw.decode("utf-8"))
    except HTTPError as err:
        body_text = err.read().decode("utf-8", errors="replace")
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"OpenProject API error {err.code} for {path}: {body_text[:200]}",
        ) from err
    except URLError as err:
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            f"OpenProject connection failed for {path}: {err.reason}",
        ) from err
    except json.JSONDecodeError as err:
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"OpenProject returned invalid JSON for {path}: {err}",
        ) from err


def api_get_json(
    base_url: str,
    token: str,
    path: str,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    return api_request_json("GET", base_url, token, path, query=query)


def api_patch_json(
    base_url: str,
    token: str,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    return api_request_json("PATCH", base_url, token, path, body=body)


def embedded_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((document.get("_embedded") or {}).get("elements")) or [])


def find_named_element(elements: list[dict[str, Any]], expected_name: str) -> dict[str, Any]:
    for element in elements:
        if str(element.get("name") or "") == expected_name:
            return element

    available = sorted(str(element.get("name")) for element in elements if element.get("name"))
    raise ScriptError(
        "OPENPROJECT_LOOKUP_FAILED",
        f"OpenProject value not found: {expected_name}. Available values: {available}",
    )


def extract_id_from_href(href: str | None, resource_name: str) -> int | None:
    if not href:
        return None

    parsed_url = parse.urlparse(href)
    match = re.search(rf"/{re.escape(resource_name)}/(\d+)", parsed_url.path)
    if not match:
        return None
    return int(match.group(1))


def extract_wp_id_from_href(href: str | None) -> int | None:
    return extract_id_from_href(href, "work_packages")


def link_title(document: dict[str, Any], name: str) -> str:
    return str(((document.get("_links") or {}).get(name) or {}).get("title") or "")


def link_href(document: dict[str, Any], name: str) -> str:
    return str(((document.get("_links") or {}).get(name) or {}).get("href") or "")


def work_package_type_name(work_package: dict[str, Any]) -> str:
    return link_title(work_package, "type")


def work_package_status_name(work_package: dict[str, Any]) -> str:
    return link_title(work_package, "status")


def work_package_subject(work_package: dict[str, Any]) -> str:
    return str(work_package.get("subject") or "")


def work_package_id(work_package: dict[str, Any]) -> int:
    return int(work_package["id"])

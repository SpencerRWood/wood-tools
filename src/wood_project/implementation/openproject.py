from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from wood_project.openproject import (
    OpenProjectClient,
    OpenProjectSettings,
    extract_id_from_href,
    link_title,
)

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
        return {}

    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def require_env(env: dict[str, str], keys: list[str]) -> None:
    missing = [key for key in keys if not env_value(env, key)]
    if missing:
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            f"Missing required OpenProject configuration: {', '.join(missing)}. "
            "Set it in the environment or a resolved env file.",
        )


def env_value(env: dict[str, str], key: str) -> str:
    value = os.environ.get(key) or env.get(key) or ""
    return value.strip()


def client_from_env(base_url: str, token: str, *, project_id: str = "") -> OpenProjectClient:
    return OpenProjectClient(
        OpenProjectSettings(
            base_url=base_url,
            project_id=project_id,
            token=token,
            token_provider="implementation-env",
            user_agent="wood-project/implementation",
        )
    )


def find_named_element(elements: list[dict[str, Any]], expected_name: str) -> dict[str, Any]:
    for element in elements:
        if str(element.get("name") or "") == expected_name:
            return element

    available = sorted(str(element.get("name")) for element in elements if element.get("name"))
    raise ScriptError(
        "OPENPROJECT_LOOKUP_FAILED",
        f"OpenProject value not found: {expected_name}. Available values: {available}",
    )


def extract_wp_id_from_href(href: str | None) -> int | None:
    return extract_id_from_href(href, "work_packages")


def work_package_type_name(work_package: dict[str, Any]) -> str:
    return link_title(work_package, "type")


def work_package_status_name(work_package: dict[str, Any]) -> str:
    return link_title(work_package, "status")


def work_package_subject(work_package: dict[str, Any]) -> str:
    return str(work_package.get("subject") or "")


def work_package_id(work_package: dict[str, Any]) -> int:
    return int(work_package["id"])

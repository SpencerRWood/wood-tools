from __future__ import annotations

from typing import Any

from wood_project.openproject import (
    extract_id_from_href,
    link_title,
)


class ScriptError(RuntimeError):
    """Structured error for implementation workbook workflows."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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

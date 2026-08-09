from __future__ import annotations

from typing import Any

from wood_project.openproject import OpenProjectClient

from .models import StoryWorkflowError
from .openproject import (
    api_get_json,
    api_request_json,
    embedded_elements,
    find_named_element,
    link_href,
    work_package_status_name,
)


def set_status(
    *,
    work_package_id: int,
    target_status: str,
    client: OpenProjectClient | None,
    apply: bool,
) -> dict[str, Any]:
    if not apply:
        return {
            "ok": True,
            "dry_run": True,
            "mutation": {
                "system": "openproject",
                "work_package_id": work_package_id,
                "action": "set_status",
                "target_status": target_status,
            },
        }

    if client is None:
        raise StoryWorkflowError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            "OpenProject settings are required when applying a status change.",
        )
    work_package = api_get_json(client, f"/api/v3/work_packages/{work_package_id}")
    statuses = embedded_elements(api_get_json(client, "/api/v3/statuses"))
    status = find_named_element(statuses, target_status)
    status_href = link_href(status, "self")
    lock_version = work_package.get("lockVersion")
    if not isinstance(lock_version, int) or not status_href:
        raise StoryWorkflowError("STATUS_UPDATE_FAILED", f"WP-{work_package_id} is not updatable.")

    updated = api_request_json(
        "PATCH",
        client,
        f"/api/v3/work_packages/{work_package_id}",
        body={"lockVersion": lock_version, "_links": {"status": {"href": status_href}}},
    )
    return {
        "ok": True,
        "dry_run": False,
        "mutation": {
            "system": "openproject",
            "work_package_id": work_package_id,
            "action": "set_status",
            "from_status": work_package_status_name(work_package),
            "to_status": target_status,
        },
        "work_package": {
            "id": work_package_id,
            "subject": str(updated.get("subject") or ""),
            "status": work_package_status_name(updated),
        },
    }

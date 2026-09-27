from __future__ import annotations

import json
import re
from typing import Any

from wood_project.openproject import OpenProjectClient

from .models import StoryWorkflowError
from .openproject import (
    api_get_json,
    embedded_elements,
    extract_id_from_href,
    link_href,
    work_package_description_text,
    work_package_id,
    work_package_status_name,
    work_package_type_name,
    work_package_version_name,
)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "story"


def fetch_collection(
    client: OpenProjectClient,
    path: str,
    *,
    query: dict[str, str],
    page_size: int,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    offset = 1
    while True:
        page_query = dict(query)
        page_query["pageSize"] = str(page_size)
        page_query["offset"] = str(offset)
        document = api_get_json(client, path, query=page_query)
        page = embedded_elements(document)
        elements.extend(page)
        total = int(document.get("total") or len(elements))
        if len(elements) >= total or not page:
            return elements
        offset += len(page)


def fetch_descendants(
    client: OpenProjectClient,
    root_work_package_id: int,
    page_size: int,
) -> list[dict[str, Any]]:
    filters = [
        {"project": {"operator": "=", "values": [str(client.settings.project_id)]}},
        {"ancestor": {"operator": "=", "values": [str(root_work_package_id)]}},
    ]
    return fetch_collection(
        client,
        "/api/v3/work_packages",
        query={"filters": json.dumps(filters)},
        page_size=page_size,
    )


def normalize_relation(relation: dict[str, Any]) -> tuple[int, int, str] | None:
    from_id = extract_id_from_href(link_href(relation, "from"), "work_packages")
    to_id = extract_id_from_href(link_href(relation, "to"), "work_packages")
    relation_type = str(relation.get("type") or "")
    if from_id is None or to_id is None or not relation_type:
        return None
    inverse_types = {"follows": "precedes", "blocked": "blocks", "required": "requires"}
    if relation_type in inverse_types:
        return (to_id, from_id, inverse_types[relation_type])
    return (from_id, to_id, relation_type)


def fetch_predecessor_map(
    client: OpenProjectClient,
    story_ids: set[int],
    page_size: int,
) -> dict[int, set[int]]:
    relations_by_id: dict[int, dict[str, Any]] = {}
    for story_id in sorted(story_ids):
        filters = [{"involved": {"operator": "=", "values": [str(story_id)]}}]
        for relation in fetch_collection(
            client,
            "/api/v3/relations",
            query={"filters": json.dumps(filters)},
            page_size=page_size,
        ):
            relations_by_id[int(relation["id"])] = relation

    predecessor_map: dict[int, set[int]] = {}
    for relation in relations_by_id.values():
        normalized = normalize_relation(relation)
        if normalized is None:
            continue
        from_id, to_id, relation_type = normalized
        if relation_type == "precedes":
            predecessor_map.setdefault(to_id, set()).add(from_id)
    return predecessor_map


def version_rank(version_name: str) -> tuple[int, str]:
    match = re.search(r"\b[MV](\d+)", version_name, flags=re.IGNORECASE)
    if match:
        return (int(match.group(1)), version_name.lower())
    return (9000, version_name.lower())


def acceptance_criteria(description: str) -> list[str]:
    body = packet_section(description, "Acceptance Criteria")
    if not body:
        return [
            re.sub(r"^[-*]\s+", "", line.strip())
            for line in description.splitlines()
            if re.match(r"^[-*]\s+", line.strip())
        ]
    return [line.strip() for line in body.splitlines() if line.strip()]


def packet_section(description: str, heading: str) -> str:
    match = re.search(
        rf"\n{re.escape(heading)}\n(?P<body>.*?)(?:\n\n[A-Z][^\n]*\n|\Z)",
        description,
        flags=re.DOTALL,
    )
    if not match:
        return ""
    return match.group("body").strip()


def build_story_payload(
    client: OpenProjectClient,
    story: dict[str, Any],
    predecessor_ids: set[int],
    blocked_count: int,
) -> dict[str, Any]:
    story_id = work_package_id(story)
    description = work_package_description_text(story)
    parent_id = extract_id_from_href(link_href(story, "parent"), "work_packages")
    return {
        "ok": True,
        "story": {
            "id": story_id,
            "subject": str(story.get("subject") or ""),
            "type": work_package_type_name(story),
            "status": work_package_status_name(story),
            "version": work_package_version_name(story) or "(none)",
            "parent_id": parent_id,
            "url": f"{client.settings.base_url}/work_packages/{story_id}",
            "branch": f"feature/op-{story_id}-{slugify(str(story.get('subject') or 'story'))}",
        },
        "summary": {
            "goal": packet_section(description, "Goal")
            or (description.splitlines()[0].strip() if description.strip() else ""),
            "acceptance_criteria": acceptance_criteria(description),
            "likely_files": [],
            "risks": (
                ["Other candidate stories in this version are still blocked by dependencies."]
                if blocked_count
                else []
            ),
        },
        "read_only": True,
    }


def discover_next_story(
    *,
    client: OpenProjectClient,
    root_work_package_id: int,
    target_status: str,
    story_type: str,
    page_size: int,
) -> dict[str, Any]:
    root_id = root_work_package_id
    root = api_get_json(client, f"/api/v3/work_packages/{root_id}")
    root_project_id = extract_id_from_href(link_href(root, "project"), "projects")
    if root_project_id is not None and root_project_id != int(client.settings.project_id):
        raise StoryWorkflowError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"WP-{root_id} belongs to project {root_project_id}, not {client.settings.project_id}.",
        )

    statuses = embedded_elements(api_get_json(client, "/api/v3/statuses"))
    closed_status_names = {str(status.get("name")) for status in statuses if status.get("isClosed")}
    versions_path = f"/api/v3/projects/{client.settings.project_id}/versions"
    versions = embedded_elements(api_get_json(client, versions_path))
    version_status = {
        str(version.get("name") or ""): str(version.get("status") or "") for version in versions
    }

    descendants = fetch_descendants(client, root_id, page_size)
    stories = [wp for wp in descendants if work_package_type_name(wp) == story_type]
    if not stories:
        raise StoryWorkflowError("NO_STORY_FOUND", f"No {story_type} work packages found.")

    story_ids = {work_package_id(story) for story in stories}
    predecessor_map = fetch_predecessor_map(client, story_ids, page_size)
    stories_by_version: dict[str, list[dict[str, Any]]] = {}
    for story in stories:
        stories_by_version.setdefault(
            work_package_version_name(story) or "(none)",
            [],
        ).append(story)

    for version in sorted(stories_by_version, key=version_rank):
        unfinished = [
            story
            for story in stories_by_version[version]
            if work_package_status_name(story) not in closed_status_names
        ]
        if not unfinished:
            if version != "(none)" and version_status.get(version, "").lower() == "open":
                return {
                    "ok": True,
                    "story": None,
                    "release": {
                        "root_work_package_id": root_id,
                        "root_subject": str(root.get("subject") or ""),
                        "version": version,
                        "status": version_status.get(version, ""),
                        "completed_story_count": len(stories_by_version[version]),
                        "total_story_count": len(stories_by_version[version]),
                    },
                    "summary": {
                        "goal": f"Prepare the release for {version}.",
                        "acceptance_criteria": [],
                        "likely_files": [],
                        "risks": [],
                    },
                    "read_only": True,
                }
            continue

        in_progress = [
            story
            for story in unfinished
            if work_package_status_name(story).casefold() == "in progress"
        ]
        if in_progress:
            story = sorted(in_progress, key=lambda item: work_package_id(item))[0]
            return build_story_payload(
                client,
                story,
                predecessor_map.get(work_package_id(story), set()),
                0,
            )

        candidates = [
            story for story in unfinished if work_package_status_name(story) == target_status
        ]
        blocked_count = 0
        eligible: list[dict[str, Any]] = []
        by_id = {work_package_id(story): story for story in stories}
        for story in candidates:
            predecessor_ids = predecessor_map.get(work_package_id(story), set())
            unfinished_predecessors = [
                predecessor_id
                for predecessor_id in predecessor_ids
                if work_package_status_name(by_id.get(predecessor_id, {}))
                not in closed_status_names
            ]
            if unfinished_predecessors:
                blocked_count += 1
            else:
                eligible.append(story)
        if eligible:
            story = sorted(eligible, key=lambda item: work_package_id(item))[0]
            return build_story_payload(
                client,
                story,
                predecessor_map.get(work_package_id(story), set()),
                blocked_count,
            )
        raise StoryWorkflowError("NO_STORY_FOUND", "No eligible next story was found.")

    raise StoryWorkflowError(
        "NO_STORY_FOUND",
        f"All matching Stories beneath WP-{root_id} are closed.",
    )

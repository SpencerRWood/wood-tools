#!/usr/bin/env python3
"""Report the next dependency-ready OpenProject Story beneath a root work package.

Examples:
    python3 scripts/openproject_next_story.py 208
    python3 scripts/openproject_next_story.py 208 --json

Selection rules:
1. Only work packages descended from the supplied root are considered.
2. The earliest Version containing unfinished Stories is active.
3. If the earliest open Version has all Stories closed, the script reports release readiness.
4. A Story is eligible only when all explicit predecessors are closed.
5. Eligible Stories are sorted by Version, parent work-package ID, then Story ID.

OpenProject may store:

    A precedes B

as:

    B follows A

This script normalizes both forms to the same prerequisite relationship.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError


@dataclass(frozen=True)
class Candidate:
    story_id: int
    subject: str
    status: str
    type_name: str
    version: str
    parent_epic: str
    parent_epic_id: int | None
    predecessor_ids: tuple[int, ...]
    url: str
    branch: str
    reason: str


@dataclass(frozen=True)
class BlockedStory:
    story_id: int
    subject: str
    version: str
    unfinished_predecessors: tuple[tuple[int, str, str], ...]


@dataclass(frozen=True)
class ReleaseReadyVersion:
    name: str
    status: str
    total_story_count: int
    closed_story_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "root_work_package_id",
        type=int,
        help=(
            "Root OpenProject work-package ID. Only descendant work packages will be considered."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=".env.resolved",
        help="Resolved environment file path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the result as JSON.",
    )
    parser.add_argument(
        "--status",
        default="New",
        help="Candidate Story status name. Default: New.",
    )
    parser.add_argument(
        "--type",
        default="Story",
        help="Candidate work-package type name. Default: Story.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="OpenProject collection page size. Default: 1000.",
    )

    return parser.parse_args()


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RuntimeError(f"Environment file not found: {path}")

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
        raise RuntimeError(f"Missing required environment values: {', '.join(missing)}")


def api_get_json(
    base_url: str,
    token: str,
    path: str,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"

    if query:
        url = f"{url}?{parse.urlencode(query)}"

    credentials = base64.b64encode(f"apikey:{token}".encode()).decode("ascii")

    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/hal+json")

    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()

            if not raw:
                raise RuntimeError(f"OpenProject returned an empty response for {path}")

            return json.loads(raw.decode("utf-8"))

    except HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenProject API error {err.code} for {path}: {body[:1000]}") from err

    except URLError as err:
        raise RuntimeError(f"OpenProject connection failed for {path}: {err.reason}") from err

    except json.JSONDecodeError as err:
        raise RuntimeError(f"OpenProject returned invalid JSON for {path}: {err}") from err


def embedded_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((document.get("_embedded") or {}).get("elements")) or [])


def find_named_element(
    elements: list[dict[str, Any]],
    expected_name: str,
) -> dict[str, Any]:
    for element in elements:
        if str(element.get("name") or "") == expected_name:
            return element

    available = sorted(str(element.get("name")) for element in elements if element.get("name"))

    raise RuntimeError(
        f"OpenProject value not found: {expected_name}. Available values: {available}"
    )


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "story"


def parse_milestone_rank(version_name: str) -> tuple[int, str]:
    if not version_name:
        return (10_000, "")

    match = re.search(
        r"\b[MV](\d+)\b",
        version_name,
        flags=re.IGNORECASE,
    )

    if not match:
        return (9_000, version_name.lower())

    return (int(match.group(1)), version_name.lower())


def extract_id_from_href(
    href: str | None,
    resource_name: str,
) -> int | None:
    if not href:
        return None

    parsed_url = parse.urlparse(href)
    path = parsed_url.path
    pattern = rf"/{re.escape(resource_name)}/(\d+)"

    match = re.search(pattern, path)

    if not match:
        return None

    return int(match.group(1))


def extract_wp_id_from_href(href: str | None) -> int | None:
    return extract_id_from_href(href, "work_packages")


def extract_project_id_from_href(href: str | None) -> int | None:
    return extract_id_from_href(href, "projects")


def work_package_type_name(wp: dict[str, Any]) -> str:
    links = wp.get("_links") or {}
    return str((links.get("type") or {}).get("title") or "")


def work_package_status_name(wp: dict[str, Any]) -> str:
    links = wp.get("_links") or {}
    return str((links.get("status") or {}).get("title") or "")


def work_package_version_name(wp: dict[str, Any]) -> str:
    links = wp.get("_links") or {}
    return str((links.get("version") or {}).get("title") or "")


def work_package_parent_id(wp: dict[str, Any]) -> int | None:
    links = wp.get("_links") or {}
    parent_href = (links.get("parent") or {}).get("href")
    return extract_wp_id_from_href(parent_href)


def work_package_parent_title(wp: dict[str, Any]) -> str:
    links = wp.get("_links") or {}
    return str((links.get("parent") or {}).get("title") or "(none)")


def work_package_subject(wp: dict[str, Any]) -> str:
    return str(wp.get("subject") or "")


def work_package_id(wp: dict[str, Any]) -> int:
    return int(wp["id"])


def fetch_root_work_package(
    base_url: str,
    token: str,
    root_work_package_id: int,
) -> dict[str, Any]:
    return api_get_json(
        base_url,
        token,
        f"/api/v3/work_packages/{root_work_package_id}",
    )


def fetch_descendants(
    base_url: str,
    token: str,
    project_id: str,
    root_work_package_id: int,
    page_size: int,
) -> list[dict[str, Any]]:
    filters = [
        {
            "project": {
                "operator": "=",
                "values": [str(project_id)],
            }
        },
        {
            "ancestor": {
                "operator": "=",
                "values": [str(root_work_package_id)],
            }
        },
    ]

    document = api_get_json(
        base_url,
        token,
        "/api/v3/work_packages",
        query={
            "filters": json.dumps(filters),
            "pageSize": str(page_size),
        },
    )

    total = int(document.get("total") or 0)
    elements = embedded_elements(document)

    if total > len(elements):
        raise RuntimeError(
            f"The subtree contains {total} work packages, but only "
            f"{len(elements)} were returned. Increase --page-size or add "
            "pagination support."
        )

    return elements


def fetch_relations_for_work_package(
    base_url: str,
    token: str,
    work_package_id_value: int,
    page_size: int,
) -> list[dict[str, Any]]:
    filters = [
        {
            "involved": {
                "operator": "=",
                "values": [str(work_package_id_value)],
            }
        }
    ]

    document = api_get_json(
        base_url,
        token,
        "/api/v3/relations",
        query={
            "filters": json.dumps(filters),
            "pageSize": str(page_size),
        },
    )

    total = int(document.get("total") or 0)
    elements = embedded_elements(document)

    if total > len(elements):
        raise RuntimeError(
            f"WP-{work_package_id_value} has {total} relations, but only "
            f"{len(elements)} were returned."
        )

    return elements


def fetch_all_relations(
    base_url: str,
    token: str,
    work_package_ids: set[int],
    page_size: int,
) -> list[dict[str, Any]]:
    relations_by_id: dict[int, dict[str, Any]] = {}

    for wp_id in sorted(work_package_ids):
        for relation in fetch_relations_for_work_package(
            base_url,
            token,
            wp_id,
            page_size,
        ):
            relation_id = int(relation["id"])
            relations_by_id[relation_id] = relation

    return [relations_by_id[relation_id] for relation_id in sorted(relations_by_id)]


def normalize_relation(
    relation: dict[str, Any],
) -> tuple[int, int, str]:
    """Normalize inverse OpenProject relation forms.

    Examples:

        from=A, to=B, type=precedes
            becomes (A, B, "precedes")

        from=B, to=A, type=follows
            also becomes (A, B, "precedes")
    """
    links = relation.get("_links") or {}

    from_id = extract_wp_id_from_href((links.get("from") or {}).get("href"))
    to_id = extract_wp_id_from_href((links.get("to") or {}).get("href"))
    relation_type = str(relation.get("type") or "")

    if from_id is None or to_id is None or not relation_type:
        raise RuntimeError(
            f"Unable to parse OpenProject relation {relation.get('id')}: {json.dumps(relation)}"
        )

    inverse_types = {
        "follows": "precedes",
        "blocked": "blocks",
        "required": "requires",
        "duplicated": "duplicates",
        "includes": "partof",
    }

    if relation_type in inverse_types:
        return (to_id, from_id, inverse_types[relation_type])

    return (from_id, to_id, relation_type)


def build_predecessor_map(
    relations: list[dict[str, Any]],
) -> dict[int, set[int]]:
    predecessor_map: dict[int, set[int]] = {}

    for relation in relations:
        from_id, to_id, relation_type = normalize_relation(relation)

        if relation_type != "precedes":
            continue

        predecessor_map.setdefault(to_id, set()).add(from_id)

    return predecessor_map


def fetch_missing_work_package(
    base_url: str,
    token: str,
    work_packages_by_id: dict[int, dict[str, Any]],
    wp_id: int,
) -> dict[str, Any]:
    existing = work_packages_by_id.get(wp_id)

    if existing is not None:
        return existing

    fetched = api_get_json(
        base_url,
        token,
        f"/api/v3/work_packages/{wp_id}",
    )
    work_packages_by_id[wp_id] = fetched
    return fetched


def build_candidate(
    base_url: str,
    wp: dict[str, Any],
    predecessor_ids: set[int],
) -> Candidate:
    story_id = work_package_id(wp)
    subject = work_package_subject(wp)
    status = work_package_status_name(wp)
    type_name = work_package_type_name(wp)
    version = work_package_version_name(wp)
    parent_id = work_package_parent_id(wp)
    parent_title = work_package_parent_title(wp)

    sorted_predecessors = tuple(sorted(predecessor_ids))

    predecessor_text = (
        ", ".join(f"WP-{wp_id}" for wp_id in sorted_predecessors) if sorted_predecessors else "none"
    )

    reason = (
        f"selected from version={version or '(none)'}; "
        f"status={status}; type={type_name}; "
        f"all predecessors closed; predecessors={predecessor_text}; "
        f"parent_id={parent_id if parent_id is not None else 'none'}; "
        f"story_id={story_id}"
    )

    return Candidate(
        story_id=story_id,
        subject=subject,
        status=status,
        type_name=type_name,
        version=version or "(none)",
        parent_epic=parent_title,
        parent_epic_id=parent_id,
        predecessor_ids=sorted_predecessors,
        url=f"{base_url.rstrip('/')}/work_packages/{story_id}",
        branch=f"feature/op-{story_id}-{slugify(subject)}",
        reason=reason,
    )


def candidate_sort_key(
    candidate: Candidate,
) -> tuple[tuple[int, str], int, int]:
    milestone = parse_milestone_rank(candidate.version if candidate.version != "(none)" else "")
    parent_id = candidate.parent_epic_id if candidate.parent_epic_id is not None else 10_000_000

    return (milestone, parent_id, candidate.story_id)


def choose_candidate(
    *,
    base_url: str,
    token: str,
    stories: list[dict[str, Any]],
    target_status: str,
    closed_status_names: set[str],
    predecessor_map: dict[int, set[int]],
    work_packages_by_id: dict[int, dict[str, Any]],
    root_work_package_id: int,
    version_status_by_name: dict[str, str],
) -> tuple[Candidate | None, list[BlockedStory], str, ReleaseReadyVersion | None]:
    stories_by_version: dict[str, list[dict[str, Any]]] = {}

    for story in stories:
        version_name = work_package_version_name(story) or "(none)"
        stories_by_version.setdefault(version_name, []).append(story)

    active_version_stories: list[dict[str, Any]] | None = None
    active_version_name = "(none)"

    for version_name in sorted(
        stories_by_version,
        key=lambda name: parse_milestone_rank(name if name != "(none)" else ""),
    ):
        version_stories = stories_by_version[version_name]
        unfinished_version_stories = [
            story
            for story in version_stories
            if work_package_status_name(story) not in closed_status_names
        ]

        if not unfinished_version_stories:
            version_status = version_status_by_name.get(version_name, "")

            if version_name != "(none)" and version_status.lower() == "open":
                return (
                    None,
                    [],
                    version_name,
                    ReleaseReadyVersion(
                        name=version_name,
                        status=version_status,
                        total_story_count=len(version_stories),
                        closed_story_count=len(version_stories),
                    ),
                )

            continue

        active_version_stories = unfinished_version_stories
        active_version_name = version_name
        break

    if active_version_stories is None:
        raise RuntimeError(f"All matching Stories beneath WP-{root_work_package_id} are closed.")

    status_candidates = [
        story
        for story in active_version_stories
        if work_package_status_name(story) == target_status
    ]

    if not status_candidates:
        active_statuses = sorted(
            {work_package_status_name(story) for story in active_version_stories}
        )

        raise RuntimeError(
            f"The active version is {active_version_name}, but it has no "
            f"Stories with status {target_status!r}. Current unfinished "
            f"statuses: {active_statuses}"
        )

    eligible_candidates: list[Candidate] = []
    blocked_stories: list[BlockedStory] = []

    for story in status_candidates:
        story_id = work_package_id(story)
        predecessor_ids = predecessor_map.get(story_id, set())

        unfinished_predecessors: list[tuple[int, str, str]] = []

        for predecessor_id in sorted(predecessor_ids):
            predecessor = fetch_missing_work_package(
                base_url,
                token,
                work_packages_by_id,
                predecessor_id,
            )

            predecessor_status = work_package_status_name(predecessor)

            if predecessor_status not in closed_status_names:
                unfinished_predecessors.append(
                    (
                        predecessor_id,
                        work_package_subject(predecessor),
                        predecessor_status,
                    )
                )

        if unfinished_predecessors:
            blocked_stories.append(
                BlockedStory(
                    story_id=story_id,
                    subject=work_package_subject(story),
                    version=work_package_version_name(story) or "(none)",
                    unfinished_predecessors=tuple(unfinished_predecessors),
                )
            )
            continue

        eligible_candidates.append(
            build_candidate(
                base_url,
                story,
                predecessor_ids,
            )
        )

    if not eligible_candidates:
        blocked_summary = "; ".join(
            (
                f"WP-{blocked.story_id} blocked by "
                + ", ".join(
                    f"WP-{pred_id} ({status})"
                    for pred_id, _, status in blocked.unfinished_predecessors
                )
            )
            for blocked in blocked_stories
        )

        raise RuntimeError(
            f"No dependency-ready {target_status} Stories exist in active "
            f"version {active_version_name}. {blocked_summary}"
        )

    selected = sorted(eligible_candidates, key=candidate_sort_key)[0]
    return selected, blocked_stories, active_version_name, None


def main() -> int:
    args = parse_args()

    try:
        env = parse_env_file(Path(args.env_file))
        require_env(
            env,
            [
                "OPENPROJECT_URL",
                "OPENPROJECT_PROJECT_ID",
                "OPENPROJECT_API_TOKEN",
            ],
        )

        base_url = env["OPENPROJECT_URL"].rstrip("/")
        project_id = env["OPENPROJECT_PROJECT_ID"]
        token = env["OPENPROJECT_API_TOKEN"]

        root_wp = fetch_root_work_package(
            base_url,
            token,
            args.root_work_package_id,
        )

        root_project_href = ((root_wp.get("_links") or {}).get("project") or {}).get("href")
        root_project_id = extract_project_id_from_href(root_project_href)

        if root_project_id is not None and root_project_id != int(project_id):
            raise RuntimeError(
                f"WP-{args.root_work_package_id} belongs to project "
                f"{root_project_id}, but OPENPROJECT_PROJECT_ID is "
                f"{project_id}."
            )

        types_document = api_get_json(
            base_url,
            token,
            f"/api/v3/projects/{project_id}/types",
        )
        statuses_document = api_get_json(
            base_url,
            token,
            "/api/v3/statuses",
        )
        versions_document = api_get_json(
            base_url,
            token,
            f"/api/v3/projects/{project_id}/versions",
        )

        types = embedded_elements(types_document)
        statuses = embedded_elements(statuses_document)
        versions = embedded_elements(versions_document)

        chosen_type = find_named_element(types, args.type)
        chosen_status = find_named_element(statuses, args.status)

        closed_status_names = {
            str(status.get("name")) for status in statuses if bool(status.get("isClosed"))
        }

        if not closed_status_names:
            raise RuntimeError("OpenProject returned no statuses marked as closed.")

        descendants = fetch_descendants(
            base_url,
            token,
            project_id,
            args.root_work_package_id,
            args.page_size,
        )

        work_packages_by_id = {work_package_id(wp): wp for wp in descendants}

        stories = [
            wp
            for wp in descendants
            if work_package_type_name(wp) == str(chosen_type.get("name") or args.type)
        ]

        if not stories:
            raise RuntimeError(
                f"No {args.type} work packages were found beneath WP-{args.root_work_package_id}."
            )

        story_ids = {work_package_id(story) for story in stories}

        relations = fetch_all_relations(
            base_url,
            token,
            story_ids,
            args.page_size,
        )

        predecessor_map = build_predecessor_map(relations)
        version_status_by_name = {
            str(version.get("name") or ""): str(version.get("status") or "") for version in versions
        }

        candidate, blocked_stories, active_version, release_ready = choose_candidate(
            base_url=base_url,
            token=token,
            stories=stories,
            target_status=str(chosen_status.get("name") or args.status),
            closed_status_names=closed_status_names,
            predecessor_map=predecessor_map,
            work_packages_by_id=work_packages_by_id,
            root_work_package_id=args.root_work_package_id,
            version_status_by_name=version_status_by_name,
        )

    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    if args.json:
        if release_ready is not None:
            print(
                json.dumps(
                    {
                        "result": "release_ready",
                        "root_work_package_id": args.root_work_package_id,
                        "root_subject": work_package_subject(root_wp),
                        "active_version": active_version,
                        "active_milestone": active_version,
                        "release_ready_version": {
                            "name": release_ready.name,
                            "status": release_ready.status,
                            "total_story_count": release_ready.total_story_count,
                            "closed_story_count": release_ready.closed_story_count,
                        },
                        "next_action": (
                            f"Prepare the release for {release_ready.name}, then close "
                            "the OpenProject Version when the release ships."
                        ),
                    },
                    indent=2,
                )
            )
            return 0

        print(
            json.dumps(
                {
                    "result": "next_story",
                    "root_work_package_id": args.root_work_package_id,
                    "root_subject": work_package_subject(root_wp),
                    "active_version": active_version,
                    "active_milestone": active_version,
                    "openproject_id": candidate.story_id,
                    "subject": candidate.subject,
                    "status": candidate.status,
                    "type": candidate.type_name,
                    "version": candidate.version,
                    "parent_epic": candidate.parent_epic,
                    "parent_epic_id": candidate.parent_epic_id,
                    "predecessor_ids": list(candidate.predecessor_ids),
                    "suggested_branch": candidate.branch,
                    "url": candidate.url,
                    "reason": candidate.reason,
                    "blocked_candidates": [
                        {
                            "openproject_id": blocked.story_id,
                            "subject": blocked.subject,
                            "version": blocked.version,
                            "unfinished_predecessors": [
                                {
                                    "openproject_id": predecessor_id,
                                    "subject": predecessor_subject,
                                    "status": predecessor_status,
                                }
                                for (
                                    predecessor_id,
                                    predecessor_subject,
                                    predecessor_status,
                                ) in blocked.unfinished_predecessors
                            ],
                        }
                        for blocked in blocked_stories
                    ],
                    "next_action": (
                        f"Create or checkout {candidate.branch}, then give "
                        f"Codex the Story packet for OP-{candidate.story_id}."
                    ),
                },
                indent=2,
            )
        )
        return 0

    if release_ready is not None:
        print("Release Ready:")
        print(
            f"- Root Work Package: WP-{args.root_work_package_id} — "
            f"{work_package_subject(root_wp) or '(unnamed)'}"
        )
        print(f"- Active Version: {active_version}")
        print(f"- Version Status: {release_ready.status}")
        print(
            f"- Completed Stories: {release_ready.closed_story_count}/"
            f"{release_ready.total_story_count}"
        )
        print("\nNext action:")
        print(
            f"Prepare the release for {release_ready.name}, then close the "
            "OpenProject Version when the release ships."
        )
        return 0

    print("Next Story:")
    print(
        f"- Root Work Package: WP-{args.root_work_package_id} — "
        f"{work_package_subject(root_wp) or '(unnamed)'}"
    )
    print(f"- Active Version: {active_version}")
    print(f"- OpenProject ID: {candidate.story_id}")
    print(f"- Subject: {candidate.subject}")
    print(f"- Status: {candidate.status}")
    print(f"- Type: {candidate.type_name}")
    print(f"- Version: {candidate.version}")

    if candidate.parent_epic_id is not None:
        print(f"- Parent: {candidate.parent_epic} (WP-{candidate.parent_epic_id})")
    else:
        print(f"- Parent: {candidate.parent_epic}")

    if candidate.predecessor_ids:
        predecessor_text = ", ".join(f"WP-{wp_id}" for wp_id in candidate.predecessor_ids)
    else:
        predecessor_text = "(none)"

    print(f"- Predecessors: {predecessor_text}")
    print(f"- Branch: {candidate.branch}")
    print(f"- URL: {candidate.url}")
    print(f"- Selection Reason: {candidate.reason}")

    if blocked_stories:
        print("\nOther candidates blocked in this version:")

        for blocked in blocked_stories:
            blockers = ", ".join(
                f"WP-{wp_id} ({status})" for wp_id, _, status in blocked.unfinished_predecessors
            )

            print(f"- WP-{blocked.story_id}: {blocked.subject} — blocked by {blockers}")

    print("\nNext action:")
    print(
        "Create or checkout the branch above, then give Codex the Story "
        f"packet for OP-{candidate.story_id}."
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())

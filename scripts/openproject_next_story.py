#!/usr/bin/env python3
"""Read-only OpenProject next-story discovery for local Story Loop workflows."""

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

ROOT_ID_ENV_KEYS = (
    "OPENPROJECT_INITIATIVE_ID",
    "OPENPROJECT_ROOT_WORK_PACKAGE_ID",
    "OPENPROJECT_ROOT_ID",
)


class ScriptError(RuntimeError):
    """Structured error for safe script output."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root_work_package_id",
        nargs="?",
        type=int,
        help=(
            "Root OpenProject work-package ID. If omitted, the script falls back to "
            "OPENPROJECT_INITIATIVE_ID, OPENPROJECT_ROOT_WORK_PACKAGE_ID, or "
            "OPENPROJECT_ROOT_ID."
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
        help="Emit structured JSON output.",
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
    return parser.parse_args(argv)


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ScriptError(
            "OPENPROJECT_ACCESS_UNAVAILABLE",
            f"Environment file not found: {path}. Run python scripts/resolve_env_refs.py --apply "
            "or execute the script through the existing secrets workflow.",
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
            "script through the secrets execution workflow so OPENPROJECT_API_TOKEN is available.",
        )


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


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "story"


def parse_milestone_rank(version_name: str) -> tuple[int, str]:
    if not version_name:
        return (10_000, "")

    match = re.search(r"\b[MV](\d+)\b", version_name, flags=re.IGNORECASE)
    if not match:
        return (9_000, version_name.lower())

    return (int(match.group(1)), version_name.lower())


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


def extract_project_id_from_href(href: str | None) -> int | None:
    return extract_id_from_href(href, "projects")


def work_package_type_name(wp: dict[str, Any]) -> str:
    return str(((wp.get("_links") or {}).get("type") or {}).get("title") or "")


def work_package_status_name(wp: dict[str, Any]) -> str:
    return str(((wp.get("_links") or {}).get("status") or {}).get("title") or "")


def work_package_version_name(wp: dict[str, Any]) -> str:
    return str(((wp.get("_links") or {}).get("version") or {}).get("title") or "")


def work_package_parent_id(wp: dict[str, Any]) -> int | None:
    parent_href = ((wp.get("_links") or {}).get("parent") or {}).get("href")
    return extract_wp_id_from_href(parent_href)


def work_package_parent_title(wp: dict[str, Any]) -> str:
    return str(((wp.get("_links") or {}).get("parent") or {}).get("title") or "(none)")


def work_package_subject(wp: dict[str, Any]) -> str:
    return str(wp.get("subject") or "")


def work_package_id(wp: dict[str, Any]) -> int:
    return int(wp["id"])


def work_package_description_text(wp: dict[str, Any]) -> str:
    description = wp.get("description")
    if isinstance(description, dict):
        raw = description.get("raw")
        if isinstance(raw, str):
            return raw
    if isinstance(description, str):
        return description
    return ""


def fetch_root_work_package(base_url: str, token: str, root_work_package_id: int) -> dict[str, Any]:
    return api_get_json(base_url, token, f"/api/v3/work_packages/{root_work_package_id}")


def fetch_descendants(
    base_url: str,
    token: str,
    project_id: str,
    root_work_package_id: int,
    page_size: int,
) -> list[dict[str, Any]]:
    filters = [
        {"project": {"operator": "=", "values": [str(project_id)]}},
        {"ancestor": {"operator": "=", "values": [str(root_work_package_id)]}},
    ]
    document = api_get_json(
        base_url,
        token,
        "/api/v3/work_packages",
        query={"filters": json.dumps(filters), "pageSize": str(page_size)},
    )
    total = int(document.get("total") or 0)
    elements = embedded_elements(document)
    if total > len(elements):
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"The subtree contains {total} work packages, but only {len(elements)} were returned. "
            "Increase --page-size or add pagination support.",
        )
    return elements


def fetch_relations_for_work_package(
    base_url: str,
    token: str,
    work_package_id_value: int,
    page_size: int,
) -> list[dict[str, Any]]:
    filters = [{"involved": {"operator": "=", "values": [str(work_package_id_value)]}}]
    document = api_get_json(
        base_url,
        token,
        "/api/v3/relations",
        query={"filters": json.dumps(filters), "pageSize": str(page_size)},
    )
    total = int(document.get("total") or 0)
    elements = embedded_elements(document)
    if total > len(elements):
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"WP-{work_package_id_value} has {total} relations, but only {len(elements)} were "
            "returned.",
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
        for relation in fetch_relations_for_work_package(base_url, token, wp_id, page_size):
            relations_by_id[int(relation["id"])] = relation
    return [relations_by_id[relation_id] for relation_id in sorted(relations_by_id)]


def normalize_relation(relation: dict[str, Any]) -> tuple[int, int, str]:
    links = relation.get("_links") or {}
    from_id = extract_wp_id_from_href((links.get("from") or {}).get("href"))
    to_id = extract_wp_id_from_href((links.get("to") or {}).get("href"))
    relation_type = str(relation.get("type") or "")
    if from_id is None or to_id is None or not relation_type:
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"Unable to parse OpenProject relation {relation.get('id')}.",
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


def build_predecessor_map(relations: list[dict[str, Any]]) -> dict[int, set[int]]:
    predecessor_map: dict[int, set[int]] = {}
    for relation in relations:
        from_id, to_id, relation_type = normalize_relation(relation)
        if relation_type == "precedes":
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
    fetched = api_get_json(base_url, token, f"/api/v3/work_packages/{wp_id}")
    work_packages_by_id[wp_id] = fetched
    return fetched


def build_candidate(base_url: str, wp: dict[str, Any], predecessor_ids: set[int]) -> Candidate:
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
        f"selected from version={version or '(none)'}; status={status}; type={type_name}; "
        f"all predecessors closed; predecessors={predecessor_text}; "
        f"parent_id={parent_id if parent_id is not None else 'none'}; story_id={story_id}"
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


def candidate_sort_key(candidate: Candidate) -> tuple[tuple[int, str], int, int]:
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
        raise ScriptError(
            "NO_STORY_FOUND",
            f"All matching Stories beneath WP-{root_work_package_id} are closed.",
        )

    status_candidates = [
        story
        for story in active_version_stories
        if work_package_status_name(story) == target_status
    ]
    if not status_candidates:
        active_statuses = sorted(
            {work_package_status_name(story) for story in active_version_stories}
        )
        raise ScriptError(
            "NO_STORY_FOUND",
            f"The active version is {active_version_name}, but it has no Stories with status "
            f"{target_status!r}. Current unfinished statuses: {active_statuses}",
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

        eligible_candidates.append(build_candidate(base_url, story, predecessor_ids))

    if not eligible_candidates:
        raise ScriptError("NO_STORY_FOUND", "No eligible next story was found.")

    selected = sorted(eligible_candidates, key=candidate_sort_key)[0]
    return selected, blocked_stories, active_version_name, None


def resolve_root_work_package_id(args: argparse.Namespace, env: dict[str, str]) -> int:
    if args.root_work_package_id is not None:
        return args.root_work_package_id

    for env_key in ROOT_ID_ENV_KEYS:
        raw_value = env.get(env_key)
        if not raw_value:
            continue
        try:
            return int(raw_value)
        except ValueError as err:
            raise ScriptError(
                "INVALID_ROOT_WORK_PACKAGE_ID",
                f"{env_key} must be an integer work package ID.",
            ) from err

    raise ScriptError(
        "MISSING_ROOT_WORK_PACKAGE_ID",
        "No root work package ID was provided. Pass <root_work_package_id> explicitly or set "
        "OPENPROJECT_INITIATIVE_ID in .env.resolved.",
    )


def extract_acceptance_criteria(description: str) -> list[str]:
    criteria: list[str] = []
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if re.match(r"^[-*]\s+", line):
            criteria.append(re.sub(r"^[-*]\s+", "", line))
    return criteria


def build_story_summary(
    candidate: Candidate,
    candidate_wp: dict[str, Any],
    blocked_stories: list[BlockedStory],
) -> dict[str, Any]:
    description = work_package_description_text(candidate_wp)
    risks = []
    if candidate.predecessor_ids:
        risks.append(
            "Story depends on completed predecessor tracking: "
            + ", ".join(f"WP-{wp_id}" for wp_id in candidate.predecessor_ids)
        )
    if blocked_stories:
        risks.append(
            f"Other candidate stories in {candidate.version} are still blocked by dependencies."
        )
    return {
        "goal": description.splitlines()[0].strip() if description.strip() else candidate.subject,
        "acceptance_criteria": extract_acceptance_criteria(description),
        "likely_files": [],
        "risks": risks,
    }


def build_story_payload(
    candidate: Candidate,
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "story": {
            "id": candidate.story_id,
            "subject": candidate.subject,
            "type": candidate.type_name,
            "status": candidate.status,
            "version": candidate.version,
            "parent_id": candidate.parent_epic_id,
            "url": candidate.url,
        },
        "summary": summary,
        "read_only": True,
    }


def build_release_ready_payload(
    root_work_package_id: int,
    root_work_package: dict[str, Any],
    active_version: str,
    release_ready: ReleaseReadyVersion,
) -> dict[str, Any]:
    return {
        "ok": True,
        "story": None,
        "release": {
            "root_work_package_id": root_work_package_id,
            "root_subject": work_package_subject(root_work_package),
            "version": release_ready.name,
            "status": release_ready.status,
            "completed_story_count": release_ready.closed_story_count,
            "total_story_count": release_ready.total_story_count,
            "active_version": active_version,
        },
        "summary": {
            "goal": f"Prepare the release for {release_ready.name}.",
            "acceptance_criteria": [],
            "likely_files": [],
            "risks": [],
        },
        "read_only": True,
    }


def build_error_payload(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message},
        "read_only": True,
    }


def discover_next_story(args: argparse.Namespace) -> dict[str, Any]:
    env = parse_env_file(Path(args.env_file))
    require_env(env, ["OPENPROJECT_URL", "OPENPROJECT_PROJECT_ID", "OPENPROJECT_API_TOKEN"])
    root_work_package_id = resolve_root_work_package_id(args, env)

    base_url = env["OPENPROJECT_URL"].rstrip("/")
    project_id = env["OPENPROJECT_PROJECT_ID"]
    token = env["OPENPROJECT_API_TOKEN"]

    root_wp = fetch_root_work_package(base_url, token, root_work_package_id)
    root_project_href = ((root_wp.get("_links") or {}).get("project") or {}).get("href")
    root_project_id = extract_project_id_from_href(root_project_href)
    if root_project_id is not None and root_project_id != int(project_id):
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            f"WP-{root_work_package_id} belongs to project {root_project_id}, but "
            "OPENPROJECT_PROJECT_ID points at a different project.",
        )

    types_document = api_get_json(base_url, token, f"/api/v3/projects/{project_id}/types")
    statuses_document = api_get_json(base_url, token, "/api/v3/statuses")
    versions_document = api_get_json(base_url, token, f"/api/v3/projects/{project_id}/versions")

    types = embedded_elements(types_document)
    statuses = embedded_elements(statuses_document)
    versions = embedded_elements(versions_document)

    chosen_type = find_named_element(types, args.type)
    chosen_status = find_named_element(statuses, args.status)
    closed_status_names = {
        str(status.get("name")) for status in statuses if bool(status.get("isClosed"))
    }
    if not closed_status_names:
        raise ScriptError(
            "OPENPROJECT_LOOKUP_FAILED",
            "OpenProject returned no statuses marked as closed.",
        )

    descendants = fetch_descendants(
        base_url,
        token,
        project_id,
        root_work_package_id,
        args.page_size,
    )
    work_packages_by_id = {work_package_id(wp): wp for wp in descendants}
    stories = [
        wp
        for wp in descendants
        if work_package_type_name(wp) == str(chosen_type.get("name") or args.type)
    ]
    if not stories:
        raise ScriptError(
            "NO_STORY_FOUND",
            f"No {args.type} work packages were found beneath WP-{root_work_package_id}.",
        )

    story_ids = {work_package_id(story) for story in stories}
    relations = fetch_all_relations(base_url, token, story_ids, args.page_size)
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
        root_work_package_id=root_work_package_id,
        version_status_by_name=version_status_by_name,
    )

    if release_ready is not None:
        return build_release_ready_payload(
            root_work_package_id,
            root_wp,
            active_version,
            release_ready,
        )

    candidate_wp = fetch_missing_work_package(
        base_url,
        token,
        work_packages_by_id,
        candidate.story_id,
    )
    summary = build_story_summary(candidate, candidate_wp, blocked_stories)
    return build_story_payload(candidate, summary)


def emit_non_json(payload: dict[str, Any]) -> int:
    if not payload["ok"]:
        print(f"Error: {payload['error']['message']}", file=sys.stderr)
        return 2

    story = payload.get("story")
    if story is None:
        release = payload["release"]
        print("Release Ready:")
        print(
            f"- Root Work Package: WP-{release['root_work_package_id']} — {release['root_subject']}"
        )
        print(f"- Active Version: {release['active_version']}")
        print(f"- Version Status: {release['status']}")
        print(
            f"- Completed Stories: {release['completed_story_count']}/"
            f"{release['total_story_count']}"
        )
        print()
        print(f"Next action: Prepare the release for {release['version']}.")
        return 0

    print("Next Story:")
    print(f"- OpenProject ID: {story['id']}")
    print(f"- Subject: {story['subject']}")
    print(f"- Type: {story['type']}")
    print(f"- Status: {story['status']}")
    print(f"- Version: {story['version']}")
    print(f"- Parent ID: {story['parent_id']}")
    print(f"- URL: {story['url']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = discover_next_story(args)
    except ScriptError as err:
        payload = build_error_payload(err.code, str(err))

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 2

    return emit_non_json(payload)


if __name__ == "__main__":
    sys.exit(main())

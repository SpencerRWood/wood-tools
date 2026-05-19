#!/usr/bin/env python3
"""Read .env.resolved and report the next OpenProject Story to implement."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
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
    url: str
    branch: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.resolved", help="Resolved env file path.")
    parser.add_argument("--json", action="store_true", help="Output result as JSON.")
    parser.add_argument("--status", default="New", help="Target Story status name.")
    parser.add_argument("--type", default="Story", help="Target work package type name.")
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
        env[key.strip()] = value
    return env


def require_env(env: dict[str, str], keys: list[str]) -> None:
    missing = [key for key in keys if not env.get(key)]
    if missing:
        raise RuntimeError(f"Missing required environment values: {', '.join(missing)}")


def api_get_json(base_url: str, token: str, path: str, query: dict[str, str] | None = None) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"

    credentials = base64.b64encode(f"apikey:{token}".encode()).decode("ascii")
    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/json")

    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenProject API error {err.code} for {path}: {body[:200]}") from err
    except URLError as err:
        raise RuntimeError(f"OpenProject connection failed for {path}: {err.reason}") from err


def find_named_element(elements: list[dict], expected_name: str) -> dict:
    for element in elements:
        if str(element.get("name") or "") == expected_name:
            return element
    raise RuntimeError(f"OpenProject value not found: {expected_name}")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "story"


def parse_milestone_rank(version_name: str) -> tuple[int, str]:
    if not version_name:
        return (10_000, "")
    match = re.search(r"\bM(\d+)\b", version_name, flags=re.IGNORECASE)
    if not match:
        return (9_000, version_name.lower())
    return (int(match.group(1)), version_name.lower())


def extract_wp_id_from_href(href: str | None) -> int | None:
    if not href:
        return None
    match = re.search(r"/work_packages/(\d+)", href)
    if not match:
        return None
    return int(match.group(1))


def build_candidate(base_url: str, wp: dict) -> Candidate:
    links = wp.get("_links") or {}
    version = (links.get("version") or {}).get("title") or ""
    parent = links.get("parent") or {}
    parent_title = parent.get("title") or "(none)"
    parent_id = extract_wp_id_from_href(parent.get("href"))
    story_id = int(wp.get("id"))
    subject = str(wp.get("subject") or "")
    status = (links.get("status") or {}).get("title") or ""
    type_name = (links.get("type") or {}).get("title") or ""
    self_href = (links.get("self") or {}).get("href") or f"/api/v3/work_packages/{story_id}"
    url = f"{base_url.rstrip('/')}/work_packages/{story_id}"
    reason = (
        f"selected by status={status}, type={type_name}, milestone={version or '(none)'}, "
        f"parent_epic_id={parent_id if parent_id is not None else 'none'}, story_id={story_id}"
    )

    return Candidate(
        story_id=story_id,
        subject=subject,
        status=status,
        type_name=type_name,
        version=version or "(none)",
        parent_epic=parent_title,
        parent_epic_id=parent_id,
        url=url if self_href else url,
        branch=f"feature/op-{story_id}-{slugify(subject)}",
        reason=reason,
    )


def choose_candidate(base_url: str, stories: list[dict]) -> Candidate:
    candidates = [build_candidate(base_url, story) for story in stories]
    if not candidates:
        raise RuntimeError("No matching Story work packages found.")

    def sort_key(c: Candidate) -> tuple[tuple[int, str], int, int]:
        milestone = parse_milestone_rank(c.version if c.version != "(none)" else "")
        parent = c.parent_epic_id if c.parent_epic_id is not None else 10_000_000
        return (milestone, parent, c.story_id)

    return sorted(candidates, key=sort_key)[0]


def main() -> int:
    args = parse_args()

    try:
        env = parse_env_file(Path(args.env_file))
        require_env(env, ["OPENPROJECT_URL", "OPENPROJECT_PROJECT_ID", "OPENPROJECT_API_TOKEN"])

        base_url = env["OPENPROJECT_URL"].rstrip("/")
        project_id = env["OPENPROJECT_PROJECT_ID"]
        token = env["OPENPROJECT_API_TOKEN"]

        types_doc = api_get_json(base_url, token, f"/api/v3/projects/{project_id}/types")
        statuses_doc = api_get_json(base_url, token, "/api/v3/statuses")

        types = (types_doc.get("_embedded") or {}).get("elements") or []
        statuses = (statuses_doc.get("_embedded") or {}).get("elements") or []

        chosen_type = find_named_element(types, args.type)
        chosen_status = find_named_element(statuses, args.status)

        filters = [
            {"project": {"operator": "=", "values": [str(project_id)]}},
            {"type": {"operator": "=", "values": [str(chosen_type.get("id"))]}},
            {"status": {"operator": "=", "values": [str(chosen_status.get("id"))]}},
        ]

        wp_doc = api_get_json(
            base_url,
            token,
            "/api/v3/work_packages",
            query={"filters": json.dumps(filters), "pageSize": "200"},
        )

        stories = (wp_doc.get("_embedded") or {}).get("elements") or []
        candidate = choose_candidate(base_url, stories)

    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "openproject_id": candidate.story_id,
                    "subject": candidate.subject,
                    "status": candidate.status,
                    "type": candidate.type_name,
                    "version": candidate.version,
                    "parent_epic": candidate.parent_epic,
                    "suggested_branch": candidate.branch,
                    "url": candidate.url,
                    "reason": candidate.reason,
                    "next_action": (
                        f"Create or checkout {candidate.branch}, then give Codex the Story packet "
                        f"for OP-{candidate.story_id}."
                    ),
                },
                indent=2,
            )
        )
        return 0

    print("Next Story:")
    print(f"- OpenProject ID: {candidate.story_id}")
    print(f"- Subject: {candidate.subject}")
    print(f"- Status: {candidate.status}")
    print(f"- Type: {candidate.type_name}")
    print(f"- Version: {candidate.version}")
    print(f"- Parent: {candidate.parent_epic}")
    print(f"- Branch: {candidate.branch}")
    print(f"- URL: {candidate.url}")
    print(f"- Selection Reason: {candidate.reason}")
    print("\nNext action:")
    print(
        "Create or checkout the branch above, then give Codex the Story packet "
        f"for OP-{candidate.story_id}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

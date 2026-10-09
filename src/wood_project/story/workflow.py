"""Bounded Story business operations shared by the public CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wood_project.openproject import (
    OpenProjectClient,
    embedded_elements,
    link_href,
    summarize_work_package,
)
from wood_project.openproject.context import RepositoryContextError, repository_context
from wood_project.planning_release import release_number

from . import discovery, session
from .epics import completed_status_names, epic_stories, incomplete_stories
from .models import StoryWorkflowError
from .openproject import (
    api_get_json,
    api_request_json,
    extract_id_from_href,
    find_named_element,
    work_package_description_text,
    work_package_status_name,
    work_package_type_name,
    work_package_version_name,
)

PAGE_SIZE = 100
TRANSITIONS = {
    "new": {"in progress", "blocked", "on hold", "rejected"},
    "in progress": {"blocked", "on hold", "closed", "rejected"},
    "blocked": {"in progress", "rejected"},
    "on hold": {"in progress", "rejected"},
}


def _verify_ci_run(url: str, expected_repository: str) -> None:
    parsed = urlparse(url)
    match = re.fullmatch(r"/([^/]+)/([^/]+)/actions/runs/(\d+)", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or match is None:
        raise StoryWorkflowError("INVALID_EVIDENCE", "CI URL must identify a GitHub Actions run.")
    owner, repository, run_id = match.groups()
    if repository.casefold() != expected_repository.casefold():
        raise StoryWorkflowError("INVALID_EVIDENCE", "CI run belongs to another repository.")
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repository}/actions/runs/{run_id}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StoryWorkflowError(
            "CI_UNAVAILABLE", "Could not verify the GitHub Actions run."
        ) from exc
    if result.returncode != 0:
        raise StoryWorkflowError("CI_UNAVAILABLE", "Could not verify the GitHub Actions run.")
    try:
        run = json.loads(result.stdout)
    except ValueError as exc:
        raise StoryWorkflowError("CI_UNAVAILABLE", "GitHub returned invalid run data.") from exc
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise StoryWorkflowError("VALIDATION_REQUIRED", "GitHub Actions run has not passed.")


def _repository(description: str) -> str:
    match = re.search(r"^Primary Repository:\s*(.+)$", description, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _story(client: OpenProjectClient, story_id: int) -> dict[str, Any]:
    story = api_get_json(client, f"/api/v3/work_packages/{story_id}")
    if work_package_type_name(story) != "Story":
        raise StoryWorkflowError("NOT_A_STORY", f"WP-{story_id} is not a Story.")
    return story


def _root(client: OpenProjectClient, ref: str) -> tuple[int, int | None]:
    if ref.isdecimal():
        try:
            root = api_get_json(client, f"/api/v3/work_packages/{ref}")
        except StoryWorkflowError:
            root = {}
        if work_package_type_name(root) == "Initiative":
            return int(ref), extract_id_from_href(link_href(root, "project"), "projects")
        project = api_get_json(client, f"/api/v3/projects/{ref}")
        return 0, int(project["id"])
    projects = discovery.fetch_collection(client, "/api/v3/projects", query={}, page_size=PAGE_SIZE)
    matches = [
        p for p in projects if ref in {str(p.get("identifier") or ""), str(p.get("name") or "")}
    ]
    if len(matches) == 1:
        return 0, int(matches[0]["id"])
    if len(matches) > 1:
        raise StoryWorkflowError("AMBIGUOUS_SELECTOR", "Project reference is ambiguous.")
    try:
        project_id = repository_context(keys=("project_id",)).project_id
    except RepositoryContextError as exc:
        raise StoryWorkflowError(exc.code, str(exc)) from exc
    filters = [{"project": {"operator": "=", "values": [str(project_id)]}}] if project_id else []
    packages = discovery.fetch_collection(
        client,
        "/api/v3/work_packages",
        query={"filters": json.dumps(filters)},
        page_size=PAGE_SIZE,
    )
    matches = [
        wp
        for wp in packages
        if work_package_type_name(wp) == "Initiative" and wp.get("subject") == ref
    ]
    if len(matches) == 1:
        return int(matches[0]["id"]), extract_id_from_href(
            link_href(matches[0], "project"), "projects"
        )
    if len(matches) > 1:
        raise StoryWorkflowError("AMBIGUOUS_SELECTOR", "Initiative reference is ambiguous.")
    raise StoryWorkflowError("NOT_FOUND", "Project or Initiative reference did not match.")


def list_stories(
    client: OpenProjectClient,
    ref: str,
    *,
    status: str | None = None,
    version: str | None = None,
    offset: int = 0,
    configured_project_id: int | None = None,
) -> dict[str, Any]:
    if offset < 0:
        raise StoryWorkflowError("INVALID_OFFSET", "Offset must be nonnegative.")
    root_id, project_id = _root(client, ref)
    _check_configured_project(project_id, configured_project_id)
    if root_id:
        stories = discovery.fetch_descendants(client, root_id, PAGE_SIZE, project_id)
    else:
        assert project_id is not None
        filters = [{"project": {"operator": "=", "values": [str(project_id)]}}]
        stories = discovery.fetch_collection(
            client,
            "/api/v3/work_packages",
            query={"filters": json.dumps(filters)},
            page_size=PAGE_SIZE,
        )
    stories = [s for s in stories if work_package_type_name(s) == "Story"]
    if status:
        stories = [
            s for s in stories if work_package_status_name(s).casefold() == status.casefold()
        ]
    if version:
        stories = [
            s for s in stories if work_package_version_name(s).casefold() == version.casefold()
        ]
    stories.sort(key=lambda s: int(s["id"]))
    page = stories[offset : offset + 50]
    return {
        "stories": [summarize_work_package(s) for s in page],
        "total": len(stories),
        "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < len(stories) else None,
    }


def get_story(client: OpenProjectClient, story_id: int, *, offset: int = 0) -> dict[str, Any]:
    if offset < 0:
        raise StoryWorkflowError("INVALID_OFFSET", "Offset must be nonnegative.")
    story = _story(client, story_id)
    context = client.story_context(story_id)
    description = work_package_description_text(story)
    chunks = [description[index : index + 450] for index in range(0, len(description), 450)]
    relations = context["relations"]
    context["relations"] = relations[offset : offset + 50]
    context["relation_total"] = len(relations)
    context["next_offset"] = offset + 50 if offset + 50 < max(len(relations), len(chunks)) else None
    context["description"] = {
        "chunks": chunks[offset : offset + 50],
        "offset": offset,
        "next_offset": offset + 50 if offset + 50 < len(chunks) else None,
    }
    context["implementation"] = {
        "goal": discovery.packet_section(description, "Goal"),
        "acceptance_criteria": discovery.acceptance_criteria(description),
        "requirements": discovery.packet_section(description, "Requirement IDs"),
        "dependencies": discovery.packet_section(description, "Dependencies"),
        "repository": _repository(description),
        "non_goals": discovery.packet_section(description, "Non-Goals"),
        "implementation_notes": discovery.packet_section(description, "Implementation Notes"),
    }
    return context


def _check_configured_project(project_id: int | None, configured_project_id: int | None) -> None:
    if configured_project_id is not None and project_id != configured_project_id:
        raise StoryWorkflowError(
            "PROJECT_MISMATCH", "Configured project_id does not match the selected Initiative."
        )


def next_story(
    client: OpenProjectClient, ref: str, *, configured_project_id: int | None = None
) -> dict[str, Any]:
    root_id, project_id = _root(client, ref)
    _check_configured_project(project_id, configured_project_id)
    if not root_id:
        assert project_id is not None
        filters = [{"project": {"operator": "=", "values": [str(project_id)]}}]
        packages = discovery.fetch_collection(
            client,
            "/api/v3/work_packages",
            query={"filters": json.dumps(filters)},
            page_size=PAGE_SIZE,
        )
        initiatives = sorted(
            (wp for wp in packages if work_package_type_name(wp) == "Initiative"),
            key=lambda wp: int(wp["id"]),
        )
        if len(initiatives) != 1:
            raise StoryWorkflowError(
                "INITIATIVE_REQUIRED",
                "Project has multiple Initiatives; pass an Initiative reference.",
            )
        root_id = int(initiatives[0]["id"])
    return discovery.discover_next_story(
        client=client,
        root_work_package_id=root_id,
        target_status="New",
        story_type="Story",
        page_size=PAGE_SIZE,
    )


def _status(
    client: OpenProjectClient, story: dict[str, Any], target: str, *, apply: bool
) -> dict[str, Any]:
    story_id = int(story["id"])
    current = work_package_status_name(story)
    if current.casefold() == target.casefold():
        raise StoryWorkflowError("INVALID_TRANSITION", f"WP-{story_id} is already {current}.")
    statuses = embedded_elements(api_get_json(client, "/api/v3/statuses"))
    status = find_named_element(statuses, target)
    allowed = TRANSITIONS.get(current.casefold(), set())
    closing = bool(status.get("isClosed")) and current.casefold() == "in progress"
    if target.casefold() not in allowed and not closing:
        raise StoryWorkflowError(
            "INVALID_TRANSITION", f"Transition from {current} to {target} is not allowed."
        )
    href = link_href(status, "self")
    if not href or not isinstance(story.get("lockVersion"), int):
        raise StoryWorkflowError("STATUS_UPDATE_FAILED", f"WP-{story_id} is not updatable.")
    result = {"id": story_id, "from_status": current, "to_status": target, "dry_run": not apply}
    if apply:
        updated = api_request_json(
            "PATCH",
            client,
            f"/api/v3/work_packages/{story_id}",
            body={"lockVersion": story["lockVersion"], "_links": {"status": {"href": href}}},
        )
        if work_package_status_name(updated).casefold() != target.casefold():
            raise StoryWorkflowError(
                "STATUS_UPDATE_FAILED", "OpenProject returned a different status."
            )
        result["status"] = work_package_status_name(updated)
    return result


def set_story_status(
    client: OpenProjectClient, story_id: int, target: str, *, apply: bool
) -> dict[str, Any]:
    story = _story(client, story_id)
    target_status = find_named_element(
        embedded_elements(api_get_json(client, "/api/v3/statuses")), target
    )
    if (target_status.get("isClosed") and target.casefold() != "rejected") or target.casefold() in {
        "blocked",
        "on hold",
    }:
        raise StoryWorkflowError(
            "LIFECYCLE_COMMAND_REQUIRED",
            "Use story complete or story block for this transition.",
        )
    if target.casefold() == "in progress":
        _ready(client, story, allow_on_hold=True)
    return _status(client, story, target, apply=apply)


def _ready(
    client: OpenProjectClient,
    story: dict[str, Any],
    *,
    allow_on_hold: bool = False,
    allow_in_progress: bool = False,
) -> None:
    story_id = int(story["id"])
    allowed = {"new", "on hold", "blocked"} if allow_on_hold else {"new"}
    if allow_in_progress:
        allowed.add("in progress")
    if work_package_status_name(story).casefold() not in allowed:
        raise StoryWorkflowError("NOT_READY", "Story is not ready to enter In progress.")
    version_name = work_package_version_name(story)
    version_id = extract_id_from_href(link_href(story, "version"), "versions")
    if not version_name or version_id is None or release_number(version_name) is None:
        raise StoryWorkflowError("NOT_READY", "Story has no active R# planning version.")
    version = api_get_json(client, f"/api/v3/versions/{version_id}")
    if str(version.get("status") or "").casefold() != "open":
        raise StoryWorkflowError("NOT_READY", "Story's planning version is not open.")
    predecessors = discovery.fetch_predecessor_map(client, {story_id}, PAGE_SIZE).get(
        story_id, set()
    )
    for predecessor in sorted(predecessors):
        previous = api_get_json(client, f"/api/v3/work_packages/{predecessor}")
        if work_package_status_name(previous).casefold() != "closed":
            raise StoryWorkflowError("DEPENDENCY_BLOCKED", f"WP-{predecessor} is not Closed.")


def start_story(
    client: OpenProjectClient,
    story_id: int,
    *,
    apply: bool,
    owner: str,
    worktree: Path,
    project_id: int | None = None,
    initiative_id: int | None = None,
) -> dict[str, Any]:
    owner = session.owner_id(owner)
    root, directory = session.locations()
    with session.locked(directory, apply=apply):
        story = _story(client, story_id)
        _ready(client, story, allow_on_hold=True, allow_in_progress=True)
        primary = Path(session.worktrees(root)[0]["worktree"]).resolve()
        repository = _repository(work_package_description_text(story))
        if not repository or primary.name != repository:
            raise StoryWorkflowError(
                "REPOSITORY_MISMATCH", "Primary checkout must match the Story's Primary Repository."
            )
        try:
            context = repository_context(
                root=primary,
                keys=tuple(
                    key
                    for key, value in (("project_id", project_id), ("initiative_id", initiative_id))
                    if value is None
                ),
            )
        except RepositoryContextError as exc:
            raise StoryWorkflowError(exc.code, str(exc)) from exc
        selected_project = project_id if project_id is not None else context.project_id
        selected_initiative = initiative_id if initiative_id is not None else context.initiative_id
        if any(
            value is not None and value <= 0 for value in (selected_project, selected_initiative)
        ):
            raise StoryWorkflowError(
                "INVALID_INPUT", "Project and Initiative IDs must be positive."
            )
        project_id = extract_id_from_href(link_href(story, "project"), "projects")
        if selected_project is not None and selected_project != project_id:
            raise StoryWorkflowError(
                "REPOSITORY_MISMATCH", "Story project differs from repository mapping."
            )
        if selected_initiative is not None:
            ancestor = story
            seen = {story_id}
            for _ in range(20):
                parent = extract_id_from_href(link_href(ancestor, "parent"), "work_packages")
                if parent == selected_initiative:
                    break
                if parent is None or parent in seen:
                    raise StoryWorkflowError(
                        "REPOSITORY_MISMATCH", "Story is outside the mapped Initiative."
                    )
                seen.add(parent)
                ancestor = api_get_json(client, f"/api/v3/work_packages/{parent}")
            else:
                raise StoryWorkflowError(
                    "REPOSITORY_MISMATCH", "Story ancestry exceeds the inspection bound."
                )
        record = session.read_record(directory, story_id)
        owned = record is not None and record["state"] == "claimed"
        branch = f"feature/op-{story_id}-{discovery.slugify(str(story.get('subject') or ''))}"
        server = client.settings.base_url.rstrip("/")
        if owned:
            session.require_owner(record, owner)
            assert record is not None
            if (
                record["server"] != server
                or record["branch"] != branch
                or record["worktree"] != str(worktree.resolve())
                or record["repository"] != str(primary)
                or record["project_id"] != project_id
                or record["initiative_id"] != selected_initiative
            ):
                raise StoryWorkflowError(
                    "CLAIM_CONFLICT", "Recorded Story/worktree identity differs."
                )
        plan = session.plan_worktree(root, worktree, branch, owned=owned)
        if not owned:
            record = session.new_record(
                story_id=story_id,
                subject=str(story.get("subject") or ""),
                owner=owner,
                server=server,
                repository=str(primary),
                worktree=plan["path"],
                project_id=project_id,
                initiative_id=selected_initiative,
            )
            previous = session.read_record(directory, story_id)
            if previous is not None:
                if any(
                    previous[key] != record[key]
                    for key in ("server", "repository", "branch", "worktree")
                ):
                    raise StoryWorkflowError(
                        "CLAIM_CONFLICT", "Released claim has a different Story/worktree identity."
                    )
                record["records"] = previous["records"]
                record["phase"] = previous["phase"]
                record["next_action"] = previous["next_action"]
        assert record is not None
        if work_package_status_name(story).casefold() != "in progress":
            _status(client, story, "In progress", apply=False)
        if apply:
            # Persist recovery identity before either Git or OpenProject mutation.
            session.save(directory, story_id, record)
            session.prepare(root, plan)
        status = (
            {"id": story_id, "status": "In progress", "dry_run": not apply, "resumed": True}
            if work_package_status_name(story).casefold() == "in progress"
            else _status(client, story, "In progress", apply=apply)
        )
        if apply:
            if record["phase"] == "starting":
                record["phase"] = "implementation"
                record["next_action"] = (
                    "Implement the Story and run wood repo validate --json in its worktree."
                )
            record["updated_at"] = datetime.now(UTC).isoformat()
            session.save(directory, story_id, record)
        return {
            "status": status,
            "worktree": plan,
            "session": record,
            "session_file": str(session.record_path(directory, story_id)),
            "next_action": record["next_action"],
            "dry_run": not apply,
        }


def block_story(
    client: OpenProjectClient, story_id: int, reason: str, *, apply: bool
) -> dict[str, Any]:
    if not reason.strip():
        raise StoryWorkflowError("INVALID_REASON", "A blocked reason is required.")
    story = _story(client, story_id)
    statuses = embedded_elements(api_get_json(client, "/api/v3/statuses"))
    available = {str(status.get("name") or "") for status in statuses}
    blocked_status = "Blocked" if "Blocked" in available else "On hold"
    if blocked_status not in available:
        raise StoryWorkflowError(
            "BLOCKED_STATUS_UNAVAILABLE", "No supported blocked status exists."
        )
    result = _status(client, story, blocked_status, apply=apply)
    if apply:
        api_request_json(
            "POST",
            client,
            f"/api/v3/work_packages/{story_id}/activities",
            body={"comment": {"raw": f"Blocked: {reason.strip()}"}},
        )
    return {
        "status": result,
        "reason": reason.strip()[:500],
        "next_action": (
            f"Resolve the blocker, then run story set-status {story_id} 'In progress'."
        ),
    }


def _complete_parent_epic(
    client: OpenProjectClient,
    story: dict[str, Any],
    closed: dict[str, Any],
    completed_statuses: set[str],
) -> dict[str, Any] | None:
    """Check live descendants after Story completion, then update only its parent Epic."""
    epic_id = extract_id_from_href(link_href(story, "parent"), "work_packages")
    if epic_id is None:
        return None
    epic = api_get_json(client, f"/api/v3/work_packages/{epic_id}")
    if work_package_type_name(epic) != "Epic":
        return None
    current = work_package_status_name(epic)
    result: dict[str, Any] = {"id": epic_id, "status": current, "automatically_completed": False}
    if current.casefold() in completed_statuses:
        return {**result, "reason": "already_complete"}
    stories = epic_stories(client, epic_id)
    if not any(int(child["id"]) == int(story["id"]) for child in stories):
        raise StoryWorkflowError(
            "INVALID_CONTEXT", "Completed Story is missing from Epic children."
        )
    incomplete = [int(child["id"]) for child in incomplete_stories(stories, completed_statuses)]
    if incomplete:
        return {
            **result,
            "reason": "incomplete_stories",
            "incomplete_story_count": len(incomplete),
            "incomplete_story_ids": sorted(incomplete)[:50],
        }
    href = link_href(closed, "self")
    if not href or not isinstance(epic.get("lockVersion"), int):
        raise StoryWorkflowError("STATUS_UPDATE_FAILED", f"Epic WP-{epic_id} is not updatable.")
    updated = api_request_json(
        "PATCH",
        client,
        f"/api/v3/work_packages/{epic_id}",
        body={"lockVersion": epic["lockVersion"], "_links": {"status": {"href": href}}},
    )
    if work_package_status_name(updated).casefold() != str(closed["name"]).casefold():
        raise StoryWorkflowError("STATUS_UPDATE_FAILED", "Epic returned a different status.")
    return {**result, "status": work_package_status_name(updated), "automatically_completed": True}


def complete_story(
    client: OpenProjectClient, story_id: int, *, evidence: dict[str, Any], apply: bool
) -> dict[str, Any]:
    story = _story(client, story_id)
    statuses = embedded_elements(api_get_json(client, "/api/v3/statuses"))
    completed_statuses = completed_status_names(statuses)
    current = work_package_status_name(story)
    already_complete = current.casefold() in completed_statuses
    if current.casefold() != "in progress" and not already_complete:
        raise StoryWorkflowError(
            "INVALID_TRANSITION", "Only In progress or already complete Stories can be completed."
        )
    repository = _repository(work_package_description_text(story))
    checks = evidence.get("repository_checks")
    ci = evidence.get("ci")
    if repository and (
        not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(check, dict) or not check.get("name") or check.get("status") != "passed"
            for check in checks
        )
        or not isinstance(ci, dict)
        or ci.get("status") != "passed"
        or not str(ci.get("url") or "").startswith("https://")
    ):
        raise StoryWorkflowError(
            "VALIDATION_REQUIRED",
            "Repository checks and a passed CI run URL are required as validation evidence.",
        )
    if not repository and not evidence.get("validation_summary"):
        raise StoryWorkflowError("VALIDATION_REQUIRED", "Validation evidence is required.")
    if apply and repository:
        _verify_ci_run(str(ci["url"]), repository)
    closed_status = os.environ.get("OPENPROJECT_STORY_CLOSED_STATUS", "Closed")
    closed = find_named_element(statuses, closed_status)
    if not closed.get("isClosed") or closed_status.casefold() == "rejected":
        raise StoryWorkflowError("INVALID_CONTEXT", "Configured completion status is not closed.")
    result = (
        {
            "id": story_id,
            "from_status": current,
            "to_status": current,
            "status": current,
            "dry_run": not apply,
            "already_complete": True,
        }
        if already_complete
        else _status(client, story, closed_status, apply=apply)
    )
    epic = None
    if apply:
        try:
            # Refetch the Story too: a concurrent reparent or reopen must not be inferred away.
            epic = _complete_parent_epic(
                client, _story(client, story_id), closed, completed_statuses
            )
        except StoryWorkflowError as exc:
            raise StoryWorkflowError(
                "EPIC_COMPLETION_FAILED",
                f"Story WP-{story_id} is complete, but its parent Epic check failed ({exc.code}). "
                "Retry story complete with the same evidence to recheck current OpenProject state.",
            ) from exc
    return {
        "status": result,
        "epic": epic,
        "validation_evidence": {
            "repository_checks": [check["name"] for check in checks] if repository else [],
            "ci_url": ci["url"] if repository else None,
        },
        "next_action": "Select the next dependency-ready Story.",
    }


def create_story(
    client: OpenProjectClient,
    *,
    project_id: int,
    initiative_id: int,
    epic_id: int,
    subject: str,
    goal: str,
    requirements: str,
    acceptance: list[str],
    repository: str | None,
    version_id: int,
    apply: bool,
) -> dict[str, Any]:
    if not all((subject.strip(), goal.strip(), requirements.strip())) or not all(
        item.strip() for item in acceptance
    ):
        raise StoryWorkflowError(
            "INVALID_INPUT", "Subject, goal, requirement IDs, and acceptance criteria are required."
        )
    parent = api_get_json(client, f"/api/v3/work_packages/{initiative_id}")
    if (
        work_package_type_name(parent) != "Initiative"
        or extract_id_from_href(link_href(parent, "project"), "projects") != project_id
    ):
        raise StoryWorkflowError(
            "INVALID_CONTEXT", "Initiative must belong to the selected project."
        )
    epic = api_get_json(client, f"/api/v3/work_packages/{epic_id}")
    if (
        work_package_type_name(epic) != "Epic"
        or extract_id_from_href(link_href(epic, "parent"), "work_packages") != initiative_id
    ):
        raise StoryWorkflowError("INVALID_CONTEXT", "Epic must belong to the selected Initiative.")
    types = embedded_elements(api_get_json(client, f"/api/v3/projects/{project_id}/types"))
    story_type = find_named_element(types, "Story")
    version = api_get_json(client, f"/api/v3/versions/{version_id}")
    project_versions = discovery.fetch_collection(
        client, f"/api/v3/projects/{project_id}/versions", query={}, page_size=PAGE_SIZE
    )
    if not any(int(item["id"]) == version_id for item in project_versions) or (
        release_number(str(version.get("name") or "")) is None
        or str(version.get("status") or "").casefold() != "open"
    ):
        raise StoryWorkflowError(
            "INVALID_CONTEXT", "Planning version must be an open R# release in the project."
        )
    description = (
        "Codex Implementation Packet\n\n"
        f"OpenProject\nPrimary Repository: {repository.strip() if repository else ''}\n\n"
        f"Goal\n{goal.strip()}\n\n"
        f"Requirement IDs\n{requirements.strip()}\n\n"
        "Acceptance Criteria\n" + "\n".join(item.strip() for item in acceptance)
    )
    payload = {
        "subject": subject.strip(),
        "description": {"format": "markdown", "raw": description},
        "_links": {
            "type": {"href": link_href(story_type, "self")},
            "parent": {"href": f"/api/v3/work_packages/{epic_id}"},
            "version": {"href": f"/api/v3/versions/{version_id}"},
        },
    }
    if not apply:
        return {
            "dry_run": True,
            "project_id": project_id,
            "initiative_id": initiative_id,
            "epic_id": epic_id,
            "subject": subject.strip(),
            "version_id": version_id,
            "requirements": requirements.strip(),
        }
    created = api_request_json(
        "POST", client, f"/api/v3/projects/{project_id}/work_packages", body=payload
    )
    return {"dry_run": False, "work_package": summarize_work_package(created)}

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectClient, OpenProjectError, load_settings
from wood_project.story.discovery import (
    fetch_descendants,
    fetch_predecessor_map,
    resolve_project_id_from_root,
    version_rank,
)
from wood_project.story.openproject import (
    api_get_json,
    embedded_elements,
    work_package_id,
    work_package_status_name,
    work_package_type_name,
    work_package_version_name,
)

from .github import release_exists, run_gh
from .models import ReleaseWorkflowError
from .tag import inspect_repo_state, normalize_tag, resolve_version, tag_exists


def check(name: str, status: str, message: str, **data: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, **data}


def github_release_status(tag_name: str) -> dict[str, Any]:
    if shutil.which("gh") is None:
        return {
            "checked": False,
            "exists": None,
            "reason": "GitHub CLI (gh) is not installed.",
        }
    auth = run_gh(["auth", "status"], check=False)
    if auth.returncode != 0:
        return {
            "checked": False,
            "exists": None,
            "reason": "GitHub CLI is not authenticated.",
        }
    return {"checked": True, "exists": release_exists(tag_name), "reason": ""}


def story_readiness(
    *,
    root_work_package_id: int,
    openproject_version: str | None,
    config_path: Path | None,
    profile: str | None,
    story_type: str,
    page_size: int,
) -> dict[str, Any]:
    client = OpenProjectClient(load_settings(config_path=config_path, profile=profile))
    root = api_get_json(client, f"/api/v3/work_packages/{root_work_package_id}")
    project_id = resolve_project_id_from_root(
        root,
        configured_project_id=client.settings.project_id,
        root_work_package_id=root_work_package_id,
    )
    statuses = embedded_elements(api_get_json(client, "/api/v3/statuses"))
    closed_status_names = {str(status.get("name")) for status in statuses if status.get("isClosed")}
    versions = embedded_elements(
        api_get_json(client, f"/api/v3/projects/{project_id}/versions")
    )
    version_status = {
        str(version.get("name") or ""): str(version.get("status") or "") for version in versions
    }
    descendants = fetch_descendants(
        client,
        root_work_package_id,
        page_size,
        project_id=project_id,
    )
    stories = [
        work_package
        for work_package in descendants
        if work_package_type_name(work_package) == story_type
    ]
    if openproject_version:
        stories = [
            story for story in stories if work_package_version_name(story) == openproject_version
        ]
    story_ids = {work_package_id(story) for story in stories}
    predecessor_map = fetch_predecessor_map(client, story_ids, page_size)
    by_id = {work_package_id(story): story for story in stories}

    incomplete: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for story in sorted(
        stories,
        key=lambda item: (
            version_rank(work_package_version_name(item) or ""),
            work_package_id(item),
        ),
    ):
        status = work_package_status_name(story)
        if status in closed_status_names:
            continue
        story_id = work_package_id(story)
        entry = {
            "id": story_id,
            "subject": str(story.get("subject") or ""),
            "status": status,
            "version": work_package_version_name(story) or "(none)",
        }
        incomplete.append(entry)
        unfinished_predecessors = [
            predecessor_id
            for predecessor_id in predecessor_map.get(story_id, set())
            if work_package_status_name(by_id.get(predecessor_id, {})) not in closed_status_names
        ]
        if unfinished_predecessors:
            blocked.append({**entry, "unfinished_predecessor_ids": unfinished_predecessors})

    return {
        "checked": True,
        "root_work_package_id": root_work_package_id,
        "version": openproject_version,
        "version_status": version_status.get(openproject_version or "", ""),
        "story_type": story_type,
        "story_count": len(stories),
        "closed_statuses": sorted(closed_status_names),
        "incomplete_stories": incomplete,
        "blocked_stories": blocked,
    }


def check_release(
    *,
    version: str | None,
    pyproject: Path,
    root_work_package_id: int | None,
    openproject_version: str | None,
    config_path: Path | None,
    profile: str | None,
    story_type: str,
    page_size: int,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    resolved_version = resolve_version(version, pyproject)
    tag_name = normalize_tag(resolved_version)
    checks.append(
        check(
            "version",
            "pass",
            f"Resolved release version {resolved_version}.",
            pyproject=str(pyproject),
            version=resolved_version,
        )
    )

    repo_root, repo = inspect_repo_state()
    checks.append(
        check(
            "repository",
            "pass" if repo["working_tree_clean"] and repo["behind"] == 0 else "block",
            "Repository is clean and not behind upstream."
            if repo["working_tree_clean"] and repo["behind"] == 0
            else "Repository has local changes or is behind upstream.",
            repo=repo,
        )
    )

    local_tag_exists = tag_exists(repo_root, tag_name)
    checks.append(
        check(
            "local-tag",
            "block" if local_tag_exists else "pass",
            f"Local tag already exists: {tag_name}."
            if local_tag_exists
            else f"Local tag is available: {tag_name}.",
            tag=tag_name,
            exists=local_tag_exists,
        )
    )

    github_status = github_release_status(tag_name)
    github_check_status = (
        "block" if github_status["checked"] and github_status["exists"] else "pass"
    )
    if not github_status["checked"]:
        github_check_status = "skip"
    checks.append(
        check(
            "github-release",
            github_check_status,
            f"GitHub release already exists for {tag_name}."
            if github_status["exists"]
            else github_status["reason"] or f"GitHub release is available for {tag_name}.",
            tag=tag_name,
            **github_status,
        )
    )

    if root_work_package_id is None:
        try:
            root_work_package_id = load_settings(
                config_path=config_path,
                profile=profile,
            ).initiative_id
        except OpenProjectError:
            root_work_package_id = None

    openproject: dict[str, Any] = {"checked": False}
    if root_work_package_id is None:
        checks.append(
            check(
                "openproject-stories",
                "skip",
                (
                    "OpenProject story readiness requires --root-work-package-id "
                    "or initiative_id in the selected integrations.openproject.projects entry."
                ),
            )
        )
    else:
        try:
            openproject = story_readiness(
                root_work_package_id=root_work_package_id,
                openproject_version=openproject_version,
                config_path=config_path,
                profile=profile,
                story_type=story_type,
                page_size=page_size,
            )
            blockers = len(openproject["incomplete_stories"]) + len(openproject["blocked_stories"])
            checks.append(
                check(
                    "openproject-stories",
                    "pass" if blockers == 0 else "block",
                    "No incomplete or blocked OpenProject stories found."
                    if blockers == 0
                    else "OpenProject has incomplete or blocked stories.",
                    incomplete_count=len(openproject["incomplete_stories"]),
                    blocked_count=len(openproject["blocked_stories"]),
                )
            )
        except (OpenProjectError, ReleaseWorkflowError) as err:
            openproject = {"checked": False, "error": str(err)}
            checks.append(
                check("openproject-stories", "block", f"OpenProject readiness failed: {err}")
            )

    return {
        "ok": True,
        "read_only": True,
        "ready": all(item["status"] != "block" for item in checks),
        "version": {"version": resolved_version, "tag": tag_name},
        "repo": repo,
        "github_release": github_status,
        "openproject": openproject,
        "checks": checks,
    }

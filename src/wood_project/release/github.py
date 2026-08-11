from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .models import ReleaseWorkflowError
from .tag import inspect_repo_state, normalize_tag, resolve_version, run_git, tag_exists


def require_gh_cli() -> None:
    if shutil.which("gh") is None:
        raise ReleaseWorkflowError(
            "GITHUB_RELEASE_FAILED",
            "GitHub CLI (gh) is required to create a GitHub release.",
        )


def run_gh(
    args: list[str],
    *,
    check: bool = True,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        input=stdin_text,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "gh command failed"
        raise ReleaseWorkflowError("GITHUB_RELEASE_FAILED", message)
    return result


def release_exists(tag_name: str) -> bool:
    return run_gh(["release", "view", tag_name], check=False).returncode == 0


def build_command(tag_name: str, generate_notes: bool, notes_body: str | None = None) -> list[str]:
    command = ["gh", "release", "create", tag_name, "--title", tag_name]
    if generate_notes:
        command.append("--generate-notes")
    if notes_body is not None:
        command.extend(["--notes-file", "-"])
    return command


def parse_semver_tag(tag_name: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag_name)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def list_release_tags(repo_root: Path) -> list[str]:
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for line in run_git(["tag", "--list", "v*"], cwd=repo_root).stdout.splitlines():
        tag_name = line.strip()
        if not tag_name:
            continue
        parsed = parse_semver_tag(tag_name)
        if parsed is not None:
            candidates.append((parsed, tag_name))
    candidates.sort()
    return [tag_name for _version, tag_name in candidates]


def resolve_history_boundary(repo_root: Path, current_tag_name: str) -> dict[str, Any]:
    release_tags = [
        tag_name for tag_name in list_release_tags(repo_root) if tag_name != current_tag_name
    ]
    if release_tags:
        previous_tag = release_tags[-1]
        return {
            "kind": "previous-release",
            "label": previous_tag,
            "previous_tag": previous_tag,
            "revision_range": f"{previous_tag}..HEAD",
        }

    return {
        "kind": "first-release",
        "label": "repository start",
        "previous_tag": None,
        "revision_range": "HEAD",
    }


def summarize_commit_subject(subject: str) -> str:
    normalized = subject.strip()
    if not normalized:
        return "Update repository history"
    normalized = re.sub(
        r"^(feat|fix|chore|docs|refactor|test|style)(\([^)]+\))?:\s*",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"^merge:\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+for\s+op-\d+\b", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+op-\d+\b", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s{2,}", " ", normalized).strip(" -:")
    if not normalized:
        return "Update repository history"
    return normalized[0].upper() + normalized[1:]


def is_merge_subject(subject: str) -> bool:
    normalized = subject.strip().lower()
    return (
        normalized.startswith("merge ")
        or normalized.startswith("merge:")
        or normalized.startswith("merge branch")
        or normalized.startswith("merge pull request")
    )


def categorize_subject(subject: str) -> str:
    normalized = subject.strip().lower()
    if any(token in normalized for token in ("release", "tag", "version bump")):
        return "Release Tooling"
    if any(token in normalized for token in ("story", "next-story", "next story")):
        return "Workflow Automation"
    if any(token in normalized for token in ("wood-secrets", "vaultwarden", "secret")):
        return "Secrets and Integrations"
    if any(token in normalized for token in ("wood-project", "project metadata")):
        return "Project Metadata"
    if any(token in normalized for token in ("wood-config", "doctor", "validate", "config")):
        return "Configuration and Validation"
    if any(token in normalized for token in ("init_project", "scaffolding", "initialize")):
        return "Project Setup"
    if any(token in normalized for token in ("documentation", "readme", ".vscode")):
        return "Documentation and Developer Experience"
    return "Other Changes"


def group_subjects(subjects: list[str]) -> list[dict[str, Any]]:
    source_subjects = [subject for subject in subjects if not is_merge_subject(subject)] or subjects
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for subject in source_subjects:
        category = categorize_subject(subject)
        if category not in grouped:
            grouped[category] = []
            order.append(category)
        grouped[category].append(summarize_commit_subject(subject))
    return [{"title": title, "items": grouped[title]} for title in order]


def collect_history_notes(version: str) -> dict[str, Any]:
    repo_root, repo = inspect_repo_state()
    current_tag_name = normalize_tag(version)
    boundary = resolve_history_boundary(repo_root, current_tag_name)
    subjects = [
        line.strip()
        for line in run_git(
            ["log", "--reverse", "--format=%s", boundary["revision_range"]],
            cwd=repo_root,
        ).stdout.splitlines()
        if line.strip()
    ]
    grouped_changes = group_subjects(subjects)

    if boundary["kind"] == "previous-release":
        summary_text = (
            f"Release `v{version}` includes {len(subjects)} commit(s) since the previous "
            f"release `{boundary['previous_tag']}`."
        )
    else:
        summary_text = (
            f"Release `v{version}` is the first release and includes {len(subjects)} commit(s) "
            "from the repository history."
        )

    note_lines = ["## Summary", summary_text, "", "## Changes"]
    if grouped_changes:
        for group in grouped_changes:
            note_lines.extend(["", f"### {group['title']}"])
            note_lines.extend(f"- {item}" for item in group["items"])
    elif boundary["kind"] == "previous-release":
        note_lines.append(
            f"- No commits found after the previous release `{boundary['previous_tag']}`."
        )
    else:
        note_lines.append("- No commits found in repository history.")

    return {
        "source": "branch-history",
        "history_boundary": boundary["kind"],
        "previous_tag": boundary["previous_tag"],
        "boundary_label": boundary["label"],
        "head_commit": repo["head"],
        "revision_range": boundary["revision_range"],
        "commit_count": len(subjects),
        "commits": subjects,
        "grouped_changes": grouped_changes,
        "body": "\n".join(note_lines),
    }


def create_github_release(
    *,
    version: str | None,
    pyproject: Path,
    generate_notes: bool,
    notes_from_history: bool,
    apply: bool,
) -> dict[str, Any]:
    resolved_version = resolve_version(version, pyproject)
    tag_name = normalize_tag(resolved_version)
    notes = collect_history_notes(resolved_version) if notes_from_history else None
    notes_body = notes["body"] if notes is not None else None
    command = build_command(tag_name, generate_notes, notes_body)

    release_payload: dict[str, Any] = {
        "tag": tag_name,
        "version": resolved_version,
        "title": tag_name,
        "generate_notes": generate_notes,
    }
    if notes is not None:
        release_payload["notes"] = notes

    if not apply:
        release_payload["would_create"] = True
        return {
            "ok": True,
            "dry_run": True,
            "release": release_payload,
            "command": command,
            "mutation": {"system": "github", "action": "create_release"},
        }

    require_gh_cli()
    repo_root, _repo = inspect_repo_state()
    if not tag_exists(repo_root, tag_name):
        raise ReleaseWorkflowError(
            "GITHUB_RELEASE_FAILED",
            f"Local git tag does not exist: {tag_name}",
        )
    if run_gh(["auth", "status"], check=False).returncode != 0:
        raise ReleaseWorkflowError(
            "GITHUB_RELEASE_FAILED",
            "GitHub CLI is not authenticated. Run gh auth status or gh auth login first.",
        )
    if release_exists(tag_name):
        raise ReleaseWorkflowError(
            "GITHUB_RELEASE_FAILED",
            f"GitHub release already exists for tag {tag_name}.",
        )

    run_gh(command[1:], stdin_text=notes_body)
    release_payload["created"] = True
    return {
        "ok": True,
        "dry_run": False,
        "release": release_payload,
        "gh_status": "created",
        "mutation": {"system": "github", "action": "create_release"},
    }

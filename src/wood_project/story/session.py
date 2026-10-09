"""Host-local, durable Story claims shared by all agents and Git worktrees.

The advisory lock serializes protocol participants, including the remote status
write. Claims have no timeout: an interrupted process cannot silently surrender
an implementation to a competing agent. This is not a distributed lock or sandbox.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .discovery import slugify
from .models import StoryWorkflowError


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StoryWorkflowError(
            "GIT_UNAVAILABLE", "Cannot inspect or prepare the worktree."
        ) from exc
    if result.returncode:
        raise StoryWorkflowError("GIT_COMMAND_FAILED", "Git rejected the worktree operation.")
    return result.stdout.strip()


def locations() -> tuple[Path, Path]:
    cwd = Path.cwd()
    root = Path(git(cwd, "rev-parse", "--show-toplevel")).resolve()
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    directory = common / "wood-stories"
    if directory.is_symlink():
        raise StoryWorkflowError("SESSION_INVALID", "Claim directory must not be a symlink.")
    return root, directory


def owner_id(owner: str | None) -> str:
    if owner is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", owner) is None:
        raise StoryWorkflowError(
            "INVALID_OWNER", "Supply a stable --owner ID (1–100 safe characters)."
        )
    return owner


def record_path(directory: Path, story_id: int) -> Path:
    if story_id <= 0:
        raise StoryWorkflowError("INVALID_INPUT", "Story ID must be positive.")
    return directory / f"{story_id}.json"


def read_record(directory: Path, story_id: int) -> dict[str, Any] | None:
    path = record_path(directory, story_id)
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("unsafe record")
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != 1
            or record.get("story_id") != story_id
            or record.get("state") not in {"claimed", "released"}
            or not all(
                isinstance(record.get(key), str)
                for key in (
                    "owner",
                    "repository",
                    "worktree",
                    "branch",
                    "server",
                    "phase",
                    "next_action",
                )
            )
            or not isinstance(record.get("records"), list)
            or len(record["records"]) > 20
            or not all(isinstance(path, str) for path in record["records"])
        ):
            raise ValueError("invalid record")
        owner_id(record["owner"])
        return record
    except (OSError, ValueError, UnicodeError) as exc:
        raise StoryWorkflowError(
            "SESSION_INVALID", "Story claim is invalid; inspect it without replacing it."
        ) from exc


def write_atomic(path: Path, record: dict[str, Any]) -> None:
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def save(directory: Path, story_id: int, record: dict[str, Any]) -> None:
    write_atomic(record_path(directory, story_id), record)


@contextmanager
def locked(directory: Path, *, apply: bool) -> Iterator[None]:
    # Previews never create protocol state. Apply always rechecks under the lock.
    if not apply:
        yield
        return
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise StoryWorkflowError("SESSION_INVALID", "Claim directory must not be a symlink.")
        descriptor = os.open(directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StoryWorkflowError(
                    "CLAIM_BUSY", "Another Story operation holds the repository lock; retry later."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    except OSError as exc:
        raise StoryWorkflowError(
            "SESSION_IO_FAILED", "Cannot read or write Story claim state."
        ) from exc


def require_owner(record: dict[str, Any] | None, owner: str | None) -> dict[str, Any]:
    owner_id(owner)
    if record is None or record["state"] != "claimed":
        raise StoryWorkflowError(
            "CLAIM_REQUIRED", "Start the Story to acquire a local claim first."
        )
    if record["owner"] != owner:
        raise StoryWorkflowError("CLAIM_CONFLICT", "Story is claimed by another session owner.")
    return record


def worktrees(root: Path) -> list[dict[str, str]]:
    entries = []
    for block in git(root, "worktree", "list", "--porcelain", "-z").split("\0\0"):
        entry = {}
        for line in block.split("\0"):
            key, _, value = line.partition(" ")
            if key:
                entry[key] = value
        if "worktree" in entry:
            entries.append(entry)
    return entries


def plan_worktree(root: Path, target: Path, branch: str, *, owned: bool) -> dict[str, Any]:
    if not target.is_absolute():
        raise StoryWorkflowError("INVALID_WORKTREE", "--worktree must be an absolute path.")
    target = target.resolve()
    entries = worktrees(root)
    primary = Path(entries[0]["worktree"]).resolve()
    if target == primary or target.is_relative_to(primary):
        raise StoryWorkflowError("INVALID_WORKTREE", "Use a worktree outside the primary checkout.")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if target.is_relative_to(common):
        raise StoryWorkflowError("INVALID_WORKTREE", "Worktree must be outside Git metadata.")
    matching = [entry for entry in entries if entry.get("branch") == f"refs/heads/{branch}"]
    if matching and Path(matching[0]["worktree"]).resolve() != target:
        raise StoryWorkflowError(
            "WORKTREE_CONFLICT", "Story branch is checked out at another path."
        )
    registered = next(
        (entry for entry in entries if Path(entry["worktree"]).resolve() == target), None
    )
    if registered:
        if registered.get("branch") != f"refs/heads/{branch}" or "prunable" in registered:
            raise StoryWorkflowError(
                "WORKTREE_CONFLICT", "Worktree has a different or missing branch."
            )
        dirty = bool(git(target, "status", "--porcelain", "--untracked-files=all"))
        if dirty and not owned:
            raise StoryWorkflowError(
                "DIRTY_WORKTREE", "Unclaimed worktree has changes; inspect them first."
            )
        return {"path": str(target), "branch": branch, "reused": True, "dirty": dirty}
    if target.exists():
        raise StoryWorkflowError(
            "WORKTREE_CONFLICT", "Target exists but is not this Story's worktree."
        )
    exists = bool(git(root, "branch", "--list", branch))
    if not exists:
        git(root, "rev-parse", "--verify", "refs/heads/main^{commit}")
    return {"path": str(target), "branch": branch, "reused": False, "branch_exists": exists}


def prepare(root: Path, plan: dict[str, Any]) -> None:
    if not plan["reused"]:
        args = ["worktree", "add"]
        if not plan["branch_exists"]:
            args += ["-b", plan["branch"]]
        args += [plan["path"], plan["branch"] if plan["branch_exists"] else "refs/heads/main"]
        git(root, *args)


def new_record(
    *,
    story_id: int,
    subject: str,
    owner: str,
    server: str,
    repository: str,
    worktree: str,
    project_id: int | None,
    initiative_id: int | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "story_id": story_id,
        "owner": owner,
        "server": server,
        "repository": repository,
        "worktree": worktree,
        "branch": f"feature/op-{story_id}-{slugify(subject)}",
        "project_id": project_id,
        "initiative_id": initiative_id,
        "state": "claimed",
        "phase": "starting",
        "records": [],
        "updated_at": datetime.now(UTC).isoformat(),
        "next_action": "Resume story start with the same owner and worktree after an interruption.",
    }


def snapshot(directory: Path, story_id: int) -> dict[str, Any]:
    record = read_record(directory, story_id)
    if record is None:
        raise StoryWorkflowError(
            "CLAIM_REQUIRED", "No local Story session exists in this repository."
        )
    target = Path(record["worktree"])
    entries = worktrees(Path.cwd())
    if not any(
        Path(entry["worktree"]).resolve() == target
        and entry.get("branch") == f"refs/heads/{record['branch']}"
        for entry in entries
    ):
        if record["phase"] != "starting" or target.exists():
            raise StoryWorkflowError(
                "WORKTREE_CONFLICT", "Recorded worktree/branch binding changed."
            )
        revision = None
        changes = []
        worktree_state = "unavailable; activation was interrupted before worktree preparation"
    else:
        revision = git(target, "rev-parse", "HEAD")
        changes = git(target, "status", "--porcelain", "--untracked-files=all").splitlines()
        worktree_state = "available"
    return {
        "session": record,
        "session_file": str(record_path(directory, story_id)),
        "observed_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "working_tree_clean": not changes if revision is not None else None,
        "worktree_state": worktree_state,
        "changed_files": changes[:20],
        "changed_files_total": len(changes),
        "changed_files_truncated": len(changes) > 20,
        "records_available": [Path(path).is_file() for path in record["records"]],
        "delivery": "unavailable; use saved Wood delivery evidence separately",
        "next_action": record["next_action"],
    }


def session_command(
    action: str,
    story_id: int,
    *,
    owner: str | None,
    apply: bool,
    phase: str | None = None,
    next_action: str | None = None,
    records: list[Path] | None = None,
) -> dict[str, Any]:
    _, directory = locations()
    with locked(directory, apply=apply):
        record = read_record(directory, story_id)
        if action in {"checkpoint", "release"}:
            record = require_owner(record, owner)
            if action == "checkpoint":
                if phase not in {"implementation", "review", "delivery", "blocked", "complete"}:
                    raise StoryWorkflowError(
                        "INVALID_INPUT", "Choose a supported checkpoint phase."
                    )
                if not next_action or not next_action.strip() or len(next_action) > 500:
                    raise StoryWorkflowError(
                        "INVALID_INPUT", "Supply --next-action (1–500 characters)."
                    )
                references = [str(path.resolve()) for path in records or []]
                if len(references) > 20 or not all(Path(path).is_file() for path in references):
                    raise StoryWorkflowError(
                        "INVALID_INPUT", "Supply at most 20 existing --record files."
                    )
                record.update(phase=phase, next_action=next_action, records=references)
            else:
                record["state"] = "released"
                record["next_action"] = "Inspect saved records before starting another session."
            record["updated_at"] = datetime.now(UTC).isoformat()
            if apply:
                save(directory, story_id, record)
            return {
                "session": record,
                "session_file": str(record_path(directory, story_id)),
                "dry_run": not apply,
                "next_action": record["next_action"],
            }
        data = snapshot(directory, story_id)
        if action == "handoff" and apply:
            # JSON is the cross-agent artifact; no transcript or inferred CI results.
            path = directory / f"{story_id}-handoff.json"
            write_atomic(path, data)
            data["handoff_file"] = str(path)
        return data

"""Configured local workflow files and content-bound validation snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .output import Status


class WorkflowFilesError(Exception):
    def __init__(self, code: str, message: str, status: Status = "invalid") -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def output_directory(root: Path) -> Path:
    project = root / "pyproject.toml"
    value: object = "/private/tmp"
    if project.exists():
        try:
            document = tomllib.loads(project.read_text(encoding="utf-8"))
            section = document.get("tool", {}).get("wood", {}).get("workflow", {})
            if not isinstance(section, dict):
                raise ValueError("workflow must be a table")
            value = section.get("output_directory", value)
        except (OSError, ValueError, AttributeError, UnicodeError) as exc:
            raise WorkflowFilesError(
                "WORKFLOW_CONFIG_INVALID", "Cannot read [tool.wood.workflow] in pyproject.toml."
            ) from exc
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise WorkflowFilesError(
            "WORKFLOW_CONFIG_INVALID", "workflow.output_directory must be a nonblank path."
        )
    try:
        directory = Path(value).expanduser()
        resolved = (directory if directory.is_absolute() else root / directory).resolve()
        if resolved == root.resolve():
            raise WorkflowFilesError(
                "WORKFLOW_CONFIG_INVALID", "Output directory must differ from the repository root."
            )
        return resolved
    except (OSError, RuntimeError) as exc:
        raise WorkflowFilesError(
            "WORKFLOW_CONFIG_INVALID", "Output path cannot be resolved."
        ) from exc


def run_directory(root: Path, prefix: str) -> Path:
    directory = output_directory(root)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
    except OSError as exc:
        raise WorkflowFilesError(
            "WORKFLOW_OUTPUT_UNWRITABLE",
            "Cannot create a run directory in workflow.output_directory.",
        ) from exc


def write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except (OSError, ValueError) as exc:
        raise WorkflowFilesError(
            "WORKFLOW_WRITE_FAILED", "Cannot write workflow JSON file."
        ) from exc


def read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 1_000_000:
            raise ValueError("file too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        raise WorkflowFilesError(
            "WORKFLOW_FILE_INVALID", "Workflow JSON is missing or invalid; regenerate it."
        ) from exc


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkflowFilesError(
            "VALIDATION_SOURCE_UNAVAILABLE", "Cannot inspect Git source."
        ) from exc


def _excluded(name: str, root: Path, directory: Path) -> bool:
    path = Path(name)
    return (
        (directory.is_relative_to(root.resolve()) and (root / path).is_relative_to(directory))
        or any(
            part
            in {
                ".git",
                ".venv",
                "node_modules",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
            }
            for part in path.parts
        )
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.name == ".coverage"
    )


def _digest(entries: list[list[str]]) -> str:
    return hashlib.sha256(json.dumps(sorted(entries), separators=(",", ":")).encode()).hexdigest()


def snapshot_fingerprint(root: Path, checkout: Path, directory: Path) -> str | None:
    """Hash the Git inputs in the disposable copy, including uncommitted new files."""
    listing = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if listing.returncode:
        return None  # Standalone validation can run without a source Git repository.
    algorithm = _git(root, "rev-parse", "--show-object-format").stdout.strip()
    if algorithm not in {"sha1", "sha256"}:
        raise WorkflowFilesError("VALIDATION_SOURCE_UNAVAILABLE", "Unknown Git object format.")
    entries: list[list[str]] = []
    for name in sorted(set(listing.stdout.split("\x00")) - {""}):
        if _excluded(name, root, directory):
            continue
        path = checkout / name
        try:
            if (root / name).is_symlink():
                content = os.readlink(root / name).encode()
                mode = "120000"
            elif path.exists():
                content = path.read_bytes()
                mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
            else:
                continue  # A tracked deletion is part of the validated snapshot.
        except OSError as exc:
            raise WorkflowFilesError(
                "VALIDATION_SOURCE_UNAVAILABLE", "Cannot hash source files."
            ) from exc
        blob = hashlib.new(algorithm, f"blob {len(content)}\0".encode() + content).hexdigest()
        entries.append([name, mode, blob])
    return _digest(entries)


def revision_fingerprint(root: Path, revision: str) -> str:
    result = _git(root, "ls-tree", "-r", "-z", revision)
    if result.returncode:
        raise WorkflowFilesError(
            "VALIDATION_SOURCE_UNAVAILABLE", "PR revision is unavailable locally; fetch it first."
        )
    entries: list[list[str]] = []
    directory = output_directory(root)
    for entry in result.stdout.split("\x00"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, kind, blob = metadata.split()
        if kind != "blob":
            raise WorkflowFilesError(
                "VALIDATION_SOURCE_UNAVAILABLE", "Submodule evidence is unsupported."
            )
        if not _excluded(name, root, directory):
            entries.append([name, mode, blob])
    return _digest(entries)

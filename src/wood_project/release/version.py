from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from .models import ReleaseWorkflowError

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
PROJECT_HEADER_RE = re.compile(r"^\[project\]\s*$")
TABLE_HEADER_RE = re.compile(r"^\[(?!\[)(?P<name>[^\]]+)\]\s*$")
VERSION_LINE_RE = re.compile(
    r'^(?P<prefix>\s*version\s*=\s*)(?P<quote>["\'])(?P<value>[^"\']+)(?P=quote)(?P<suffix>\s*(?:#.*)?)$'
)


def validate_semver(version: str) -> tuple[int, int, int]:
    if version.startswith("v"):
        raise ReleaseWorkflowError(
            "VERSION_BUMP_FAILED",
            "Explicit versions for pyproject.toml must not include a leading 'v'.",
        )
    match = SEMVER_RE.fullmatch(version)
    if match is None:
        raise ReleaseWorkflowError(
            "VERSION_BUMP_FAILED",
            "Version must be a plain semantic version of the form X.Y.Z.",
        )
    return tuple(int(part) for part in match.groups())


def compute_new_version(current_version: str, bump: str) -> str:
    current_major, current_minor, current_patch = validate_semver(current_version)
    if bump == "patch":
        return f"{current_major}.{current_minor}.{current_patch + 1}"
    if bump == "minor":
        return f"{current_major}.{current_minor + 1}.0"
    if bump == "major":
        return f"{current_major + 1}.0.0"
    validate_semver(bump)
    return bump


def load_pyproject(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReleaseWorkflowError(
            "VERSION_BUMP_FAILED",
            f"pyproject.toml not found: {path}",
        )
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ReleaseWorkflowError(
            "VERSION_BUMP_FAILED",
            f"pyproject.toml is not valid TOML: {err}",
        ) from err


def read_static_version(path: Path) -> str:
    document = load_pyproject(path)
    project = document.get("project")
    if not isinstance(project, dict):
        raise ReleaseWorkflowError(
            "VERSION_BUMP_FAILED",
            "pyproject.toml is missing the [project] table.",
        )

    dynamic = project.get("dynamic")
    if isinstance(dynamic, list) and "version" in dynamic:
        raise ReleaseWorkflowError(
            "VERSION_BUMP_FAILED",
            "pyproject.toml uses dynamic versioning for [project].version.",
        )

    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseWorkflowError(
            "VERSION_BUMP_FAILED",
            "pyproject.toml is missing [project].version.",
        )

    validate_semver(version)
    return version


def replace_project_version(text: str, new_version: str) -> str:
    lines = text.splitlines(keepends=True)
    in_project = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if PROJECT_HEADER_RE.match(stripped):
            in_project = True
            continue

        table_match = TABLE_HEADER_RE.match(stripped)
        if table_match:
            in_project = table_match.group("name") == "project"
            continue

        if not in_project:
            continue

        version_match = VERSION_LINE_RE.match(line.rstrip("\n"))
        if version_match:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = (
                f"{version_match.group('prefix')}{version_match.group('quote')}{new_version}"
                f"{version_match.group('quote')}{version_match.group('suffix')}{newline}"
            )
            return "".join(lines)

    raise ReleaseWorkflowError(
        "VERSION_BUMP_FAILED",
        "Unable to locate the [project].version assignment in pyproject.toml.",
    )


def bump_version(*, bump: str, pyproject: Path, apply: bool) -> dict[str, Any]:
    old_version = read_static_version(pyproject)
    new_version = compute_new_version(old_version, bump)
    original_text = pyproject.read_text(encoding="utf-8")
    updated_text = replace_project_version(original_text, new_version)

    if apply:
        pyproject.write_text(updated_text, encoding="utf-8")

    return {
        "ok": True,
        "dry_run": not apply,
        "file": str(pyproject),
        "old_version": old_version,
        "new_version": new_version,
        "changed": apply,
        "changed_files": [str(pyproject)] if apply else [],
        "mutation": {"system": "filesystem", "action": "bump_version"},
    }

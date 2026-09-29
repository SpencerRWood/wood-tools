"""Read the optional OpenProject mapping from the current Git repository."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from .models import StoryWorkflowError


def _context_error(detail: str) -> StoryWorkflowError:
    return StoryWorkflowError(
        "REPOSITORY_CONTEXT_INVALID",
        f"{detail} Pass an explicit project or Initiative reference to wood story next/list.",
    )


def story_reference(ref: str | None, *, cwd: Path | None = None) -> tuple[str, int | None]:
    """Return an explicit reference or the current repository's initiative mapping."""
    if ref is not None:
        return ref, None
    working_directory = cwd or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=working_directory,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _context_error("Cannot find the current Git repository.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise _context_error("Cannot find the current Git repository.")
    pyproject = Path(result.stdout.strip()) / "pyproject.toml"
    try:
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise _context_error("The repository root pyproject.toml is missing or malformed.") from exc
    try:
        mapping = document["tool"]["wood"]["openproject"]
    except (KeyError, TypeError) as exc:
        raise _context_error("The repository has no [tool.wood.openproject] mapping.") from exc
    if not isinstance(mapping, dict):
        raise _context_error("The [tool.wood.openproject] mapping is malformed.")
    initiative_id = mapping.get("initiative_id")
    project_id = mapping.get("project_id")
    if type(initiative_id) is not int or initiative_id <= 0:
        raise _context_error("The mapping needs a positive integer initiative_id.")
    if project_id is not None and (type(project_id) is not int or project_id <= 0):
        raise _context_error("The mapping's project_id must be a positive integer.")
    return str(initiative_id), project_id

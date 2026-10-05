"""Read the optional OpenProject mapping from the current Git repository."""

from __future__ import annotations

from pathlib import Path

from wood_project.openproject.context import RepositoryContextError, repository_context

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
    try:
        context = repository_context(cwd=cwd)
    except RepositoryContextError as exc:
        raise _context_error(str(exc)) from exc
    if context.initiative_id is None:
        raise _context_error("Configure initiative_id in [tool.wood.openproject].")
    return str(context.initiative_id), context.project_id

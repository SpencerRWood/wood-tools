"""Repository-owned planning context, independent of connection credentials."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


class RepositoryContextError(ValueError):
    code = "REPOSITORY_CONTEXT_INVALID"


@dataclass(frozen=True)
class RepositoryContext:
    project_id: int | None = None
    initiative_id: int | None = None


def repository_context(
    *,
    cwd: Path | None = None,
    root: Path | None = None,
    keys: tuple[str, ...] = ("project_id", "initiative_id"),
) -> RepositoryContext:
    """Resolve requested IDs exclusively from the repository's Git-root TOML.

    Missing mappings are optional here; consumers enforce the IDs they need.
    Malformed mappings fail closed without including file contents or values.
    """
    if not keys:
        return RepositoryContext()
    mapping: dict[str, object] = {}
    try:
        if root is None:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=cwd or Path.cwd(),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                root = Path(result.stdout.strip())
        if root is not None:
            path = root / "pyproject.toml"
            if path.exists():
                document = tomllib.loads(path.read_text(encoding="utf-8"))
                mapping = document.get("tool", {}).get("wood", {}).get("openproject", {})
                if not isinstance(mapping, dict):
                    raise ValueError("mapping")
    except (OSError, UnicodeError, ValueError, AttributeError, subprocess.TimeoutExpired) as exc:
        raise RepositoryContextError(
            "Use a readable pyproject.toml with a valid [tool.wood.openproject] mapping."
        ) from exc
    resolved: dict[str, int | None] = {}
    for key in keys:
        if key not in mapping:
            resolved[key] = None
            continue
        identifier = mapping[key]
        if type(identifier) is not int or identifier <= 0:
            raise RepositoryContextError(f"The mapping's {key} must be a positive integer.")
        resolved[key] = identifier
    return RepositoryContext(**resolved)

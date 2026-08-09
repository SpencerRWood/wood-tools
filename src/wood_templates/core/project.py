from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .manifest import TemplateError

PROJECT_FILE_NAME = "project.json"
WOOD_HOME_ENV = "WOOD_HOME"
WOOD_HOME_DIR_NAME = ".wood"


def resolve_project_root(project_root: Path | None = None) -> Path:
    root = (project_root or Path.cwd()).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise TemplateError(f"Project root must be an existing directory: {root}")
    return root


def load_project_document(project_file: Path) -> dict[str, Any]:
    if not project_file.exists():
        raise TemplateError(f"Project file not found: {project_file}")
    try:
        document = json.loads(project_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"Invalid JSON in project file: {exc}") from exc
    if not isinstance(document, dict):
        raise TemplateError("Project document root must be an object.")
    for field in ("project_root", "wood_home"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
            raise TemplateError(f"{field} must be a non-empty absolute path.")
    return document


def slugify_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise TemplateError("Project slug cannot be empty.")
    return normalized

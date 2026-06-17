from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

PROJECT_SCHEMA_VERSION = 1
PROJECT_FILE_NAME = "project.json"
METADATA_DIR_NAME = ".wood"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ProjectError(ValueError):
    """Raised for invalid project metadata or invalid user input."""


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    metadata_dir: Path
    project_file: Path
    artifact_root: Path
    artifact_dir: Path


def slugify_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ProjectError("Project slug cannot be empty.")
    if not SLUG_PATTERN.fullmatch(normalized):
        raise ProjectError("Project slug must use lowercase letters, numbers, and hyphens.")
    return normalized


def resolve_project_root(project_root: Path | None = None) -> Path:
    root = (project_root or Path.cwd()).expanduser().resolve()
    if not root.exists():
        raise ProjectError(f"Project root does not exist: {root}")
    if not root.is_dir():
        raise ProjectError(f"Project root must be a directory: {root}")
    return root


def build_paths(
    *,
    project_root: Path | None = None,
    artifact_root: Path | None = None,
    project_slug: str | None = None,
) -> ProjectPaths:
    resolved_root = resolve_project_root(project_root)
    resolved_slug = project_slug or slugify_project_name(resolved_root.name)
    resolved_artifact_root = (
        artifact_root.expanduser().resolve()
        if artifact_root is not None
        else resolved_root / METADATA_DIR_NAME / "artifacts"
    )
    metadata_dir = resolved_root / METADATA_DIR_NAME
    project_file = resolved_root / PROJECT_FILE_NAME
    artifact_dir = resolved_artifact_root / resolved_slug
    return ProjectPaths(
        project_root=resolved_root,
        metadata_dir=metadata_dir,
        project_file=project_file,
        artifact_root=resolved_artifact_root,
        artifact_dir=artifact_dir,
    )


def generate_project_id() -> str:
    return str(uuid4())


def create_project_document(
    *,
    project_id: str | None,
    project_slug: str | None,
    project_root: Path | None,
    artifact_root: Path | None,
) -> dict[str, Any]:
    paths = build_paths(
        project_root=project_root,
        artifact_root=artifact_root,
        project_slug=project_slug,
    )
    slug = project_slug or slugify_project_name(paths.project_root.name)
    document = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project_id or generate_project_id(),
        "project_slug": slug,
        "project_root": str(paths.project_root),
        "artifact_root": str(paths.artifact_root),
        "artifact_dir": str(paths.artifact_dir),
        "metadata_dir": str(paths.metadata_dir),
    }
    validate_project_document(document)
    return document


def validate_project_document(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise ProjectError("Project document root must be an object.")

    if document.get("schema_version") != PROJECT_SCHEMA_VERSION:
        raise ProjectError(f"schema_version must be {PROJECT_SCHEMA_VERSION}.")

    project_id = document.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ProjectError("project_id must be a non-empty string.")

    project_slug = document.get("project_slug")
    if not isinstance(project_slug, str) or not SLUG_PATTERN.fullmatch(project_slug):
        raise ProjectError("project_slug must use lowercase letters, numbers, and hyphens only.")

    for field in ("project_root", "artifact_root", "artifact_dir", "metadata_dir"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProjectError(f"{field} must be a non-empty string.")
        if not Path(value).is_absolute():
            raise ProjectError(f"{field} must be an absolute path.")

    project_root = Path(document["project_root"])
    artifact_root = Path(document["artifact_root"])
    artifact_dir = Path(document["artifact_dir"])
    metadata_dir = Path(document["metadata_dir"])

    if metadata_dir != project_root / METADATA_DIR_NAME:
        raise ProjectError("metadata_dir must be '<project_root>/.wood'.")
    if artifact_dir.parent != artifact_root:
        raise ProjectError("artifact_dir must be inside artifact_root.")
    if artifact_dir.name != project_slug:
        raise ProjectError("artifact_dir must end with project_slug.")


def save_project_document(project_file: Path, document: dict[str, Any]) -> None:
    validate_project_document(document)
    metadata_dir = Path(document["metadata_dir"])
    artifact_root = Path(document["artifact_root"])
    artifact_dir = Path(document["artifact_dir"])

    metadata_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile("w", encoding="utf-8", dir=project_file.parent, delete=False) as tmp:
        json.dump(document, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(project_file)


def load_project_document(project_file: Path) -> dict[str, Any]:
    if not project_file.exists():
        raise ProjectError(f"Project file not found: {project_file}")
    try:
        document = json.loads(project_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in project file: {exc}") from exc
    validate_project_document(document)
    return document


def init_project(
    *,
    project_root: Path | None,
    artifact_root: Path | None,
    project_id: str | None,
    project_slug: str | None,
    apply: bool,
) -> dict[str, Any]:
    document = create_project_document(
        project_id=project_id,
        project_slug=project_slug,
        project_root=project_root,
        artifact_root=artifact_root,
    )
    project_file = Path(document["project_root"]) / PROJECT_FILE_NAME

    changed = False
    if project_file.exists():
        existing = load_project_document(project_file)
        return {"changed": False, "path": str(project_file), "project": existing}

    if apply:
        save_project_document(project_file, document)
        changed = True

    return {"changed": changed, "path": str(project_file), "project": document}


def show_project(
    project_file: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    return load_project_document(file_path)


def validate_project(
    project_file: Path | None = None, project_root: Path | None = None
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    document = load_project_document(file_path)
    return {"valid": True, "path": str(file_path), "project": document}

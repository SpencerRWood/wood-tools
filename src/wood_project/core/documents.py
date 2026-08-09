from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from .models import ProjectError
from .paths import (
    SLUG_PATTERN,
    WOOD_CONFIG_FILE_NAME,
    WOOD_HOME_DIRECTORY_NAMES,
    _check_mutation_parent,
    _validate_wood_home_location,
    build_paths,
)

PROJECT_SCHEMA_VERSION = 1
PROJECT_DOCUMENT_FIELDS = {
    "schema_version",
    "project_id",
    "project_slug",
    "project_root",
    "wood_home",
    "wood_config_file",
    "linked_repositories",
}


def slugify_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ProjectError("Project slug cannot be empty.")
    if not SLUG_PATTERN.fullmatch(normalized):
        raise ProjectError("Project slug must use lowercase letters, numbers, and hyphens.")
    return normalized


def generate_project_id() -> str:
    return str(uuid4())


def create_project_document(
    *,
    project_id: str | None,
    project_slug: str | None,
    project_root: Path | None,
    wood_home: Path | None,
) -> dict[str, Any]:
    paths = build_paths(
        project_root=project_root,
        wood_home=wood_home,
        project_slug=project_slug,
    )
    slug = project_slug or slugify_project_name(paths.project_root.name)
    document = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_id": project_id or generate_project_id(),
        "project_slug": slug,
        "project_root": str(paths.project_root),
        "wood_home": str(paths.wood_home),
        "wood_config_file": str(paths.wood_config_file),
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

    unexpected_fields = sorted(
        field for field in set(document) - PROJECT_DOCUMENT_FIELDS if not field.endswith("_packs")
    )
    if unexpected_fields:
        raise ProjectError("Unexpected project metadata fields: " + ", ".join(unexpected_fields))

    for field in ("project_root", "wood_home", "wood_config_file"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProjectError(f"{field} must be a non-empty string.")
        if not Path(value).is_absolute():
            raise ProjectError(f"{field} must be an absolute path.")

    project_root = Path(document["project_root"])
    wood_home = Path(document["wood_home"])
    wood_config_file = Path(document["wood_config_file"])

    _validate_wood_home_location(project_root, wood_home)
    if wood_config_file != wood_home / WOOD_CONFIG_FILE_NAME:
        raise ProjectError("wood_config_file must be '<wood_home>/config.toml'.")


def save_project_document(project_file: Path, document: dict[str, Any]) -> None:
    validate_project_document(document)
    wood_home = Path(document["wood_home"])
    wood_config_file = Path(document["wood_config_file"])
    wood_home_dirs = tuple(wood_home / name for name in WOOD_HOME_DIRECTORY_NAMES)

    _check_mutation_parent("project_root", project_file.parent)
    _check_mutation_parent("wood_home", wood_home)

    wood_home.mkdir(parents=True, exist_ok=True)
    if wood_config_file.exists() and not wood_config_file.is_file():
        raise ProjectError(f"wood_config_file must be a file: {wood_config_file}")
    for directory in wood_home_dirs:
        if directory.exists() and not directory.is_dir():
            field = f"wood_home_dirs.{directory.relative_to(wood_home)}"
            raise ProjectError(f"{field} must be a directory: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
    if not wood_config_file.exists():
        wood_config_file.write_text("version = 1\n", encoding="utf-8")

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

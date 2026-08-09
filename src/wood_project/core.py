from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
from uuid import uuid4

PROJECT_SCHEMA_VERSION = 1
RESOURCE_SCHEMA_VERSION = 1
WOOD_TOOLS_VERSION = "0.2.0"
PROJECT_FILE_NAME = "project.json"
RESOURCE_MANIFEST_FILE_NAME = "wood-resource.json"
RESOURCE_INSTALL_METADATA_FILE_NAME = ".wood-resource-install.json"
TEMPLATE_PACK_RESOURCE_KIND = "template"
TEMPLATE_PACK_CONTRACT_FIELD = "template_pack"
TEMPLATE_LOCK_FILE_NAME = "wood.lock.json"
TEMPLATE_LOCK_SCHEMA_VERSION = 1
WOOD_HOME_ENV = "WOOD_HOME"
WOOD_HOME_DIR_NAME = ".wood"
WOOD_CONFIG_FILE_NAME = "config.toml"
WOOD_HOME_DIRECTORY_NAMES = (
    "packs/templates",
    "packs/references",
    "packs/agents",
    "tools",
    "scripts",
    "cache",
    "state",
)
RESOURCE_KIND_DIRECTORIES = {
    "template": "packs/templates",
    "reference": "packs/references",
    "agent": "packs/agents",
    "tool": "tools",
    "script": "scripts",
}
HELPER_RESOURCE_KINDS = {"tool", "script"}
PROJECT_DOCUMENT_FIELDS = {
    "schema_version",
    "project_id",
    "project_slug",
    "project_root",
    "wood_home",
    "wood_config_file",
    "linked_repositories",
    "template_packs",
}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ProjectError(ValueError):
    """Raised for invalid project metadata or invalid user input."""


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    project_file: Path
    wood_home: Path
    wood_config_file: Path
    wood_home_dirs: tuple[Path, ...]


@dataclass(frozen=True)
class LinkedRepository:
    name: str
    path: Path
    role: str | None = None


@dataclass(frozen=True)
class ResourceManifest:
    kind: str
    name: str
    version: str
    digest: str
    compatibility: dict[str, Any]
    helper_contract: dict[str, Any] | None
    template_pack: dict[str, Any] | None
    raw: dict[str, Any]


def slugify_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ProjectError("Project slug cannot be empty.")
    if not SLUG_PATTERN.fullmatch(normalized):
        raise ProjectError("Project slug must use lowercase letters, numbers, and hyphens.")
    return normalized


def _validate_slug(field: str, value: object) -> str:
    if not isinstance(value, str) or not SLUG_PATTERN.fullmatch(value):
        raise ProjectError(f"{field} must use lowercase letters, numbers, and hyphens only.")
    return value


def _validate_version(field: str, value: object) -> str:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ProjectError(f"{field} must be a non-empty version string.")
    return value


def _validate_resource_digest(value: object) -> str:
    if not isinstance(value, str) or not SHA256_DIGEST_PATTERN.fullmatch(value):
        raise ProjectError("digest must be sha256:<64 lowercase hex characters>.")
    return value


def _parse_version_tuple(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    numbers: list[int] = []
    for part in parts:
        match = re.match(r"^(\d+)", part)
        if match is None:
            break
        numbers.append(int(match.group(1)))
    return tuple(numbers or [0])


def _version_satisfies(requirement: str, current_version: str) -> bool:
    requirement = requirement.strip()
    if requirement.startswith(">="):
        return _parse_version_tuple(current_version) >= _parse_version_tuple(requirement[2:])
    if requirement.startswith("=="):
        return current_version == requirement[2:].strip()
    return current_version == requirement


def resolve_project_root(project_root: Path | None = None) -> Path:
    root = (project_root or Path.cwd()).expanduser().resolve()
    if not root.exists():
        raise ProjectError(f"Project root does not exist: {root}")
    if not root.is_dir():
        raise ProjectError(f"Project root must be a directory: {root}")
    return root


def resolve_wood_home(wood_home: Path | None = None) -> Path:
    raw_home = wood_home
    if raw_home is None:
        env_value = os.environ.get(WOOD_HOME_ENV)
        if env_value is not None and not env_value.strip():
            raise ProjectError("WOOD_HOME must be a non-empty path when set.")
        raw_home = Path(env_value) if env_value else Path.home() / WOOD_HOME_DIR_NAME
    return raw_home.expanduser().resolve()


def _validate_wood_home_location(project_root: Path, wood_home: Path) -> None:
    if wood_home == project_root / WOOD_HOME_DIR_NAME:
        raise ProjectError(
            "Wood home must not be the project-local .wood directory. Set WOOD_HOME to a "
            "user-global path or use the default ~/.wood."
        )


def build_paths(
    *,
    project_root: Path | None = None,
    wood_home: Path | None = None,
    project_slug: str | None = None,
) -> ProjectPaths:
    resolved_root = resolve_project_root(project_root)
    resolved_wood_home = resolve_wood_home(wood_home)
    _validate_wood_home_location(resolved_root, resolved_wood_home)
    project_file = resolved_root / PROJECT_FILE_NAME
    return ProjectPaths(
        project_root=resolved_root,
        project_file=project_file,
        wood_home=resolved_wood_home,
        wood_config_file=resolved_wood_home / WOOD_CONFIG_FILE_NAME,
        wood_home_dirs=tuple(resolved_wood_home / name for name in WOOD_HOME_DIRECTORY_NAMES),
    )


def _iter_resource_files(source_dir: Path) -> list[Path]:
    files = [
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and path.name not in {RESOURCE_MANIFEST_FILE_NAME, RESOURCE_INSTALL_METADATA_FILE_NAME}
    ]
    return sorted(files, key=lambda path: path.relative_to(source_dir).as_posix())


def compute_resource_digest(source_dir: Path) -> str:
    resolved_source = source_dir.expanduser().resolve()
    if not resolved_source.exists():
        raise ProjectError(f"Resource source directory does not exist: {resolved_source}")
    if not resolved_source.is_dir():
        raise ProjectError(f"Resource source must be a directory: {resolved_source}")

    digest = sha256()
    for path in _iter_resource_files(resolved_source):
        relative = path.relative_to(resolved_source).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _load_resource_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        raise ProjectError(f"Resource manifest not found: {manifest_path}")
    if not manifest_path.is_file():
        raise ProjectError(f"Resource manifest must be a file: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in resource manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ProjectError("Resource manifest root must be an object.")
    return manifest


def _validate_helper_contract(kind: str, raw_contract: object) -> dict[str, Any] | None:
    if kind not in HELPER_RESOURCE_KINDS:
        if raw_contract is not None and not isinstance(raw_contract, dict):
            raise ProjectError("helper_contract must be an object when provided.")
        return raw_contract if isinstance(raw_contract, dict) else None

    if not isinstance(raw_contract, dict):
        raise ProjectError(f"{kind} resources must define helper_contract.")
    if raw_contract.get("deterministic") is not True:
        raise ProjectError("helper_contract.deterministic must be true.")
    for field in ("input", "output", "errors"):
        value = raw_contract.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ProjectError(f"helper_contract.{field} must be a non-empty string.")
    return raw_contract


def _validate_template_variable(name: str, raw_variable: object) -> dict[str, Any]:
    _validate_slug("template_pack.variables key", name)
    if not isinstance(raw_variable, dict):
        raise ProjectError(f"template_pack.variables.{name} must be an object.")
    variable_type = raw_variable.get("type")
    if variable_type not in {"string", "boolean", "integer", "path"}:
        raise ProjectError(
            f"template_pack.variables.{name}.type must be one of: boolean, integer, path, string."
        )
    required = raw_variable.get("required", False)
    if not isinstance(required, bool):
        raise ProjectError(f"template_pack.variables.{name}.required must be a boolean.")
    variable = {
        "type": variable_type,
        "required": required,
    }
    description = raw_variable.get("description")
    if description is not None:
        if not isinstance(description, str) or not description.strip():
            raise ProjectError(
                f"template_pack.variables.{name}.description must be a non-empty string."
            )
        variable["description"] = description
    default = raw_variable.get("default")
    if default is not None:
        variable["default"] = default
    return variable


def _validate_template_operation(index: int, raw_operation: object) -> dict[str, Any]:
    if not isinstance(raw_operation, dict):
        raise ProjectError(f"template_pack.operations[{index}] must be an object.")
    operation_type = raw_operation.get("type", "render")
    if operation_type != "render":
        raise ProjectError(f"template_pack.operations[{index}].type must be render.")
    template = raw_operation.get("template")
    output = raw_operation.get("output")
    if not isinstance(template, str) or not template.strip():
        raise ProjectError(f"template_pack.operations[{index}].template must be non-empty.")
    if Path(template).is_absolute() or ".." in Path(template).parts:
        raise ProjectError(f"template_pack.operations[{index}].template must be relative.")
    if not isinstance(output, str) or not output.strip():
        raise ProjectError(f"template_pack.operations[{index}].output must be non-empty.")
    if Path(output).is_absolute() or ".." in Path(output).parts:
        raise ProjectError(f"template_pack.operations[{index}].output must be relative.")
    overwrite = raw_operation.get("overwrite", "never")
    if overwrite not in {"never", "safe", "always"}:
        raise ProjectError(
            f"template_pack.operations[{index}].overwrite must be one of: always, never, safe."
        )
    operation = {
        "type": operation_type,
        "template": template,
        "output": output,
        "overwrite": overwrite,
    }
    safe_overwrite = raw_operation.get("safe_overwrite")
    if safe_overwrite is not None:
        if not isinstance(safe_overwrite, dict):
            raise ProjectError(
                f"template_pack.operations[{index}].safe_overwrite must be an object."
            )
        strategy = safe_overwrite.get("strategy")
        if strategy not in {"if-unchanged", "if-missing"}:
            raise ProjectError(
                f"template_pack.operations[{index}].safe_overwrite.strategy must be one of: "
                "if-missing, if-unchanged."
            )
        operation["safe_overwrite"] = {"strategy": strategy}
    return operation


def _validate_template_operations(field: str, raw_operations: object) -> list[dict[str, Any]]:
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ProjectError(f"{field} must be a non-empty array.")
    return [
        _validate_template_operation(index, operation)
        for index, operation in enumerate(raw_operations)
    ]


def _validate_template_expected_tree(field: str, raw_tree: object) -> list[str]:
    if not isinstance(raw_tree, list) or not raw_tree:
        raise ProjectError(f"{field} must be a non-empty array.")
    expected_tree: list[str] = []
    seen: set[str] = set()
    for index, raw_path in enumerate(raw_tree):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ProjectError(f"{field}[{index}] must be a non-empty string.")
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            raise ProjectError(f"{field}[{index}] must be relative.")
        if raw_path in seen:
            raise ProjectError(f"{field} contains duplicate path '{raw_path}'.")
        seen.add(raw_path)
        expected_tree.append(raw_path)
    return expected_tree


def _validate_template_features(raw_features: object) -> dict[str, Any]:
    if raw_features is None:
        return {}
    if not isinstance(raw_features, dict):
        raise ProjectError("template_pack.features must be an object.")

    features: dict[str, Any] = {}
    for feature_name, raw_feature in sorted(raw_features.items()):
        _validate_slug("template_pack.features key", feature_name)
        if not isinstance(raw_feature, dict):
            raise ProjectError(f"template_pack.features.{feature_name} must be an object.")
        description = raw_feature.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ProjectError(
                f"template_pack.features.{feature_name}.description must be non-empty."
            )
        conflicts_with_raw = raw_feature.get("conflicts_with", [])
        if not isinstance(conflicts_with_raw, list):
            raise ProjectError(
                f"template_pack.features.{feature_name}.conflicts_with must be an array."
            )
        conflicts_with: list[str] = []
        for index, conflict in enumerate(conflicts_with_raw):
            conflict_name = _validate_slug(
                f"template_pack.features.{feature_name}.conflicts_with[{index}]",
                conflict,
            )
            conflicts_with.append(conflict_name)
        operations = _validate_template_operations(
            f"template_pack.features.{feature_name}.operations",
            raw_feature.get("operations"),
        )
        expected_tree = _validate_template_expected_tree(
            f"template_pack.features.{feature_name}.expected_tree",
            raw_feature.get("expected_tree"),
        )
        features[feature_name] = {
            "description": description,
            "conflicts_with": conflicts_with,
            "operations": operations,
            "expected_tree": expected_tree,
        }
    for feature_name, feature in features.items():
        for conflict_name in feature["conflicts_with"]:
            if conflict_name not in features:
                raise ProjectError(
                    f"template_pack.features.{feature_name}.conflicts_with references "
                    f"unknown feature '{conflict_name}'."
                )
    return features


def _validate_template_pack_contract(
    kind: str,
    raw_contract: object,
    *,
    manifest_name: str,
    manifest_version: str,
) -> dict[str, Any] | None:
    if kind != TEMPLATE_PACK_RESOURCE_KIND:
        if raw_contract is not None and not isinstance(raw_contract, dict):
            raise ProjectError("template_pack must be an object when provided.")
        return raw_contract if isinstance(raw_contract, dict) else None

    if not isinstance(raw_contract, dict):
        raise ProjectError("template resources must define template_pack.")
    if raw_contract.get("schema_version") != RESOURCE_SCHEMA_VERSION:
        raise ProjectError(f"template_pack.schema_version must be {RESOURCE_SCHEMA_VERSION}.")
    name = _validate_slug("template_pack.name", raw_contract.get("name"))
    version = _validate_version("template_pack.version", raw_contract.get("version"))
    if name != manifest_name:
        raise ProjectError("template_pack.name must match resource name.")
    if version != manifest_version:
        raise ProjectError("template_pack.version must match resource version.")
    implementation_stack = raw_contract.get("implementation_stack")
    if not isinstance(implementation_stack, str) or not implementation_stack.strip():
        raise ProjectError("template_pack.implementation_stack must be non-empty.")

    raw_variables = raw_contract.get("variables", {})
    if not isinstance(raw_variables, dict):
        raise ProjectError("template_pack.variables must be an object.")
    variables = {
        key: _validate_template_variable(key, value) for key, value in sorted(raw_variables.items())
    }

    operations = _validate_template_operations(
        "template_pack.operations",
        raw_contract.get("operations"),
    )
    expected_tree = _validate_template_expected_tree(
        "template_pack.expected_tree",
        raw_contract.get("expected_tree"),
    )
    operation_outputs = {operation["output"] for operation in operations}
    missing_outputs = sorted(set(expected_tree) - operation_outputs)
    if missing_outputs:
        raise ProjectError(
            "template_pack.expected_tree entries must have matching operations: "
            + ", ".join(missing_outputs)
        )
    features = _validate_template_features(raw_contract.get("features"))

    raw_validation = raw_contract.get("validation", [])
    if not isinstance(raw_validation, list):
        raise ProjectError("template_pack.validation must be an array.")
    validation: list[dict[str, str]] = []
    for index, raw_rule in enumerate(raw_validation):
        if not isinstance(raw_rule, dict):
            raise ProjectError(f"template_pack.validation[{index}] must be an object.")
        rule = raw_rule.get("rule")
        message = raw_rule.get("message")
        if not isinstance(rule, str) or not rule.strip():
            raise ProjectError(f"template_pack.validation[{index}].rule must be non-empty.")
        if not isinstance(message, str) or not message.strip():
            raise ProjectError(f"template_pack.validation[{index}].message must be non-empty.")
        validation.append({"rule": rule, "message": message})

    return {
        "schema_version": RESOURCE_SCHEMA_VERSION,
        "name": name,
        "version": version,
        "implementation_stack": implementation_stack,
        "variables": variables,
        "operations": operations,
        "expected_tree": expected_tree,
        "features": features,
        "validation": validation,
    }


def _validate_resource_compatibility(raw_compatibility: object) -> dict[str, Any]:
    if not isinstance(raw_compatibility, dict):
        raise ProjectError("compatibility must be an object.")
    requirement = raw_compatibility.get("wood_tools")
    if not isinstance(requirement, str) or not requirement.strip():
        raise ProjectError("compatibility.wood_tools must be a non-empty string.")
    if not _version_satisfies(requirement, WOOD_TOOLS_VERSION):
        raise ProjectError(
            f"Resource requires wood-tools {requirement}, current version is {WOOD_TOOLS_VERSION}."
        )
    return raw_compatibility


def validate_resource_manifest(
    manifest: dict[str, Any],
    *,
    source_dir: Path,
) -> ResourceManifest:
    if manifest.get("schema_version") != RESOURCE_SCHEMA_VERSION:
        raise ProjectError(f"schema_version must be {RESOURCE_SCHEMA_VERSION}.")

    kind = _validate_slug("kind", manifest.get("kind"))
    if kind not in RESOURCE_KIND_DIRECTORIES:
        available = ", ".join(sorted(RESOURCE_KIND_DIRECTORIES))
        raise ProjectError(f"kind must be one of: {available}.")

    name = _validate_slug("name", manifest.get("name"))
    version = _validate_version("version", manifest.get("version"))
    digest = _validate_resource_digest(manifest.get("digest"))
    compatibility = _validate_resource_compatibility(manifest.get("compatibility"))
    helper_contract = _validate_helper_contract(kind, manifest.get("helper_contract"))
    template_pack = _validate_template_pack_contract(
        kind,
        manifest.get(TEMPLATE_PACK_CONTRACT_FIELD),
        manifest_name=name,
        manifest_version=version,
    )

    actual_digest = compute_resource_digest(source_dir)
    if actual_digest != digest:
        raise ProjectError(f"Resource digest mismatch: expected {digest}, got {actual_digest}.")

    return ResourceManifest(
        kind=kind,
        name=name,
        version=version,
        digest=digest,
        compatibility=compatibility,
        helper_contract=helper_contract,
        template_pack=template_pack,
        raw=dict(manifest),
    )


def _resource_base_dir(wood_home: Path, kind: str) -> Path:
    return wood_home / RESOURCE_KIND_DIRECTORIES[kind]


def _resource_install_dir(wood_home: Path, manifest: ResourceManifest) -> Path:
    return _resource_base_dir(wood_home, manifest.kind) / manifest.name / manifest.version


def _resource_metadata_path(install_dir: Path) -> Path:
    return install_dir / RESOURCE_INSTALL_METADATA_FILE_NAME


def _build_resource_metadata(
    manifest: ResourceManifest,
    *,
    install_dir: Path,
) -> dict[str, Any]:
    metadata = {
        "schema_version": RESOURCE_SCHEMA_VERSION,
        "resource": {
            "kind": manifest.kind,
            "name": manifest.name,
            "version": manifest.version,
            "digest": manifest.digest,
            "compatibility": manifest.compatibility,
            "installed_location": str(install_dir),
        },
        "install": {
            "layout": "wood-home-global-resource",
            "manifest_file": RESOURCE_MANIFEST_FILE_NAME,
        },
    }
    if manifest.helper_contract is not None:
        metadata["resource"]["helper_contract"] = manifest.helper_contract
    if manifest.template_pack is not None:
        metadata["resource"][TEMPLATE_PACK_CONTRACT_FIELD] = manifest.template_pack
    return metadata


def _copy_resource_tree(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=False)
    for path in _iter_resource_files(source_dir):
        relative = path.relative_to(source_dir)
        destination = target_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


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

    unexpected_fields = sorted(set(document) - PROJECT_DOCUMENT_FIELDS)
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

    raw_template_packs = document.get("template_packs", [])
    if raw_template_packs is None:
        raw_template_packs = []
    if not isinstance(raw_template_packs, list):
        raise ProjectError("template_packs must be an array when provided.")
    seen_template_packs: set[str] = set()
    for index, entry in enumerate(raw_template_packs):
        if not isinstance(entry, dict):
            raise ProjectError(f"template_packs[{index}] must be an object.")
        name = _validate_slug(f"template_packs[{index}].name", entry.get("name"))
        _validate_version(f"template_packs[{index}].version", entry.get("version"))
        _validate_resource_digest(entry.get("digest"))
        if entry.get("kind", TEMPLATE_PACK_RESOURCE_KIND) != TEMPLATE_PACK_RESOURCE_KIND:
            raise ProjectError(f"template_packs[{index}].kind must be template.")
        if "source" in entry:
            source = entry["source"]
            if not isinstance(source, str) or not source.strip():
                raise ProjectError(f"template_packs[{index}].source must be a non-empty string.")
            if Path(source).is_absolute():
                raise ProjectError(f"template_packs[{index}].source must not be an absolute path.")
        if name in seen_template_packs:
            raise ProjectError(f"template_packs contains duplicate name '{name}'.")
        seen_template_packs.add(name)


def _validate_existing_directory(field: str, path: Path) -> None:
    if not path.exists():
        nearest_parent = _nearest_existing_parent(path)
        if nearest_parent is not None and nearest_parent != path.parent:
            raise ProjectError(
                f"{field} is unavailable: {path} (nearest existing parent: {nearest_parent}; "
                "the expected mount may not be available)"
            )
        raise ProjectError(f"{field} does not exist: {path}")
    if not path.is_dir():
        raise ProjectError(f"{field} must be a directory: {path}")


def _validate_existing_file(field: str, path: Path) -> None:
    if not path.exists():
        nearest_parent = _nearest_existing_parent(path)
        if nearest_parent is not None and nearest_parent != path.parent:
            raise ProjectError(
                f"{field} is unavailable: {path} (nearest existing parent: {nearest_parent}; "
                "the expected Wood home may not be available)"
            )
        raise ProjectError(f"{field} does not exist: {path}")
    if not path.is_file():
        raise ProjectError(f"{field} must be a file: {path}")


def _ensure_unique_paths(entries: list[tuple[str, Path]]) -> None:
    seen: dict[Path, str] = {}
    for field, path in entries:
        resolved = path.resolve()
        other = seen.get(resolved)
        if other is not None:
            raise ProjectError(f"{field} conflicts with {other}: both resolve to {resolved}")
        seen[resolved] = field


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while True:
        if current.exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _check_directory_access(field: str, path: Path, *, require_write: bool) -> dict[str, Any]:
    readable = os.access(path, os.R_OK | os.X_OK)
    writable = os.access(path, os.W_OK | os.X_OK)
    if not readable:
        raise ProjectError(f"{field} is not readable: {path}")
    if require_write and not writable:
        raise ProjectError(f"{field} is not writable: {path}")
    return {
        "path": str(path),
        "readable": readable,
        "writable": writable,
    }


def _check_file_access(field: str, path: Path, *, require_write: bool) -> dict[str, Any]:
    readable = os.access(path, os.R_OK)
    writable = os.access(path, os.W_OK)
    if not readable:
        raise ProjectError(f"{field} is not readable: {path}")
    if require_write and not writable:
        raise ProjectError(f"{field} is not writable: {path}")
    return {
        "path": str(path),
        "readable": readable,
        "writable": writable,
    }


def _check_mutation_parent(field: str, path: Path) -> None:
    parent = path if path.exists() else _nearest_existing_parent(path)
    if parent is None:
        raise ProjectError(
            f"{field} is unavailable: {path} (no existing parent found; the expected mount may "
            "not be available)"
        )
    if not parent.is_dir():
        raise ProjectError(f"{field} parent must be a directory: {parent}")
    _check_directory_access(field, parent, require_write=True)


def _load_linked_repositories(document: dict[str, Any]) -> list[LinkedRepository]:
    raw = document.get("linked_repositories")
    if raw is None:
        return []

    repositories: list[LinkedRepository] = []
    if isinstance(raw, dict):
        items = raw.items()
        for name, value in items:
            if not isinstance(name, str) or not name.strip():
                raise ProjectError("linked_repositories keys must be non-empty strings.")
            if isinstance(value, str):
                path_value = value
                role = None
            elif isinstance(value, dict):
                path_value = value.get("path")
                role = value.get("role")
            else:
                path_value = None
                role = None
            if not isinstance(path_value, str) or not path_value.strip():
                raise ProjectError(
                    f"linked_repositories['{name}'] must define a non-empty absolute path."
                )
            if role is not None and (not isinstance(role, str) or not role.strip()):
                raise ProjectError(
                    f"linked_repositories['{name}'] role must be a non-empty string when set."
                )
            path = Path(path_value)
            if not path.is_absolute():
                raise ProjectError(f"linked_repositories['{name}'] path must be an absolute path.")
            repositories.append(
                LinkedRepository(name=name, path=path, role=role.strip() if role else None)
            )
        return repositories

    if isinstance(raw, list):
        seen_names: set[str] = set()
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ProjectError("linked_repositories entries must be objects.")
            name = entry.get("name")
            path_value = entry.get("path")
            role = entry.get("role")
            if not isinstance(name, str) or not name.strip():
                raise ProjectError(f"linked_repositories[{index}] must define a non-empty name.")
            if name in seen_names:
                raise ProjectError(f"linked_repositories contains duplicate name '{name}'.")
            seen_names.add(name)
            if not isinstance(path_value, str) or not path_value.strip():
                raise ProjectError(
                    f"linked_repositories[{index}] must define a non-empty absolute path."
                )
            if role is not None and (not isinstance(role, str) or not role.strip()):
                raise ProjectError(
                    f"linked_repositories[{index}] role must be a non-empty string when set."
                )
            path = Path(path_value)
            if not path.is_absolute():
                raise ProjectError(f"linked_repositories[{index}] path must be an absolute path.")
            repositories.append(
                LinkedRepository(name=name, path=path, role=role.strip() if role else None)
            )
        return repositories

    raise ProjectError("linked_repositories must be an object or list of objects.")


def validate_project_state(
    document: dict[str, Any],
) -> dict[str, Any]:
    validate_project_document(document)

    project_root = Path(document["project_root"])
    wood_home = Path(document["wood_home"])
    wood_config_file = Path(document["wood_config_file"])
    wood_home_dirs = tuple(wood_home / name for name in WOOD_HOME_DIRECTORY_NAMES)
    linked_repositories = _load_linked_repositories(document)

    _validate_existing_directory("project_root", project_root)
    _validate_existing_directory("wood_home", wood_home)
    _validate_existing_file("wood_config_file", wood_config_file)
    for directory in wood_home_dirs:
        field = f"wood_home_dirs.{directory.relative_to(wood_home)}"
        _validate_existing_directory(field, directory)

    mount_checks = {
        "project_root": _check_directory_access("project_root", project_root, require_write=False),
        "wood_home": _check_directory_access("wood_home", wood_home, require_write=False),
        "wood_config_file": _check_file_access(
            "wood_config_file", wood_config_file, require_write=False
        ),
        "wood_home_dirs": {
            str(directory.relative_to(wood_home)): _check_directory_access(
                f"wood_home_dirs.{directory.relative_to(wood_home)}",
                directory,
                require_write=False,
            )
            for directory in wood_home_dirs
        },
    }

    _ensure_unique_paths(
        [
            ("project_root", project_root),
            ("wood_home", wood_home),
            ("wood_config_file", wood_config_file),
            *[
                (f"wood_home_dirs.{directory.relative_to(wood_home)}", directory)
                for directory in wood_home_dirs
            ],
        ]
    )

    linked_payload: list[dict[str, str]] = []
    linked_entries: list[tuple[str, Path]] = []
    for repository in linked_repositories:
        _validate_existing_directory(
            f"linked_repositories['{repository.name}']",
            repository.path,
        )
        access = _check_directory_access(
            f"linked_repositories['{repository.name}']",
            repository.path,
            require_write=False,
        )
        linked_entries.append((f"linked_repositories['{repository.name}']", repository.path))
        entry = {"name": repository.name, "path": str(repository.path)}
        if repository.role is not None:
            entry["role"] = repository.role
        entry["access"] = {
            "readable": access["readable"],
            "writable": access["writable"],
        }
        linked_payload.append(entry)

    _ensure_unique_paths(
        [
            ("project_root", project_root),
            ("wood_home", wood_home),
            ("wood_config_file", wood_config_file),
            *[
                (f"wood_home_dirs.{directory.relative_to(wood_home)}", directory)
                for directory in wood_home_dirs
            ],
            *linked_entries,
        ]
    )

    return {
        "valid": True,
        "project": document,
        "checked_paths": {
            "project_root": str(project_root),
            "wood_home": str(wood_home),
            "wood_config_file": str(wood_config_file),
            "wood_home_dirs": [str(directory) for directory in wood_home_dirs],
        },
        "mount_checks": mount_checks,
        "linked_repositories": linked_payload,
    }


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


def init_project(
    *,
    project_root: Path | None,
    wood_home: Path | None,
    project_id: str | None,
    project_slug: str | None,
    apply: bool,
) -> dict[str, Any]:
    document = create_project_document(
        project_id=project_id,
        project_slug=project_slug,
        project_root=project_root,
        wood_home=wood_home,
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
    payload = validate_project_state(document)
    payload["path"] = str(file_path)
    return payload


def _load_project_for_resource(
    *,
    project_file: Path | None,
    project_root: Path | None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    document = load_project_document(file_path)
    state = validate_project_state(document)
    return file_path, document, state


def _load_installed_resource_metadata(install_dir: Path) -> dict[str, Any]:
    metadata_path = _resource_metadata_path(install_dir)
    if not metadata_path.exists():
        raise ProjectError(f"Installed resource metadata not found: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in installed resource metadata: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ProjectError("Installed resource metadata root must be an object.")
    return metadata


def _assert_same_installed_resource(
    metadata: dict[str, Any],
    manifest: ResourceManifest,
    install_dir: Path,
) -> None:
    resource = metadata.get("resource")
    if not isinstance(resource, dict):
        raise ProjectError(
            f"Installed resource metadata is corrupt: {_resource_metadata_path(install_dir)}"
        )
    expected = {
        "kind": manifest.kind,
        "name": manifest.name,
        "version": manifest.version,
        "digest": manifest.digest,
    }
    actual = {key: resource.get(key) for key in expected}
    if actual != expected:
        raise ProjectError(
            f"Resource conflict at {install_dir}: installed metadata does not match requested "
            f"{manifest.kind}/{manifest.name}/{manifest.version}."
        )


def install_resource(
    *,
    source_dir: Path,
    project_file: Path | None = None,
    project_root: Path | None = None,
    apply: bool,
) -> dict[str, Any]:
    _, document, _ = _load_project_for_resource(
        project_file=project_file,
        project_root=project_root,
    )
    wood_home = Path(document["wood_home"])
    resolved_source = source_dir.expanduser().resolve()
    manifest = validate_resource_manifest(
        _load_resource_manifest(resolved_source / RESOURCE_MANIFEST_FILE_NAME),
        source_dir=resolved_source,
    )
    install_dir = _resource_install_dir(wood_home, manifest)
    metadata = _build_resource_metadata(manifest, install_dir=install_dir)

    if install_dir.exists():
        if not install_dir.is_dir():
            raise ProjectError(f"Resource install location is not a directory: {install_dir}")
        installed_metadata = _load_installed_resource_metadata(install_dir)
        _assert_same_installed_resource(installed_metadata, manifest, install_dir)
        return {
            "changed": False,
            "source": str(resolved_source),
            "resource": installed_metadata["resource"],
            "metadata": installed_metadata,
        }

    if not apply:
        preview_metadata = dict(metadata)
        preview_metadata["install"] = dict(metadata["install"])
        preview_metadata["install"]["preview"] = True
        return {
            "changed": False,
            "source": str(resolved_source),
            "resource": preview_metadata["resource"],
            "metadata": preview_metadata,
        }

    _check_mutation_parent("resource install base", _resource_base_dir(wood_home, manifest.kind))
    staging_root = wood_home / "cache" / "resource-installs"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_dir = staging_root / (
        f"{manifest.kind}-{manifest.name}-{manifest.version}-{manifest.digest.removeprefix('sha256:')[:12]}"
    )
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    _copy_resource_tree(resolved_source, staging_dir)
    (staging_dir / RESOURCE_MANIFEST_FILE_NAME).write_text(
        json.dumps(manifest.raw, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _resource_metadata_path(staging_dir).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    if install_dir.exists():
        shutil.rmtree(staging_dir)
        installed_metadata = _load_installed_resource_metadata(install_dir)
        _assert_same_installed_resource(installed_metadata, manifest, install_dir)
        return {
            "changed": False,
            "source": str(resolved_source),
            "resource": installed_metadata["resource"],
            "metadata": installed_metadata,
        }
    staging_dir.replace(install_dir)
    return {
        "changed": True,
        "source": str(resolved_source),
        "resource": metadata["resource"],
        "metadata": metadata,
    }


def _select_installed_version(base_dir: Path, requested_version: str | None) -> Path:
    if requested_version is not None:
        return base_dir / requested_version
    if not base_dir.exists():
        raise ProjectError(f"Resource is not installed: {base_dir}")
    versions = sorted(path for path in base_dir.iterdir() if path.is_dir())
    if not versions:
        raise ProjectError(f"Resource has no installed versions: {base_dir}")
    return versions[-1]


def inspect_resource(
    *,
    kind: str,
    name: str,
    version: str | None = None,
    project_file: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    _, document, _ = _load_project_for_resource(
        project_file=project_file,
        project_root=project_root,
    )
    resolved_kind = _validate_slug("kind", kind)
    if resolved_kind not in RESOURCE_KIND_DIRECTORIES:
        available = ", ".join(sorted(RESOURCE_KIND_DIRECTORIES))
        raise ProjectError(f"kind must be one of: {available}.")
    resolved_name = _validate_slug("name", name)
    resolved_version = _validate_version("version", version) if version is not None else None
    wood_home = Path(document["wood_home"])
    install_dir = _select_installed_version(
        _resource_base_dir(wood_home, resolved_kind) / resolved_name,
        resolved_version,
    )
    metadata = _load_installed_resource_metadata(install_dir)
    resource = metadata.get("resource")
    if not isinstance(resource, dict):
        raise ProjectError(
            f"Installed resource metadata is corrupt: {_resource_metadata_path(install_dir)}"
        )
    digest = compute_resource_digest(install_dir)
    if digest != resource.get("digest"):
        raise ProjectError(
            f"Installed resource digest mismatch: expected {resource.get('digest')}, got {digest}."
        )
    return {
        "resource": resource,
        "metadata": metadata,
        "path": str(install_dir),
    }


def resolve_resource_path(
    *,
    kind: str,
    name: str,
    version: str | None = None,
    relative_path: str | None = None,
    project_file: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    payload = inspect_resource(
        kind=kind,
        name=name,
        version=version,
        project_file=project_file,
        project_root=project_root,
    )
    install_dir = Path(payload["path"])
    if relative_path is not None:
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise ProjectError("relative_path must stay within the installed resource.")
        resolved_path = install_dir / requested
        if not resolved_path.exists():
            raise ProjectError(f"Resource path does not exist: {resolved_path}")
    else:
        resolved_path = install_dir
    return {
        "resource": payload["resource"],
        "path": str(resolved_path),
    }


def _template_pack_payload(
    *,
    source: str,
    source_detail: str | None,
    resource: dict[str, Any],
) -> dict[str, Any]:
    contract = resource.get(TEMPLATE_PACK_CONTRACT_FIELD)
    if not isinstance(contract, dict):
        raise ProjectError(
            f"Template resource {resource.get('name', '<unknown>')} does not define "
            f"{TEMPLATE_PACK_CONTRACT_FIELD}."
        )
    operations = contract.get("operations")
    if not isinstance(operations, list):
        raise ProjectError("template_pack.operations must be an array.")
    payload = {
        "name": resource["name"],
        "source": source,
        "version": resource["version"],
        "digest": resource["digest"],
        "implementation_stack": contract["implementation_stack"],
        "variables": contract.get("variables", {}),
        "planned_outputs": [operation["output"] for operation in operations],
        "operations": operations,
        "expected_tree": contract["expected_tree"],
        "features": contract.get("features", {}),
        "validation": contract.get("validation", []),
    }
    if source_detail is not None:
        payload["source_detail"] = source_detail
    return payload


def _load_template_pack_from_source(source_dir: Path, *, source: str) -> dict[str, Any]:
    resolved_source = source_dir.expanduser().resolve()
    manifest = validate_resource_manifest(
        _load_resource_manifest(resolved_source / RESOURCE_MANIFEST_FILE_NAME),
        source_dir=resolved_source,
    )
    if manifest.kind != TEMPLATE_PACK_RESOURCE_KIND:
        raise ProjectError(
            f"Template pack source must be a template resource, got {manifest.kind}."
        )
    metadata = _build_resource_metadata(manifest, install_dir=resolved_source)
    source_detail = str(resolved_source) if source == "explicit" else None
    return _template_pack_payload(
        source=source,
        source_detail=source_detail,
        resource=metadata["resource"],
    )


def _iter_builtin_template_pack_dirs() -> list[Path]:
    try:
        root = resources.files("wood_templates").joinpath("builtin_template_packs")
    except ModuleNotFoundError:
        return []
    if not root.is_dir():
        return []
    dirs: list[Path] = []
    for name_dir in root.iterdir():
        if not name_dir.is_dir():
            continue
        for version_dir in name_dir.iterdir():
            if version_dir.is_dir() and (version_dir / RESOURCE_MANIFEST_FILE_NAME).is_file():
                dirs.append(Path(str(version_dir)))
    return sorted(dirs, key=lambda path: (path.parent.name, path.name))


def _iter_installed_template_pack_payloads(wood_home: Path) -> list[dict[str, Any]]:
    base_dir = _resource_base_dir(wood_home, TEMPLATE_PACK_RESOURCE_KIND)
    if not base_dir.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for name_dir in sorted(path for path in base_dir.iterdir() if path.is_dir()):
        version_dirs = sorted((path for path in name_dir.iterdir() if path.is_dir()), reverse=True)
        for version_dir in version_dirs:
            metadata = _load_installed_resource_metadata(version_dir)
            resource = metadata.get("resource")
            if not isinstance(resource, dict):
                metadata_path = _resource_metadata_path(version_dir)
                raise ProjectError(f"Installed resource metadata is corrupt: {metadata_path}")
            if resource.get("kind") != TEMPLATE_PACK_RESOURCE_KIND:
                continue
            digest = compute_resource_digest(version_dir)
            if digest != resource.get("digest"):
                raise ProjectError(
                    "Installed template pack digest mismatch: "
                    f"expected {resource.get('digest')}, got {digest}."
                )
            payloads.append(
                _template_pack_payload(
                    source="installed",
                    source_detail=None,
                    resource=resource,
                )
            )
    return payloads


def _locked_template_pack_payloads(document: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for entry in document.get("template_packs", []) or []:
        name = entry["name"]
        version = entry["version"]
        expected_digest = entry["digest"]
        payload = inspect_resource(
            kind=TEMPLATE_PACK_RESOURCE_KIND,
            name=name,
            version=version,
            project_root=Path(document["project_root"]),
        )
        resource = payload["resource"]
        if resource.get("digest") != expected_digest:
            raise ProjectError(
                f"Locked template pack {name} {version} digest mismatch: expected "
                f"{expected_digest}, got {resource.get('digest')}."
            )
        payloads.append(
            _template_pack_payload(
                source="locked",
                source_detail=f"template_packs[{name}]",
                resource=resource,
            )
        )
    return payloads


def _builtin_template_pack_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for source_dir in _iter_builtin_template_pack_dirs():
        payloads.append(_load_template_pack_from_source(source_dir, source="built-in"))
    return payloads


def list_template_packs(
    *,
    source_dir: Path | None = None,
    project_file: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    if source_dir is not None:
        return {
            "precedence": ["explicit-source"],
            "template_packs": [_load_template_pack_from_source(source_dir, source="explicit")],
        }

    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    if not file_path.exists():
        if project_file is not None:
            load_project_document(file_path)
        return {
            "precedence": ["built-in"],
            "template_packs": _builtin_template_pack_payloads(),
        }

    document = load_project_document(file_path)
    validate_project_state(document)
    seen_names: set[str] = set()
    selected: list[dict[str, Any]] = []
    for payload in [
        *_locked_template_pack_payloads(document),
        *_iter_installed_template_pack_payloads(Path(document["wood_home"])),
        *_builtin_template_pack_payloads(),
    ]:
        if payload["name"] in seen_names:
            continue
        seen_names.add(payload["name"])
        selected.append(payload)
    return {
        "precedence": ["locked", "installed", "built-in"],
        "template_packs": selected,
    }


def show_template_pack(
    *,
    name: str,
    source_dir: Path | None = None,
    project_file: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    requested_name = _validate_slug("name", name)
    payload = list_template_packs(
        source_dir=source_dir,
        project_file=project_file,
        project_root=project_root,
    )
    for template_pack in payload["template_packs"]:
        if template_pack["name"] == requested_name:
            return {
                "precedence": payload["precedence"],
                "template_pack": template_pack,
            }
    source_hint = f" from {source_dir.expanduser().resolve()}" if source_dir is not None else ""
    raise ProjectError(f"Template pack not found: {requested_name}{source_hint}")


def _replace_template_variables(value: str, variables: dict[str, str]) -> str:
    rendered = value
    for key, replacement in sorted(variables.items()):
        rendered = rendered.replace("{{" + key + "}}", replacement)
    return rendered


def _infer_template_variables(template_pack: dict[str, Any], target_root: Path) -> dict[str, str]:
    project_name = target_root.name
    package_name = slugify_project_name(project_name)
    inferred = {
        "project-name": project_name,
        "package-name": package_name,
        "package-module": package_name.replace("-", "_"),
    }
    variables: dict[str, str] = {}
    for name, contract in template_pack.get("variables", {}).items():
        if name in inferred:
            variables[name] = inferred[name]
            continue
        if "default" in contract:
            variables[name] = str(contract["default"])
            continue
        if contract.get("required") is True:
            raise ProjectError(f"Template variable requires an explicit value: {name}.")
    return variables


def _template_summary(template_pack: dict[str, Any]) -> dict[str, str]:
    summary = {
        "name": template_pack["name"],
        "version": template_pack["version"],
        "source": template_pack["source"],
        "digest": template_pack["digest"],
    }
    if "source_detail" in template_pack:
        summary["source_detail"] = template_pack["source_detail"]
    return summary


def _content_digest(content: bytes) -> str:
    return f"sha256:{sha256(content).hexdigest()}"


def _validate_portable_rendered_content(content: str) -> None:
    forbidden_paths = {str(Path.home() / WOOD_HOME_DIR_NAME), f"~/{WOOD_HOME_DIR_NAME}"}
    configured_wood_home = os.environ.get(WOOD_HOME_ENV)
    if configured_wood_home:
        forbidden_paths.add(str(Path(configured_wood_home).expanduser().resolve()))
    if any(path in content for path in forbidden_paths):
        raise ProjectError("Generated template content must not contain Wood home paths.")


def _load_template_lock(lock_path: Path) -> dict[str, Any] | None:
    if not lock_path.exists():
        return None
    if not lock_path.is_file():
        raise ProjectError(f"Template lock must be a file: {TEMPLATE_LOCK_FILE_NAME}")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in {TEMPLATE_LOCK_FILE_NAME}: {exc}") from exc
    if not isinstance(lock, dict) or lock.get("schema_version") != TEMPLATE_LOCK_SCHEMA_VERSION:
        raise ProjectError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
    template = lock.get("template")
    files = lock.get("files")
    if not isinstance(template, dict) or not isinstance(files, list):
        raise ProjectError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
    return lock


def _locked_output_digests(lock: dict[str, Any] | None) -> dict[str, str]:
    if lock is None:
        return {}
    digests: dict[str, str] = {}
    for entry in lock["files"]:
        if not isinstance(entry, dict):
            raise ProjectError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
        path = entry.get("path")
        digest = entry.get("digest")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise ProjectError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
        _validate_resource_digest(digest)
        digests[path] = digest
    return digests


def _template_lock_payload(plan: dict[str, Any], rendered: dict[str, bytes]) -> dict[str, Any]:
    return {
        "schema_version": TEMPLATE_LOCK_SCHEMA_VERSION,
        "template": {key: plan["template"][key] for key in ("name", "source", "version", "digest")},
        "wood_tools_version": WOOD_TOOLS_VERSION,
        "declared_inputs": plan["variables"],
        "reference_packs": [],
        "agent_packs": [],
        "files": [
            {"path": path, "digest": _content_digest(content)}
            for path, content in sorted(rendered.items())
        ],
    }


def _replace_staged_file(source: Path, destination: Path) -> None:
    source.replace(destination)


def _created_parent_directories(destination: Path, target_root: Path) -> list[Path]:
    created: list[Path] = []
    parent = destination.parent
    while parent != target_root and not parent.exists():
        created.append(parent)
        parent = parent.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    return created


def _activate_template_files(
    *,
    target_root: Path,
    staged_files: dict[str, Path],
) -> None:
    records: list[dict[str, Any]] = []
    created_directories: list[Path] = []
    try:
        for index, (relative_path, staged_path) in enumerate(staged_files.items()):
            destination = target_root / relative_path
            created_directories.extend(_created_parent_directories(destination, target_root))
            backup = staged_path.parent / f".backup-{index}"
            record = {
                "destination": destination,
                "backup": backup,
                "had_existing": destination.exists(),
                "activated": False,
            }
            records.append(record)
            if record["had_existing"]:
                _replace_staged_file(destination, backup)
            _replace_staged_file(staged_path, destination)
            record["activated"] = True
    except OSError as exc:
        for record in reversed(records):
            destination = record["destination"]
            backup = record["backup"]
            if record["activated"] and destination.exists():
                destination.unlink()
            if record["had_existing"] and backup.exists():
                _replace_staged_file(backup, destination)
        for directory in sorted(
            set(created_directories), key=lambda path: len(path.parts), reverse=True
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise ProjectError(
            "Template application failed; prior project state was restored."
        ) from exc


def _template_pack_from_install_dir(
    install_dir: Path,
    *,
    source: str,
    source_detail: str | None,
) -> dict[str, Any]:
    metadata = _load_installed_resource_metadata(install_dir)
    resource = metadata.get("resource")
    if not isinstance(resource, dict):
        metadata_path = _resource_metadata_path(install_dir)
        raise ProjectError(f"Installed resource metadata is corrupt: {metadata_path}")
    digest = compute_resource_digest(install_dir)
    if digest != resource.get("digest"):
        raise ProjectError(
            "Installed template pack digest mismatch: "
            f"expected {resource.get('digest')}, got {digest}."
        )
    return _template_pack_payload(source=source, source_detail=source_detail, resource=resource)


def _resolve_template_pack_for_render(
    *,
    name: str,
    source_dir: Path | None,
    project_file: Path | None,
    project_root: Path | None,
) -> tuple[dict[str, Any], Path]:
    requested_name = _validate_slug("name", name)
    if source_dir is not None:
        resolved_source = source_dir.expanduser().resolve()
        payload = _load_template_pack_from_source(resolved_source, source="explicit")
        if payload["name"] != requested_name:
            raise ProjectError(f"Template pack not found: {requested_name} from {resolved_source}")
        return payload, resolved_source

    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    if file_path.exists():
        document = load_project_document(file_path)
        validate_project_state(document)
        for entry in document.get("template_packs", []) or []:
            if entry["name"] != requested_name:
                continue
            resource_payload = inspect_resource(
                kind=TEMPLATE_PACK_RESOURCE_KIND,
                name=requested_name,
                version=entry["version"],
                project_root=Path(document["project_root"]),
            )
            resource = resource_payload["resource"]
            if resource.get("digest") != entry["digest"]:
                raise ProjectError(
                    f"Locked template pack {requested_name} {entry['version']} digest mismatch: "
                    f"expected {entry['digest']}, got {resource.get('digest')}."
                )
            return (
                _template_pack_payload(
                    source="locked",
                    source_detail=f"template_packs[{requested_name}]",
                    resource=resource,
                ),
                Path(resource_payload["path"]),
            )

        wood_home = Path(document["wood_home"])
        installed_base = _resource_base_dir(wood_home, TEMPLATE_PACK_RESOURCE_KIND) / requested_name
        if installed_base.exists():
            install_dir = _select_installed_version(installed_base, None)
            return (
                _template_pack_from_install_dir(
                    install_dir,
                    source="installed",
                    source_detail=None,
                ),
                install_dir,
            )
    elif project_file is not None:
        load_project_document(file_path)

    builtin_dirs = [
        path for path in _iter_builtin_template_pack_dirs() if path.parent.name == requested_name
    ]
    if builtin_dirs:
        source_path = sorted(builtin_dirs, reverse=True)[0]
        return _load_template_pack_from_source(source_path, source="built-in"), source_path

    raise ProjectError(f"Template pack not found: {requested_name}")


def render_template_pack(
    *,
    name: str,
    source_dir: Path | None = None,
    project_file: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    plan = plan_template_pack(
        name=name,
        source_dir=source_dir,
        project_file=project_file,
        project_root=project_root,
        include_template_root=True,
    )
    if plan["conflicts"]:
        conflict = plan["conflicts"][0]
        raise ProjectError(f"Template output already exists: {conflict['path']}")

    target_root = Path(plan["target_root"])
    template_root = Path(plan["template_root"])
    rendered_files: dict[str, bytes] = {}
    for operation in plan["operations"]:
        template_path = template_root / operation["template"]
        rendered = _replace_template_variables(
            template_path.read_text(encoding="utf-8"),
            plan["variables"],
        )
        rendered_files[operation["path"]] = rendered.encode("utf-8")

    lock_path = target_root / TEMPLATE_LOCK_FILE_NAME
    existing_lock = _load_template_lock(lock_path)
    if existing_lock is not None:
        locked_template = existing_lock["template"]
        if locked_template.get("name") != plan["template"]["name"]:
            raise ProjectError(
                f"Template lock already belongs to {locked_template.get('name', '<unknown>')}."
            )
    lock_payload = _template_lock_payload(plan, rendered_files)
    lock_content = (json.dumps(lock_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")

    changed_paths = {
        operation["path"]
        for operation in plan["operations"]
        if operation["decision"] in {"create", "overwrite"}
    }
    if not lock_path.exists() or lock_path.read_bytes() != lock_content:
        changed_paths.add(TEMPLATE_LOCK_FILE_NAME)

    if changed_paths:
        with TemporaryDirectory(prefix=".wood-template-stage-", dir=target_root) as temp_dir:
            staging_root = Path(temp_dir)
            staged_files: dict[str, Path] = {}
            content_by_path = {**rendered_files, TEMPLATE_LOCK_FILE_NAME: lock_content}
            for relative_path in sorted(changed_paths):
                staged_path = staging_root / relative_path
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path.write_bytes(content_by_path[relative_path])
                staged_files[relative_path] = staged_path

            actual_digest = compute_resource_digest(template_root)
            if actual_digest != plan["template"]["digest"]:
                raise ProjectError(
                    "Template pack changed after planning: "
                    f"expected {plan['template']['digest']}, got {actual_digest}."
                )
            _activate_template_files(target_root=target_root, staged_files=staged_files)

    return {
        "changed": bool(changed_paths),
        "target_root": str(target_root),
        "template": {
            key: value
            for key, value in plan["template"].items()
            if key in {"name", "version", "source", "digest"}
        },
        "variables": plan["variables"],
        "lock_file": TEMPLATE_LOCK_FILE_NAME,
        "files": [
            {
                "path": operation["path"],
                "template": operation["template"],
                "overwrite": operation["overwrite"],
            }
            for operation in plan["operations"]
        ],
    }


def plan_template_pack(
    *,
    name: str,
    source_dir: Path | None = None,
    project_file: Path | None = None,
    project_root: Path | None = None,
    include_template_root: bool = False,
) -> dict[str, Any]:
    target_root = resolve_project_root(project_root)
    template_pack, template_root = _resolve_template_pack_for_render(
        name=name,
        source_dir=source_dir,
        project_file=project_file,
        project_root=target_root,
    )
    variables = _infer_template_variables(template_pack, target_root)
    operations: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    lock = _load_template_lock(target_root / TEMPLATE_LOCK_FILE_NAME)
    locked_digests = _locked_output_digests(lock)
    seen_outputs: set[str] = set()

    for operation in template_pack["operations"]:
        template_path = template_root / operation["template"]
        if not template_path.exists():
            raise ProjectError(f"Template source file does not exist: {operation['template']}")
        if not template_path.is_file():
            raise ProjectError(f"Template source must be a file: {operation['template']}")
        relative_output = _replace_template_variables(operation["output"], variables)
        output_path = Path(relative_output)
        if output_path.is_absolute() or ".." in output_path.parts:
            raise ProjectError(
                f"Rendered template output must stay within the project: {relative_output}"
            )
        if relative_output in seen_outputs:
            raise ProjectError(
                f"Template operations resolve to duplicate output: {relative_output}"
            )
        seen_outputs.add(relative_output)
        destination = target_root / relative_output
        if not destination.resolve().is_relative_to(target_root.resolve()):
            raise ProjectError(
                f"Rendered template output must stay within the project: {relative_output}"
            )
        exists = destination.exists()
        rendered = _replace_template_variables(
            template_path.read_text(encoding="utf-8"),
            variables,
        ).encode("utf-8")
        _validate_portable_rendered_content(rendered.decode("utf-8"))
        decision = "create"
        conflict_reason = "output-exists"
        if exists and not destination.is_file():
            decision = "conflict"
        elif exists and destination.read_bytes() == rendered:
            decision = "unchanged"
        elif exists and operation["overwrite"] == "always":
            decision = "overwrite"
        elif (
            exists
            and operation["overwrite"] == "safe"
            and operation.get("safe_overwrite", {}).get("strategy") == "if-unchanged"
            and locked_digests.get(relative_output) == _content_digest(destination.read_bytes())
        ):
            decision = "overwrite"
        elif exists:
            decision = "conflict"
            conflict_reason = (
                "output-modified" if relative_output in locked_digests else "output-exists"
            )
        if decision == "conflict":
            conflicts.append(
                {
                    "path": relative_output,
                    "reason": conflict_reason,
                }
            )
        planned_operation = {
            "type": operation["type"],
            "template": operation["template"],
            "path": relative_output,
            "output": operation["output"],
            "overwrite": operation["overwrite"],
            "exists": exists,
            "decision": decision,
        }
        if "safe_overwrite" in operation:
            planned_operation["safe_overwrite"] = operation["safe_overwrite"]
        operations.append(planned_operation)

    payload: dict[str, Any] = {
        "changed": False,
        "target_root": str(target_root),
        "template": _template_summary(template_pack),
        "variables": variables,
        "features": template_pack.get("features", {}),
        "operations": operations,
        "conflicts": conflicts,
    }
    if include_template_root:
        payload["template_root"] = str(template_root)
    return payload


def _resolve_repository_path(repo_path: Path) -> Path:
    resolved = repo_path.expanduser().resolve()
    if not resolved.exists():
        raise ProjectError(f"Repository path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ProjectError(f"Repository path must be a directory: {resolved}")
    return resolved


def _canonicalize_linked_repositories(
    repositories: list[LinkedRepository],
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for repository in repositories:
        entry = {"name": repository.name, "path": str(repository.path)}
        if repository.role is not None:
            entry["role"] = repository.role
        payload.append(entry)
    return payload


def link_repository(
    *,
    repo_path: Path,
    repo_name: str | None,
    repo_role: str | None,
    project_file: Path | None = None,
    project_root: Path | None = None,
    apply: bool,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    document = load_project_document(file_path)

    resolved_repo_path = _resolve_repository_path(repo_path)
    resolved_name = (repo_name or resolved_repo_path.name).strip()
    if not resolved_name:
        raise ProjectError("Repository name cannot be empty.")

    resolved_role = repo_role.strip() if repo_role is not None else None
    if repo_role is not None and not resolved_role:
        raise ProjectError("Repository role cannot be empty when provided.")

    linked_repositories = _load_linked_repositories(document)
    linked_repositories.append(
        LinkedRepository(name=resolved_name, path=resolved_repo_path, role=resolved_role)
    )

    updated_document = dict(document)
    updated_document["linked_repositories"] = _canonicalize_linked_repositories(linked_repositories)
    validate_project_state(updated_document)

    changed = False
    if apply:
        save_project_document(file_path, updated_document)
        changed = True

    repository_payload = {
        "name": resolved_name,
        "path": str(resolved_repo_path),
    }
    if resolved_role is not None:
        repository_payload["role"] = resolved_role

    return {
        "changed": changed,
        "path": str(file_path),
        "project": updated_document,
        "repository": repository_payload,
    }

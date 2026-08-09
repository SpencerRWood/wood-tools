from __future__ import annotations

import json
import re
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .models import ResourceError, ResourceManifest

RESOURCE_SCHEMA_VERSION = 1
RESOURCE_MANIFEST_FILE_NAME = "wood-resource.json"
RESOURCE_INSTALL_METADATA_FILE_NAME = ".wood-resource-install.json"
RESOURCE_KIND_DIRECTORIES = {
    "template": "packs/templates",
    "reference": "packs/references",
    "agent": "packs/agents",
    "tool": "tools",
    "script": "scripts",
}
HELPER_RESOURCE_KINDS = {"tool", "script"}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def wood_tools_version() -> str:
    try:
        return version("wood-tools")
    except PackageNotFoundError:
        return "0.0.0"


def validate_slug(field: str, value: object) -> str:
    if not isinstance(value, str) or not SLUG_PATTERN.fullmatch(value):
        raise ResourceError(f"{field} must use lowercase letters, numbers, and hyphens only.")
    return value


def validate_version(field: str, value: object) -> str:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ResourceError(f"{field} must be a non-empty version string.")
    return value


def validate_resource_digest(value: object) -> str:
    if not isinstance(value, str) or not SHA256_DIGEST_PATTERN.fullmatch(value):
        raise ResourceError("digest must be sha256:<64 lowercase hex characters>.")
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
        raise ResourceError(f"Resource source directory does not exist: {resolved_source}")
    if not resolved_source.is_dir():
        raise ResourceError(f"Resource source must be a directory: {resolved_source}")

    digest = sha256()
    for path in _iter_resource_files(resolved_source):
        relative = path.relative_to(resolved_source).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def load_resource_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        raise ResourceError(f"Resource manifest not found: {manifest_path}")
    if not manifest_path.is_file():
        raise ResourceError(f"Resource manifest must be a file: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResourceError(f"Invalid JSON in resource manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ResourceError("Resource manifest root must be an object.")
    return manifest


def _validate_helper_contract(kind: str, raw_contract: object) -> dict[str, Any] | None:
    if kind not in HELPER_RESOURCE_KINDS:
        if raw_contract is not None and not isinstance(raw_contract, dict):
            raise ResourceError("helper_contract must be an object when provided.")
        return raw_contract if isinstance(raw_contract, dict) else None

    if not isinstance(raw_contract, dict):
        raise ResourceError(f"{kind} resources must define helper_contract.")
    if raw_contract.get("deterministic") is not True:
        raise ResourceError("helper_contract.deterministic must be true.")
    for field in ("input", "output", "errors"):
        value = raw_contract.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ResourceError(f"helper_contract.{field} must be a non-empty string.")
    return raw_contract


def _validate_resource_compatibility(raw_compatibility: object) -> dict[str, Any]:
    if not isinstance(raw_compatibility, dict):
        raise ResourceError("compatibility must be an object.")
    requirement = raw_compatibility.get("wood_tools")
    if not isinstance(requirement, str) or not requirement.strip():
        raise ResourceError("compatibility.wood_tools must be a non-empty string.")
    current_version = wood_tools_version()
    if not _version_satisfies(requirement, current_version):
        raise ResourceError(
            f"Resource requires wood-tools {requirement}, current version is {current_version}."
        )
    return raw_compatibility


def validate_resource_manifest(
    manifest: dict[str, Any],
    *,
    source_dir: Path,
) -> ResourceManifest:
    if manifest.get("schema_version") != RESOURCE_SCHEMA_VERSION:
        raise ResourceError(f"schema_version must be {RESOURCE_SCHEMA_VERSION}.")

    kind = validate_slug("kind", manifest.get("kind"))
    if kind not in RESOURCE_KIND_DIRECTORIES:
        available = ", ".join(sorted(RESOURCE_KIND_DIRECTORIES))
        raise ResourceError(f"kind must be one of: {available}.")

    name = validate_slug("name", manifest.get("name"))
    version = validate_version("version", manifest.get("version"))
    digest = validate_resource_digest(manifest.get("digest"))
    compatibility = _validate_resource_compatibility(manifest.get("compatibility"))
    helper_contract = _validate_helper_contract(kind, manifest.get("helper_contract"))
    actual_digest = compute_resource_digest(source_dir)
    if actual_digest != digest:
        raise ResourceError(f"Resource digest mismatch: expected {digest}, got {actual_digest}.")

    return ResourceManifest(
        kind=kind,
        name=name,
        version=version,
        digest=digest,
        compatibility=compatibility,
        helper_contract=helper_contract,
        raw=dict(manifest),
    )

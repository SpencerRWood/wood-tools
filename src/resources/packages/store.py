from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .manifest import (
    RESOURCE_INSTALL_METADATA_FILE_NAME,
    RESOURCE_KIND_DIRECTORIES,
    RESOURCE_MANIFEST_FILE_NAME,
    RESOURCE_SCHEMA_VERSION,
    _iter_resource_files,
    compute_resource_digest,
    load_resource_manifest,
    validate_resource_manifest,
    validate_slug,
    validate_version,
)
from .models import ResourceError, ResourceManifest


def _ensure_writable_parent(path: Path) -> None:
    parent = path
    while not parent.exists() and parent.parent != parent:
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise ResourceError(f"Resource install base is not writable: {parent}")


def resource_base_dir(wood_home: Path, kind: str) -> Path:
    return wood_home / RESOURCE_KIND_DIRECTORIES[kind]


def _resource_install_dir(wood_home: Path, manifest: ResourceManifest) -> Path:
    return resource_base_dir(wood_home, manifest.kind) / manifest.name / manifest.version


def resource_metadata_path(install_dir: Path) -> Path:
    return install_dir / RESOURCE_INSTALL_METADATA_FILE_NAME


def build_resource_metadata(
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
    base_fields = {
        "schema_version",
        "kind",
        "name",
        "version",
        "digest",
        "compatibility",
        "helper_contract",
    }
    for field, value in manifest.raw.items():
        if field not in base_fields:
            metadata["resource"][field] = value
    return metadata


def _copy_resource_tree(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=False)
    for path in _iter_resource_files(source_dir):
        relative = path.relative_to(source_dir)
        destination = target_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def load_installed_resource_metadata(install_dir: Path) -> dict[str, Any]:
    metadata_path = resource_metadata_path(install_dir)
    if not metadata_path.exists():
        raise ResourceError(f"Installed resource metadata not found: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResourceError(f"Invalid JSON in installed resource metadata: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ResourceError("Installed resource metadata root must be an object.")
    return metadata


def _assert_same_installed_resource(
    metadata: dict[str, Any],
    manifest: ResourceManifest,
    install_dir: Path,
) -> None:
    resource = metadata.get("resource")
    if not isinstance(resource, dict):
        raise ResourceError(
            f"Installed resource metadata is corrupt: {resource_metadata_path(install_dir)}"
        )
    expected = {
        "kind": manifest.kind,
        "name": manifest.name,
        "version": manifest.version,
        "digest": manifest.digest,
    }
    actual = {key: resource.get(key) for key in expected}
    if actual != expected:
        raise ResourceError(
            f"Resource conflict at {install_dir}: installed metadata does not match requested "
            f"{manifest.kind}/{manifest.name}/{manifest.version}."
        )


def install_resource(
    *,
    source_dir: Path,
    wood_home: Path,
    apply: bool,
) -> dict[str, Any]:
    wood_home = wood_home.expanduser().resolve()
    resolved_source = source_dir.expanduser().resolve()
    manifest = validate_resource_manifest(
        load_resource_manifest(resolved_source / RESOURCE_MANIFEST_FILE_NAME),
        source_dir=resolved_source,
    )
    install_dir = _resource_install_dir(wood_home, manifest)
    metadata = build_resource_metadata(manifest, install_dir=install_dir)

    if install_dir.exists():
        if not install_dir.is_dir():
            raise ResourceError(f"Resource install location is not a directory: {install_dir}")
        installed_metadata = load_installed_resource_metadata(install_dir)
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

    _ensure_writable_parent(resource_base_dir(wood_home, manifest.kind))
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
    resource_metadata_path(staging_dir).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    if install_dir.exists():
        shutil.rmtree(staging_dir)
        installed_metadata = load_installed_resource_metadata(install_dir)
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


def select_installed_version(base_dir: Path, requested_version: str | None) -> Path:
    if requested_version is not None:
        return base_dir / requested_version
    if not base_dir.exists():
        raise ResourceError(f"Resource is not installed: {base_dir}")
    versions = sorted(path for path in base_dir.iterdir() if path.is_dir())
    if not versions:
        raise ResourceError(f"Resource has no installed versions: {base_dir}")
    return versions[-1]


def inspect_resource(
    *,
    kind: str,
    name: str,
    version: str | None = None,
    wood_home: Path,
) -> dict[str, Any]:
    wood_home = wood_home.expanduser().resolve()
    resolved_kind = validate_slug("kind", kind)
    if resolved_kind not in RESOURCE_KIND_DIRECTORIES:
        available = ", ".join(sorted(RESOURCE_KIND_DIRECTORIES))
        raise ResourceError(f"kind must be one of: {available}.")
    resolved_name = validate_slug("name", name)
    resolved_version = validate_version("version", version) if version is not None else None
    install_dir = select_installed_version(
        resource_base_dir(wood_home, resolved_kind) / resolved_name,
        resolved_version,
    )
    metadata = load_installed_resource_metadata(install_dir)
    resource = metadata.get("resource")
    if not isinstance(resource, dict):
        raise ResourceError(
            f"Installed resource metadata is corrupt: {resource_metadata_path(install_dir)}"
        )
    digest = compute_resource_digest(install_dir)
    if digest != resource.get("digest"):
        raise ResourceError(
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
    wood_home: Path,
) -> dict[str, Any]:
    payload = inspect_resource(
        kind=kind,
        name=name,
        version=version,
        wood_home=wood_home,
    )
    install_dir = Path(payload["path"])
    if relative_path is not None:
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise ResourceError("relative_path must stay within the installed resource.")
        resolved_path = install_dir / requested
        if not resolved_path.exists():
            raise ResourceError(f"Resource path does not exist: {resolved_path}")
    else:
        resolved_path = install_dir
    return {
        "resource": payload["resource"],
        "path": str(resolved_path),
    }

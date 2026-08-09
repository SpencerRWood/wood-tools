from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from resources.packages import (
    RESOURCE_MANIFEST_FILE_NAME,
    build_resource_metadata,
    compute_resource_digest,
    inspect_resource,
    load_installed_resource_metadata,
    load_resource_manifest,
    resource_base_dir,
    resource_metadata_path,
    validate_resource_manifest,
    validate_slug,
)

from .manifest import (
    TEMPLATE_PACK_CONTRACT_FIELD,
    TEMPLATE_PACK_RESOURCE_KIND,
    TemplateError,
    validate_project_template_packs,
    validate_template_pack_contract,
)
from .project import PROJECT_FILE_NAME, load_project_document, resolve_project_root


def _template_pack_payload(
    *,
    source: str,
    source_detail: str | None,
    resource: dict[str, Any],
) -> dict[str, Any]:
    contract = validate_template_pack_contract(
        resource.get(TEMPLATE_PACK_CONTRACT_FIELD),
        manifest_name=str(resource.get("name") or ""),
        manifest_version=str(resource.get("version") or ""),
    )
    if not isinstance(contract, dict):
        raise TemplateError(
            f"Template resource {resource.get('name', '<unknown>')} does not define "
            f"{TEMPLATE_PACK_CONTRACT_FIELD}."
        )
    operations = contract.get("operations")
    if not isinstance(operations, list):
        raise TemplateError("template_pack.operations must be an array.")
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
        load_resource_manifest(resolved_source / RESOURCE_MANIFEST_FILE_NAME),
        source_dir=resolved_source,
    )
    if manifest.kind != TEMPLATE_PACK_RESOURCE_KIND:
        raise TemplateError(
            f"Template pack source must be a template resource, got {manifest.kind}."
        )
    metadata = build_resource_metadata(manifest, install_dir=resolved_source)
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
    base_dir = resource_base_dir(wood_home, TEMPLATE_PACK_RESOURCE_KIND)
    if not base_dir.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for name_dir in sorted(path for path in base_dir.iterdir() if path.is_dir()):
        version_dirs = sorted((path for path in name_dir.iterdir() if path.is_dir()), reverse=True)
        for version_dir in version_dirs:
            metadata = load_installed_resource_metadata(version_dir)
            resource = metadata.get("resource")
            if not isinstance(resource, dict):
                metadata_path = resource_metadata_path(version_dir)
                raise TemplateError(f"Installed resource metadata is corrupt: {metadata_path}")
            if resource.get("kind") != TEMPLATE_PACK_RESOURCE_KIND:
                continue
            digest = compute_resource_digest(version_dir)
            if digest != resource.get("digest"):
                raise TemplateError(
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
            wood_home=Path(document["wood_home"]),
        )
        resource = payload["resource"]
        if resource.get("digest") != expected_digest:
            raise TemplateError(
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
    validate_project_template_packs(document)
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
    requested_name = validate_slug("name", name)
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
    raise TemplateError(f"Template pack not found: {requested_name}{source_hint}")


__all__ = ["list_template_packs", "show_template_pack"]

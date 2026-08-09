from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from resources.packages import (
    compute_resource_digest,
    inspect_resource,
    load_installed_resource_metadata,
    resource_base_dir,
    resource_metadata_path,
    select_installed_version,
    validate_resource_digest,
    validate_slug,
    wood_tools_version,
)

from .catalog import (
    _iter_builtin_template_pack_dirs,
    _load_template_pack_from_source,
    _template_pack_payload,
)
from .manifest import TEMPLATE_PACK_RESOURCE_KIND, TemplateError, validate_project_template_packs
from .project import (
    PROJECT_FILE_NAME,
    WOOD_HOME_DIR_NAME,
    WOOD_HOME_ENV,
    load_project_document,
    resolve_project_root,
    slugify_project_name,
)

TEMPLATE_LOCK_FILE_NAME = "wood.lock.json"
TEMPLATE_LOCK_SCHEMA_VERSION = 1


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
            raise TemplateError(f"Template variable requires an explicit value: {name}.")
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
        raise TemplateError("Generated template content must not contain Wood home paths.")


def _load_template_lock(lock_path: Path) -> dict[str, Any] | None:
    if not lock_path.exists():
        return None
    if not lock_path.is_file():
        raise TemplateError(f"Template lock must be a file: {TEMPLATE_LOCK_FILE_NAME}")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"Invalid JSON in {TEMPLATE_LOCK_FILE_NAME}: {exc}") from exc
    if not isinstance(lock, dict) or lock.get("schema_version") != TEMPLATE_LOCK_SCHEMA_VERSION:
        raise TemplateError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
    template = lock.get("template")
    files = lock.get("files")
    if not isinstance(template, dict) or not isinstance(files, list):
        raise TemplateError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
    return lock


def _locked_output_digests(lock: dict[str, Any] | None) -> dict[str, str]:
    if lock is None:
        return {}
    digests: dict[str, str] = {}
    for entry in lock["files"]:
        if not isinstance(entry, dict):
            raise TemplateError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
        path = entry.get("path")
        digest = entry.get("digest")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise TemplateError(f"Unsupported or invalid {TEMPLATE_LOCK_FILE_NAME}.")
        validate_resource_digest(digest)
        digests[path] = digest
    return digests


def _template_lock_payload(plan: dict[str, Any], rendered: dict[str, bytes]) -> dict[str, Any]:
    return {
        "schema_version": TEMPLATE_LOCK_SCHEMA_VERSION,
        "template": {key: plan["template"][key] for key in ("name", "source", "version", "digest")},
        "wood_tools_version": wood_tools_version(),
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
        raise TemplateError(
            "Template application failed; prior project state was restored."
        ) from exc


def _template_pack_from_install_dir(
    install_dir: Path,
    *,
    source: str,
    source_detail: str | None,
) -> dict[str, Any]:
    metadata = load_installed_resource_metadata(install_dir)
    resource = metadata.get("resource")
    if not isinstance(resource, dict):
        metadata_path = resource_metadata_path(install_dir)
        raise TemplateError(f"Installed resource metadata is corrupt: {metadata_path}")
    digest = compute_resource_digest(install_dir)
    if digest != resource.get("digest"):
        raise TemplateError(
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
    requested_name = validate_slug("name", name)
    if source_dir is not None:
        resolved_source = source_dir.expanduser().resolve()
        payload = _load_template_pack_from_source(resolved_source, source="explicit")
        if payload["name"] != requested_name:
            raise TemplateError(f"Template pack not found: {requested_name} from {resolved_source}")
        return payload, resolved_source

    root = resolve_project_root(project_root)
    file_path = project_file or root / PROJECT_FILE_NAME
    if file_path.exists():
        document = load_project_document(file_path)
        validate_project_template_packs(document)
        for entry in document.get("template_packs", []) or []:
            if entry["name"] != requested_name:
                continue
            resource_payload = inspect_resource(
                kind=TEMPLATE_PACK_RESOURCE_KIND,
                name=requested_name,
                version=entry["version"],
                wood_home=Path(document["wood_home"]),
            )
            resource = resource_payload["resource"]
            if resource.get("digest") != entry["digest"]:
                raise TemplateError(
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
        installed_base = resource_base_dir(wood_home, TEMPLATE_PACK_RESOURCE_KIND) / requested_name
        if installed_base.exists():
            install_dir = select_installed_version(installed_base, None)
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

    raise TemplateError(f"Template pack not found: {requested_name}")


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
        raise TemplateError(f"Template output already exists: {conflict['path']}")

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
            raise TemplateError(
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
                raise TemplateError(
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
            raise TemplateError(f"Template source file does not exist: {operation['template']}")
        if not template_path.is_file():
            raise TemplateError(f"Template source must be a file: {operation['template']}")
        relative_output = _replace_template_variables(operation["output"], variables)
        output_path = Path(relative_output)
        if output_path.is_absolute() or ".." in output_path.parts:
            raise TemplateError(
                f"Rendered template output must stay within the project: {relative_output}"
            )
        if relative_output in seen_outputs:
            raise TemplateError(
                f"Template operations resolve to duplicate output: {relative_output}"
            )
        seen_outputs.add(relative_output)
        destination = target_root / relative_output
        if not destination.resolve().is_relative_to(target_root.resolve()):
            raise TemplateError(
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


__all__ = ["plan_template_pack", "render_template_pack"]

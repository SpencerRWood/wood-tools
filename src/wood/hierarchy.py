"""Plan and provision a verified OpenProject hierarchy for a repository."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from wood_project.openproject import OpenProjectClient, OpenProjectError, load_settings
from wood_project.story.discovery import fetch_collection
from wood_project.story.models import StoryWorkflowError
from wood_project.story.openproject import extract_id_from_href, work_package_type_name

from .operations import OperationsError, repository_root
from .output import Status, envelope
from .project import ProjectLookupError, _resolve

TABLE = "tool.wood.openproject"
KEYS = ("project_id", "initiative_id", "release_id", "epic_id")


class HierarchyError(Exception):
    def __init__(self, code: str, message: str, status: Status = "invalid") -> None:
        super().__init__(message)
        self.code, self.status = code, status
        self.applied: list[dict[str, Any]] = []


def _error_status(exc: Exception) -> Status:
    if (
        isinstance(exc, OpenProjectError | OSError)
        or isinstance(exc.__cause__, OpenProjectError)
        or getattr(exc, "code", "") == "INCOMPLETE_COLLECTION"
    ):
        return "unavailable"
    if getattr(exc, "code", "") == "AMBIGUOUS_SELECTOR":
        return "ambiguous"
    return getattr(exc, "status", "invalid")


def _error_message(exc: Exception) -> str:
    if isinstance(exc, OpenProjectError) or isinstance(exc.__cause__, OpenProjectError):
        return "Check OpenProject connection, credentials, and access."
    if isinstance(exc, OSError):
        return "Cannot access hierarchy files."
    return str(exc)


def add_hierarchy_parser(commands: argparse._SubParsersAction[Any]) -> None:
    parser = commands.add_parser("hierarchy", help="Plan or ensure repository planning hierarchy")
    actions = parser.add_subparsers(dest="hierarchy_action", required=True)
    for action in ("plan", "ensure"):
        item = actions.add_parser(action)
        item.add_argument("--project", help="Project ID, identifier, or exact name")
        for kind in ("initiative", "release", "epic"):
            item.add_argument(f"--{kind}", help="ID or exact name; omitted uses repository mapping")
        if action == "ensure":
            item.add_argument("--apply", action="store_true")
            item.add_argument("--plan-hash", help="Hash returned by the reviewed plan")
        item.add_argument("--json", dest="hierarchy_json", action="store_true")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _read(root: Path) -> tuple[str, dict[str, Any]]:
    path = root / "pyproject.toml"
    try:
        if path.is_symlink():
            raise ValueError("symlink")
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if len(text) > 1_000_000:
            raise ValueError("oversized")
        mapping = tomllib.loads(text).get("tool", {}).get("wood", {}).get("openproject", {})
        if not isinstance(mapping, dict):
            raise ValueError("mapping")
        for key in KEYS:
            if key in mapping and (type(mapping[key]) is not int or mapping[key] <= 0):
                raise ValueError("identifier")
        # Check that the file can be patched without flattening unrelated TOML.
        _render(text, dict.fromkeys(KEYS, 1))
        return text, mapping
    except (OSError, ValueError, AttributeError, UnicodeError) as exc:
        raise HierarchyError(
            "HIERARCHY_MAPPING_INVALID",
            "Use a writable pyproject.toml with a standard [tool.wood.openproject] table "
            "and positive integer mapping IDs.",
        ) from exc


def _render(text: str, mapping: dict[str, int]) -> str:
    """Preserve unrelated TOML and comments; reject unsupported mapping syntax."""
    lines = text.splitlines(keepends=True)
    starts = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(r"\s*\[tool\.wood\.openproject\]\s*(?:#.*)?\n?", line)
    ]
    if not starts:
        if tomllib.loads(text).get("tool", {}).get("wood", {}).get("openproject") is not None:
            raise ValueError("Inline/dotted mapping cannot be patched safely")
        result = text.rstrip() + ("\n\n" if text.strip() else "") + f"[{TABLE}]\n"
        result += "".join(f"{key} = {mapping[key]}\n" for key in KEYS)
    else:
        start = starts[0] + 1
        end = next(
            (i for i in range(start, len(lines)) if lines[i].lstrip().startswith("[")), len(lines)
        )
        found = set()
        for i in range(start, end):
            match = re.fullmatch(r"(\s*)(\w+)(\s*=\s*)(\d+)(\s*(?:#.*)?)(\n?)", lines[i])
            if match and match[2] in mapping:
                key = match[2]
                lines[i] = f"{match[1]}{key}{match[3]}{mapping[key]}{match[5]}{match[6]}"
                found.add(key)
        present = tomllib.loads(text).get("tool", {}).get("wood", {}).get("openproject", {})
        if set(present).intersection(KEYS) - found:
            raise ValueError("Unsupported mapping assignment syntax")
        added = "".join(f"{key} = {mapping[key]}\n" for key in KEYS if key not in found)
        if added and end and not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines.insert(end, added)
        result = "".join(lines)
    if tomllib.loads(result)["tool"]["wood"]["openproject"] != {
        **tomllib.loads(text).get("tool", {}).get("wood", {}).get("openproject", {}),
        **mapping,
    }:
        raise ValueError("Mapping patch did not round-trip")
    return result


def _collection(
    client: OpenProjectClient, path: str, query: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    return fetch_collection(client, path, query=query or {}, page_size=100, require_total=True)


def _link(item: dict[str, Any], name: str, resource: str) -> int | None:
    return extract_id_from_href(item.get("_links", {}).get(name, {}).get("href"), resource)


def _choose(items: list[dict[str, Any]], ref: str, kind: str) -> dict[str, Any] | None:
    if not ref.strip() or len(ref) > 120 or any(ord(c) < 32 for c in ref):
        raise HierarchyError(
            "INVALID_INPUT", "Selectors must be nonblank and at most 120 characters."
        )
    try:
        return _resolve(items, ref, kind=kind)
    except ProjectLookupError as exc:
        if exc.code == "NOT_FOUND" and not ref.isdecimal():
            return None
        raise


def plan_hierarchy(
    client: OpenProjectClient, root: Path, args: argparse.Namespace
) -> dict[str, Any]:
    text, mapping = _read(root)
    project_ref = args.project or str(mapping.get("project_id") or "")
    if not project_ref:
        raise HierarchyError("INVALID_CONTEXT", "Pass --project or configure project_id.")
    project = _choose(_collection(client, "/api/v3/projects"), project_ref, "Project")
    if project is None:
        raise HierarchyError("NOT_FOUND", "Project must already exist.")
    project_id = int(project["id"])
    if project.get("active") is False:
        raise HierarchyError("HIERARCHY_INACTIVE", "Selected project is inactive.")
    filters = [
        {"project": {"operator": "=", "values": [str(project_id)]}},
        {"status": {"operator": "*", "values": []}},
    ]
    packages = _collection(client, "/api/v3/work_packages", {"filters": json.dumps(filters)})
    versions = _collection(client, f"/api/v3/projects/{project_id}/versions")
    types = _collection(client, f"/api/v3/projects/{project_id}/types")
    statuses = _collection(client, "/api/v3/statuses")
    operations = []
    ids: dict[str, int | str] = {"project_id": project_id}
    for kind in ("initiative", "release", "epic"):
        ref = getattr(args, kind) or str(mapping.get(f"{kind}_id") or "")
        if not ref:
            raise HierarchyError("INVALID_CONTEXT", f"Pass --{kind} or configure {kind}_id.")
        items = (
            versions
            if kind == "release"
            else [item for item in packages if work_package_type_name(item) == kind.title()]
        )
        item = _choose(items, ref, kind.title())
        if item is not None:
            identifier = int(item["id"])
            if kind == "release":
                if _link(item, "definingProject", "projects") != project_id:
                    raise HierarchyError(
                        "HIERARCHY_CONFLICT", "Release belongs to another project."
                    )
                if item.get("status") != "open":
                    raise HierarchyError("HIERARCHY_INACTIVE", "Release must be open.")
            else:
                if _link(item, "project", "projects") != project_id:
                    raise HierarchyError(
                        "HIERARCHY_CONFLICT", "Work package belongs to another project."
                    )
                status_id = _link(item, "status", "statuses")
                status = next((s for s in statuses if s["id"] == status_id), None)
                if status is None or status.get("isClosed") is not False:
                    raise HierarchyError(
                        "HIERARCHY_INACTIVE", "Work package must have an open status."
                    )
                if kind == "epic" and (
                    _link(item, "parent", "work_packages") != ids["initiative_id"]
                    or _link(item, "version", "versions") != ids["release_id"]
                ):
                    raise HierarchyError(
                        "HIERARCHY_CONFLICT",
                        "Epic parent or Release differs from the requested hierarchy.",
                    )
            ids[f"{kind}_id"] = identifier
            operations.append(
                {
                    "kind": kind,
                    "action": "reuse",
                    "id": identifier,
                    "name": item.get("name") if kind == "release" else item["subject"],
                }
            )
            continue
        ids[f"{kind}_id"] = f"planned:{kind}"
        if kind == "release":
            path = "/api/v3/versions"
            body: dict[str, Any] = {
                "name": ref,
                "status": "open",
                "_links": {"definingProject": {"href": f"/api/v3/projects/{project_id}"}},
            }
        else:
            type_item = _resolve(types, kind.title(), kind="Type")
            new = _resolve(statuses, "New", kind="Status")
            if new.get("isClosed") is not False:
                raise HierarchyError("HIERARCHY_INACTIVE", "New status must be open.")
            path = f"/api/v3/projects/{project_id}/work_packages"
            body = {
                "subject": ref,
                "_links": {
                    "project": {"href": f"/api/v3/projects/{project_id}"},
                    "type": {"href": f"/api/v3/types/{type_item['id']}"},
                    "status": {"href": f"/api/v3/statuses/{new['id']}"},
                },
            }
            if kind == "epic":
                for name, key, resource in (
                    ("parent", "initiative_id", "work_packages"),
                    ("version", "release_id", "versions"),
                ):
                    value = ids[key]
                    body["_links"][name] = {
                        "href": f"/api/v3/{resource}/{value}" if isinstance(value, int) else value
                    }
        body_json = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if len(body_json) > 500:
            raise HierarchyError(
                "INVALID_INPUT",
                "Creation request exceeds the bounded preview size; shorten the name.",
            )
        operations.append(
            {
                "kind": kind,
                "action": "create",
                "name": ref,
                "method": "POST",
                "path": path,
                "body_json": body_json,
            }
        )
    known = all(isinstance(value, int) for value in ids.values())
    rendered = _render(text, {key: int(value) for key, value in ids.items()}) if known else None
    plan = {
        "repository_root": str(root),
        "project_id": project_id,
        "operations": operations,
        "mapping": ids,
        "mapping_file": str(root / "pyproject.toml"),
        "mapping_action": "reuse" if rendered == text else "write",
        "source_sha256": _digest(text),
    }
    plan["plan_hash"] = _digest(json.dumps(plan, sort_keys=True))
    return plan


def _verify(
    client: OpenProjectClient,
    operation: dict[str, Any],
    identifier: int,
    ids: dict[str, int],
    types: dict[str, Any] | None = None,
) -> None:
    kind = operation["kind"]
    resource = "versions" if kind == "release" else "work_packages"
    item = client.get_json(f"/api/v3/{resource}/{identifier}")
    valid = item.get("id") == identifier
    if kind == "release":
        valid = valid and item.get("name") == operation["name"] and item.get("status") == "open"
        valid = valid and _link(item, "definingProject", "projects") == ids["project_id"]
    else:
        valid = valid and item.get("subject") == operation["name"]
        valid = valid and work_package_type_name(item) == kind.title()
        valid = valid and _link(item, "project", "projects") == ids["project_id"]
        if kind == "epic":
            valid = valid and _link(item, "parent", "work_packages") == ids["initiative_id"]
            valid = valid and _link(item, "version", "versions") == ids["release_id"]
        if types:
            for name, value in types.items():
                valid = valid and item.get("_links", {}).get(name, {}).get("href") == value["href"]
    if not valid:
        raise HierarchyError(
            "HIERARCHY_VERIFICATION_FAILED",
            "Resulting hierarchy differs from the planned request.",
            "error",
        )


def _persist(root: Path, original: str, mapping: dict[str, int]) -> bool:
    text, _ = _read(root)
    if text != original:
        raise HierarchyError(
            "HIERARCHY_PLAN_STALE", "Repository mapping changed; plan again.", "stale"
        )
    rendered = _render(text, mapping)
    if rendered == text:
        return False
    path = root / "pyproject.toml"
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=root, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if _read(root)[0] != original:
            raise HierarchyError(
                "HIERARCHY_PLAN_STALE", "Repository file changed; plan again.", "stale"
            )
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if _read(root)[1] != {
        **tomllib.loads(original).get("tool", {}).get("wood", {}).get("openproject", {}),
        **mapping,
    }:
        raise HierarchyError(
            "HIERARCHY_VERIFICATION_FAILED", "Saved mapping verification failed.", "error"
        )
    return True


def ensure_hierarchy(
    client: OpenProjectClient, root: Path, args: argparse.Namespace
) -> dict[str, Any]:
    # Serialize cooperating writers for this checkout. Remote APIs have no atomic
    # name uniqueness guarantee; re-discover immediately before each creation.
    lock = Path(tempfile.gettempdir()) / f"wood-hierarchy-{_digest(str(root))}.lock"
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HierarchyError(
                "HIERARCHY_BUSY", "Another hierarchy writer is active.", "blocked"
            ) from exc
        plan = plan_hierarchy(client, root, args)
        if not args.plan_hash or args.plan_hash != plan["plan_hash"]:
            raise HierarchyError(
                "HIERARCHY_PLAN_STALE", "Pass the hash of a fresh hierarchy plan.", "stale"
            )
        original, _ = _read(root)
        ids = {"project_id": plan["project_id"]}
        applied = []
        try:
            for operation in plan["operations"]:
                kind = operation["kind"]
                body = None
                if operation["action"] == "create":
                    fresh = plan_hierarchy(client, root, args)
                    current = next(op for op in fresh["operations"] if op["kind"] == kind)
                    if (
                        current["action"] != "create"
                        or _digest(_read(root)[0]) != plan["source_sha256"]
                    ):
                        raise HierarchyError(
                            "HIERARCHY_PLAN_STALE",
                            "Hierarchy changed; inspect a fresh plan before retrying.",
                            "stale",
                        )
                    body = json.loads(operation["body_json"])
                    for link in body.get("_links", {}).values():
                        if link["href"].startswith("planned:"):
                            target = link["href"].split(":")[1]
                            resource = "versions" if target == "release" else "work_packages"
                            link["href"] = f"/api/v3/{resource}/{ids[target + '_id']}"
                    if body != json.loads(current["body_json"]):
                        raise HierarchyError(
                            "HIERARCHY_PLAN_STALE", "Creation request changed; plan again.", "stale"
                        )
                    created = client.request_json("POST", operation["path"], body=body)
                    identifier = created.get("id")
                    if type(identifier) is not int or identifier <= 0:
                        raise HierarchyError(
                            "HIERARCHY_VERIFICATION_FAILED",
                            "Creation returned no positive ID; inspect before retrying.",
                            "error",
                        )
                else:
                    identifier = operation["id"]
                ids[f"{kind}_id"] = identifier
                applied.append(
                    {
                        "kind": kind,
                        "action": operation["action"],
                        "id": identifier,
                        "verified": False,
                    }
                )
                _verify(client, operation, identifier, ids, body.get("_links") if body else None)
                applied[-1]["verified"] = True
            # Recheck live uniqueness, status, and linkage before writing local context.
            plan_hierarchy(client, root, args)
            for operation in plan["operations"]:
                _verify(client, operation, ids[operation["kind"] + "_id"], ids)
            changed = _persist(root, original, ids)
        except (
            HierarchyError,
            OpenProjectError,
            ProjectLookupError,
            StoryWorkflowError,
            OSError,
        ) as exc:
            # Never roll back verified objects: the next plan safely reuses them.
            error = HierarchyError(
                getattr(exc, "code", "HIERARCHY_IO_UNAVAILABLE"),
                _error_message(exc),
                _error_status(exc),
            )
            error.applied = applied
            raise error from exc
        return {
            "dry_run": False,
            "plan_hash": plan["plan_hash"],
            "mapping": ids,
            "mapping_file": plan["mapping_file"],
            "mapping_written": changed,
            "applied": applied,
        }


def run_hierarchy_command(args: argparse.Namespace, cwd: Path) -> dict[str, object]:
    command = f"hierarchy {args.hierarchy_action}"
    applying = getattr(args, "apply", False)
    try:
        root = repository_root(cwd)
        client = OpenProjectClient(load_settings())
        data = (
            ensure_hierarchy(client, root, args)
            if applying
            else {**plan_hierarchy(client, root, args), "dry_run": True}
        )
        return envelope(
            command=command,
            status="success",
            summary=f"{command}: success.",
            mutation="mutating"
            if applying
            else "read-only"
            if args.hierarchy_action == "plan"
            else "preview",
            data=data,
        )
    except (
        HierarchyError,
        OperationsError,
        OpenProjectError,
        ProjectLookupError,
        StoryWorkflowError,
        OSError,
    ) as exc:
        status = _error_status(exc)
        return envelope(
            command=command,
            status=status,
            summary=f"{command}: {status}.",
            mutation="mutating" if applying else "read-only",
            errors=[
                {
                    "code": getattr(exc, "code", "HIERARCHY_IO_UNAVAILABLE"),
                    "message": _error_message(exc),
                }
            ],
            data={
                "applied": getattr(exc, "applied", []),
                "candidates": exc.candidates if isinstance(exc, ProjectLookupError) else [],
            },
            next_actions=["Inspect a fresh hierarchy plan before retrying."],
        )

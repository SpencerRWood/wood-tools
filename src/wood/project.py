"""OpenProject discovery and canonical Implementation Workbook import."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from wood_project.implementation import apply as workbook_apply
from wood_project.implementation import openproject as implementation_op
from wood_project.implementation import planning, workbook
from wood_project.openproject import (
    OpenProjectClient,
    OpenProjectError,
    embedded_elements,
    load_settings,
)
from wood_project.openproject.context import RepositoryContextError, repository_context
from wood_project.planning_release import release_number, release_sort_key

from .output import Status, envelope


def _client() -> OpenProjectClient:
    return OpenProjectClient(load_settings())


def _collection(
    client: OpenProjectClient, path: str, query: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    return planning.fetch_collection(client, path, query=query or {})


def _projects(client: OpenProjectClient) -> list[dict[str, Any]]:
    return sorted(_collection(client, "/api/v3/projects"), key=lambda item: int(item["id"]))


def _resolve(items: list[dict[str, Any]], ref: str, *, kind: str) -> dict[str, Any]:
    if ref.isdecimal():
        matches = [item for item in items if str(item.get("id")) == ref]
    else:
        matches = [item for item in items if str(item.get("identifier") or "") == ref]
        if not matches:
            matches = [
                item for item in items if str(item.get("name") or item.get("subject") or "") == ref
            ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        candidates = [
            {"id": item["id"], "name": item.get("name") or item.get("subject")} for item in matches
        ]
        raise ProjectLookupError("AMBIGUOUS_SELECTOR", f"{kind} selector is ambiguous.", candidates)
    raise ProjectLookupError("NOT_FOUND", f"{kind} selector did not match.")


class ProjectLookupError(Exception):
    def __init__(self, code: str, message: str, candidates: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.code = code
        self.candidates = candidates or []


def _project_work_packages(client: OpenProjectClient, project_id: int) -> list[dict[str, Any]]:
    return planning.project_work_packages(client=client, project_id=str(project_id))


def _initiatives(work_packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (
            item
            for item in work_packages
            if implementation_op.work_package_type_name(item) == "Initiative"
        ),
        key=lambda item: int(item["id"]),
    )


def _initiative_summary(item: dict[str, Any]) -> dict[str, object]:
    return {
        "id": item["id"],
        "identifier": item.get("identifier"),
        "name": item.get("subject"),
        "status": implementation_op.work_package_status_name(item),
    }


def _project_summary(item: dict[str, Any], initiatives: list[dict[str, Any]]) -> dict[str, object]:
    return {
        "id": item["id"],
        "identifier": item.get("identifier"),
        "name": item.get("name"),
        "status": item.get("status") or ("active" if item.get("active") else "inactive"),
        "initiatives": [_initiative_summary(value) for value in initiatives],
    }


def list_projects(
    client: OpenProjectClient, initiative_ref: str | None = None, offset: int = 0
) -> dict[str, object]:
    if offset < 0:
        raise ProjectLookupError("INVALID_OFFSET", "Offset must be zero or greater.")
    projects = _projects(client)
    rows = [
        (project, _initiatives(_project_work_packages(client, int(project["id"]))))
        for project in projects
    ]
    if initiative_ref:
        all_initiatives = [initiative for _, initiatives in rows for initiative in initiatives]
        selected = _resolve(all_initiatives, initiative_ref, kind="Initiative")
        rows = [(project, [selected]) for project, initiatives in rows if selected in initiatives]
    page = rows[offset : offset + 50]
    return {
        "projects": [_project_summary(project, initiatives) for project, initiatives in page],
        "total": len(rows),
        "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
    }


def project_status(
    client: OpenProjectClient, ref: str, initiative_ref: str | None = None
) -> dict[str, object]:
    project = _resolve(_projects(client), ref, kind="Project")
    project_id = int(project["id"])
    work_packages = _project_work_packages(client, project_id)
    initiatives = _initiatives(work_packages)
    if initiative_ref:
        initiative = _resolve(initiatives, initiative_ref, kind="Initiative")
        initiative_id = int(initiative["id"])
        work_packages = [
            initiative,
            *planning.fetch_descendants(client, initiative_id),
        ]
        initiatives = [initiative]
    versions = [
        item
        for item in _collection(client, f"/api/v3/projects/{project_id}/versions")
        if release_number(str(item.get("name") or "")) is not None
    ]
    stories = [
        item for item in work_packages if implementation_op.work_package_type_name(item) == "Story"
    ]
    counts: dict[str, int] = {}
    for story in stories:
        status = implementation_op.work_package_status_name(story)
        counts[status] = counts.get(status, 0) + 1
    return {
        "project": _project_summary(project, initiatives),
        "planning_versions": sorted(
            (
                {"id": item.get("id"), "name": item.get("name"), "status": item.get("status")}
                for item in versions
            ),
            key=lambda item: release_sort_key(str(item["name"])),
        ),
        "stories": {"total": len(stories), "by_status": dict(sorted(counts.items()))},
    }


def import_workbook(
    client: OpenProjectClient,
    path: Path,
    *,
    sheet_name: str,
    project_ref: str | None,
    initiative_ref: str | None,
    apply: bool,
    plan_hash: str | None,
    operation_offset: int = 0,
) -> dict[str, object]:
    if operation_offset < 0:
        raise ProjectLookupError("INVALID_OFFSET", "Operation offset must be zero or greater.")
    rows = workbook.workbook_rows(path, sheet_name)
    metadata = workbook.workbook_metadata(path)
    workbook_project = planning.unique_workbook_value(rows, "Project")
    context = repository_context(
        keys=tuple(
            key
            for key, ref in (("project_id", project_ref), ("initiative_id", initiative_ref))
            if ref is None
        )
    )
    configured_project_id = str(context.project_id or "")
    selected_project_ref = project_ref
    if selected_project_ref:
        configured_project_id = str(
            _resolve(_projects(client), selected_project_ref, kind="Project")["id"]
        )
    project = planning.resolve_project(
        client=client,
        configured_project_id=configured_project_id,
        workbook_project=workbook_project,
    )
    project_id = int(project["id"])
    statuses = embedded_elements(client.get_json("/api/v3/statuses"))
    types = embedded_elements(client.get_json(f"/api/v3/projects/{project_id}/types"))
    versions = _collection(client, f"/api/v3/projects/{project_id}/versions")
    selected_initiative_id = None
    if initiative_ref:
        selected_initiative_id = int(
            _resolve(
                _initiatives(_project_work_packages(client, project_id)),
                initiative_ref,
                kind="Initiative",
            )["id"]
        )
    initiative = planning.resolve_initiative_plan(
        client=client,
        project=project,
        statuses=statuses,
        types=types,
        rows=rows,
        metadata=metadata,
        explicit_initiative_id=selected_initiative_id,
        configured_initiative_id=context.initiative_id,
    )
    phases = planning.build_implementation_plan(
        rows=rows,
        client=client,
        statuses=statuses,
        types=types,
        versions=versions,
        initiative=initiative,
    )
    digest = hashlib.sha256(
        json.dumps(phases, sort_keys=True).encode() + path.read_bytes()
    ).hexdigest()
    operations: list[dict[str, Any]] = []
    for item in planning.flat_plan(phases):
        operation: dict[str, Any] = {
            key: item.get(key)
            for key in (
                "kind",
                "key",
                "action",
                "work_package_id",
                "row_number",
                "subject",
                "name",
                "from_story_key",
                "to_story_key",
                "relation_type",
            )
            if key in item
        }
        patch = item.get("patch")
        if isinstance(patch, dict):
            operation["fields"] = sorted(
                key for key in patch if key not in {"lockVersion", "_links"}
            )
            operation["links"] = sorted((patch.get("_links") or {}).keys())
        operations.append(operation)
    operation_counts: dict[str, int] = {}
    for item in operations:
        label = f"{item['kind']}:{item['action']}"
        operation_counts[label] = operation_counts.get(label, 0) + 1
    data: dict[str, object] = {
        "project": {
            "id": project_id,
            "identifier": project.get("identifier"),
            "name": project.get("name"),
        },
        "initiative": {
            key: initiative.get(key) for key in ("action", "work_package_id", "subject")
        },
        "row_count": len(rows),
        "plan_hash": digest,
        "operations": operations[operation_offset : operation_offset + 50],
        "operation_count": len(operations),
        "operation_counts": dict(sorted(operation_counts.items())),
        "operation_offset": operation_offset,
        "next_operation_offset": (
            operation_offset + 50 if operation_offset + 50 < len(operations) else None
        ),
    }
    if not apply:
        return envelope(
            command="project import-workbook",
            status="success",
            mutation="preview",
            summary=f"Planned {len(operations)} workbook operations.",
            data=data,
            next_actions=["Review the plan, then rerun with --apply and --plan-hash."],
        )
    if plan_hash != digest:
        return envelope(
            command="project import-workbook",
            status="blocked",
            mutation="preview",
            summary="Workbook or OpenProject context differs from the reviewed plan.",
            data=data,
            errors=[{"code": "PLAN_HASH_MISMATCH", "message": "Supply the current plan hash."}],
            next_actions=["Review this plan and rerun with its plan hash."],
        )
    applied = workbook_apply.apply_plan(client, project, phases)
    row_update = workbook_apply.write_openproject_ids_to_workbook(
        path, sheet_name=sheet_name, rows=rows, applied=applied
    )
    metadata_update = workbook_apply.write_initiative_metadata_to_workbook(
        path, applied=applied, base_url=client.settings.base_url
    )
    data["applied_counts"] = {kind: len(items) for kind, items in applied.items()}
    data["workbook_update"] = {
        "rows": len(row_update["updated_rows"]),
        "metadata": metadata_update["updated"],
    }
    return envelope(
        command="project import-workbook",
        status="success",
        mutation="mutating",
        summary="Workbook import applied and verified.",
        data=data,
    )


def run_project_command(
    action: str,
    *,
    ref: str | None = None,
    initiative: str | None = None,
    offset: int = 0,
    path: Path | None = None,
    sheet_name: str = "Implementation",
    project: str | None = None,
    apply: bool = False,
    plan_hash: str | None = None,
    operation_offset: int = 0,
) -> dict[str, object]:
    command = f"project {action}"
    try:
        client = _client()
        if action == "list":
            data = list_projects(client, initiative, offset)
        elif action == "status":
            assert ref is not None
            data = project_status(client, ref, initiative)
        else:
            assert path is not None
            return import_workbook(
                client,
                path,
                sheet_name=sheet_name,
                project_ref=project,
                initiative_ref=initiative,
                apply=apply,
                plan_hash=plan_hash,
                operation_offset=operation_offset,
            )
        return envelope(
            command=command,
            status="success",
            summary="OpenProject project context loaded.",
            data=data,
        )
    except (ProjectLookupError, RepositoryContextError) as err:
        status: Status = "ambiguous" if err.code == "AMBIGUOUS_SELECTOR" else "invalid"
        return envelope(
            command=command,
            status=status,
            summary=str(err),
            data={"candidates": err.candidates} if isinstance(err, ProjectLookupError) else {},
            errors=[{"code": err.code, "message": str(err)}],
        )
    except implementation_op.ScriptError as err:
        status = (
            "ambiguous"
            if err.code == "AMBIGUOUS_OPENPROJECT_MATCH"
            else "error"
            if err.code == "WORKBOOK_UPLOAD_FAILED"
            else "invalid"
        )
        return envelope(
            command=command,
            status=status,
            mutation="mutating" if apply else "read-only",
            summary=str(err),
            errors=[{"code": err.code, "message": str(err)}],
            next_actions=["Re-preview the workbook, then retry the import."] if apply else [],
        )
    except OpenProjectError as err:
        return envelope(
            command=command,
            status="unavailable",
            mutation="mutating" if apply else "read-only",
            summary="OpenProject request failed.",
            errors=[{"code": err.code, "message": "Check connection, credentials, and access."}],
            next_actions=["Re-preview the workbook, then retry the import."] if apply else [],
        )
    except OSError:
        return envelope(
            command=command,
            status="unavailable",
            mutation="mutating" if apply else "read-only",
            summary="Workbook could not be read or updated.",
            errors=[
                {"code": "WORKBOOK_IO_ERROR", "message": "Check the workbook path and permissions."}
            ],
            next_actions=["Check the workbook, then retry the import."] if apply else [],
        )

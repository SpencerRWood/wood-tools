"""Reviewed ruleset plan/apply and effective branch readback, without bypass grants."""

from __future__ import annotations

import base64
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from . import operations
from .initialization import digest, require, specification, workflows
from .workflow_documents import document, read_only, reusable_only
from .workflow_files import WorkflowFilesError, run_directory, write_json


def rulesets(spec: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for branch in ("main", "development"):
        contexts = ["validation / validation", "pr-routing"]
        if branch == "main":
            contexts.append("readiness / release-readiness")
        result.append(
            {
                "name": f"wood-{branch}",
                "target": "branch",
                "enforcement": "active",
                "bypass_actors": [],
                "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
                "rules": [
                    {"type": "deletion"},
                    {"type": "non_fast_forward"},
                    {
                        "type": "pull_request",
                        "parameters": {
                            "required_approving_review_count": spec["required_approvals"],
                            "dismiss_stale_reviews_on_push": True,
                            "require_code_owner_review": False,
                            "require_last_push_approval": False,
                            "required_review_thread_resolution": True,
                            "allowed_merge_methods": ["merge"]
                            if branch == "main"
                            else ["merge", "squash", "rebase"],
                        },
                    },
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "strict_required_status_checks_policy": True,
                            "do_not_enforce_on_create": False,
                            "required_status_checks": [
                                {"context": name, "integration_id": spec["checks_integration_id"]}
                                for name in contexts
                            ],
                        },
                    },
                ],
            }
        )
    return result


def normalized_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Ignore only GitHub's documented non-weakening PR defaults, not policy drift."""
    result = {"type": rule["type"]}
    if "parameters" in rule:
        parameters = dict(rule["parameters"])
        if rule["type"] == "pull_request":
            if parameters.get("required_reviewers") == []:
                parameters.pop("required_reviewers")
            if parameters.get("require_extra_approval_for_unattributed_changes") is True:
                parameters.pop("require_extra_approval_for_unattributed_changes")
            if "allowed_merge_methods" in parameters:
                parameters["allowed_merge_methods"] = sorted(parameters["allowed_merge_methods"])
        if "required_status_checks" in parameters:
            parameters["required_status_checks"] = sorted(
                parameters["required_status_checks"], key=lambda value: digest(value)
            )
        result["parameters"] = parameters
    return result


def matches_policy(observed: dict[str, Any], desired: dict[str, Any]) -> bool:
    for key, value in desired.items():
        if key == "rules":

            def normalize(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return sorted(
                    (normalized_rule(item) for item in values), key=lambda item: item["type"]
                )

            if normalize(observed.get(key, [])) != normalize(value):
                return False
        elif observed.get(key) != value:
            return False
    return True


def _list(root: Path, repository: str) -> list[dict[str, Any]]:
    items: Any = operations._gh(
        root, f"repos/{repository}/rulesets?includes_parents=true&per_page=100"
    )
    require(
        isinstance(items, list) and len(items) < 100, "Ruleset list is unavailable or truncated."
    )
    return cast(list[dict[str, Any]], items)


def _details(root: Path, repository: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    require(len(items) <= 25, "Ruleset authority exceeds the supported inspection bound.")
    result = []
    for item in items:
        source = item.get("source_type")
        require(source == "Repository", "Inherited rulesets require separate administrator review.")
        detail: Any = operations._gh(root, f"repos/{repository}/rulesets/{item['id']}")
        require(isinstance(detail, dict), "Ruleset detail is unavailable.")
        result.append(detail)
    return result


def _repository(root: Path, spec: dict[str, Any]) -> str:
    repository = operations._slug(root)
    require(
        repository.casefold() == spec["repository"].casefold(), "Protection repository differs."
    )
    metadata = operations._gh(root, f"repos/{repository}")
    require(
        isinstance(metadata, dict)
        and metadata.get("archived") is False
        and metadata.get("default_branch") == "main",
        "Enforcement requires an active repository with default branch main.",
    )
    app = operations._gh(root, "apps/github-actions")
    require(
        isinstance(app, dict) and app.get("id") == spec["checks_integration_id"],
        "Required checks must bind the verified GitHub Actions integration ID.",
    )
    return repository


def plan(root: Path, specification_value: dict[str, Any]) -> dict[str, Any]:
    spec = specification(specification_value)
    repository = _repository(root, spec)
    existing = _details(root, repository, _list(root, repository))
    desired = rulesets(spec)
    operations_plan = []
    for payload in desired:
        matches = [item for item in existing if item["name"] == payload["name"]]
        require(len(matches) <= 1, "Managed ruleset names are ambiguous.")
        observed = matches[0] if matches else None
        if observed is not None:
            # Existing managed rules must already be this policy. Never silently
            # weaken/replace protections installed under another approval.
            require(
                matches_policy(observed, payload),
                "Existing managed ruleset differs; resolve its policy separately.",
            )
        operations_plan.append(
            {
                "action": "verify" if observed else "create",
                "ruleset_id": observed["id"] if observed else None,
                "payload": payload,
            }
        )
    # Bind all observed rulesets, including unmanaged ones, to reject stale plans.
    record = {
        "version": 1,
        "repository": repository,
        "specification": spec,
        "observed_hash": digest(existing),
        "operations": operations_plan,
    }
    record["plan_hash"] = digest(record)
    return record


def _create(root: Path, repository: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "POST", f"repos/{repository}/rulesets", "--input", "-"],
            cwd=root,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkflowFilesError(
            "PROTECTION_APPLY_UNAVAILABLE", "Ruleset creation could not complete.", "unavailable"
        ) from exc
    if result.returncode:
        raise WorkflowFilesError(
            "PROTECTION_APPLY_FAILED",
            "Ruleset creation failed; read back and replan before retry.",
            "error",
        )
    value = json.loads(result.stdout)
    require(
        isinstance(value, dict) and type(value.get("id")) is int,
        "Ruleset creation returned no authority ID; read back before retry.",
    )
    return cast(dict[str, Any], value)


def apply(root: Path, spec: dict[str, Any], plan_hash: str, approval: str) -> dict[str, Any]:
    require(
        isinstance(approval, str) and approval.strip() and len(approval) <= 500,
        "Live enforcement apply needs a separate explicit approval reference.",
    )
    record = plan(root, spec)
    require(record["plan_hash"] == plan_hash, "Protection plan is stale or unreviewed; replan.")
    # Verify workflow installation and check compatibility before imposing required checks.
    installed = workflow_readback(root, record["specification"])
    require(installed["passed"], "Install/verify generated routing and validation workflows first.")
    ids: list[int] = []
    journal = run_directory(root, "wood-protection-apply-") / "apply.json"
    progress = {
        "plan_hash": plan_hash,
        "repository": record["repository"],
        "approval_reference": approval,
        "ruleset_ids": ids,
        "state": "incomplete",
    }
    write_json(journal, progress)
    try:
        for operation in record["operations"]:
            value = (
                _create(root, record["repository"], operation["payload"])
                if operation["action"] == "create"
                else {"id": operation["ruleset_id"]}
            )
            ids.append(value["id"])
            write_json(journal, progress)
    except WorkflowFilesError as exc:
        raise WorkflowFilesError(exc.code, f"{exc} Recovery record: {journal}", exc.status) from exc
    progress["state"] = "applied-readback-required"
    write_json(journal, progress)
    return {
        "ruleset_ids": ids,
        "approval_reference": approval,
        "apply_record": str(journal),
        "readback": readback(root, record["specification"]),
    }


def workflow_readback(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    expected = workflows(spec)
    repository = spec["repository"]
    checks = []
    revisions = {}
    for branch in ("main", "development"):
        branch_data: Any = operations._gh(root, f"repos/{repository}/branches/{branch}")
        require(isinstance(branch_data, dict), "Branch authority is unavailable.")
        revision = branch_data["commit"]["sha"]
        revisions[branch] = revision
        listing: Any = operations._gh(
            root, f"repos/{repository}/contents/.github/workflows?ref={revision}"
        )
        require(isinstance(listing, list), "Workflow listing is unavailable.")
        names = {item["name"] for item in listing if item.get("type") == "file"}
        checks.append({"branch": branch, "check": "workflow-set", "passed": set(expected) <= names})
        for name, text in expected.items():
            source: Any = operations._gh(
                root, f"repos/{repository}/contents/.github/workflows/{name}?ref={revision}"
            )
            require(
                isinstance(source, dict) and source.get("encoding") == "base64",
                "Workflow source is unavailable.",
            )
            actual = base64.b64decode(source["content"], validate=False).decode()
            try:
                matched = document(actual) == document(text)
            except WorkflowFilesError:
                matched = False
            checks.append({"branch": branch, "check": name, "passed": matched})
        for name in sorted(names - set(expected)):
            if not name.endswith((".yml", ".yaml")):
                continue
            extra: Any = operations._gh(
                root, f"repos/{repository}/contents/.github/workflows/{name}?ref={revision}"
            )
            safe = False
            try:
                value = document(base64.b64decode(extra["content"]).decode())
                safe = reusable_only(value) or read_only(value)
            except WorkflowFilesError, ValueError, KeyError, TypeError:
                pass
            checks.append({"branch": branch, "check": name, "passed": safe})
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "revisions": revisions,
    }


def readback(root: Path, spec_value: dict[str, Any]) -> dict[str, Any]:
    spec = specification(spec_value)
    repository = _repository(root, spec)
    desired = rulesets(spec)
    details = _details(root, repository, _list(root, repository))
    checks = []
    for payload in desired:
        branch = payload["name"].removeprefix("wood-")
        effective: Any = operations._gh(
            root, f"repos/{repository}/rules/branches/{branch}?per_page=100"
        )
        require(
            isinstance(effective, list) and len(effective) < 100,
            "Effective rule authority is unavailable or truncated.",
        )
        managed = [item for item in details if item["name"] == payload["name"]]
        matched = len(managed) == 1 and matches_policy(managed[0], payload)
        checks.append({"branch": branch, "check": "active-no-bypass-ruleset", "passed": matched})
        # An extra applicable ruleset with bypass actors would undermine the
        # expected effective approval model. Do not claim enforcement from a POST.
        ids = {item.get("ruleset_id") for item in effective}
        known_ids = {item["id"] for item in details}
        checks.append(
            {
                "branch": branch,
                "check": "effective-no-bypass",
                "passed": bool(ids)
                and ids <= known_ids
                and all(not item.get("bypass_actors") for item in details if item["id"] in ids),
            }
        )
        for rule in payload["rules"]:
            candidates = [item for item in effective if item["type"] == rule["type"]]
            checks.append(
                {
                    "branch": branch,
                    "check": rule["type"],
                    "passed": matched
                    and any(
                        item.get("ruleset_id") == managed[0]["id"]
                        and normalized_rule(item) == normalized_rule(rule)
                        for item in candidates
                    ),
                }
            )
    installation = workflow_readback(root, spec)
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "repository": repository,
        "passed": all(check["passed"] for check in checks) and installation["passed"],
        "checks": checks,
        "workflows": installation,
        "authorization_granted": False,
    }

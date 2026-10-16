"""Reviewed ruleset plan/apply and effective branch readback, without bypass grants."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from . import operations
from .initialization import digest, require, specification, workflows
from .workflow_documents import document, events, main_publication, read_only, reusable_only
from .workflow_files import WorkflowFilesError, run_directory, write_json


def rulesets(spec: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for branch in ("main", "development"):
        contexts = list(
            dict.fromkeys(
                ["validation / validation", "pr-routing", *spec.get("validation_checks", [])]
            )
        )
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
                            "allowed_merge_methods": ["merge"],
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


def _adoptable(
    observed: dict[str, Any],
    desired: dict[str, Any],
    app: int,
    replaced: set[tuple[str, int]] | None = None,
) -> bool:
    """Strengthen policy or replace only the exact checks separately approved for this rule."""
    conditions = json.loads(json.dumps(observed.get("conditions", {})))
    if desired["name"] == "wood-main":
        names = conditions.get("ref_name", {}).get("include", [])
        conditions.setdefault("ref_name", {})["include"] = [
            "refs/heads/main" if name == "~DEFAULT_BRANCH" else name for name in names
        ]
    if (
        observed.get("target") != "branch"
        or observed.get("enforcement") not in {"active", "disabled"}
        or observed.get("bypass_actors") != []
        or conditions != desired["conditions"]
    ):
        return False
    old = {rule["type"]: normalized_rule(rule) for rule in observed["rules"]}
    new = {rule["type"]: normalized_rule(rule) for rule in desired["rules"]}
    if not old.keys() <= new.keys():
        return False
    for kind, rule in old.items():
        if kind == "pull_request":
            previous = rule["parameters"]
            target = new[kind]["parameters"]
            special = {
                "required_approving_review_count",
                "dismiss_stale_reviews_on_push",
                "required_review_thread_resolution",
                "allowed_merge_methods",
            }
            if (
                any(
                    target.get(key) != value
                    for key, value in previous.items()
                    if key not in special
                )
                or target["required_approving_review_count"]
                < previous["required_approving_review_count"]
                or not set(target["allowed_merge_methods"])
                <= set(previous["allowed_merge_methods"])
                or any(
                    previous.get(key, False) and not target.get(key, False)
                    for key in (
                        "dismiss_stale_reviews_on_push",
                        "required_review_thread_resolution",
                    )
                )
            ):
                return False
        elif kind == "required_status_checks":
            previous = rule["parameters"]
            target = new[kind]["parameters"]
            if any(
                target.get(key) != value
                for key, value in previous.items()
                if key not in {"required_status_checks", "strict_required_status_checks_policy"}
            ):
                return False
            if previous.get("strict_required_status_checks_policy", False) and not target.get(
                "strict_required_status_checks_policy", False
            ):
                return False
            required = {
                (check["context"], check["integration_id"])
                for check in target["required_status_checks"]
            }
            for check in previous["required_status_checks"]:
                context = check["context"]
                if context == "validation" and check["integration_id"] == app:
                    context = "validation / validation"
                if (context, check["integration_id"]) not in required | (replaced or set()):
                    return False
        elif rule != new[kind]:
            return False
    return True


def _candidate(root: Path, spec: dict[str, Any], number: int) -> dict[str, Any]:
    repository = spec["repository"]
    pr: Any = operations._gh(root, f"repos/{repository}/pulls/{number}")
    require(
        pr["state"] == "open"
        and pr["base"]["ref"] == "main"
        and pr["head"]["ref"] == "development"
        and pr["head"]["repo"]["full_name"] == repository
        and pr["base"]["repo"]["full_name"] == repository,
        "Enforcement bootstrap requires this repository's open development-to-main PR.",
    )
    revision = pr["head"]["sha"]
    branches: dict[str, Any] = {
        name: operations._gh(root, f"repos/{repository}/branches/{name}")
        for name in ("main", "development")
    }
    require(
        branches["development"]["commit"]["sha"] == revision
        and branches["main"]["commit"]["sha"] == pr["base"]["sha"],
        "Enforcement candidate or main changed; review the current PR.",
    )
    runs: Any = operations._gh(
        root, f"repos/{repository}/commits/{revision}/check-runs?per_page=100"
    )
    require(
        isinstance(runs.get("check_runs"), list) and runs.get("total_count", 100) < 100,
        "Candidate checks are unavailable or truncated.",
    )
    checks = [
        check
        for check in runs["check_runs"]
        if check.get("head_sha") == revision
        and check.get("status") == "completed"
        and check.get("conclusion") == "success"
        and check.get("app", {}).get("id") == spec["checks_integration_id"]
    ]
    required = {
        check["context"]
        for rule in rulesets(spec)[0]["rules"]
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }
    require(
        required <= {check["name"] for check in checks},
        "Candidate needs passing revision-bound validation and routing.",
    )
    installed = workflow_readback(root, spec, {"main": revision})
    require(
        installed["passed"],
        "Candidate and development callers differ from the reviewed specification.",
    )
    return {
        "pr": number,
        "revision": revision,
        "base_revision": pr["base"]["sha"],
        "checks": sorted(check["id"] for check in checks if check["name"] in required),
    }


def plan(
    root: Path, specification_value: dict[str, Any], candidate_pr: int | None = None
) -> dict[str, Any]:
    spec = specification(specification_value)
    repository = _repository(root, spec)
    existing = _details(root, repository, _list(root, repository))
    desired = rulesets(spec)
    operations_plan = []
    for payload in desired:
        matches = [item for item in existing if item["name"] == payload["name"]]
        require(len(matches) <= 1, "Managed ruleset names are ambiguous.")
        observed = matches[0] if matches else None
        adopted_id = spec.get("adopt_rulesets", {}).get(payload["name"].removeprefix("wood-"))
        action = "verify" if observed else "create"
        if adopted_id is not None:
            adopted = [item for item in existing if item.get("id") == adopted_id]
            require(
                len(adopted) == 1 and (observed is None or observed["id"] == adopted_id),
                "Adoption rule is missing or conflicts with an existing managed rule.",
            )
            observed = adopted[0]
            approved_replacements = [
                item
                for item in spec.get("approved_check_replacements", [])
                if item["ruleset_id"] == adopted_id
                and item["branch"] == payload["name"].removeprefix("wood-")
            ]
            require(
                matches_policy(observed, payload)
                or all(
                    item["observed_ruleset_hash"] == digest(observed)
                    for item in approved_replacements
                ),
                "Approved check replacement targets a changed ruleset; review the current policy.",
            )
            replacements = {
                (item["context"], item["integration_id"]) for item in approved_replacements
            }
            require(
                matches_policy(observed, payload)
                or _adoptable(observed, payload, spec["checks_integration_id"], replacements),
                "Ruleset adoption would drop unreviewed checks or weaken existing policy.",
            )
            action = "verify" if matches_policy(observed, payload) else "adopt"
        if observed is not None:
            # Existing managed rules must already be this policy. Never silently
            # weaken/replace protections installed under another approval.
            require(
                action == "adopt" or matches_policy(observed, payload),
                "Existing managed ruleset differs; resolve its policy separately.",
            )
        operations_plan.append(
            {
                "action": action,
                "ruleset_id": observed["id"] if observed else None,
                "payload": payload,
            }
        )
    # Bind all observed rulesets, including unmanaged ones, to reject stale plans.
    record: dict[str, Any] = {
        "version": 3,
        "repository": repository,
        "specification": spec,
        "observed_hash": digest(existing),
        "operations": operations_plan,
        "candidate": _candidate(root, spec, candidate_pr) if candidate_pr is not None else None,
    }
    # Bind the reviewed plan to the entire contents of both remote branches,
    # including callers and root metadata. A ref advance requires a fresh review
    # even when the desired rules and current rulesets have not changed.
    revisions = {}
    for branch in ("main", "development"):
        authority: Any = operations._gh(root, f"repos/{repository}/branches/{branch}")
        revision = authority.get("commit", {}).get("sha") if isinstance(authority, dict) else None
        require(
            isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{40}", revision) is not None,
            "Protection planning requires exact main and development revisions.",
        )
        revisions[branch] = revision
    if record["candidate"] is not None:
        require(
            revisions
            == {
                "main": record["candidate"]["base_revision"],
                "development": record["candidate"]["revision"],
            },
            "Enforcement candidate changed during planning; review the current PR.",
        )
    record["branch_revisions"] = revisions
    observed_checks = {}
    for branch, revision in revisions.items():
        runs: Any = operations._gh(
            root, f"repos/{repository}/commits/{revision}/check-runs?per_page=100"
        )
        require(
            isinstance(runs, dict)
            and isinstance(runs.get("check_runs"), list)
            and type(runs.get("total_count")) is int
            and runs["total_count"] < 100
            and len(runs["check_runs"]) == runs["total_count"],
            "Protection planning requires complete available CI context observations.",
        )
        observed_checks[branch] = sorted(
            [
                {
                    "id": check["id"],
                    "name": check["name"],
                    "head_sha": check["head_sha"],
                    "status": check["status"],
                    "conclusion": check["conclusion"],
                    "app_id": check.get("app", {}).get("id"),
                }
                for check in runs["check_runs"]
            ],
            key=lambda check: check["id"],
        )
    record["observed_checks"] = observed_checks
    record["plan_hash"] = digest(record)
    return record


def _write(
    root: Path, repository: str, payload: dict[str, Any], identifier: int | None
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "PUT" if identifier is not None else "POST",
                f"repos/{repository}/rulesets"
                + (f"/{identifier}" if identifier is not None else ""),
                "--input",
                "-",
            ],
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


def _create(root: Path, repository: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _write(root, repository, payload, None)


def _update(
    root: Path, repository: str, payload: dict[str, Any], identifier: int
) -> dict[str, Any]:
    return _write(root, repository, payload, identifier)


def apply(
    root: Path, spec: dict[str, Any], plan_hash: str, approval: str, candidate_pr: int | None = None
) -> dict[str, Any]:
    require(
        isinstance(approval, str) and approval.strip() and len(approval) <= 500,
        "Live enforcement apply needs a separate explicit approval reference.",
    )
    record = plan(root, spec, candidate_pr)
    require(record["plan_hash"] == plan_hash, "Protection plan is stale or unreviewed; replan.")
    # Verify workflow installation and check compatibility before imposing required checks.
    candidate = record["candidate"]
    installed = workflow_readback(
        root, record["specification"], {"main": candidate["revision"]} if candidate else None
    )
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
                else _update(
                    root, record["repository"], operation["payload"], operation["ruleset_id"]
                )
                if operation["action"] == "adopt"
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
        "pending_main_integration": candidate is not None,
    }


def workflow_readback(
    root: Path, spec: dict[str, Any], candidate_revisions: dict[str, str] | None = None
) -> dict[str, Any]:
    expected = {
        name: text
        for name, text in workflows(spec).items()
        if name.endswith(("validate.yml", "routing.yml"))
    }
    repository = spec["repository"]
    checks = []
    revisions = {}
    for branch in ("main", "development"):
        branch_data: Any = operations._gh(root, f"repos/{repository}/branches/{branch}")
        require(isinstance(branch_data, dict), "Branch authority is unavailable.")
        revision = (candidate_revisions or {}).get(branch, branch_data["commit"]["sha"])
        revisions[branch] = revision
        try:
            listing: Any = operations._gh(
                root, f"repos/{repository}/contents/.github/workflows?ref={revision}"
            )
        except operations.OperationsError:
            # A documentation-only baseline can legitimately lack .github.
            # Confirm that absence; other API failures must remain unavailable.
            root_listing = operations._gh(root, f"repos/{repository}/contents?ref={revision}")
            if not isinstance(root_listing, list) or any(
                item.get("name") == ".github" for item in root_listing
            ):
                raise
            listing = []
        require(isinstance(listing, list), "Workflow listing is unavailable.")
        names = {item["name"] for item in listing if item.get("type") == "file"}
        checks.append({"branch": branch, "check": "workflow-set", "passed": set(expected) <= names})
        for name, text in expected.items():
            if name not in names:
                checks.append({"branch": branch, "check": name, "passed": False})
                continue
            source: Any = operations._gh(
                root, f"repos/{repository}/contents/.github/workflows/{name}?ref={revision}"
            )
            require(
                isinstance(source, dict) and source.get("encoding") == "base64",
                "Workflow source is unavailable.",
            )
            actual = base64.b64decode(source["content"], validate=False).decode()
            try:
                observed, desired = document(actual), document(text)
                matched = observed == desired
                if name.endswith("validate.yml"):
                    extra_jobs = {
                        key: job
                        for key, job in observed["jobs"].items()
                        if key not in desired["jobs"]
                    }
                    triggers = dict(events(observed))
                    triggers.pop("workflow_dispatch", None)
                    matched = (
                        triggers == events(desired)
                        and observed.get("permissions") == desired["permissions"]
                        and all(
                            observed["jobs"].get(key) == job for key, job in desired["jobs"].items()
                        )
                        and read_only({**observed, "jobs": extra_jobs})
                    )
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
                safe = reusable_only(value) or read_only(value) or main_publication(value)
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
        expected_checks = {
            (check["context"], check["integration_id"])
            for rule in payload["rules"]
            if rule["type"] == "required_status_checks"
            for check in rule["parameters"]["required_status_checks"]
        }
        checks.append(
            {
                "branch": branch,
                "check": "effective-check-compatibility",
                "passed": all(
                    (check.get("context"), check.get("integration_id")) in expected_checks
                    for rule in effective
                    if rule["type"] == "required_status_checks"
                    for check in rule["parameters"]["required_status_checks"]
                ),
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

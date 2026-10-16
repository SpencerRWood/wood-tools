"""Existing-repository adoption using BR-03 configuration, worktrees and enforcement."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from wood_project.story import session

from . import initialization as init
from . import operations
from .workflow_documents import (
    centralized_validation,
    document,
    events,
    main_publication,
    read_only,
    reusable_only,
    with_events,
)
from .workflow_files import read_json


def common(root: Path) -> Path:
    return Path(session.git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def _ancestor(root: Path, older: str, newer: str) -> bool:
    result = operations._run(["git", "merge-base", "--is-ancestor", older, newer], root)
    init.require(result.returncode in {0, 1}, "Fetch complete branch history before migration.")
    return result.returncode == 0


def assess(root: Path) -> dict[str, Any]:
    repository = operations._slug(root)
    metadata: Any = operations._gh(root, f"repos/{repository}")
    branches: Any = operations._gh(root, f"repos/{repository}/branches?per_page=100")
    pulls: Any = operations._gh(root, f"repos/{repository}/pulls?state=open&per_page=100")
    init.require(
        isinstance(branches, list)
        and len(branches) < 100
        and isinstance(pulls, list)
        and len(pulls) < 100,
        "Branch or PR inventory is unavailable/truncated.",
    )
    refs = {item["name"]: item["commit"]["sha"] for item in branches}
    claims = []
    directory = common(root) / "wood-stories"
    records = sorted(
        path for path in directory.glob("*.json") if not path.name.endswith("-handoff.json")
    )
    init.require(len(records) < 100, "Claim inventory exceeds the inspection bound.")
    for path in records:
        record = session.read_record(directory, int(path.stem))
        if record and record["state"] == "claimed":
            claims.append({key: record[key] for key in ("story_id", "owner", "branch", "worktree")})
    rules = {}
    for branch in ("main", "development"):
        if branch in refs:
            value = operations._gh(root, f"repos/{repository}/rules/branches/{branch}?per_page=100")
            init.require(isinstance(value, list) and len(value) < 100, "Rule inventory truncated.")
            rules[branch] = value
    return {
        "version": 1,
        "repository": repository,
        "archived": metadata["archived"],
        "default_branch": metadata["default_branch"],
        "remote_branches": refs,
        "open_prs": [
            {
                "number": item["number"],
                "base": item["base"]["ref"],
                "head": item["head"]["ref"],
                "head_revision": item["head"]["sha"],
            }
            for item in pulls
        ],
        "claims": claims,
        "worktrees": session.git(root, "worktree", "list", "--porcelain"),
        "dirty_files": session.git(root, "status", "--porcelain").splitlines(),
        "effective_rules": rules,
        "enforcement_verified": False,
        "authorization_granted": False,
    }


def _development_safe(files: dict[str, bytes]) -> bool:
    """Before creating development, require every automatic baseline workflow to be read-only."""
    for path, contents in files.items():
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            value = document(contents.decode())
            trigger = events(value)
            push = trigger.get("push")
            automatic = set(trigger) & {"push", "create", "workflow_run"}
            if not automatic or (
                automatic == {"push"}
                and isinstance(push, dict)
                and push.get("branches") == ["main"]
            ):
                continue
            if not (read_only(value) or centralized_validation(value)):
                return False
    return True


def configuration(
    files: dict[str, bytes], specification: dict[str, Any], contract: str | None = None
) -> dict[str, bytes]:
    """Preserve publication capabilities and reusable interfaces during existing-repo adoption."""
    spec = init.specification(specification)
    original = files.get(".github/release.toml")
    init.require(
        original is not None or contract is not None, "Supply the missing release contract."
    )
    if contract is not None:
        release = tomllib.loads(contract)
    else:
        assert original is not None
        release = tomllib.loads(original.decode())
    if original is not None and contract is not None:
        previous_contract = tomllib.loads(original.decode())
        init.require(previous_contract == release, "Do not replace an existing release contract.")
    init.require(
        release.get("version") == 1
        and isinstance(release.get("validation"), dict)
        and type(release.get("release", {}).get("semantic_release")) is bool,
        "Declare the existing repository-specific validation and publication contract.",
    )
    container = release.get("container", {})
    init.require(
        bool(spec["images"]) == bool(container.get("publish", False)),
        "Preserve configured image publication outputs.",
    )
    if spec["images"]:
        init.require(
            spec["images"][0]["image_name"]
            == container.get("image_name", spec["repository"].split("/", 1)[1].lower()),
            "Preserve the configured image identity.",
        )
    result = dict(files)
    result[".github/release.toml"] = (
        original if original is not None else init.toml(release).encode()
    )
    config = tomllib.loads(files.get("pyproject.toml", b"").decode())
    wood = config.setdefault("tool", {}).setdefault("wood", {})
    wood.setdefault("openproject", {}).update(
        {key: spec[key] for key in ("project_id", "initiative_id")}
    )
    wood["branching"] = {
        "strategy": "integration",
        "release_id": spec["release_id"],
        "integration_branch": "development",
        "scope_reference": spec["scope_reference"],
    }
    result["pyproject.toml"] = init.toml(config).encode()
    # Adoption installs integration validation/routing. Publication remains
    # repository-owned; never replace a working publisher with new governance.
    generated = {
        name: text
        for name, text in init.workflows(spec).items()
        if name.endswith(("validate.yml", "routing.yml"))
    }
    for name in ("validate.yml", "routing.yml", "release.yml"):
        path = f".github/workflows/{name}"
        if path not in files:
            continue
        value = document(files[path].decode())
        if "workflow_call" in events(value):
            init.require(
                spec.get("workflow_prefix") == "wood-", "Preserve reusable workflow paths."
            )
            # Retain reusable jobs/inputs/outputs; the new caller owns this repository's CI.
            if not reusable_only(value) and not (name == "release.yml" and main_publication(value)):
                result[path] = with_events(
                    files[path].decode(), {"workflow_call": events(value)["workflow_call"]}
                ).encode()
    for name, text in generated.items():
        path = f".github/workflows/{name}"
        if name.endswith("validate.yml") and path in files:
            previous = document(files[path].decode())
            if "workflow_call" not in events(previous):
                current = document(text)
                # Keep repository-specific browser, runtime and security jobs.
                # They are application semantics, not a branching exception.
                extra = {
                    key: job for key, job in previous["jobs"].items() if key not in current["jobs"]
                }
                init.require(
                    not extra or read_only({**previous, "jobs": extra}),
                    "Review additional validation jobs before enabling development events.",
                )
                current["jobs"].update(extra)
                if "workflow_dispatch" in events(previous):
                    current["on"]["workflow_dispatch"] = events(previous)["workflow_dispatch"]
                text = json.dumps(current, indent=2) + "\n"
        result[f".github/workflows/{name}"] = text.encode()
    # Reuse the same engines and inputs. Tag the approved main merge rather
    # than asking a no-bypass protected branch to accept a version commit.
    for path, contents in list(result.items()):
        if not (path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))):
            continue
        value = document(contents.decode())
        trigger = events(value)
        if (
            Path(path).name == "release.yml"
            and isinstance(trigger.get("push"), dict)
            and trigger["push"].get("branches") == ["main"]
            and "workflow_dispatch" in trigger
            and any(
                str(job.get("uses", "")).startswith(
                    "SpencerRWood/workflows/.github/workflows/release"
                )
                for job in value["jobs"].values()
            )
        ):
            # Scope-resolution jobs and local deployment calls must share the
            # publisher's main boundary, including manual dispatches.
            for job in value["jobs"].values():
                condition = str(job.get("if", "true"))
                if "github.ref == 'refs/heads/main'" not in condition:
                    if condition.startswith("${{") and condition.endswith("}}"):
                        condition = condition[3:-2].strip()
                    job["if"] = f"github.ref == 'refs/heads/main' && ({condition})"
        if Path(path).name == "release.yml" and set(events(value)) == {"workflow_dispatch"}:
            init.require(
                all(
                    re.fullmatch(
                        r"SpencerRWood/workflows/\.github/workflows/release(?:-container|-containers)?"
                        r"\.yml@(?:[a-f0-9]{40}|v[0-9]+)",
                        str(job.get("uses", "")),
                    )
                    for job in value["jobs"].values()
                ),
                "Review manual deployment/custom publication before enabling main integration.",
            )
            value["on"] = {**events(value), "push": {"branches": ["main"]}}
            for job in value["jobs"].values():
                condition = job.get("if", "true")
                init.require(
                    "${{" not in str(condition), "Review the existing publication condition."
                )
                job["if"] = f"github.ref == 'refs/heads/main' && ({condition})"
        if not main_publication(value):
            continue
        changed = False
        publishes = False
        for job in value["jobs"].values():
            match = re.fullmatch(
                r"(SpencerRWood/workflows/\.github/workflows/"
                r"(?:release|release-container|release-containers|deploy-ansible|promote-container-to-dev)"
                r"\.yml)@(?:[a-f0-9]{40}|v[0-9]+)",
                str(job.get("uses", "")),
            )
            if match:
                job["uses"] = match[1] + "@" + spec["workflow_revision"]
                job.setdefault("with", {})["workflow_revision"] = spec["workflow_revision"]
                if "/release-container" in match[1]:
                    job.setdefault("permissions", {}).update(
                        {
                            "contents": "write",
                            "packages": "write",
                            "id-token": "write",
                            "attestations": "write",
                        }
                    )
                changed = True
                publishes = publishes or "/release" in match[1]
        if changed:
            result[path] = (json.dumps(value, indent=2) + "\n").encode()
        if publishes and release["release"]["semantic_release"]:
            release["release"]["tag_merged_commit"] = True
            result[".github/release.toml"] = init.toml(release).encode()
    for path, contents in result.items():
        if (
            path.startswith(".github/workflows/")
            and path.endswith((".yml", ".yaml"))
            and Path(path).name not in generated
        ):
            value = document(contents.decode())
            init.require(
                reusable_only(value) or read_only(value) or main_publication(value),
                f"Review additional automatic workflow before migration: {path}",
            )
    guidance = (
        b"## Development lifecycle\n\nUse root [tool.wood.branching]: "
        b"feature branches and Story PRs "
        b"start from and target development. Development validates without publication/deployment. "
        b"Main requires a separately approved release PR and pinned release workflow. Emergency "
        b"hotfixes require incident/approval references and reconciliation into development.\n\n"
    )
    previous_guidance = result.get("AGENTS.md", b"")
    if not previous_guidance.startswith(guidance):
        result["AGENTS.md"] = (guidance + previous_guidance).rstrip() + b"\n"
    return {path: data for path, data in result.items() if files.get(path) != data}


def plan(
    root: Path,
    baseline: str,
    approval: str,
    destination: Path,
    branch: str,
    spec_value: dict[str, Any],
    contract: str | None = None,
) -> dict[str, Any]:
    spec = init.specification(spec_value)
    init.require(approval.strip() and len(approval) <= 500, "Record the approved migration scope.")
    init.require(
        re.fullmatch(r"(?:feature|feat|fix)/[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
        and ".." not in branch
        and not branch.endswith("/"),
        "Use an isolated working branch.",
    )
    observed = assess(root)
    init.require(
        not observed["archived"] and observed["default_branch"] == "main",
        "Active main repository required.",
    )
    init.require(
        observed["repository"].casefold() == spec["repository"].casefold(), "Repository differs."
    )
    refs = observed["remote_branches"]
    init.require(
        "main" in refs and _ancestor(root, refs["main"], baseline),
        "Reconcile fetched main into the candidate.",
    )
    if "development" in refs:
        init.require(
            _ancestor(root, refs["development"], baseline), "Preserve existing development history."
        )
    else:
        init.require(
            baseline == refs["main"], "Initialize development at the approved current main."
        )
    init.require(
        not destination.exists() and not destination.is_symlink(), "Preserve occupied destinations."
    )
    init.require(
        operations._run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], root
        ).returncode
        == 1,
        "Preserve existing working branches.",
    )
    files = init.baseline_files(root, baseline)
    old = (
        tomllib.loads(files.get("pyproject.toml", b"").decode())
        .get("tool", {})
        .get("wood", {})
        .get("branching", {})
    )
    release_refs = {
        name: revision for name, revision in refs.items() if name.startswith("release/")
    }
    for revision in release_refs.values():
        init.require(
            _ancestor(root, revision, baseline),
            "Reconcile existing release branches into the approved baseline before migration.",
        )
    changes = configuration(files, spec, contract)
    initial = "development" not in refs
    init.require(
        not initial or _development_safe(files), "Baseline development event may publish/deploy."
    )
    record = {
        "version": 1,
        "kind": "wood-existing-repository-migration",
        "source": str(root.resolve()),
        "baseline": baseline,
        "approval_reference": approval,
        "destination": str(destination.resolve()),
        "branch": branch,
        "specification": spec,
        "observed": observed,
        "original_branching": old,
        "reconciled_release_branches": release_refs,
        "initialize_development": initial,
        "contents": {path: data.decode() for path, data in changes.items()},
        "before": {
            path: hashlib.sha256(files[path]).hexdigest() if path in files else None
            for path in changes
        },
        "enforcement_verified": False,
        "publication_authorized": False,
    }
    record["plan_hash"] = init.digest(record)
    return record


def apply(
    root: Path, record: dict[str, Any], expected_hash: str, initialize: bool
) -> dict[str, Any]:
    init.require(
        record.get("kind") == "wood-existing-repository-migration"
        and record.get("version") == 1
        and record.get("plan_hash") == expected_hash
        and init.digest({key: value for key, value in record.items() if key != "plan_hash"})
        == expected_hash,
        "Migration plan changed or is unreviewed.",
    )
    init.require(Path(record["source"]).resolve() == root.resolve(), "Migration source differs.")
    directory = common(root) / "wood-migrations"
    with session.locked(directory, apply=True):
        journal = directory / f"{expected_hash}.json"
        target = Path(record["destination"])
        if journal.exists():
            progress = read_json(journal)
            init.require(progress.get("plan_hash") == expected_hash, "Migration journal differs.")
        else:
            init.require(
                assess(root) == record["observed"], "Migration observations changed; replan."
            )
            init.require(
                not target.exists() and not target.is_symlink(), "Preserve occupied destination."
            )
            progress = {
                "plan_hash": expected_hash,
                "state": "preparing",
                "destination": str(target),
            }
            session.write_atomic(journal, progress)
        current = assess(root)
        init.require(
            current["claims"] == record["observed"]["claims"]
            and current["open_prs"] == record["observed"]["open_prs"],
            "Active claims or PRs changed; preserve them and replan.",
        )
        refs = current["remote_branches"]
        prior = record["observed"]["remote_branches"]
        expected_refs = {**prior}
        if record["initialize_development"] and "development" in refs:
            init.require(
                refs["development"] == record["baseline"], "Development advanced; preserve it."
            )
            expected_refs["development"] = record["baseline"]
        init.require(refs == expected_refs, "Remote branches changed; preserve them and replan.")
        if record["initialize_development"] and "development" not in refs:
            init.require(initialize, "Explicitly select safe development initialization.")
            # Compare-and-create: a concurrent new branch is never overwritten.
            session.git(
                root,
                "push",
                "--force-with-lease=refs/heads/development:",
                "origin",
                f"{record['baseline']}:refs/heads/development",
            )
            progress["state"] = "development-created"
            session.write_atomic(journal, progress)
        if target.exists():
            init.require(
                progress["state"] in {"worktree-created", "prepared-validation-required"},
                "Unattested destination exists; inspect it without overwriting.",
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            session.git(
                root, "worktree", "add", "-b", record["branch"], str(target), record["baseline"]
            )
            progress["state"] = "worktree-created"
            session.write_atomic(journal, progress)
        init.require(not target.is_symlink() and target.is_dir(), "Preserve occupied destination.")
        init.require(
            common(target) == common(root)
            and session.git(target, "branch", "--show-current") == record["branch"]
            and session.git(target, "rev-parse", "HEAD") == record["baseline"],
            "Destination branch advanced or differs; do not overwrite it.",
        )
        status = operations._run(["git", "status", "--porcelain", "--untracked-files=all"], target)
        init.require(status.returncode == 0, "Destination status unavailable.")
        changed = status.stdout.splitlines()
        init.require(
            all(line[3:] in record["contents"] for line in changed),
            "Preserve unrelated destination changes.",
        )
        for name, text in record["contents"].items():
            init.relative(name)
            path = target / name
            init.require(
                not any(parent.is_symlink() for parent in [path, *path.parents]),
                "Unsafe destination path.",
            )
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            desired = text.encode()
            init.require(
                current_hash in {record["before"][name], hashlib.sha256(desired).hexdigest()},
                "Preserve changed migration contents; inspect the journal.",
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(desired)
        progress["state"] = "prepared-validation-required"
        session.write_atomic(journal, progress)
        return {
            **progress,
            "record_file": str(journal),
            "branch": record["branch"],
            "enforcement_verified": False,
            "publication_authorized": False,
            "next_action": "Validate and PR into development; plan separate live enforcement.",
        }

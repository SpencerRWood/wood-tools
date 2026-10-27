"""One local, baseline-bound initializer; remote enforcement is a separate operation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from wood_project.story import session

from .workflow_documents import document as workflow_document
from .workflow_documents import main_publication, reusable_only
from .workflow_files import WorkflowFilesError, read_json

SHA = r"[a-f0-9]{40}"
SLUG = r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*"
MANIFEST = ".wood-initialization.json"


def require(value: object, message: str) -> None:
    if not value:
        raise WorkflowFilesError("INITIALIZATION_INVALID", message)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def relative(value: object) -> str:
    require(isinstance(value, str), "Repository paths must be strings.")
    text = str(value)
    require(
        bool(re.fullmatch(r"[A-Za-z0-9._/-]+", text))
        and not Path(text).is_absolute()
        and not text.startswith("-")
        and ".." not in Path(text).parts,
        "Paths must be safe and repository relative.",
    )
    return text


def specification(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "repository",
        "project_id",
        "initiative_id",
        "release_id",
        "scope_reference",
        "workflow_revision",
        "wood_revision",
        "checks_integration_id",
        "author_name",
        "author_email",
    }
    require(
        required
        <= value.keys()
        <= required
        | {
            "images",
            "deployment",
            "required_approvals",
            "workflow_prefix",
            "adopt_rulesets",
            "validation_checks",
            "approved_check_replacements",
        },
        "Supply the supported initialization specification fields.",
    )
    require(re.fullmatch(SLUG, value["repository"]), "Use a canonical owner/repository.")
    for key in ("project_id", "initiative_id", "release_id", "checks_integration_id"):
        require(type(value[key]) is int and value[key] > 0, f"{key} must be a positive ID.")
    for key in ("workflow_revision", "wood_revision"):
        require(
            isinstance(value[key], str) and re.fullmatch(SHA, value[key]),
            f"{key} must be an approved immutable source SHA.",
        )
    for key in (
        "scope_reference",
        "author_name",
        "author_email",
    ):
        require(
            isinstance(value[key], str)
            and value[key].strip()
            and len(value[key]) <= 500
            and not any(c in value[key] for c in "\r\n\x00"),
            f"{key} must be a nonblank bounded single line.",
        )
    approvals = value.get("required_approvals", 0)
    require(type(approvals) is int and approvals == 0, "Canonical review count must be zero.")
    checks = value.get("validation_checks", [])
    require(
        isinstance(checks, list)
        and len(checks) <= 20
        and all(
            isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 /_.-]{0,99}", name)
            for name in checks
        )
        and len(set(checks)) == len(checks),
        "Additional validation checks must be unique bounded check names.",
    )
    require(value.get("workflow_prefix", "") in {"", "wood-"}, "Unsupported caller file prefix.")
    adopted = value.get("adopt_rulesets", {})
    require(
        isinstance(adopted, dict)
        and set(adopted) <= {"main", "development"}
        and all(type(identifier) is int and identifier > 0 for identifier in adopted.values()),
        "Ruleset adoption must identify explicit main/development repository rule IDs.",
    )
    replacements = value.get("approved_check_replacements", [])
    require(
        isinstance(replacements, list) and len(replacements) <= 20,
        "Bound approved check replacements.",
    )
    for replacement in replacements:
        require(
            isinstance(replacement, dict)
            and set(replacement)
            == {
                "branch",
                "ruleset_id",
                "context",
                "integration_id",
                "replacement",
                "approval_reference",
                "observed_ruleset_hash",
            },
            "A check replacement needs its exact branch, rule, check, replacement and approval.",
        )
        require(
            isinstance(replacement["branch"], str)
            and replacement["branch"] in {"main", "development"}
            and type(replacement["ruleset_id"]) is int
            and adopted.get(replacement["branch"]) == replacement["ruleset_id"]
            and type(replacement["integration_id"]) is int
            and replacement["integration_id"] == value["checks_integration_id"]
            and isinstance(replacement["context"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 /_.-]{0,99}", replacement["context"])
            and replacement["replacement"] in checks
            and replacement["context"] != replacement["replacement"]
            and isinstance(replacement["observed_ruleset_hash"], str)
            and re.fullmatch(r"[0-9a-f]{64}", replacement["observed_ruleset_hash"])
            and isinstance(replacement["approval_reference"], str)
            and replacement["approval_reference"].strip()
            and len(replacement["approval_reference"]) <= 500
            and not any(c in replacement["approval_reference"] for c in "\r\n\x00"),
            "Check replacements must bind an adopted rule and required validation "
            "with explicit approval.",
        )
    images = value.get("images", [])
    require(isinstance(images, list) and len(images) <= 16, "Declare at most sixteen images.")
    names: set[str] = set()
    for image in images:
        require(
            isinstance(image, dict) and set(image) == {"image_name", "dockerfile", "context"},
            "Images use existing image_name, dockerfile and context inputs.",
        )
        name = image["image_name"]
        require(
            isinstance(name, str)
            and re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", name)
            and name not in names
            and ".." not in name.split("/"),
            "Invalid/duplicate image.",
        )
        names.add(name)
        relative(image["dockerfile"])
        relative(image["context"])
    deployment = value.get("deployment")
    if deployment is not None:
        require(
            isinstance(deployment, dict)
            and set(deployment) == {"inputs", "approval_reference", "automatic"},
            "Deployment requires explicit inputs, standing approval and automatic selection.",
        )
        require(
            isinstance(deployment["approval_reference"], str)
            and deployment["approval_reference"].strip()
            and type(deployment["automatic"]) is bool,
            "Deployment authority is required.",
        )
        inputs = deployment["inputs"]
        mandatory = {
            "inventory_path",
            "playbook_path",
            "environment_name",
            "concurrency_group",
            "health_check_command",
        }
        optional = {
            "privileged_rollback_command",
            "deployment_state_file",
            "ansible_config_path",
            "runner",
            "ansible_extra_vars",
            "deployment_working_directory",
            "rollback_ref",
            "secrets_file",
            "validation_command",
            "check_command",
            "privileged_deploy_command",
            "privileged_health_check_command",
            "privileged_check_command",
        }
        require(
            isinstance(inputs, dict) and mandatory <= inputs.keys() <= mandatory | optional,
            "Use the supported existing deploy-ansible inputs.",
        )
        require(
            all(isinstance(v, str) and v.strip() and "\n" not in v for v in inputs.values()),
            "Deployment inputs must be explicit strings.",
        )
        for key in ("inventory_path", "playbook_path"):
            relative(inputs[key])
    return {**value, "images": images, "required_approvals": approvals}


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{json.dumps(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
        )
    raise WorkflowFilesError("INITIALIZATION_INVALID", "Unsupported source TOML value.")


def toml(document: dict[str, Any]) -> str:
    lines: list[str] = []

    def table(data: dict[str, Any], path: list[str]) -> None:
        if path:
            lines.extend(["", "[" + ".".join(json.dumps(k) for k in path) + "]"])
        for key, value in data.items():
            if not isinstance(value, dict):
                lines.append(f"{json.dumps(key)} = {_toml_value(value)}")
        for key, value in data.items():
            if isinstance(value, dict):
                table(value, [*path, key])

    table(document, [])
    return "\n".join(lines).strip() + "\n"


def publication_mode(contract: dict[str, Any], files: dict[str, bytes]) -> None:
    """Preserve Python/static-version preparation; main authority is release_integration."""
    directory = relative(contract.get("project", {}).get("working_directory", "."))
    path = str(Path(directory) / "pyproject.toml")
    document = tomllib.loads(files.get(path, b"").decode())
    semantic = document.get("tool", {}).get("semantic_release", {})
    static = (
        "version" in document.get("project", {})
        or bool(semantic.get("version_toml"))
        or bool(semantic.get("version_variables"))
        or contract.get("build", {}).get("python_package", False)
    )
    contract["release"].setdefault("tag_merged_commit", not static)


def workflows(spec: dict[str, Any]) -> dict[str, str]:
    """JSON is a YAML subset; reuse shared workflows instead of copying their logic."""
    revision = spec["workflow_revision"]

    def uses(name: str) -> str:
        return f"SpencerRWood/workflows/.github/workflows/{name}.yml@{revision}"

    dbt = {
        key: "${{ secrets." + key.upper() + " }}"
        for key in ("dbt_host", "dbt_user", "dbt_password", "dbt_port", "dbt_dbname", "dbt_schema")
    }
    validation = {
        "name": "Validate",
        "on": {
            "pull_request": {"branches": ["development", "main"]},
            "push": {"branches": ["development"]},
        },
        "permissions": {"contents": "read"},
        "jobs": {
            "validation": {
                "uses": uses("validate"),
                "with": {"workflow_revision": revision},
                "secrets": dbt,
            }
        },
    }
    route: dict[str, Any] = {
        "name": "PR routing",
        "on": {"pull_request": {"branches": ["development", "main"]}},
        "permissions": {"contents": "read"},
        "jobs": {
            "routing": {
                "name": "pr-routing",
                "runs-on": "ubuntu-latest",
                "outputs": {
                    "incident": "${{ steps.route.outputs.incident }}",
                    "approval": "${{ steps.route.outputs.approval }}",
                },
                "steps": [
                    {
                        "uses": "actions/checkout@v6",
                        "with": {
                            "ref": "${{ github.event.pull_request.head.sha }}",
                            "fetch-depth": 0,
                            "persist-credentials": False,
                        },
                    },
                    {"uses": "actions/setup-python@v6", "with": {"python-version": "3.14"}},
                    {"uses": "astral-sh/setup-uv@v9.0.0"},
                    {
                        "name": "Install approved Wood tools",
                        "env": {
                            "WOOD_REVISION": spec["wood_revision"],
                            "GH_TOKEN": "${{ github.token }}",
                        },
                        "run": "set -euo pipefail\ngh auth setup-git\n"
                        'git init "$RUNNER_TEMP/wood-tools"\n'
                        'git -C "$RUNNER_TEMP/wood-tools" remote add origin '
                        "https://github.com/SpencerRWood/wood-tools.git\n"
                        'git -C "$RUNNER_TEMP/wood-tools" fetch --depth 1 origin "$WOOD_REVISION"\n'
                        'git -C "$RUNNER_TEMP/wood-tools" checkout --detach FETCH_HEAD\n'
                        'test "$(git -C "$RUNNER_TEMP/wood-tools" rev-parse HEAD)" '
                        '= "$WOOD_REVISION"\n'
                        'uv sync --frozen --directory "$RUNNER_TEMP/wood-tools"\n'
                        'echo "$RUNNER_TEMP/wood-tools/.venv/bin" >> "$GITHUB_PATH"',
                    },
                    {
                        "id": "route",
                        "name": "Verify PR routing and ancestry",
                        "run": 'wood repo route --event-file "$GITHUB_EVENT_PATH" '
                        '--github-output "$GITHUB_OUTPUT" --json',
                    },
                ],
            },
        },
    }
    # Publication is inherited from the reviewed repository/template baseline.
    # Branch migration owns validation and routing, never a second publisher.
    documents = {"validate.yml": validation, "routing.yml": route}
    prefix = spec.get("workflow_prefix", "")
    return {
        prefix + name: json.dumps(document, indent=2) + "\n" for name, document in documents.items()
    }


def baseline_files(source: Path, baseline: str) -> dict[str, bytes]:
    import subprocess

    require(re.fullmatch(SHA, baseline), "Baseline must be a full approved commit SHA.")
    require(
        session.git(source, "rev-parse", f"{baseline}^{{commit}}") == baseline,
        "Baseline commit is unavailable.",
    )
    try:
        result = subprocess.run(
            ["git", "archive", "--format=tar", baseline],
            cwd=source,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkflowFilesError(
            "BASELINE_UNAVAILABLE", "Cannot read the baseline archive.", "unavailable"
        ) from exc
    require(result.returncode == 0, "Cannot archive the approved baseline.")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        for member in archive:
            if member.isdir():
                continue
            require(
                member.isfile()
                and not Path(member.name).is_absolute()
                and ".." not in Path(member.name).parts,
                "Baseline must contain ordinary files, without links or submodules.",
            )
            require(
                not any(part == ".git" for part in Path(member.name).parts)
                and not (
                    Path(member.name).name.startswith(".env")
                    and Path(member.name).name not in {".env.example", ".envrc"}
                ),
                "Baseline contains Git metadata or runtime environment files.",
            )
            stream = archive.extractfile(member)
            require(stream is not None, "Baseline member is unreadable.")
            if stream is not None:
                files[member.name] = stream.read()
    require(files, "Baseline must contain files.")
    return files


def plan(
    source: Path, baseline: str, approval: str, destination: Path, spec: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, bytes]]:
    spec = specification(spec)
    require(
        approval.strip() and len(approval) <= 500 and "\n" not in approval,
        "An explicit baseline approval reference is required.",
    )
    files = baseline_files(source, baseline)
    modes = {}
    for entry in session.git(source, "ls-tree", "-r", baseline).splitlines():
        metadata, name = entry.split("\t", 1)
        mode, kind, _sha = metadata.split()
        require(
            kind == "blob" and mode in {"100644", "100755"},
            "Baseline links and submodules are unsupported.",
        )
        modes[name] = mode
    require(MANIFEST not in files, "Do not reinitialize an existing managed repository.")
    require(
        ".github/release.toml" in files, "Approved baseline needs the existing release contract."
    )
    contract = tomllib.loads(files[".github/release.toml"].decode())
    require(
        contract.get("version") == 1
        and contract.get("release", {}).get("semantic_release") is True,
        "An existing semantic-release capability contract is required.",
    )
    directory = relative(contract.get("project", {}).get("working_directory", "."))
    project = str(Path(directory) / "pyproject.toml")
    require(project in files, "The declared Python working directory needs pyproject.toml.")
    for image in spec["images"]:
        require(
            image["dockerfile"] in files, "Each declared Dockerfile must exist in the baseline."
        )
        require(
            image["context"] == "."
            or any(f.startswith(image["context"].rstrip("/") + "/") for f in files),
            "Each declared image context must exist in the baseline.",
        )
    document = tomllib.loads(files.get("pyproject.toml", b"").decode())
    wood = document.setdefault("tool", {}).setdefault("wood", {})
    wood["branching"] = {
        "strategy": "integration",
        "release_id": spec["release_id"],
        "integration_branch": "development",
        "scope_reference": spec["scope_reference"],
    }
    wood["openproject"] = {k: spec[k] for k in ("project_id", "initiative_id", "release_id")}
    files["pyproject.toml"] = toml(document).encode()
    # Consumer image/deployment identities are explicit inputs, never copied from a template.
    contract.pop("container", None)
    contract.pop("delivery", None)
    # The pinned release_integration publisher keeps the tag on approved main.
    # Static-version Python preparation still needs the configured version fields.
    publication_mode(contract, files)
    if spec["images"]:
        image = spec["images"][0]
        contract["container"] = {"publish": True, **image}
    files[".github/release.toml"] = toml(contract).encode()
    generated_workflows = {
        f".github/workflows/{name}": text.encode() for name, text in workflows(spec).items()
    }
    # Review retained workflows; generated validation/routing replace the source
    # files completely, so source-only jobs at those exact paths cannot survive.
    for path, contents in files.items():
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            if path in generated_workflows:
                continue
            value = workflow_document(contents.decode())
            require(
                reusable_only(value) or main_publication(value),
                f"Review baseline workflow before initialization: {path}",
            )
    files.update(generated_workflows)
    files["AGENTS.md"] = (
        (
            b"# Repository guidance\n\nFollow root [tool.wood.branching]: working branches start "
            b"from development and Story PRs target development. Protect main and development "
            b"through the separately reviewed Wood enforcement plan and verify readback.\n\n"
            b"Reviewed release PRs target main after accumulated validation. "
            b"Development never publishes or deploys. Hotfixes need incident and approval "
            b"references and verified main back-integration before release review.\n\n"
            b"Retain application guidance and use wood repo validate --json.\n"
        )
        + b"\n## Approved baseline guidance\n\n"
        + files.get("AGENTS.md", b"")
    )
    content = {p: hashlib.sha256(c).hexdigest() for p, c in sorted(files.items())}
    record = {
        "version": 1,
        "kind": "wood-repository-initialization",
        "source": str(source.resolve()),
        "source_revision": baseline,
        "baseline_approval": approval,
        "destination": str(destination.absolute()),
        "specification": spec,
        "files": content,
        "modes": {p: modes.get(p, "100644") for p in files},
    }
    record["plan_hash"] = digest(record)
    files[MANIFEST] = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode()
    return record, files


def apply(record: dict[str, Any], files: dict[str, bytes], expected_hash: str) -> dict[str, Any]:
    require(record["plan_hash"] == expected_hash, "Initialization plan is stale or unreviewed.")
    require(
        digest({k: v for k, v in record.items() if k != "plan_hash"}) == expected_hash,
        "Initialization record changed after review.",
    )
    require(
        {p: hashlib.sha256(c).hexdigest() for p, c in files.items() if p != MANIFEST}
        == record["files"]
        and json.loads(files[MANIFEST]) == record,
        "Initialization files changed after review.",
    )
    destination = Path(record["destination"])
    if destination.exists():
        require(destination.is_dir() and not destination.is_symlink(), "Destination is occupied.")
        manifest = destination / MANIFEST
        require(
            manifest.is_file() and read_json(manifest) == record,
            "Preserve the existing destination.",
        )
        require(
            not session.git(destination, "status", "--porcelain"), "Preserve modified destinations."
        )
        main = session.git(destination, "rev-parse", "refs/heads/main")
        require(
            main == session.git(destination, "rev-parse", "refs/heads/development")
            and main == session.git(destination, "rev-parse", "HEAD"),
            "Preserve branches that advanced after initialization.",
        )
        for path, content in files.items():
            require(
                (destination / path).is_file() and (destination / path).read_bytes() == content,
                "Initialized contents changed; do not overwrite them.",
            )
        return {
            "plan_hash": expected_hash,
            "baseline_revision": main,
            "reused": True,
            "enforcement_verified": False,
        }
    require(destination.parent.is_dir(), "Create the destination parent before initialization.")
    staging = Path(tempfile.mkdtemp(prefix=".wood-init-", dir=destination.parent))
    try:
        for name, content in files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o755 if record["modes"].get(name) == "100755" else 0o644)
        session.git(staging, "init", "-q", "-b", "main")
        session.git(staging, "add", "--all", "--force")
        spec = record["specification"]
        session.git(
            staging,
            "-c",
            f"user.name={spec['author_name']}",
            "-c",
            f"user.email={spec['author_email']}",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-q",
            "-m",
            "chore: approved repository baseline",
            "-m",
            f"Source: {record['source_revision']}\nApproval: {record['baseline_approval']}\n"
            f"Initialization-Plan: {expected_hash}",
        )
        revision = session.git(staging, "rev-parse", "HEAD")
        session.git(staging, "branch", "development", revision)
        session.git(staging, "switch", "-q", "development")
        session.git(
            staging, "remote", "add", "origin", f"https://github.com/{spec['repository']}.git"
        )
        # Reserve exclusively so even an empty destination created by another
        # initializer/operator during staging is preserved. Rename replaces only
        # our own empty reservation; a failed rename removes no nonempty directory.
        destination.mkdir()
        try:
            os.rename(staging, destination)
        except OSError:
            try:
                destination.rmdir()
            except OSError:
                pass
            raise
        return {
            "plan_hash": expected_hash,
            "baseline_revision": revision,
            "reused": False,
            "enforcement_verified": False,
            "remote_created": False,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)

#!/usr/bin/env python3
# ruff: noqa: I001
"""Initialize a minimal deterministic project scaffold."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


DESCRIPTION = (
    "Create a minimal deterministic baseline scaffold for your project. "
    "Use exactly one mode: --dry-run or --apply."
)


README_TEMPLATE = """# {project_name}

## Quick Setup

1. Create and activate a Python 3.11+ virtual environment.
2. Install development tools you plan to use (for example, pytest, ruff, and pre-commit).
3. Copy `.env.example` to `.env` and set reference-only values (never raw secrets).

## Initialize Scaffold

Run from repository root:

```bash
python scripts/init_project.py --dry-run
python scripts/init_project.py --apply
python scripts/init_project.py --apply --force
```

## Local Workflow

### Resolve local environment references

1. Put reference-only values in `.env`.
2. Unlock Vaultwarden/Bitwarden CLI:
   `export BW_SESSION="$(bw unlock --raw)"`
3. Resolve refs:
   `python scripts/resolve_env_refs.py --apply`
   Optional interactive unlock on macOS:
   `python scripts/resolve_env_refs.py --apply --prompt-unlock`
4. Confirm `.env.resolved` exists but is ignored by Git.

### Find the next OpenProject Story

1. Ensure `.env.resolved` exists.
2. Run:
   `python scripts/openproject_next_story.py`
3. Create or checkout the suggested branch.
4. Give Codex the Story packet for that OpenProject ID.

Notes:
- `.env.resolved` may contain secrets and must never be committed.
- OpenProject next-story lookup is read-only.

## Run Tests

```bash
pytest
```

## Run Lint and Format Checks

```bash
ruff check .
ruff format --check .
```
"""


PYPROJECT_TEMPLATE = """[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_name}"
version = "0.1.0"
description = "Deterministic Python CLI tooling for project delivery workflows."
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = [
    "pre-commit>=4.0.0",
    "pytest>=8.0.0",
    "ruff>=0.11.0",
]

[tool.setuptools]
package-dir = {{"" = "src"}}

[tool.setuptools.packages.find]
where = ["src"]
include = ["*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
markers = [
    "unit: Unit tests",
    "integration: Integration tests",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = []

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "auto"
"""


GITIGNORE_TEMPLATE = """# Python artifacts
__pycache__/
*.py[cod]
*.egg-info/
.Python

# Virtual environments
.venv/
venv/
env/

# Test and lint caches
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
.coverage.*
htmlcov/

# Editor files
.vscode/
.idea/
*.swp
*.swo

# OS files
.DS_Store
Thumbs.db

# Local environment files
.env
.env.*
!.env.example
.env.resolved

# Local/generated project files
docs/*
AGENTS.md
"""


ENV_EXAMPLE_TEMPLATE = """WOOD_CONFIG_PROFILE=local
WOOD_PROJECT_ROOT_BASE=
WOOD_ARTIFACT_ROOT_BASE=
WOOD_SCHEDULER_ROOT=
OPENPROJECT_URL=
OPENPROJECT_PROJECT_ID=
OPENPROJECT_API_TOKEN_REF=vaultwarden://wood/openproject/prod/api-token
NTFY_URL=
NTFY_TOKEN_REF=vaultwarden://wood/ntfy/prod/token
VAULTWARDEN_URL=
"""


PRE_COMMIT_TEMPLATE = """repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: no-commit-to-branch
        args: ["--branch", "main"]
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-json
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.13
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format
  - repo: local
    hooks:
      - id: block-resolved-env
        name: block staged .env.resolved
        entry: >-
          bash -c 'git diff --cached --name-only | grep -qx "\\.env\\.resolved" &&
          { echo "Do not commit .env.resolved"; exit 1; } || exit 0'
        language: system
        pass_filenames: false

      - id: block-raw-env-secrets
        name: block raw tokens in staged .env
        entry: >-
          bash -c 'git diff --cached --name-only | grep -qx "\\.env" || exit 0;
          git show :.env | grep -Eq "^(OPENPROJECT_API_TOKEN|NTFY_TOKEN)=" &&
          { echo "Use *_REF entries in .env, not raw tokens"; exit 1; } || exit
          0'
        language: system
        pass_filenames: false
"""


AGENTS_TEMPLATE = """# AGENTS.md

## Operating Principles

- Inspect before editing.
- Make the smallest coherent change that satisfies the assigned Story.
- Do not implement unrelated Stories.
- Prefer deterministic scripts over ad hoc manual changes.
- Preserve explicit approval gates for mutating operations.
- Do not store secrets in source code, config files, logs, generated docs, test
  fixtures, or output files.
- Do not mutate OpenProject, Vaultwarden, ntfy, GitHub, or other external
  systems unless explicitly instructed.
- Run relevant tests and lint checks before claiming completion.
- If requirements are ambiguous, stop and ask for clarification.

## OpenProject Next Story Workflow

- To find the next implementation Story, run:

  python scripts/openproject_next_story.py

- This command requires `.env.resolved`.
- If `.env.resolved` is missing, resolve local environment references first:

  python scripts/resolve_env_refs.py --apply

- The next-story command is read-only and must not mutate OpenProject.
- If no OpenProject ID is found, stop and ask the user for one.
- Do not create a Story, branch, or placeholder ID automatically.
- Do not invent OpenProject IDs.

## OpenProject ID Requirement

- Every implementation session must be tied to an existing OpenProject Story work package.
- Confirm the OpenProject ID before making code changes.
- Branch names must include the existing OpenProject ID:

  feature/op-<openproject-work-package-id>-<slug>

- If no OpenProject ID is provided, stop and ask the user for one.
- Do not invent OpenProject IDs.
- Do not create placeholder OpenProject IDs.

## Branch Workflow

- One OpenProject Story per feature branch.
- Do not commit directly to `main`.
- Do not switch branches if there are uncommitted changes unless explicitly instructed.
- Do not perform destructive Git operations unless explicitly instructed.

## Story Execution Workflow

For each assigned Story:

1. Read the Story packet.
2. Confirm OpenProject ID, branch name, goal, acceptance criteria, dependencies, and non-goals.
3. Identify minimal files to change.
4. Implement only the requested Story.
5. Run relevant checks.
6. Review the diff.
7. Provide the structured completion report.

## Structured Output Requirement

At the end of each implementation session, provide this report:

### Story

- OpenProject ID:
- External Story ID:
- Branch:
- Target Area:

### Summary

- Briefly describe what changed.

### Files Changed

- List changed files and the purpose of each change.

### Commands Run

- List commands run, including tests and lint checks.
- Include pass/fail result for each command.

### Acceptance Criteria Status

- Mark each acceptance criterion as met, not met, or partially met.

### Risks / Notes

- Mention risks, assumptions, skipped checks, or follow-up work.

### Recommended Commit Message

- Provide one concise commit message aligned to Story scope.
- Use `type(scope): short summary for op-<openproject-work-package-id>`.

### Next Suggested Action

- State whether the Story appears ready for review, needs more work, or is blocked.

## Safety Rules

- Do not print secrets.
- Use secret references instead of secret values.
- Do not mutate external systems unless explicitly instructed.
"""


RESOLVE_ENV_REFS_TEMPLATE = """#!/usr/bin/env python3
\"\"\"Resolve Vaultwarden-backed *_REF values from .env into .env.resolved.\"\"\"

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvLine:
    raw: str
    key: str | None = None
    value: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing output.",
    )
    mode.add_argument("--apply", action="store_true", help="Write .env.resolved output file.")
    parser.add_argument("--input", default=".env", help="Path to input .env file.")
    parser.add_argument("--output", default=".env.resolved", help="Path to output resolved file.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output if it already exists.",
    )
    args = parser.parse_args()
    if args.force and not args.apply:
        parser.error("--force can only be used with --apply")
    return args


def parse_env_lines(content: str) -> list[EnvLine]:
    lines: list[EnvLine] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            lines.append(EnvLine(raw=raw_line))
            continue
        key, value = raw_line.split("=", 1)
        lines.append(EnvLine(raw=raw_line, key=key.strip(), value=value))
    return lines


def require_bw_session() -> None:
    if os.environ.get("BW_SESSION"):
        return
    raise RuntimeError(
        "BW_SESSION is not set. Unlock Bitwarden/Vaultwarden first: "
        "export BW_SESSION=\\\"$(bw unlock --raw)\\\""
    )


def run_bw_json(args: list[str]) -> object:
    try:
        proc = subprocess.run(["bw", *args], check=True, capture_output=True, text=True)
    except FileNotFoundError as err:
        raise RuntimeError("'bw' CLI is required but was not found on PATH.") from err
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.strip()
        raise RuntimeError(f"bw command failed: {' '.join(args)} ({stderr})") from err
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"bw returned invalid JSON for: {' '.join(args)}") from err


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def extract_custom_field(item: dict, candidates: list[str]) -> str | None:
    fields = item.get("fields") or []
    normalized = {normalize_token(name): name for name in candidates}
    for field in fields:
        name = str(field.get("name") or "")
        if normalize_token(name) in normalized:
            value = field.get("value")
            if isinstance(value, str) and value:
                return value
    return None


def resolve_vault_ref(key: str, reference: str) -> str:
    if not reference.startswith("vaultwarden://"):
        raise RuntimeError(f"Unsupported reference for {key}: {reference}")

    path = reference[len("vaultwarden://") :].strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise RuntimeError(f"Invalid vaultwarden reference for {key}: {reference}")

    item_hint = parts[-2]
    field_hint = parts[-1]
    items = run_bw_json(["list", "items", "--search", item_hint])
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected bw output while resolving {key}")

    exact_name = [item for item in items if str(item.get("name") or "") == item_hint]
    candidates = exact_name or items
    if len(candidates) != 1:
        raise RuntimeError(
            f"Ambiguous or missing vault item for {key}: {reference}. "
            "Use a more specific reference."
        )

    item = candidates[0]
    candidate_names = [
        key,
        key.removesuffix("_REF"),
        field_hint,
        field_hint.replace("-", "_"),
        "api_token",
        "token",
    ]

    resolved = extract_custom_field(item, candidate_names)
    if resolved:
        return resolved

    login = item.get("login") or {}
    password = login.get("password")
    if isinstance(password, str) and password:
        return password

    raise RuntimeError(
        f"Unable to resolve secret value for {key}: {reference}. "
        "Add a matching custom field or login.password."
    )


def check_output_not_staged(output_path: Path) -> None:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return

    staged = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    if output_path.as_posix() in staged or output_path.name in staged:
        raise RuntimeError(f"Refusing to continue: {output_path} is staged for commit.")


def build_resolved_content(lines: list[EnvLine]) -> tuple[str, list[str]]:
    output_lines: list[str] = []
    actions: list[str] = []

    for line in lines:
        if line.key is None:
            output_lines.append(line.raw)
            continue

        key = line.key
        value = line.value or ""

        if key.endswith("_REF") and value.startswith("vaultwarden://"):
            resolved_key = key[: -len("_REF")]
            secret_value = resolve_vault_ref(key, value)
            output_lines.append(f"{resolved_key}={secret_value}")
            actions.append(f"resolved {resolved_key} from {value}")
            continue

        output_lines.append(f"{key}={value}")

    return "\\n".join(output_lines) + "\\n", actions


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        require_bw_session()
        check_output_not_staged(output_path)
        lines = parse_env_lines(input_path.read_text(encoding="utf-8"))
        content, actions = build_resolved_content(lines)
    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    for action in actions:
        print(action)

    if args.dry_run:
        print("dry-run complete; no files were written")
        return 0

    if output_path.exists() and not args.force:
        print(f"Error: output exists ({output_path}); use --force to overwrite", file=sys.stderr)
        return 2

    output_path.write_text(content, encoding="utf-8")
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


OPENPROJECT_NEXT_STORY_TEMPLATE = """#!/usr/bin/env python3
\"\"\"Read .env.resolved and report the next OpenProject Story to implement.\"\"\"

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError


@dataclass(frozen=True)
class Candidate:
    story_id: int
    subject: str
    status: str
    type_name: str
    version: str
    parent_epic: str
    parent_epic_id: int | None
    url: str
    branch: str
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.resolved", help="Resolved env file path.")
    parser.add_argument("--json", action="store_true", help="Output result as JSON.")
    parser.add_argument("--status", default="New", help="Target Story status name.")
    parser.add_argument("--type", default="Story", help="Target work package type name.")
    return parser.parse_args()


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise RuntimeError(f"Environment file not found: {path}")

    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        env[key.strip()] = value
    return env


def require_env(env: dict[str, str], keys: list[str]) -> None:
    missing = [key for key in keys if not env.get(key)]
    if missing:
        raise RuntimeError(f"Missing required environment values: {', '.join(missing)}")


def api_get_json(base_url: str, token: str, path: str, query: dict[str, str] | None = None) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"

    credentials = base64.b64encode(f"apikey:{token}".encode("utf-8")).decode("ascii")
    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/json")

    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenProject API error {err.code} for {path}: {body[:200]}") from err
    except URLError as err:
        raise RuntimeError(f"OpenProject connection failed for {path}: {err.reason}") from err


def find_named_element(elements: list[dict], expected_name: str) -> dict:
    for element in elements:
        if str(element.get("name") or "") == expected_name:
            return element
    raise RuntimeError(f"OpenProject value not found: {expected_name}")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug) or "story"


def parse_milestone_rank(version_name: str) -> tuple[int, str]:
    if not version_name:
        return (10000, "")
    match = re.search(r"\\bM(\\d+)\\b", version_name, flags=re.IGNORECASE)
    if not match:
        return (9000, version_name.lower())
    return (int(match.group(1)), version_name.lower())


def extract_wp_id_from_href(href: str | None) -> int | None:
    if not href:
        return None
    match = re.search(r"/work_packages/(\\d+)", href)
    if not match:
        return None
    return int(match.group(1))


def build_candidate(base_url: str, wp: dict) -> Candidate:
    links = wp.get("_links") or {}
    version = (links.get("version") or {}).get("title") or ""
    parent = links.get("parent") or {}
    parent_title = parent.get("title") or "(none)"
    parent_id = extract_wp_id_from_href(parent.get("href"))
    story_id = int(wp.get("id"))
    subject = str(wp.get("subject") or "")
    status = (links.get("status") or {}).get("title") or ""
    type_name = (links.get("type") or {}).get("title") or ""
    url = f"{base_url.rstrip('/')}/work_packages/{story_id}"
    reason = (
        f"selected by status={status}, type={type_name}, milestone={version or '(none)'}, "
        f"parent_epic_id={parent_id if parent_id is not None else 'none'}, story_id={story_id}"
    )

    return Candidate(
        story_id=story_id,
        subject=subject,
        status=status,
        type_name=type_name,
        version=version or "(none)",
        parent_epic=parent_title,
        parent_epic_id=parent_id,
        url=url,
        branch=f"feature/op-{story_id}-{slugify(subject)}",
        reason=reason,
    )


def choose_candidate(base_url: str, stories: list[dict]) -> Candidate:
    candidates = [build_candidate(base_url, story) for story in stories]
    if not candidates:
        raise RuntimeError("No matching Story work packages found.")

    def sort_key(c: Candidate) -> tuple[tuple[int, str], int, int]:
        milestone = parse_milestone_rank(c.version if c.version != "(none)" else "")
        parent = c.parent_epic_id if c.parent_epic_id is not None else 10000000
        return (milestone, parent, c.story_id)

    return sorted(candidates, key=sort_key)[0]


def main() -> int:
    args = parse_args()

    try:
        env = parse_env_file(Path(args.env_file))
        require_env(env, ["OPENPROJECT_URL", "OPENPROJECT_PROJECT_ID", "OPENPROJECT_API_TOKEN"])

        base_url = env["OPENPROJECT_URL"].rstrip("/")
        project_id = env["OPENPROJECT_PROJECT_ID"]
        token = env["OPENPROJECT_API_TOKEN"]

        types_doc = api_get_json(base_url, token, f"/api/v3/projects/{project_id}/types")
        statuses_doc = api_get_json(base_url, token, "/api/v3/statuses")

        types = (types_doc.get("_embedded") or {}).get("elements") or []
        statuses = (statuses_doc.get("_embedded") or {}).get("elements") or []

        chosen_type = find_named_element(types, args.type)
        chosen_status = find_named_element(statuses, args.status)

        filters = [
            {"project": {"operator": "=", "values": [str(project_id)]}},
            {"type": {"operator": "=", "values": [str(chosen_type.get("id"))]}},
            {"status": {"operator": "=", "values": [str(chosen_status.get("id"))]}},
        ]

        wp_doc = api_get_json(
            base_url,
            token,
            "/api/v3/work_packages",
            query={"filters": json.dumps(filters), "pageSize": "200"},
        )

        stories = (wp_doc.get("_embedded") or {}).get("elements") or []
        candidate = choose_candidate(base_url, stories)

    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "openproject_id": candidate.story_id,
                    "subject": candidate.subject,
                    "status": candidate.status,
                    "type": candidate.type_name,
                    "version": candidate.version,
                    "parent_epic": candidate.parent_epic,
                    "suggested_branch": candidate.branch,
                    "url": candidate.url,
                    "reason": candidate.reason,
                    "next_action": (
                        f"Create or checkout {candidate.branch}, then give Codex the Story packet "
                        f"for OP-{candidate.story_id}."
                    ),
                },
                indent=2,
            )
        )
        return 0

    print("Next Story:")
    print(f"- OpenProject ID: {candidate.story_id}")
    print(f"- Subject: {candidate.subject}")
    print(f"- Status: {candidate.status}")
    print(f"- Type: {candidate.type_name}")
    print(f"- Version: {candidate.version}")
    print(f"- Parent: {candidate.parent_epic}")
    print(f"- Branch: {candidate.branch}")
    print(f"- URL: {candidate.url}")
    print(f"- Selection Reason: {candidate.reason}")
    print("\\nNext action:")
    print(
        "Create or checkout the branch above, then give Codex the Story packet "
        f"for OP-{candidate.story_id}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


@dataclass(frozen=True)
class ScaffoldItem:
    relative_path: str
    content: str


def normalize_project_name(workspace_name: str) -> str:
    lowered = workspace_name.strip().lower()
    replaced = re.sub(r"[ _]+", "-", lowered)
    collapsed = re.sub(r"-+", "-", replaced)
    cleaned = collapsed.strip("-")
    if not cleaned:
        raise ValueError("Unable to derive project name from workspace directory name.")
    return cleaned


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Show planned changes without writing files."
    )
    mode.add_argument("--apply", action="store_true", help="Apply scaffold changes to disk.")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite managed files when used with --apply."
    )
    args = parser.parse_args()
    if args.force and not args.apply:
        parser.error("--force can only be used with --apply")
    return args


def ensure_repo_root(root: Path) -> None:
    if not (root / ".git").is_dir():
        raise RuntimeError(
            "This script must be run from the repository root (missing .git directory)."
        )


def directories_to_create() -> tuple[str, ...]:
    return ("docs", "src", "scripts", "tests")


def build_scaffold_items(project_name: str) -> tuple[ScaffoldItem, ...]:
    script_dir = Path(__file__).parent
    return (
        ScaffoldItem("README.md", README_TEMPLATE.format(project_name=project_name)),
        ScaffoldItem("pyproject.toml", PYPROJECT_TEMPLATE.format(project_name=project_name)),
        ScaffoldItem(".gitignore", GITIGNORE_TEMPLATE),
        ScaffoldItem(".env.example", ENV_EXAMPLE_TEMPLATE),
        ScaffoldItem(".pre-commit-config.yaml", PRE_COMMIT_TEMPLATE),
        ScaffoldItem("AGENTS.md", AGENTS_TEMPLATE),
        ScaffoldItem(
            "scripts/resolve_env_refs.py",
            (script_dir / "resolve_env_refs.py").read_text(encoding="utf-8"),
        ),
        ScaffoldItem(
            "scripts/openproject_next_story.py",
            (script_dir / "openproject_next_story.py").read_text(encoding="utf-8"),
        ),
        ScaffoldItem(
            "scripts/init_wood_tools_project.py", Path(__file__).read_text(encoding="utf-8")
        ),
    )


def main() -> int:
    try:
        args = parse_args()
        root = Path.cwd()
        ensure_repo_root(root)
        project_name = normalize_project_name(root.name)
    except (RuntimeError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    created_dirs = 0
    existing_dirs = 0
    created_files: list[str] = []
    skipped_files: list[str] = []
    overwritten_files: list[str] = []

    for rel_dir in directories_to_create():
        target_dir = root / rel_dir
        if target_dir.exists():
            print(f"[DIR] exists: {rel_dir}")
            existing_dirs += 1
            continue
        print(f"[DIR] create: {rel_dir}")
        created_dirs += 1
        if args.apply:
            target_dir.mkdir(parents=True, exist_ok=True)

    for item in build_scaffold_items(project_name):
        target = root / item.relative_path
        if target.exists():
            if args.apply and args.force:
                print(f"[FILE] overwrite: {item.relative_path}")
                target.write_text(item.content, encoding="utf-8")
                overwritten_files.append(item.relative_path)
            else:
                print(f"[FILE] skip exists: {item.relative_path}")
                skipped_files.append(item.relative_path)
            continue

        print(f"[FILE] create: {item.relative_path}")
        created_files.append(item.relative_path)
        if args.apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8")

    mode_name = "apply" if args.apply else "dry-run"
    print("\nSummary")
    print(f"- mode: {mode_name}")
    print(f"- project_name: {project_name}")
    print(f"- directories created: {created_dirs}")
    print(f"- directories existing: {existing_dirs}")
    print(f"- files created: {len(created_files)}")
    print(f"- files skipped: {len(skipped_files)}")
    print(f"- files overwritten: {len(overwritten_files)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

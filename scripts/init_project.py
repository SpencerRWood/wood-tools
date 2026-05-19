#!/usr/bin/env python3
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
3. Copy `.env.example` to `.env` and fill values with references, not secrets.

## Initialize Scaffold

Run from repository root:

```bash
python scripts/init_wood_tools_project.py --dry-run
python scripts/init_wood_tools_project.py --apply
python scripts/init_wood_tools_project.py --apply --force
```

## Run Tests

```bash
pytest
```

## Run Lint and Format Checks

```bash
ruff check .
ruff format --check .
```

## Workflow Notes

- Do not commit directly to `main`.
- Each implementation Story should map to one OpenProject-backed feature branch.
- Store only secret references in repo-managed files, never raw secrets.
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

[tool.setuptools]
package-dir = {{"" = "src"}}

[tool.setuptools.packages.find]
where = ["src"]
include = ["*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
  "unit: Unit tests",
  "integration: Integration tests",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
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
OPENPROJECT_API_TOKEN=
NTFY_URL=
NTFY_TOKEN=
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
      - id: ruff-format
"""


AGENTS_TEMPLATE = """# AGENTS.md

## Operating Principles

- Inspect before editing.
- Make the smallest coherent change that satisfies the assigned Story.
- Do not implement unrelated Stories.
- Prefer deterministic scripts over ad hoc manual changes.
- Preserve explicit approval gates for mutating operations.
- Do not store secrets in source code, config files, logs, generated docs, test fixtures, or output files.
- Do not mutate OpenProject, Vaultwarden, ntfy, GitHub, or other external systems unless explicitly instructed.
- Run relevant tests and lint checks before claiming completion.
- If requirements are ambiguous, stop and ask for clarification.

## OpenProject ID Requirement

- Every implementation session must be tied to an existing OpenProject Story work package.
- Confirm the OpenProject ID before making code changes.
- Branch names must include the existing OpenProject ID:

  feature/op-<openproject-work-package-id>-<slug>

- If no OpenProject ID is provided, stop and ask the user for one.
- Do not invent OpenProject IDs.
- Do not create placeholder OpenProject IDs.
- Do not create a new OpenProject work package unless explicitly instructed.
- Do not create a feature branch without a real OpenProject ID.
- Do not proceed with placeholder branch names such as:
  - feature/op-<work-package-id>-...
  - feature/op-TBD-...
  - feature/op-000-...

## Branch Workflow

- Work from one OpenProject Story at a time.
- Each Story should map to one feature branch.
- Before editing, inspect the current branch:

  git status
  git branch --show-current

- If already on the correct feature branch, continue.
- If not on the correct feature branch and the branch exists locally, check it out.
- If not on the correct feature branch and it does not exist, create it from the current base branch.
- Do not commit directly to main.
- Do not switch branches if there are uncommitted changes unless explicitly instructed.
- Do not perform destructive Git operations unless explicitly instructed.

## Story Execution Workflow

For each assigned Story:

1. Read the Story packet.
2. Confirm the OpenProject ID, branch name, goal, acceptance criteria, dependencies, and non-goals.
3. Inspect the repository structure.
4. Identify the minimal files/modules that should change.
5. Implement only the requested Story.
6. Add or update tests where appropriate.
7. Run relevant tests and lint checks.
8. Review the diff.
9. Provide a structured completion report.

Do not continue if the Story packet is missing:

- OpenProject ID
- branch name
- target area
- goal
- acceptance criteria

Ask the user for the missing information instead.

## Test and Lint Expectations

Use the project tooling defined in the repository.

Preferred checks, when available:

  pytest
  ruff check .
  ruff format --check .

If a check cannot be run, explain why in the final report.

Do not fabricate test results.

## Commit Guidance

- Do not commit changes unless explicitly instructed by the user.
- If asked to commit, commit only changes related to the assigned OpenProject Story.
- Before recommending or creating a commit, inspect the diff:

  git status
  git diff --stat
  git diff

- Commit messages must include the OpenProject work package ID.
- Prefer Conventional Commit style:

  type(scope): short summary for op-<openproject-work-package-id>

Common types:

- feat: new functionality
- fix: bug fix
- test: test-only change
- docs: documentation-only change
- refactor: code restructuring without behavior change
- chore: tooling, config, scaffold, or maintenance

Examples:

  chore(scaffold): initialize minimal project scaffold for op-233
  feat(config): add config profile initialization for op-233
  test(config): add config profile tests for op-233

Do not use vague commit messages such as:

- update files
- changes
- fix stuff
- work in progress

If multiple unrelated changes are present, stop and ask whether to split commits.

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

- Provide one concise commit message aligned to the Story scope.
- Use:

  type(scope): short summary for op-<openproject-work-package-id>

- If unrelated changes are present, recommend splitting commits instead.

### Next Suggested Action

- State whether the Story appears ready for review, needs more work, or is blocked.

## Safety Rules

- Do not print secrets.
- Do not add real tokens, passwords, API keys, or private credentials to files.
- Use secret references instead of secret values.
- Do not remove existing user work without explicit instruction.
- Do not perform destructive Git operations unless explicitly instructed.
- Do not call production APIs unless explicitly instructed.
- Do not mutate external systems unless explicitly instructed.
- Do not create pull requests or push branches unless explicitly instructed.
- Do not install dependencies globally unless explicitly instructed.
- Do not alter unrelated local machine configuration.

## When to Stop and Ask

Stop and ask the user before continuing if:

- no OpenProject ID is available
- the branch name contains a placeholder ID
- the current branch does not match the assigned Story and there are uncommitted changes
- the Story packet is missing acceptance criteria
- the requested change spans multiple unrelated Stories
- implementing the request would mutate an external system
- tests or lint checks require unavailable dependencies and installing them would change the environment
- there is a risk of overwriting existing user work
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
    mode.add_argument("--dry-run", action="store_true", help="Show planned changes without writing files.")
    mode.add_argument("--apply", action="store_true", help="Apply scaffold changes to disk.")
    parser.add_argument("--force", action="store_true", help="Overwrite managed files when used with --apply.")
    args = parser.parse_args()
    if args.force and not args.apply:
        parser.error("--force can only be used with --apply")
    return args


def ensure_repo_root(root: Path) -> None:
    if not (root / ".git").is_dir():
        raise RuntimeError("This script must be run from the repository root (missing .git directory).")


def directories_to_create() -> tuple[str, ...]:
    return ("docs", "src", "scripts", "tests")


def build_scaffold_items(project_name: str) -> tuple[ScaffoldItem, ...]:
    return (
        ScaffoldItem("README.md", README_TEMPLATE.format(project_name=project_name)),
        ScaffoldItem("pyproject.toml", PYPROJECT_TEMPLATE.format(project_name=project_name)),
        ScaffoldItem(".gitignore", GITIGNORE_TEMPLATE),
        ScaffoldItem(".env.example", ENV_EXAMPLE_TEMPLATE),
        ScaffoldItem(".pre-commit-config.yaml", PRE_COMMIT_TEMPLATE),
        ScaffoldItem("AGENTS.md", AGENTS_TEMPLATE),
        ScaffoldItem("scripts/init_wood_tools_project.py", Path(__file__).read_text(encoding="utf-8")),
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

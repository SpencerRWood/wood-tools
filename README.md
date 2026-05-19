
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

# wood-tools

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

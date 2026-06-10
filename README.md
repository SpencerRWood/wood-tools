# wood-tools

## Quick Setup

1. Create and activate a Python virtual environment in local runtime storage.
2. Install development tools you plan to use (for example, pytest, ruff, and pre-commit).
3. Copy `.env.example` to `.env` and set reference-only values (never raw secrets).

### Find Runtime Venv Path

Use your current project folder name as the slug:

```bash
project_slug="$(basename "$PWD" | tr '[:upper:] _' '[:lower:]-' | tr -s '-')"
venv_path="$HOME/.wood/runtime/venvs/${project_slug}"
echo "${venv_path}"
```

Create and activate it:

```bash
mkdir -p "$HOME/.wood/runtime/venvs"
python3 -m venv "${venv_path}"
source "${venv_path}/bin/activate"
```

Install project tooling into the active runtime venv:

```bash
export UV_LINK_MODE=copy
bash scripts/uv_active.sh sync --group dev
pre-commit install --install-hooks
```

Note: Run project `uv` commands through `scripts/uv_active.sh` to guarantee
they target your external runtime venv and do not recreate a local `./.venv`.

Or set it explicitly when initializing scaffold content:

```bash
python scripts/init_project.py --dry-run --project-name wood-tools
python scripts/init_project.py --apply --project-name wood-tools
```

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

## wood-config

Configuration file path defaults to:

- `$XDG_CONFIG_HOME/wood-tools/config.json` when `XDG_CONFIG_HOME` is set
- `~/.config/wood-tools/config.json` otherwise

Profile format:

```json
{
  "version": 1,
  "active_profile": "default",
  "profiles": {
    "default": {
      "paths": {
        "project_root": "./projects"
      },
      "integrations": {
        "openproject": {
          "url": "https://openproject.example.test",
          "project_id": "wood",
          "token_ref": "env://OPENPROJECT_TOKEN",
          "user_agent": "wood-tools/0.1"
        },
        "vaultwarden": {
          "config_ref": "env://VAULTWARDEN_CONFIG",
          "session_file": "~/.config/wood-tools/vaultwarden-session.json",
          "cli": {
            "executable": "bw"
          }
        }
      },
      "wood_agents": {
        "boundary_ref": "docs://wood-agents/boundary",
        "adapters_ref": "pkg://wood-agents/adapters"
      },
      "diagnostics": {
        "agent_readiness": {
          "enabled": true
        }
      },
      "output": {
        "json_envelope": {
          "enabled": true
        }
      }
    }
  }
}
```

Commands:

```bash
wood-config init --apply
wood-config show
wood-config get example.key
wood-config set example.key '"new value"' --apply
wood-config set env.name dev --profile dev --activate-profile --apply
```

Mutating commands keep an explicit approval gate via `--apply`.

## Run Tests

```bash
bash scripts/uv_active.sh run pytest
```

## Run Lint and Format Checks

```bash
bash scripts/uv_active.sh run ruff check .
bash scripts/uv_active.sh run ruff format --check .
```

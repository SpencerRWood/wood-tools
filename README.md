# wood-tools

Deterministic Python CLI tooling for project delivery workflows.

## What This Repo Includes

- `wood-config` for local config initialization, profile management, validation, and diagnostics
- `wood-project` for workspace `project.json` initialization, display, and validation
- `wood-secrets` for provider health checks and redacted secret reference resolution
- `scripts/init_project.py` for scaffolding a Wood-tools-style project
- `scripts/resolve_env_refs.py` for resolving reference-only `.env` values into `.env.resolved`
- `scripts/openproject_next_story.py` for read-only next-story selection from OpenProject
- `scripts/uv_active.sh` for running `uv` against the external runtime venv

## Quick Setup

1. Create and activate a Python virtual environment in local runtime storage.
2. Install development tools you plan to use.
3. Copy `.env.example` to `.env` and set reference-only values.

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

## CLI Reference

### `bash scripts/uv_active.sh`

Wrapper for `uv run --active ...` and `uv sync --active ...` so the project uses the external runtime venv instead of creating `./.venv`.

Supported subcommands:

- `run`
- `sync`

Examples:

```bash
bash scripts/uv_active.sh run pytest
bash scripts/uv_active.sh run ruff check .
bash scripts/uv_active.sh run ruff format --check .
bash scripts/uv_active.sh sync --group dev
```

If the runtime venv does not exist, the wrapper exits with code `2` and prints the `python3 -m venv ...` command to create it.

### `python3 scripts/init_project.py`

Scaffolds a Wood-tools-style repository from the current repo root.

Modes:

- `--dry-run` preview planned changes
- `--apply` write scaffold changes

Options:

- `--force` overwrite managed files when used with `--apply`
- `--project-name <name>` override the default project name derived from the current directory

Examples:

```bash
python3 scripts/init_project.py --dry-run
python3 scripts/init_project.py --apply
python3 scripts/init_project.py --apply --force
python3 scripts/init_project.py --dry-run --project-name wood-tools
```

Behavior notes:

- Must run from the repository root.
- Writes only when `--apply` is present.
- Prints created, skipped, and overwritten file counts at the end.

### `python3 scripts/resolve_env_refs.py`

Resolves Vaultwarden-backed `*_REF` values from `.env` into `.env.resolved`.

Modes:

- `--dry-run` preview the resolved file
- `--apply` write the output file

Options:

- `--input <path>` input file path, default `".env"`
- `--output <path>` output file path, default `".env.resolved"`
- `--force` overwrite an existing output file when used with `--apply`
- `--prompt-unlock` prompt for the Vaultwarden/Bitwarden master password on macOS if `BW_SESSION` is not already set

Examples:

```bash
export BW_SESSION="$(bw unlock --raw)"
python3 scripts/resolve_env_refs.py --dry-run
python3 scripts/resolve_env_refs.py --apply
python3 scripts/resolve_env_refs.py --apply --force
python3 scripts/resolve_env_refs.py --apply --input .env.local --output .env.resolved.local
python3 scripts/resolve_env_refs.py --apply --prompt-unlock
```

Behavior notes:

- Never commit `.env.resolved`.
- Keeps resolved secret values out of normal command output.
- `--prompt-unlock` is macOS-only.

### `python3 scripts/openproject_next_story.py`

Reports the next dependency-ready OpenProject Story beneath a supplied root work package.

Required argument:

- `<root_work_package_id>` integer root work package ID

Options:

- `--env-file <path>` resolved environment file path, default `".env.resolved"`
- `--json` emit JSON output
- `--status <name>` candidate status name, default `"New"`
- `--type <name>` candidate work package type name, default `"Story"`
- `--page-size <n>` OpenProject collection page size, default `1000`

Examples:

```bash
python3 scripts/openproject_next_story.py 208
python3 scripts/openproject_next_story.py 208 --json
python3 scripts/openproject_next_story.py 208 --env-file .env.resolved
python3 scripts/openproject_next_story.py 208 --status "In Progress"
python3 scripts/openproject_next_story.py 208 --type Story --page-size 500
```

Behavior notes:

- Read-only: it does not mutate OpenProject.
- Requires `.env.resolved` values for `OPENPROJECT_URL`, `OPENPROJECT_PROJECT_ID`, and `OPENPROJECT_API_TOKEN`.
- Selects only descendant work packages under the supplied root.
- Normalizes predecessor/follows relationships before determining readiness.

### `wood-config`

Local config manager for Wood-tools.

Configuration file path defaults to:

- `$XDG_CONFIG_HOME/wood-tools/config.json` when `XDG_CONFIG_HOME` is set
- `~/.config/wood-tools/config.json` otherwise

Global option:

- `--config-path <path>` override the config file path

#### Profile Format

```json
{
  "version": 1,
  "active_profile": "default",
  "profiles": {
    "default": {
      "paths": {
        "project_root": "./projects",
        "artifact_root": "./artifacts",
        "project_aliases": {
          "demo": {
            "path": "//nas/projects/demo",
            "targets": [
              "/Volumes/Projects/demo",
              "/mnt/projects/demo"
            ]
          }
        },
        "artifact_aliases": {
          "demo": {
            "path": "//nas/artifacts/demo",
            "targets": [
              "/Volumes/Artifacts/demo",
              "/mnt/artifacts/demo"
            ]
          }
        },
        "scheduler_root": "./scheduler",
        "template_search_paths": [
          "./templates"
        ]
      },
      "integrations": {
        "openproject": {
          "url": "https://openproject.example.test",
          "project_id": "wood",
          "token_ref": "env://OPENPROJECT_TOKEN",
          "user_agent": "wood-tools/0.1"
        },
        "ntfy": {
          "url": null,
          "token_ref": "env://NTFY_TOKEN"
        },
        "vaultwarden": {
          "url": null,
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

Alias notes:

- `paths.project_aliases` and `paths.artifact_aliases` map stable shared paths to machine-specific candidate targets.
- The object form uses `path` for the canonical NAS-backed location and `targets` for candidate local mount paths checked in order.
- Legacy string aliases such as `"demo": "./projects/demo"` are still accepted and resolve as a single direct target.
- `show`, `get`, `validate`, and `doctor` include alias resolution metadata so you can see which target matched on the current machine.

### `wood-secrets`

Secret reference utility for validating providers, managing protected Vaultwarden runtime
sessions, and resolving references without printing secret values.

Supported commands:

- `wood-secrets check`
- `wood-secrets check --ref <reference>`
- `wood-secrets providers`
- `wood-secrets status --provider vaultwarden`
- `wood-secrets unlock --provider vaultwarden --interactive --write-session`
- `wood-secrets unlock --provider vaultwarden --gui --write-session`
- `wood-secrets lock --provider vaultwarden`
- `wood-secrets session --provider vaultwarden`
- `wood-secrets resolve --ref <reference> --redacted`
- `wood-secrets doctor`

Supported reference syntax:

- `env://NAME` reads a secret directly from the `NAME` environment variable
- `vaultwarden://<path>/<field>` resolves a Vaultwarden/Bitwarden secret from the `bw` CLI

Environment fallback behavior:

- For any reference, Wood-tools also recognizes an override environment variable named
  `WOOD_SECRETS_REF_<NORMALIZED_REFERENCE>`.
- Example:
  `vaultwarden://wood/openproject/prod/api-token` maps to
  `WOOD_SECRETS_REF_VAULTWARDEN_WOOD_OPENPROJECT_PROD_API_TOKEN`.
- Fallback values are resolved in memory only and are still emitted as `[REDACTED]`.

Examples:

```bash
wood-secrets check
wood-secrets check --ref env://OPENPROJECT_TOKEN --json
wood-secrets providers --json
wood-secrets status --provider vaultwarden
wood-secrets unlock --provider vaultwarden --interactive --write-session
wood-secrets unlock --provider vaultwarden --gui --write-session
wood-secrets lock --provider vaultwarden
wood-secrets session --provider vaultwarden --json
wood-secrets resolve --ref vaultwarden://wood/openproject/prod/api-token --redacted
wood-secrets doctor --json
```

Behavior notes:

- Secret values are never printed by the CLI; resolved output is redacted.
- `wood-secrets check` inspects `integrations.openproject.token_ref` and `integrations.ntfy.token_ref` from the active `wood-config` profile.
- `vaultwarden://` references require at least two path segments after the scheme.
- Vaultwarden runtime session files are written outside the repository, defaulting to `~/.wood/runtime/secrets/vaultwarden-session.json`.
- Vaultwarden runtime session files are restricted to mode `0600`, and command output never prints the session token.
- `wood-secrets unlock --gui` is available on macOS where `osascript` is present.

### `wood-project`

Workspace metadata manager for canonical `project.json` files.

Default paths:

- `project.json` at the selected project root
- `.wood/` metadata directory at the selected project root
- `.wood/artifacts/<project-slug>` as the default project-specific artifact directory

Global options:

- `--project-root <path>` override the project root, default current working directory
- `--project-file <path>` override the `project.json` path for read-only commands

Project file format:

```json
{
  "schema_version": 1,
  "project_id": "2ff7b7be-6c19-49fd-bc6c-5f3064fe7bf7",
  "project_slug": "wood-tools",
  "project_root": "/workspace/wood-tools",
  "artifact_root": "/workspace/wood-tools/.wood/artifacts",
  "artifact_dir": "/workspace/wood-tools/.wood/artifacts/wood-tools",
  "metadata_dir": "/workspace/wood-tools/.wood"
}
```

Optional fields:

- `linked_repositories` may be provided as either an object keyed by repository name or a list of `{ "name", "path" }` objects. Each linked repository path must be absolute, may include an optional `role`, and is validated when present.
- `wood-project validate` performs non-mutating availability checks for configured project, metadata, artifact, and linked repository directories. JSON output includes per-path access details.
- Mutating `wood-project` commands preflight the required parent directories and fail early with clear errors when a NAS mount is unavailable or not writable.

Commands:

- `wood-project init` preview the resolved project metadata
- `wood-project init --apply` write `project.json` and create required directories
- `wood-project link repo <path>` preview linking an implementation repository to the project
- `wood-project link repo <path> --apply` persist a linked repository entry in `project.json`
- `wood-project show` read and print the current `project.json`
- `wood-project validate` verify the current `project.json` schema, required directories, path relationships, linked repository paths, and mounted path accessibility

Options for `init`:

- `--project-id <value>` provide an explicit project ID instead of generating a UUID
- `--project-slug <value>` provide an explicit slug instead of deriving one from the root folder
- `--artifact-root <path>` provide a custom shared artifact root
- `--json` emit JSON envelope output

Options for `link repo`:

- `<path>` repository directory to link
- `--name <value>` override the stored repository name, default the repository directory name
- `--role <value>` store an optional repository role such as `app`, `library`, or `infra`
- `--apply` write the updated `project.json`
- `--json` emit JSON envelope output

Examples:

```bash
wood-project init
wood-project init --apply
wood-project init --project-id proj-123 --project-slug demo-app --apply
wood-project init --artifact-root /tmp/wood-artifacts --apply
wood-project link repo ../shared-lib --role library --apply
wood-project show --json
wood-project validate --json
```

#### `wood-config init`

Initialize a config file with defaults.

Options:

- `--apply` write the file
- `--json` emit JSON output

Examples:

```bash
wood-config init
wood-config init --apply
wood-config init --apply --json
wood-config --config-path /tmp/wood-config.json init --apply
```

#### `wood-config show`

Show config values for a profile.

Options:

- `--profile <name>` select a profile
- `--json` emit JSON output

Examples:

```bash
wood-config show
wood-config show --profile dev
wood-config show --json
```

#### `wood-config get`

Read a single config value.

Options:

- `--profile <name>` select a profile
- `--json` emit JSON output

Examples:

```bash
wood-config get paths.project_root
wood-config get integrations.openproject.user_agent --json
wood-config get paths.project_aliases.demo --json
wood-config get integrations.vaultwarden.session_file --profile dev
```

#### `wood-config set`

Set a config value. Values may be JSON literals or raw strings.

Options:

- `--profile <name>` write to a profile
- `--activate-profile` switch the written profile to active
- `--apply` write changes to disk
- `--json` emit JSON output

Examples:

```bash
wood-config set paths.project_root '"./projects"' --apply
wood-config set paths.project_aliases '{"demo":{"path":"//nas/projects/demo","targets":["/Volumes/Projects/demo","/mnt/projects/demo"]}}' --profile dev --apply
wood-config set integrations.openproject.token_ref '"env://OPENPROJECT_TOKEN"' --apply
wood-config set wood_agents.boundary_ref '"docs://wood-agents/boundary"' --profile dev --apply
wood-config set env.name dev --profile dev --activate-profile --apply
wood-config set diagnostics.agent_readiness '{"enabled":true}' --apply --json
```

Behavior notes:

- Mutating operations require `--apply`.
- Sensitive values must be stored as references only, using keys ending in `_ref`.
- JSON mode returns a shared envelope with `command`, `status`, `mutation`, `requires_approval`, `summary`, `data`, `warnings`, `errors`, and `next_actions`.

#### `wood-config validate`

Validate required config settings and report actionable errors.

Options:

- `--profile <name>` validate a profile
- `--json` emit JSON output

Examples:

```bash
wood-config validate
wood-config validate --profile dev
wood-config validate --json
```

Exit codes:

- `0` when valid
- `2` when invalid or when a config error occurs

#### `wood-config doctor`

Run diagnostics using stable, redacted output envelopes.

Options:

- `--profile <name>` diagnose a profile
- `--check <name>` run only the named check; repeatable
- `--json` emit JSON output

Supported `--check` values:

- `vaultwarden`
- `openproject`
- `ntfy`
- `scheduler`
- `agent-readiness`

Examples:

```bash
wood-config doctor
wood-config doctor --json
wood-config doctor --profile dev
wood-config doctor --check openproject --json
wood-config doctor --check vaultwarden --check ntfy --json
wood-config doctor --check scheduler
wood-config doctor --check agent-readiness --profile dev --json
```

Behavior notes:

- Reports issue metadata and remediation without printing secret values.
- `--json` returns the shared command envelope; command-specific fields live under `data`.
- When `--check` is omitted, all current doctor checks run.

## Typical Local Workflow

### Resolve Local Environment References

1. Put reference-only values in `.env`.
2. Unlock Vaultwarden/Bitwarden CLI:
   `export BW_SESSION="$(bw unlock --raw)"`
3. Resolve refs:
   `python3 scripts/resolve_env_refs.py --apply`
4. Confirm `.env.resolved` exists and is ignored by Git.

Optional on macOS:

```bash
python3 scripts/resolve_env_refs.py --apply --prompt-unlock
```

### Find the Next OpenProject Story

1. Ensure `.env.resolved` exists.
2. Run:
   `python3 scripts/openproject_next_story.py 208`
3. Create or check out the suggested branch.
4. Give Codex the Story packet for that OpenProject ID.

Notes:

- `.env.resolved` may contain secrets and must never be committed.
- Next-story lookup is read-only.

## Development Commands

Run tests:

```bash
bash scripts/uv_active.sh run pytest
bash scripts/uv_active.sh run pytest tests/test_wood_config.py
```

Run lint and format checks:

```bash
bash scripts/uv_active.sh run ruff check .
bash scripts/uv_active.sh run ruff format --check .
```

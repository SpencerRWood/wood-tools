# wood-tools

Deterministic Python CLI tooling for project delivery workflows.

## What This Repo Includes

- `wood-config` for local config initialization, profile management, validation, and diagnostics
- `wood-project` as the single deterministic execution surface for Wood Agents loops
- `wood-secrets` for provider health checks and redacted secret reference resolution
- `wood-template` for planning and applying built-in or installed project templates

### Architecture Boundaries

- Each public command is implemented by its capability package's `cli.py` module.
- Domain behavior shared by a package's CLI and other consumers lives in that package's `core/`
  package.
- Wood Agents own orchestration, workflow policy, approval timing, and cross-system coordination.
- Wood Tools, through `wood-project`, owns deterministic inspection, validation, planning, and
  mutation.
- `wood_project` owns local project metadata, the deterministic Wood Agents command surface, and
  the shared OpenProject client.
- `wood_project.story`, `wood_project.implementation`, and `wood_project.release` are the three
  primary loop-specific domains.
- `wood_project.commands.openproject`, `wood_project.commands.resources`, and
  `wood_project.commands.project` provide supporting context and precondition commands.
- `resources.packages` owns generic resource manifests, validation, installation, and lookup as a
  shared library package with no console entry point.
- `wood_project.story` owns Story discovery, status, and branch behavior and reuses the
  project-owned OpenProject client.
- `wood_project.implementation` owns Implementation Workbook export, planning, and apply behavior.
- OpenProject planning Versions use numeric R# order (`R9` before `R10`); repository
  semantic-release versions remain separate. The 21-column workbook carries
  Primary Repository, Affected Repositories, and Released In. Planning requires
  Released In to be blank. After shipment and Story closure, preview and apply
  `wood-project implementation record-release <workbook> <story-id> <actual-semver>`.
  See [the migration procedure](OPENPROJECT_PLANNING_RELEASE_MIGRATION.md)
  before changing legacy live OpenProject Versions.
- `wood_project.release` owns release readiness, version bump, tag, and GitHub release behavior.
- `wood_templates` owns the template CLI, template domain API, and all built-in template packs.
- `resources.cli` owns output envelopes and audit logging shared by every command.

```text
src/
  resources/
    __init__.py
    cli/
      audit.py
      models.py
      output.py
    packages/
      manifest.py
      models.py
      store.py
  wood_config/
    cli.py
    core/
  wood_secrets/
    cli.py
    core/
  wood_project/
    cli.py
    commands/
      project.py
      resources.py
      openproject.py
      story.py
      implementation.py
      release.py
    core/
      documents.py
      models.py
      paths.py
      project.py
      validation.py
    openproject/
      client.py
      config.py
      models.py
    story/
      models.py
      openproject.py
      discovery.py
      status.py
      branches.py
    implementation/
      export.py
      planner.py
      workflows.py
    release/
      check.py
      github.py
      tag.py
      version.py
  wood_templates/
    cli.py
    core/
      catalog.py
      manifest.py
      project.py
      renderer.py
```

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
uv sync --active --group dev
pre-commit install --install-hooks
```

## CLI Reference

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
        "project_aliases": {
          "demo": {
            "path": "//nas/projects/demo",
            "targets": [
              "/Volumes/Projects/demo",
              "/mnt/projects/demo"
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
          "user_agent": "wood-tools/0.2.0"
        },
        "ntfy": {
          "url": null,
          "token_ref": "env://NTFY_TOKEN"
        },
        "vaultwarden": {
          "url": "https://vault.example.test",
          "config_ref": "env://VAULTWARDEN_CONFIG",
          "session_file": "~/.config/wood-tools/vaultwarden-session.json",
          "cli": {
            "executable": "bw",
            "appdata_dir": "~/.wood/runtime/bitwarden-cli"
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

- `paths.project_aliases` maps stable shared paths to machine-specific candidate targets.
- The object form uses `path` for the canonical NAS-backed location and `targets` for candidate local mount paths checked in order.
- `show`, `get`, `validate`, and `doctor` include alias resolution metadata so you can see which target matched on the current machine.

### `wood-secrets`

Secret reference utility for validating providers, managing protected Vaultwarden runtime
sessions, and resolving references without printing secret values.

Supported commands:

- `wood-secrets check`
- `wood-secrets check --ref <reference>`
- `wood-secrets providers`
- `wood-secrets status --provider vaultwarden`
- `wood-secrets unlock`
- `wood-secrets unlock --gui`
- `wood-secrets lock --provider vaultwarden`
- `wood-secrets session --provider vaultwarden`
- `wood-secrets list --provider vaultwarden`
- `wood-secrets exec --env <NAME> --ref <reference> -- <command> ...`
- `wood-secrets exec NAME=<reference> [OTHER_NAME=<reference> ...] -- <command> ...`
- `wood-secrets resolve --ref <reference> --redacted`
- `wood-secrets resolve-env`
- `wood-secrets resolve-env --apply`
- `wood-secrets materialize [name]`
- `wood-secrets materialize [name] --apply`
- `wood-secrets doctor`

Supported reference syntax:

- `env://NAME` reads a secret directly from the `NAME` environment variable
- `vaultwarden://<path>/<field>` resolves a Vaultwarden/Bitwarden secret from the `bw` CLI
- `vaultwarden://<path>/<item>#<field-name>` resolves a specific field from a matched item when
  the field name differs from the item-name suffix
  after `wood-secrets` confirms the active `bw` server matches
  `integrations.vaultwarden.url` from the active `wood-config` profile

Environment fallback behavior:

- For any reference, Wood-tools also recognizes an override environment variable named
  `WOOD_SECRETS_REF_<NORMALIZED_REFERENCE>`.
- Example:
  `vaultwarden://wood/openproject/prod/api-token` maps to
  `WOOD_SECRETS_REF_VAULTWARDEN_WOOD_OPENPROJECT_PROD_API_TOKEN`.
- Explicit field selectors remain part of the normalized fallback name.
- Example:
  `vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN` maps to
  `WOOD_SECRETS_REF_VAULTWARDEN_WOOD_OPENPROJECT_PROD_API_TOKEN_OPENPROJECT_API_TOKEN`.
- Fallback values are resolved in memory only and are still emitted as `[REDACTED]`.

Examples:

```bash
wood-secrets check
wood-secrets check --ref env://OPENPROJECT_TOKEN --json
wood-secrets providers --json
wood-secrets status --provider vaultwarden
wood-secrets unlock
wood-secrets unlock --gui
wood-secrets lock --provider vaultwarden
wood-secrets session --provider vaultwarden --json
wood-secrets list --provider vaultwarden --search openproject --json
wood-secrets exec --env OPENPROJECT_TOKEN --ref 'vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN' -- env
wood-secrets exec OPENPROJECT_TOKEN='vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN' -- env
wood-secrets exec OPENPROJECT_TOKEN='vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN' OTHER_TOKEN='vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN' -- env
wood-secrets resolve --ref vaultwarden://wood/openproject/prod/api-token --redacted
wood-secrets resolve --ref 'vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN' --redacted
wood-secrets resolve-env --input .env --output .env.resolved --json
wood-secrets resolve-env --input .env --output .env.resolved --apply --force
wood-secrets materialize --json
wood-secrets materialize openproject-token --apply --json
wood-secrets doctor --json
```

Behavior notes:

- Secret values are never printed by the CLI; resolved output is redacted.
- `wood-secrets check` inspects `integrations.openproject.token_ref` and `integrations.ntfy.token_ref` from the active `wood-config` profile.
- `wood-secrets` reads `integrations.vaultwarden.url` from the active `wood-config` profile.
- `wood-secrets unlock` defaults to the Vaultwarden provider, prompts in the interactive terminal, and writes the protected runtime session file for later processes.
- `wood-secrets unlock --no-write-session` keeps the session only in the current process.
- `wood-secrets` runs `bw` with `BITWARDENCLI_APPDATA_DIR` pointed at a writable runtime directory, defaulting to `~/.wood/runtime/bitwarden-cli`, so sandboxed commands do not need to write Bitwarden CLI lock files under Bitwarden's default home-directory location.
- `wood-secrets unlock --provider vaultwarden ...` applies that configured URL with `bw config server <url>` before unlocking.
- `wood-secrets unlock --provider vaultwarden ...` uses `bw unlock --passwordenv ...` for compatibility with current Bitwarden CLI releases.
- Interactive unlock fails closed when no local terminal TTY is available.
- `wood-secrets list --provider vaultwarden ...` lists only item names, field names, and whether a login password exists; it never prints secret values.
- `wood-secrets exec --env NAME --ref ... -- command ...` resolves a secret locally, injects it only into the child process environment, and does not print the secret value itself.
- `wood-secrets exec NAME=reference OTHER_NAME=reference -- command ...` is a shorthand form that also supports multiple secret-backed environment variables.
- `wood-secrets resolve-env` resolves supported `*_REF` entries from an env file into non-`_REF` keys in a local resolved env file; it previews by default and writes only with `--apply`.
- `wood-secrets resolve-env` reports resolved keys and references only; it never prints resolved secret values.
- `wood-secrets materialize [name]` previews configured local secret files by name, reference, target path, and state without printing secret values.
- `wood-secrets materialize [name] --apply` writes configured secrets beneath `paths.secrets_root`, defaulting to `~/.wood/secrets`.
- Materialized secret definitions live under `integrations.vaultwarden.materialized_secrets` as named objects with `ref` and relative `target` fields.
- Materialized secret directories and files use owner-only permissions, reject absolute/traversing/symlink-escaping targets, write atomically, and skip unchanged files.
- Read-only Vaultwarden commands fail closed when the active `bw` CLI server does not match the configured URL.
- `vaultwarden://` references require at least two path segments after the scheme.
- `vaultwarden://...#FIELD_NAME` lets you separate item matching from field selection.
- Vaultwarden runtime session files are written outside the repository, defaulting to `~/.wood/runtime/secrets/vaultwarden-session.json`.
- Vaultwarden runtime session files are restricted to mode `0600`, and command output never prints the session token.
- `wood-secrets unlock --gui` is available on macOS where `osascript` is present.

### `wood-project`

Deterministic Wood Agents execution surface for story, implementation, and release, plus
supporting project context commands.

Default paths:

- `project.json` at the selected project root
- Wood home at `WOOD_HOME` when set, otherwise `~/.wood`
- `config.toml`, `packs/templates`, `packs/references`, `packs/agents`, `tools`, `scripts`, `cache`, and `state` under the resolved Wood home

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
  "wood_home": "/home/user/.wood",
  "wood_config_file": "/home/user/.wood/config.toml"
}
```

Optional fields:

- `linked_repositories` may be provided as either an object keyed by repository name or a list of `{ "name", "path" }` objects. Each linked repository path must be absolute, may include an optional `role`, and is validated when present.
- `wood-project validate` performs non-mutating availability checks for the configured project root, Wood home resources, and linked repository directories. JSON output includes per-path access details.
- Mutating `wood-project` commands preflight the required parent directories and fail early with clear errors when the Wood home is unavailable or not writable.
- No `wood-project` command creates or requires a project-local `.wood/` directory.

Primary loop-specific command groups:

- `wood-project story show <id>` inspect one work package plus description and relation context
- `wood-project story next <root-work-package-id>` discover the next dependency-ready Story
- `wood-project story set-status <id> <status>` preview or apply Story status transitions
- `wood-project story create-branch <id>` preview or apply Story branch preparation
- `wood-project implementation export <initiative-id>` export a read-only implementation workbook
  snapshot
- `wood-project implementation plan <workbook.xlsx>` build a deterministic implementation plan
  without applying it
- `wood-project implementation apply <workbook.xlsx>` apply an approved implementation plan to
  OpenProject
- `wood-project release check` run read-only release readiness checks
- `wood-project release bump <patch|minor|major|X.Y.Z>` preview or apply a static
  `pyproject.toml` version bump
- `wood-project release tag` preview or create local tag `v<project.version>`
- `wood-project release github-create` preview or create a GitHub release from an existing tag

Supporting context and precondition command groups:

- `wood-project user` inspect the authenticated OpenProject user
- `wood-project project [project-id]` inspect an OpenProject project
- `wood-project resource install <path>` preview or apply resource installation
- `wood-project resource inspect <kind> <name>` inspect installed resource metadata
- `wood-project resource path <kind> <name>` resolve an installed resource path
- `wood-project init`, `show`, `validate`, and `link repo` manage local project metadata

Legacy compatibility policy:

- Keep one authoritative implementation path for each capability.
- Do not add hidden aliases, deprecated command shims, or duplicate legacy modules.
- Internal callers should use `story`, `implementation`, and `release` directly.
- Removed backlog or loop-specific route names are not preserved as CLI wrappers.

All commands:

- `wood-project init` preview the resolved project metadata
- `wood-project init --apply` write `project.json` and create required directories
- `wood-project link repo <path>` preview linking an implementation repository to the project
- `wood-project link repo <path> --apply` persist a linked repository entry in `project.json`
- `wood-project resource install <path>` preview installing a versioned global resource
- `wood-project resource install <path> --apply` validate and install a resource into the owned Wood home directory
- `wood-project resource inspect <kind> <name>` inspect installed metadata and verify the stored digest
- `wood-project resource path <kind> <name>` resolve an installed resource path through the stable CLI contract
- `wood-project user` inspect the authenticated OpenProject user
- `wood-project project [project-id]` inspect an OpenProject project, defaulting to the configured project
- `wood-project story show <id>` inspect one work package plus description and relation context
- `wood-project story next <root-work-package-id>` discover the next dependency-ready Story
- `wood-project implementation export <initiative-id>` export a read-only implementation workbook snapshot
- `wood-project implementation plan <workbook.xlsx>` build a deterministic implementation plan without applying it
- `wood-project implementation apply <workbook.xlsx>` apply an approved implementation plan to OpenProject
- `wood-project release check` run read-only release readiness checks
- `wood-project release bump <patch|minor|major|X.Y.Z>` preview a static `pyproject.toml` version bump
- `wood-project release bump <patch|minor|major|X.Y.Z> --apply` apply an approved version bump
- `wood-project release tag` preview creating local tag `v<project.version>`
- `wood-project release tag --apply` create the approved local release tag
- `wood-project release github-create` preview creating a GitHub release from an existing tag
- `wood-project release github-create --apply` create the approved GitHub release
- `wood-project show` read and print the current `project.json`
- `wood-project validate` verify the current `project.json` schema, required directories, path relationships, linked repository paths, and mounted path accessibility

Options for `init`:

- `--project-id <value>` provide an explicit project ID instead of generating a UUID
- `--project-slug <value>` provide an explicit slug instead of deriving one from the root folder
- `--wood-home <path>` override the user-global Wood home, defaulting to `WOOD_HOME` or `~/.wood`
- `--json` emit JSON envelope output

Options for `link repo`:

- `<path>` repository directory to link
- `--name <value>` override the stored repository name, default the repository directory name
- `--role <value>` store an optional repository role such as `app`, `library`, or `infra`
- `--apply` write the updated `project.json`
- `--json` emit JSON envelope output

Options for OpenProject inspection commands:

- `--config-path <path>` override the user-global `wood-config` file
- `--profile <name>` read OpenProject settings from a specific profile
- `--json` emit the shared JSON envelope for each inspection command

OpenProject inspection reads `integrations.openproject.url`,
`integrations.openproject.project_id`, `integrations.openproject.token_ref`, and optional
`integrations.openproject.user_agent` from the active `wood-config` profile. The token reference is
resolved in memory through `wood-secrets`; commands perform only `GET` requests and never print the
resolved token.

#### `wood-project story`

Story workflow commands for deterministic OpenProject Story intake and branch preparation.

Commands:

- `wood-project story show <id>` inspect a Story and its relation context
- `wood-project story next <root-work-package-id>` discover the next dependency-ready Story
- `wood-project story set-status <id> <status>` preview a Story status update
- `wood-project story set-status <id> <status> --apply` apply the approved status update
- `wood-project story create-branch <id> --title <title>` preview the branch operation
- `wood-project story create-branch <id> --title <title> --apply` apply the branch operation

Options:

- `--config-path <path>` override the user-global `wood-config` file for OpenProject commands
- `--profile <name>` read OpenProject settings from a specific profile
- `--json` emit deterministic JSON for agent workflows
- `--status <name>` choose the candidate status for `next`, default `New`
- `--type <name>` choose the work package type for `next`, default `Story`
- `--page-size <count>` control OpenProject collection reads for `next`, default `1000`
- `--allow-dirty` allow branch creation or checkout with a dirty worktree after explicit approval

Story workflow behavior:

- `wood-project story next` is read-only and performs only OpenProject `GET` requests.
- `wood-project story set-status` is preview-by-default; mutation requires `--apply`.
- `wood-project story create-branch` is preview-by-default; git mutation requires `--apply`.
- `wood-project story create-branch` refuses dirty worktrees unless `--allow-dirty` is set.
- Story branches use `feature/op-<openproject-id>-<slug>`.
- JSON output uses the shared command envelope and places Story details, branch names, previews,
  and mutation results under `data`.

Examples:

```bash
wood-project story next 208 --json
wood-project story set-status 301 "In progress" --json
wood-project story set-status 301 "In progress" --apply --json
wood-project story create-branch 301 --title "Productize Story commands" --json
wood-project story create-branch 301 --title "Productize Story commands" --apply --json
```

#### `wood-project implementation`

Implementation workbook commands for exporting OpenProject snapshots, planning workbook publication,
and applying approved plans.

Commands:

- `wood-project implementation export <initiative-id>` export `implementation_workbook.json` and `implementation_workbook.xlsx`
- `wood-project implementation plan <workbook.xlsx>` build the deterministic OpenProject implementation plan without applying it
- `wood-project implementation apply <workbook.xlsx>` apply the approved implementation plan to OpenProject

Options:

- `--env-file <path>` read resolved OpenProject settings from a specific file, default `.env.resolved`
- `--output-dir <path>` write export artifacts to a specific directory, default `/tmp/wood-tools/implementation-workbook`
- `--story-type <name>` choose the exported story type, default `Story`
- `--epic-type <name>` choose the hierarchy epic type, default `Epic`
- `--closed-status <name>` provide a closed status name for export; repeatable
- `--story-id-field <key>` read an optional OpenProject field into `Story ID`
- `--requirement-ids-field <key>` read an optional OpenProject field into `Requirement IDs`
- `--page-size <count>` control OpenProject collection reads for export, default `500`
- `--sheet-name <name>` choose the workbook tab for planning or apply, default `Implementation`
- `--initiative-id <id>` optionally override the workbook-derived root work-package ID for planning or apply
- `--json` emit deterministic JSON for agent workflows

For local OpenProject commands, authenticate with Infisical and run the command
through `scripts/dev`. The shared OpenProject token comes from
`Infrastructure Dev/dev:/openproject`; no `.env` or `.env.resolved` file is
required. The launcher uses the
checked-in, nonsecret OpenProject profile at `.wood/wood-tools/config.json` for
`wood-project user`, `project`, and `story` commands.

```bash
infisical login --domain=https://dev-infisical.woodhost.cloud/api --method=user --interactive
scripts/dev uv run --active wood-project implementation export 208 --json
scripts/dev uv run --active wood-project user --json
```

Implementation workbook behavior:

- `implementation export` is read-only against OpenProject and writes local snapshot artifacts.
- `implementation export <initiative-id>` still targets an existing OpenProject hierarchy.
- `implementation plan` performs no OpenProject mutations.
- `implementation apply` is the only implementation-workbook mode that mutates OpenProject.
- Planning and apply resolve the target Project and Root Work Package from workbook metadata.
- Existing root work packages are reused by ID or unique subject/type match; missing root work
  packages are planned for creation.
- The workbook is treated as the desired release state: Root Work Package, Versions, Epics, Stories, and
  predecessor relations are reused when an ID or unique deterministic match exists, and are
  planned for creation otherwise.
- Ambiguous matches stop planning instead of guessing.
- Workbook rows with explicit `OpenProject ID` values must resolve to Stories beneath the resolved
  Root Work Package; stale IDs outside that tree block planning instead of mutating unrelated work.
- Successful apply reads OpenProject writes back before reporting them as verified, writes confirmed
  Story OpenProject IDs back to the `OpenProject ID` workbook column, and writes confirmed root
  metadata back to the workbook so repeated runs are idempotent.
- Google Drive synchronization remains outside the `wood-project implementation` command.

Examples:

```bash
wood-project implementation export 208 --output-dir /tmp/wood-tools/implementation-workbook/current --json
wood-project implementation plan /tmp/wood-tools/implementation-workbook/current/implementation_workbook.xlsx --json
wood-project implementation apply /tmp/wood-tools/implementation-workbook/current/implementation_workbook.xlsx --json
```

#### `wood-project release`

Release workflow commands for deterministic version, tag, and GitHub release preparation.

Commands:

- `wood-project release check` run read-only readiness checks for the current release
- `wood-project release bump <patch|minor|major|X.Y.Z>` preview a `[project].version` update in `pyproject.toml`
- `wood-project release bump <patch|minor|major|X.Y.Z> --apply` apply the approved version update
- `wood-project release tag` preview creating local git tag `v<project.version>`
- `wood-project release tag --apply` create the approved local git tag
- `wood-project release github-create` preview a GitHub release for `v<project.version>`
- `wood-project release github-create --apply` create the approved GitHub release

Options:

- `--pyproject <path>` read version metadata from a specific `pyproject.toml`, default `pyproject.toml`
- `--version <X.Y.Z>` use an explicit version for `check`, `tag`, or `github-create`
- `--root-work-package-id <id>` include OpenProject story readiness under a root work package during `check`
- `--openproject-version <name>` limit `release check` story readiness to one OpenProject Version
- `--config-path <path>` override the user-global `wood-config` file for `release check`
- `--profile <name>` read OpenProject settings from a specific profile for `release check`
- `--type <name>` choose the story work-package type for `release check`, default `Story`
- `--page-size <count>` control OpenProject collection reads for `release check`, default `1000`
- `--allow-dirty` allow `release tag --apply` with a dirty worktree after explicit approval
- `--generate-notes` pass GitHub's generated release notes flag to `github-create`
- `--notes-from-history` build release notes from commit history before `github-create`
- `--json` emit deterministic JSON for agent workflows

Release workflow behavior:

- `wood-project release check` is read-only and returns structured readiness JSON with
  pass/block/skip checks for version state, repository state, local tag conflicts, GitHub release
  conflicts when available, and OpenProject incomplete/blocked stories when a root work package is
  provided.
- `wood-project release bump`, `release tag`, and `release github-create` are preview-by-default.
- Mutation requires `--apply`; use separate approval for version edits, tag creation, and GitHub release creation.
- `release tag` refuses dirty worktrees unless `--allow-dirty` is set.
- `release github-create --apply` requires the local tag to exist and the GitHub CLI to be authenticated.

Examples:

```bash
wood-project release check --json
wood-project release check --root-work-package-id 208 --openproject-version "R3 — Deterministic Workflow CLI" --json
wood-project release bump patch --json
wood-project release bump patch --apply --json
wood-project release tag --json
wood-project release tag --apply --json
wood-project release github-create --notes-from-history --json
wood-project release github-create --notes-from-history --apply --json
```

### Story Approval Gates

Story keeps read-only discovery and dry-run previews automatic, including repository
inspection, next-story lookup, status-change previews, and branch previews. After discovery, one
explicit start-work approval may cover the normal implementation batch for the current Story:
setting the OpenProject Story to In Progress, preparing the Story branch, editing scoped local
files, and running relevant local checks.

Separate approval is still required when the working tree already has unrelated changes and for
finalization or external side effects such as commit, push, merge, deploy, Story closure,
implementation workbook sync, Google Drive updates, archival, or notifications.

Resource manifest format:

`wood-project resource install` expects a source directory containing `wood-resource.json`. The
resource payload digest is computed over every file except `wood-resource.json`, using relative
paths and file bytes.

```json
{
  "schema_version": 1,
  "kind": "script",
  "name": "demo-helper",
  "version": "1.0.0",
  "digest": "sha256:<64 lowercase hex characters>",
  "compatibility": {
    "wood_tools": ">=0.1.1"
  },
  "helper_contract": {
    "deterministic": true,
    "input": "JSON object on stdin",
    "output": "JSON object on stdout",
    "errors": "Non-zero exit with JSON error envelope"
  }
}
```

Supported resource kinds:

- `template` installs to `packs/templates/<name>/<version>`
- `reference` installs to `packs/references/<name>/<version>`
- `agent` installs to `packs/agents/<name>/<version>`
- `tool` installs to `tools/<name>/<version>`
- `script` installs to `scripts/<name>/<version>`

Helper resources (`tool` and `script`) must include `helper_contract` so projects and agent packs
can call the stable `wood-project resource path ...` interface instead of depending on absolute
`~/.wood/tools/` or `~/.wood/scripts/` paths. Reinstalling the same identity, version, and digest is
safe and returns the existing metadata; conflicting installed files fail before activation.

Template pack resources (`kind: "template"`) must include a declarative, versioned
`template_pack` contract. Resolution uses this precedence:

1. `--source-dir` explicit source when provided
2. exact project lock entries in `project.json.template_packs`
3. installed user packs under the configured Wood home
4. built-in packs shipped with `wood-template`

Project lock entries store resource identity and digest only; they must not store absolute
`~/.wood/` paths.

```json
{
  "schema_version": 1,
  "kind": "template",
  "name": "service-app",
  "version": "1.0.0",
  "digest": "sha256:<64 lowercase hex characters>",
  "compatibility": {
    "wood_tools": ">=0.1.1"
  },
  "template_pack": {
    "schema_version": 1,
    "name": "service-app",
    "version": "1.0.0",
    "variables": {
      "project-name": {
        "type": "string",
        "required": true,
        "description": "Display name for the generated project"
      }
    },
    "operations": [
      {
        "type": "render",
        "template": "templates/README.md.tmpl",
        "output": "README.md",
        "overwrite": "safe",
        "safe_overwrite": {
          "strategy": "if-unchanged"
        }
      }
    ],
    "validation": [
      {
        "rule": "project-name",
        "message": "Project name is required."
      }
    ]
  }
}
```

`wood-template plan <name>` previews the base template in the current directory without creating
files. It reports the exact template source, version, digest, inferred variables, planned render
operations, overwrite decisions, and output conflicts.

`wood-template generate <name>` previews generation and reports that approval is required.
`wood-template generate <name> --apply` renders the base template into the current directory. It infers
`project-name`, `package-name`, and `package-module` from the current folder name, fails before
writing if a planned output would be overwritten unexpectedly, and works with built-in templates
even before `project.json` exists. Application stages and verifies the complete plan before
activation, restores the prior project state after a partial write failure, and is a no-op when
repeated against unchanged generated files.

Successful application writes a root-level `wood.lock.json` with the template name, source, exact
version and digest, Wood Tools version, declared inputs, applicable reference and agent pack
versions, and generated-file digests. The lock uses portable resource identities rather than
absolute Wood home paths, and template application never creates a project-local `.wood/`.

`wood-template list` prints only the resolved template pack names. Use
`wood-template list <name> --info` or `wood-template show <name>` to inspect one template in
detail, including source, exact version, digest, variables, planned outputs, operations, and
validation rules without exposing installed Wood home paths. JSON output is available for both
the concise list and detailed inspection modes.

Built-in first-party templates:

- `python-cli`: Python command-line application using uv, argparse, pytest, ruff, and pre-commit
- `python-library`: importable Python library package using uv, pytest, ruff, and pre-commit
- `python-api-service`: FastAPI API service using Pydantic Settings, SQLAlchemy, Alembic, SQLite, pytest, pytest-asyncio, httpx, ruff, and pre-commit
- `python-web-app`: `frontend/` React, Vite, TypeScript, Tailwind, Radix UI, lucide-react, and react-router-dom app with a `backend/` FastAPI, SQLAlchemy, and Alembic service
- `software-planning`: copyable requirements, change-order, implementation-backlog, and story-description files

These built-ins intentionally include only base files for now. Optional Docker, devcontainer,
PostgreSQL, and CI variants can be added later once the command UX supports them directly.

Examples:

```bash
wood-project init
wood-project init --apply
wood-project init --project-id proj-123 --project-slug demo-app --apply
wood-project init --wood-home /tmp/wood-home --apply
wood-project link repo ../shared-lib --role library --apply
wood-project resource install ./packs/demo-helper
wood-project resource install ./packs/demo-helper --apply --json
wood-project resource inspect script demo-helper --version 1.0.0 --json
wood-project resource path script demo-helper --version 1.0.0 --relative-path run.py --json
wood-template plan python-cli --json
wood-template generate python-cli
wood-template generate python-cli --apply
wood-template list --json
wood-template list python-cli --info --json
wood-project user --json
wood-project story 292 --json
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

### Audit Logging

Commands that emit the shared JSON envelope also write a metadata-only audit event.
Events default to JSON Lines at `~/.wood/state/logs/audit.jsonl`, or
`$WOOD_HOME/state/logs/audit.jsonl` when `WOOD_HOME` is set. Each event records
the timestamp first, followed by the CLI name, command, redacted full command,
command intent summary, outcome, mutation status, approval-gate status,
relevant target type and path, redacted reason or error evidence, and actor.
Audit events do not include command payloads, environment dumps, secret values,
resource lifecycle tracking, lineage, health state, or reproducibility inputs.

Audit logging is best-effort: Wood Tools commands do not depend on audit files
being present or writable.

Environment controls:

- `WOOD_AUDIT_LOG=disabled` turns audit logging off.
- `WOOD_AUDIT_LOG=console` writes audit events to stderr instead of a file.
- `WOOD_AUDIT_LOG_PATH=<path>` overrides the audit JSONL file path.
- `WOOD_AUDIT_LOG_MAX_BYTES=<bytes>` sets the rotation threshold, defaulting to
  `1000000`.
- `WOOD_AUDIT_LOG_MAX_FILES=<count>` bounds rotated files, defaulting to `5`.

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

## Development Commands

Run tests:

```bash
uv run --active pytest
uv run --active pytest tests/test_wood_config.py
```

Run lint and format checks:

```bash
uv run --active ruff check .
uv run --active ruff format --check .
```

The `CI` GitHub Actions workflow runs the same lint, formatting, test, and architecture checks on
pushes and pull requests.

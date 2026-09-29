# Wood Tools

Wood Tools v2 is a single `wood` command for deterministic agent workflows. Secret readiness and aggregate diagnostics are part of [WP-397](https://projects.woodhost.cloud/work_packages/397). Project discovery and Implementation Workbook import are part of [WP-398](https://projects.woodhost.cloud/work_packages/398).

## Install and inspect

Use Python 3.14 and uv:

```sh
uv sync --active
uv run --active wood contract --json
```

`wood` is the only installed public executable. The v1 project, resource, and template command routers and their unused packages have been removed. The OpenProject transport and workbook logic used by the v2 commands remain internal.

## Repository and operations inspection

`wood repo info --json` reads `.github/release.toml` and workflow files to identify repository type, validation checks, release configuration, and deployment applicability. `wood repo standards --json` reports required, optional, and not-applicable conventions. `wood repo validate --json` runs the declared Python and Node checks in a disposable checkout, keeping full logs at the returned paths so hooks and builds do not edit the working tree. It uses the repository's existing virtual environment for Python checks; missing tools are reported as unavailable or unsupported.

`wood ci status --json` reports the latest centralized validation run and marks it stale when its commit differs from the local checkout. `wood ci failures --json` returns failed job and step names with GitHub links. `wood deploy status --json` reads GitHub deployment and latest release evidence, or returns `not_applicable` for a repository without a deployment workflow. Use `--environment <name>` when several environments exist. GitHub inspection requires `gh` authentication and repository access.

## Infisical runtime

The checked-in `.infisical.json` selects the local Infisical project and domain. Infisical injects the OpenProject URL and token into the child process; Wood Tools only reports readiness and variable presence. Discover project and initiative IDs with `wood project list --json` when needed:

```sh
infisical run --env=dev --path=/openproject -- uv run --active wood doctor --json
```

Use `wood secret status --json` for Infisical CLI, context, authentication, and OpenProject prerequisites; `wood secret requirements --json` for required names; and `wood secret check --json` for injected variable presence. `--name NAME` can be repeated on `check` and `requirements`. These commands do not retrieve or print values. `wood doctor` adds repository, OpenProject connectivity, tool, and Python checks. An unavailable check exits 4 and includes a concise next action.

## OpenProject projects and workbook import

`wood project list --json` lists accessible projects with their Initiative associations. Use `--initiative <id|name>` to filter and `--offset` to page through more than 50 projects. `wood project status <id|identifier|exact name> --json` reports R# planning versions and Story counts; `--initiative <id|name>` scopes the Story counts. Ambiguous names return candidate IDs.

`wood project import-workbook <path> --json` validates the 21-column `Implementation` sheet and previews the ordered project/root/version/Epic/Story/relation plan. The preview includes a plan hash and up to 50 operations; use `--operation-offset` to inspect subsequent operations. To apply the reviewed plan, rerun with `--apply --plan-hash <hash>`. The command resolves IDs again, rejects a stale plan hash, applies changes in dependency order, verifies writes, and records created IDs in the workbook. `--project` and `--initiative` accept a numeric ID, stable identifier where available, or exact name. Inject `OPENPROJECT_URL` and `OPENPROJECT_API_TOKEN` via Infisical for these commands; `OPENPROJECT_PROJECT_ID` is optional.

## Story workflow

Run Story commands under the same Infisical environment. `wood story list <project-or-initiative-ref> --json` filters with `--status` and `--version` and pages with `--offset`. `wood story get <id> --json` returns the implementation packet, relations, and description chunks; use `--offset` to read further chunks. `wood story next <initiative-ref> --json` selects a dependency-ready Story in the earliest active R# release, prioritizing an eligible Story already In progress, then the lowest ID. Closed and Rejected Stories are terminal; Rejected predecessors do not satisfy dependencies. Blocked and unversioned Stories are ineligible. An unfinished earlier release holds later releases.

`wood story create` requires project, initiative, Epic, open R# version, subject, goal, requirement IDs, and at least one `--acceptance` value. It previews by default; `--apply` creates the Story. `wood story set-status <id> <status>` checks the live Story, lock version, and allowed transition before preview or apply. `wood story start <id>` checks version and predecessors, then prepares the standard local branch for the packet's Primary Repository. `wood story block <id> --reason <text>` records a supported blocked state (`Blocked` or `On hold`) and activity; resume with `set-status <id> "In progress"`. `wood story complete <id> --evidence <json-file>` validates the supplied repository checks and CI run before closing. These lifecycle commands preview by default and mutate only with `--apply`.

For a Story with a Primary Repository, completion evidence must contain nonempty `repository_checks` with `{ "name": "...", "status": "passed" }` entries and `ci` with `{ "url": "https://github.com/OWNER/REPO/actions/runs/ID", "status": "passed" }`. On apply, the CLI reads that GitHub Actions run and requires a completed, successful conclusion. A Story without a repository requires `validation_summary`. The closed status defaults to `Closed` and can be set with `OPENPROJECT_STORY_CLOSED_STATUS`; it must be marked closed in OpenProject. The CLI returns the next action and does not commit, push, merge, open a PR, or create a release.

## JSON and exits

Every informational command supports `--json`. The version 2 envelope has `schema_version`, `command`, `status`, `mutation`, `requires_approval`, `summary`, bounded `data`, `warnings`, `errors`, and `next_actions`. `mutation` is `read-only`, `preview`, or `mutating`. Audit events omit secret values and audit-file failure does not fail a command.

| Status | Exit code | Meaning |
| --- | ---: | --- |
| `success` | 0 | Completed |
| `not_applicable` | 0 | Capability does not apply |
| `error` | 1 | Operation failed |
| `invalid` | 2 | Input is invalid |
| `blocked` | 3 | A required approval or condition is pending |
| `unavailable` | 4 | A required dependency is unavailable |
| `ambiguous` | 5 | A selector matches more than one target |
| `stale` | 6 | Evidence refers to an older release or commit |
| `unsupported` | 7 | The release contract or check is not supported |

## Development and release

The declared checks are Ruff, Ruff format, mypy, pytest, coverage of the v2 `wood` foundation, and pre-commit. Run them with `uv run --active ...`; the commands are listed in [AGENTS.md](AGENTS.md).

[Validation](.github/workflows/validate.yml) and [release](.github/workflows/release.yml) call the current `SpencerRWood/workflows@v1` contract with capabilities in [.github/release.toml](.github/release.toml). Python semantic-release owns version changes, tags, and GitHub Releases. Wood Tools contains no manual release mutation command.

# Wood Tools

Wood Tools v2 is a single `wood` command for deterministic agent workflows. Secret readiness and aggregate diagnostics are part of [WP-397](https://projects.woodhost.cloud/work_packages/397). Project discovery and Implementation Workbook import are part of [WP-398](https://projects.woodhost.cloud/work_packages/398).

## Install and inspect

Use Python 3.14 and uv:

```sh
uv sync --active
uv run --active wood contract --json
```

`wood` is the only installed public executable. The v1 commands are intentionally unavailable. Existing domain packages remain internal while their replacement capabilities are built in later Stories.

## Infisical runtime

The checked-in `.infisical.json` selects the local Infisical project and domain. Infisical injects secret values into the child process; Wood Tools only reports readiness and variable presence. Set the public OpenProject context and run the CLI directly under Infisical:

```sh
OPENPROJECT_URL=https://projects.woodhost.cloud \
OPENPROJECT_PROJECT_ID=3 OPENPROJECT_INITIATIVE_ID=208 \
infisical run --env=dev --path=/openproject -- uv run --active wood doctor --json
```

Use `wood secret status --json` for Infisical CLI, context, authentication, and OpenProject prerequisites; `wood secret requirements --json` for required names; and `wood secret check --json` for injected variable presence. `--name NAME` can be repeated on `check` and `requirements`. These commands do not retrieve or print values. `wood doctor` adds repository, OpenProject connectivity, tool, and Python checks. An unavailable check exits 4 and includes a concise next action.

## OpenProject projects and workbook import

`wood project list --json` lists accessible projects with their Initiative associations. Use `--initiative <id|name>` to filter and `--offset` to page through more than 50 projects. `wood project status <id|identifier|exact name> --json` reports R# planning versions and Story counts; `--initiative <id|name>` scopes the Story counts. Ambiguous names return candidate IDs.

`wood project import-workbook <path> --json` validates the 21-column `Implementation` sheet and previews the ordered project/root/version/Epic/Story/relation plan. The preview includes a plan hash and up to 50 operations; use `--operation-offset` to inspect subsequent operations. To apply the reviewed plan, rerun with `--apply --plan-hash <hash>`. The command resolves IDs again, rejects a stale plan hash, applies changes in dependency order, verifies writes, and records created IDs in the workbook. `--project` and `--initiative` accept a numeric ID, stable identifier where available, or exact name. Inject `OPENPROJECT_URL` and `OPENPROJECT_API_TOKEN` via Infisical for these commands; `OPENPROJECT_PROJECT_ID` is optional.

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

## Development and release

The declared checks are Ruff, Ruff format, mypy, pytest, coverage of the v2 `wood` foundation, and pre-commit. Run them with `uv run --active ...`; the commands are listed in [AGENTS.md](AGENTS.md).

[Validation](.github/workflows/validate.yml) and [release](.github/workflows/release.yml) call the current `SpencerRWood/workflows@v1` contract with capabilities in [.github/release.toml](.github/release.toml). Python semantic-release owns version changes, tags, and GitHub Releases. Wood Tools contains no manual release mutation command.

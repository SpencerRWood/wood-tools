# Wood Tools

Wood Tools provides the `wood` command for deterministic agent workflows.

## Install and inspect

Use Python 3.14 and uv:

```sh
uv sync --active
uv run --active wood contract --json
```

`wood` is the only installed public executable. OpenProject transport and workbook logic remain internal to the supported commands.

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

Run Story commands from the target repository under the same Infisical environment, using an installed `wood` executable. In the Wood Tools checkout, use `uv run --active --frozen wood` to exercise the checked-out code. The repository root `pyproject.toml` may optionally declare:

```toml
[tool.wood.openproject]
initiative_id = 208
project_id = 3 # optional; verified against the Initiative's OpenProject project
```

`wood story next [ref] --json` and `wood story list [ref] --json` use that Initiative when `ref` is omitted. Pass an explicit project or Initiative reference when the repository has no mapping, or to override one. The CLI finds the Git root from the working directory, regardless of where `wood` or its virtual environment is installed. Missing or malformed context returns an actionable error. `list` filters with `--status` and `--version` and pages with `--offset`. `wood story get <id> --json` returns the implementation packet, relations, and description chunks; use `--offset` to read further chunks. `next` selects a dependency-ready Story in the earliest active R# release, prioritizing an eligible Story already In progress, then the lowest ID. Closed and Rejected Stories are terminal; Rejected predecessors do not satisfy dependencies. Blocked and unversioned Stories are ineligible. An unfinished earlier release holds later releases.

`wood story create` requires project, initiative, Epic, open R# version, subject, goal, requirement IDs, and at least one `--acceptance` value. It previews by default; `--apply` creates the Story. `wood story set-status <id> <status>` checks the live Story, lock version, and allowed transition before preview or apply. `wood story start <id>` checks version and predecessors, then prepares the standard local branch for the packet's Primary Repository. `wood story block <id> --reason <text>` records a supported blocked state (`Blocked` or `On hold`) and activity; resume with `set-status <id> "In progress"`. `wood story complete <id> --evidence <json-file>` validates the supplied repository checks and CI run before closing. These lifecycle commands preview by default and mutate only with `--apply`.

After `story complete --apply` closes a Story, it reads its current parent and all descendant Stories from OpenProject. If the parent is an Epic and every Story is complete or Rejected, it automatically closes the Epic using the configured completion status (`OPENPROJECT_STORY_CLOSED_STATUS`, default `Closed`). Any open, In progress, blocked, or otherwise incomplete Story keeps the Epic open. Completion uses OpenProject's closed-status definitions, excluding Rejected from successful completion. Other child types do not block this Story-based check. The text summary reports automatic Epic completion; JSON includes `data.epic` with the outcome and any incomplete Story IDs. Preview does not predict Epic completion; the check runs after applying the Story transition.

Repeating completion of an already-complete Story safely rechecks its Epic without rewriting the Story. An already-complete Epic is left alone. Evidence is still required on retries. If the Epic check or update fails after the Story closes, the command reports that partial outcome; rerun with the same evidence to retry against current OpenProject state. Status writes use OpenProject lock versions; the Story and Epic updates are separate API operations.

### Generated delivery inputs

`wood repo validate --json` saves a complete `validation.json` record and returns its path as `data.validation_file`, alongside the full check logs. The record fingerprints the validated Git inputs, including new and modified files before commit. After the implementation PR merges, generate delivery inputs with:

```text
wood story evidence <id> --validation <validation-file> --pr <number> --ci-run <run-id> --json
wood story evidence <id> --validation <validation-file> --pr <number> --ci-run <run-id> --apply --json
wood story activity add <id> --evidence <evidence-file> --json
wood story activity add <id> --evidence <evidence-file> --apply --json
wood story complete <id> --evidence <evidence-file> --json
wood story complete <id> --evidence <evidence-file> --apply --json
```

Inject OpenProject credentials through Infisical. Use `data.evidence_file` from generation for the activity and completion commands. `data.update_file` is the generated implementation-update Markdown; callers can also use the existing activity `--file` option. Evidence generation previews without creating files and `--apply` writes only local files. PR management remains through `gh`.

Generation requires a clean checkout containing the merged PR, a `feature/op-<id>-` source branch for the selected Story, a matching Primary Repository, all repository-required passing checks and their logs, and a successful `validate.yml` run on the PR source or merge SHA. The validated content fingerprint must match the PR source revision; committing unchanged validated files does not require another validation run. The PR source revision must exist locally (fetch it if needed). Generated evidence binds the Story, repository, PR, source and merge revisions, validation record, and CI run. Both `--evidence` consumers recheck live PR/CI results and the generated files before preview or apply. Altered or missing inputs must be regenerated. The generated update includes the Story subject, PR, revisions, checks, and CI; separately record material limitations or release/deployment details when applicable.

Configure all validation logs and generated delivery files in the current repository's root `pyproject.toml`:

```toml
[tool.wood.workflow]
output_directory = "/private/tmp"
```

The default is `/private/tmp` when omitted. Relative paths resolve from the repository root; each operation creates a unique private run directory. JSON and update files have owner-only permissions. An invalid or unwritable path produces a structured error instead of silently changing directories. Choose a writable override on systems without `/private/tmp`; ignore a repository-relative output directory in Git. Disposable test checkouts remain temporary. Keep the validation record and logs through delivery: operating-system cleanup may remove temporary files. Regenerate the evidence if its files disappear; if its validation record or logs disappear, validate the exact PR implementation contents again (not unrelated later release changes). Existing manually supplied completion evidence and activity files remain available for unsupported workflows.

`wood story activity add <id> --file <path> --json` previews a UTF-8 comment of up to 4096 bytes. Add `--apply` to post it and verify the activity's Story and exact comment by readback. An `Implementation update (WP-<id>)` first-line heading makes retries inspect all activity pages: identical text reuses the existing activity ID, while different text blocks a duplicate. This command posts an activity only; Story closure still uses `wood story complete` and its validation gates.

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

Run `wood repo validate --json` for the checks declared by this repository. Inspect the returned log paths for failures.

[Validation](.github/workflows/validate.yml) and [release](.github/workflows/release.yml) call the current `SpencerRWood/workflows@v1` contract with capabilities in [.github/release.toml](.github/release.toml). Python semantic-release owns version changes, tags, and GitHub Releases. Wood Tools contains no manual release mutation command.

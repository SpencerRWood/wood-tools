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

`wood delivery status <story-id> --json` reconciles one Story from its source checkout using OpenProject and GitHub authority. It returns `story_id`, normalized `fields` (repository, source revision, PR number/state, CI, release/version, image digest, infrastructure promotion, deployment environment, deployed revision, and runtime verification), `delivery_stage`, `blocker`, and one deterministic `next_action`. A successful query can report blocked delivery: envelope success means reconciliation completed, not that delivery finished. No watch loop or mutation occurs. Use the usual OpenProject credential injection.

The report includes `observed_at` in UTC, a verified `merged_revision`, and a `release_run` field for the current revision-bound release workflow attempt. It queries the latest `release.yml` run for the Story merge, with the verified published release revision as a fallback when no merge run exists. It reads at most 100 jobs from that exact attempt once. `release_jobs` returns at most five unsuccessful or pending jobs with job IDs, links, and up to three failed step names each. Names are capped at 160 characters; truncation of job and step lists is explicit. A failed publication, promotion, or deployment job yields its stage and an exact `gh run view` diagnostic command; a pending attempt yields one wait action. PR and validation blockers take precedence. Disabled semantic release is not applicable; missing, malformed, or inaccessible run evidence is reported explicitly.

Release-run success is workflow evidence only. It never supplies an image digest, a deployment revision, or runtime health on its own. A successful RAG-style release/promotion attempt with no GitHub deployment record still reports unavailable deployment fields. `release_run` is supplemental to the existing release/deployment authorities; absence of a discoverable run does not invalidate independently verified delivery facts. Observation time records this query, not the freshness of a previously published runtime attestation.

Configuration repositories that deploy directly can declare evidence applicability in `.github/release.toml`:

```toml
[delivery]
container_image = false
infrastructure_promotion = false
runtime_verification = true
```

These optional boolean fields default to `true` for repositories with deployment workflows. Explicit `false` reports that field as `not_applicable`; missing required provider evidence remains `unavailable`. Unknown keys and non-boolean values fail closed. Deployed revision is always required when deployment applies. Delivery reconciliation reads both the contract and workflows at the Story's merged revision, so uncommitted or newer policy cannot waive historical requirements. Missing revision-bound files do not fall back to current checkout policy. Consumer policy changes therefore affect subsequent merges, rather than retroactively changing earlier Story observations. Runtime evidence remains independent of successful deployment health steps.

Discovery searches up to 50 Story branch PR candidates; incomplete searches and multiple candidates require `--pr <number>`. Explicit PR selection still verifies the Story branch and repository main. CI is queried for the PR source revision, with merge-revision fallback when no source run exists. A merged PR resolves optional release and deployment links even if CI is blocked. Release and successful deployment revisions must contain the Story merge; promotion and runtime attestations must match the deployed revision and environment. Supply `--environment <name>` for deployment selection. Missing provider evidence stays unavailable; disabled release/deployment workflows are not applicable. The latest release/deployment providers do not search historical delivery records. This point-in-time observation does not replace repository validation or Story completion evidence.

## Infisical runtime

The checked-in `.infisical.json` selects the local Infisical project and domain.
Only `OPENPROJECT_URL` and `OPENPROJECT_API_TOKEN` are globally required.
The URL is non-secret global configuration and may be set in the environment;
the token remains secret-backed and is injected into the child process by Infisical.
Never put the token in `.env`, `pyproject.toml`, configuration files, logs, or artifacts.
Wood Tools does not load token values from files and only reports variable presence.
Discover project and initiative IDs with `wood project list --json`; discovery
works with URL/token alone, including outside a repository or with a malformed mapping.

Repository planning context belongs in the current Git root's `pyproject.toml`:

```toml
[tool.wood.openproject]
project_id = 3
initiative_id = 208
```

Resolution uses supported explicit CLI selectors first, then the repository mapping.
`OPENPROJECT_PROJECT_ID` and `OPENPROJECT_INITIATIVE_ID` are ignored; there are no
environment-ID overrides, deprecated-setting fallbacks, or compatibility wrappers.
Configure missing required mappings or use supported explicit CLI selectors.
These IDs are non-secret and are not runtime prerequisites.
Mapping IDs must be positive integers.
Malformed mappings fail with an actionable error for commands needing that context.
Epic and Release inspection require only project context; Story list/next require
an Initiative mapping unless an explicit reference is supplied. Story get/lifecycle
and delivery use their explicit Story ID and live relationships.

Run readiness checks under the same secret injection:

```sh
infisical run --env=dev --path=/openproject -- uv run --active wood doctor --json
```

Use `wood secret status --json` for Infisical CLI, context, authentication, and OpenProject prerequisites; `wood secret requirements --json` for required names; and `wood secret check --json` for injected variable presence. `--name NAME` can be repeated on `check` and `requirements`. These commands do not retrieve or print values. `wood doctor` adds repository, OpenProject connectivity, tool, and Python checks. An unavailable check exits 4 and includes a concise next action.

## OpenProject projects and workbook import

`wood project list --json` lists accessible projects with their Initiative associations. Use `--initiative <id|name>` to filter and `--offset` to page through more than 50 projects. `wood project status <id|identifier|exact name> --json` reports R# planning versions and Story counts; `--initiative <id|name>` scopes the Story counts. Ambiguous names return candidate IDs.

`wood project import-workbook <path> --json` validates the 21-column `Implementation` sheet and previews the ordered project/root/version/Epic/Story/relation plan. The preview includes a plan hash and up to 50 operations; use `--operation-offset` to inspect subsequent operations. To apply the reviewed plan, rerun with `--apply --plan-hash <hash>`. The command resolves IDs again, rejects a stale plan hash, applies changes in dependency order, verifies writes, and records created IDs in the workbook. `--project` and `--initiative` accept a numeric ID, stable identifier where available, or exact name and override repository context. Repository project/Initiative IDs take precedence over workbook defaults; workbook values remain available when no context is configured. A workbook Project that conflicts with the selected context is rejected. Environment ID variables are ignored. Use the global URL and Infisical-injected token for these commands.

## Deterministic hierarchy provisioning

`wood hierarchy plan --json` discovers an existing Project and plans one Initiative,
open planning Release, and Epic. `wood hierarchy ensure --json` previews the same
plan. Both use `[tool.wood.openproject]` IDs when selectors are omitted. Override
them with `--project <id|identifier|exact-name>` and
`--initiative`, `--release`, or `--epic <id|exact-name>`.
An unknown name proposes creation; an unknown numeric ID fails. Projects must
already exist. Example, under the usual Infisical credential injection:

```sh
wood hierarchy plan --project 3 --initiative Wood-Tools --release R1 \
  --epic 'Deterministic Delivery-Control Surface' --json
wood hierarchy ensure --project 3 --initiative Wood-Tools --release R1 \
  --epic 'Deterministic Delivery-Control Surface' --apply --plan-hash <hash> --json
```

The plan lists reuse IDs and exact POST endpoints and `body_json` for creations.
`planned:initiative` and `planned:release` links are resolved to verified creation
IDs during apply. It also shows the proposed four-ID mapping, mapping file/action,
and `plan_hash`. Names are exact and at most 120 characters; creation requests must
fit the bounded preview. All discovery pages must be complete. Duplicate names
fail as ambiguous; explicit IDs disambiguate existing objects. Closed objects,
shared Releases defined by another Project, and Epics with a different Initiative
parent or Release fail instead of being silently changed or duplicated.

Mutation requires `ensure --apply --plan-hash <reviewed-hash>`. Changed plans or
repository files fail before application. Apply re-discovers before each creation,
reads back both created and reused objects, verifies IDs and relationships, and
rechecks the live hierarchy before atomically saving `project_id`, `initiative_id`,
`release_id`, and `epic_id` in the root `pyproject.toml`. The writer preserves other
tables, mapping keys, and comments. It requires a standard
`[tool.wood.openproject]` table with unquoted integer ID assignments; inline, dotted,
or otherwise unsupported mapping syntax must be converted before planning.
A missing `pyproject.toml` is created only on apply. A retry with a fresh plan
reuses existing objects and leaves an identical mapping untouched.

If an apply fails partway, `data.applied` retains known IDs and their verification
state; inspect a new plan before retrying. Verified objects are retained rather
than rolled back. A local advisory lock serializes cooperating writers for the
same checkout. OpenProject provides no atomic name-uniqueness constraint, so
writers in different checkouts or external clients can race between discovery
and creation. Avoid simultaneous hierarchy provisioning; subsequent discovery
rejects duplicates. Local file preconditions likewise cannot exclude an external
edit between the final check and replacement. These commands provision planning
objects, not Stories or software releases.

## Story workflow

### Epic and planning Release inspection

```sh
wood epic list --json
wood epic get 'Foundation & Standards' --json
wood epic get 389 --json
wood release list --json
wood release get R1 --json
wood release get 20 --json
```

These commands read current OpenProject state and never mutate it. Release means an
OpenProject planning version; publishing tags and GitHub Releases remains owned by
semantic-release. Run under Infisical, using the checkout's `uv run --active --frozen wood`
during development. Project context comes from the repository's
`[tool.wood.openproject].project_id`; override it with `--project <id>`.
Lookup accepts numeric IDs or exact names within that project. Duplicate names
return an ambiguous result with candidate IDs. Other work-package types cannot be
selected as Epics. Both lists accept `--status <name>` and `--offset <n>`.

All API pages are fetched before filtering or calculating totals. Results show at
most 50 rows with `total`, `offset`, and `next_offset`; Epic get uses `--offset` for
child Stories. Epic get reports live child Story statuses, incomplete counts,
`completion_ready`, and `already_complete`. Closed status definitions and Rejected
handling are shared with Story completion. Unknown or active statuses block
readiness, including on later pages; an Epic without Stories is not ready. Already
complete Epics are still inspected without writes. Missing API pages fail the
command instead of reporting readiness. These reads are observations, not a
transactional guarantee: Story completion always rechecks live state before writing.
Release inspection reports status and available start/end dates.

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

### Repository verification

`wood repo verify --json` runs repository-owned runtime/application checks declared in the root `pyproject.toml`. Wood Tools treats each command's exit code as its result; it does not interpret application output or implement application-specific verification. This is separate from `wood repo validate` and its development checks.

```toml
[tool.wood.verify]
version = 1
retry_safe = true

[[tool.wood.verify.checks]]
name = "runtime"
argv = ["uv", "run", "--active", "--frozen", "python", "scripts/verify_runtime.py"]
required = true
timeout_seconds = 30
directory = "."

[[tool.wood.verify.checks]]
name = "reconciliation"
argv = ["uv", "run", "--active", "--frozen", "python", "scripts/verify_reconciliation.py"]
required = false
```

The repository supplies these scripts. `retry_safe = true` is the repository author's explicit promise that all checks are safe to repeat, including after partial failure; checks should inspect state or use idempotent operations. Wood Tools does not infer or enforce application side-effect semantics and does not retry automatically. It validates the entire contract before starting anything and invokes argument arrays directly without a shell. Commands inherit the invoking environment, use no interactive stdin, and run in declaration order from their declared directory. Directories must exist within the repository, including after resolving symlinks. Unknown fields fail validation.

Declare 1–20 checks with unique names of at most 60 letters, digits, underscores, or hyphens, starting with a letter or digit. Each `argv` contains 1–50 nonempty strings of at most 500 characters. `required` defaults to true; `directory` defaults to `.`; `timeout_seconds` defaults to 30 and must be an integer from 1 to 60. Combined timeouts cannot exceed 300 seconds. All checks run even if an earlier check fails. A timeout kills the process group so children do not remain running before a retry.

Results include each check's name, required flag, `passed`/`failed`/`command_error`/`timed_out` state, exit code, private full log path and hash. Raw command arguments and output stay out of the JSON envelope. Optional failures produce a warning; required failures produce an error exit. If all checks are optional, their failures do not block the result. A check that changes Git source inputs makes the overall result fail. Missing or malformed contracts return a clear error. Every invocation creates a separate private record and logs under `tool.wood.workflow.output_directory` and returns `data.verification_file`.

For a Story whose PR source declares verification, supply that saved record to `wood story evidence` using `--verification <verification.json>` alongside `--validation`, `--pr`, and `--ci-run`. Run verification against the exact implementation contents before committing, or against the PR source checkout. Evidence verifies the source fingerprint, source-revision contract, required checks, and log hashes; posting and completion reverify them without executing commands. Changing or losing the record/logs requires a new verification run. Optional check outcomes remain in the evidence. Repositories without a verification contract retain the existing validation/CI workflow and omit `--verification`.

Records attest to the observed execution and source contents, not continued runtime health. Rerun verification when the target runtime changes; saving a record does not turn it into a durable runtime attestation for `wood delivery status`.

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

`wood story activity list <id> --json` inspects the complete activity collection and returns 20 activities at a time (`--offset` advances the output). `Implementation update (WP-<id>)` and `Final summary (WP-<id>)` headings identify implementation summaries; other comments are progress, and commentless activities are system changes. JSON reports the unique summary ID, ambiguity, and full-content SHA-256 hashes. Comment previews are limited to 500 characters. Collection changes, duplicate IDs, foreign activities, incomplete pagination, or more than 10,000 activities fail rather than imply a missing summary.

To maintain one implementation summary, use `wood story activity summary <id> --file <markdown> --expected-sha256 <hash|absent> --json`, then repeat with `--apply`. `--evidence <evidence-file>` can supply the generated update instead of `--file`. Creation requires `absent`; editing requires the hash obtained by inspection. Identical content reuses the existing activity even after an uncertain previous response. Multiple summaries or changed content require fresh inspection. Updates require OpenProject's advertised edit capability, re-read content immediately before PATCH, and verify the result. OpenProject's comment PATCH API offers no atomic content precondition: a concurrent edit between the final read and PATCH can still race. Concurrent creators can also race; subsequent inspection rejects duplicate summaries. Avoid simultaneous summary writers.

Evidence generation also returns `data.delivery` and writes a separate, hash-bound `delivery-snapshot.json`. It normalizes repository, branch, source commit, PR, validation, CI, merged revision, semantic release/version, container image digest, infrastructure promotion, deployed revision/environment, and runtime verification. Every field has `available`, `unavailable`, or `not_applicable` state; available values cite their authority. Release discovery resolves the latest published release's tag revision and requires it to contain the Story merge. Deployment discovery uses `wood deploy status` and likewise requires a successful deployment containing the merge. It never substitutes an unrelated latest release or deployment. A missing or ambiguous provider remains unavailable, and a repository without a deployment workflow reports deployment fields as not applicable.

GitHub deployment payloads may provide `digest` (`sha256:<64 lowercase hex digits>`), `infrastructure_promotion`, and `runtime_verification`. The latter two are generic provider attestations with `revision`, `environment`, `status: "passed"`, and an HTTPS evidence `url`; they must match the successful deployment's revision and environment. Deployment success alone does not assert runtime verification. Wood Tools records these provider claims without performing application-specific checks. Full provider payloads are not copied into the snapshot.

Objective delivery snapshots and caller-authored narrative stay separate: generated Markdown is a deterministic fact summary, while `--file` accepts Codex's narrative under the recognized Story heading. Optional release/deployment snapshots describe generation-time observations; later releases do not invalidate matching validation/PR/CI evidence. Regenerate to refresh optional delivery facts. Completion with generated evidence additionally requires exactly one posted summary matching its update file, verified by readback. The required gates are matching repository validation, a merged Story PR, passed CI on its source or merge revision, and that posted summary. Release, deployment, promotion, and runtime observations remain advisory because this Story does not define repository-specific requirements for them. Manual evidence retains the existing validation/CI gates for unsupported repository workflows.

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

[Validation](.github/workflows/validate.yml) calls `SpencerRWood/workflows@v1`; [release](.github/workflows/release.yml) pins the reviewed shared recovery implementation from workflows PRs #31 and #32. Both use capabilities in [.github/release.toml](.github/release.toml). Python semantic-release owns version changes, tags, and GitHub Releases. Wood Tools contains no manual release mutation command.

For a partial release (version commit pushed but tag or GitHub release missing),
dispatch **Release on main** with `recovery_tag` set to the intended tag. This
starts full validation on that immutable checkout before the shared workflow
verifies the version commit, parent-derived intent, and remote publication state.
Old superseded runs remain guarded. The automatic `GITHUB_TOKEN` version push
does not trigger another push workflow. Repeated dispatch verifies the existing
release without generating another version. Conflicting tags or superseded
checkouts require diagnosis and fail safely. After a successful recovery, use
`wood delivery status <story-id> --json` and its returned next action to generate
completion evidence; workflow success alone does not establish delivery.

# Wood Tools

Wood Tools provides the `wood` command for deterministic agent workflows.

## Progressive branching adoption

Launchers must negotiate `wood contract --json` before activation. Its `branching`
contract version 1 declares `trunk`, `release`, `integration` and the supported
`feature`, `feat`, `fix` prefixes. Read `wood repo branching --json`, then preview
`story start` with matching `--base-branch` and `--target-branch`. Inspect the
preview's strategy, mapped scope, fetched base revision, branch and worktree
before applying. A session capability alone does not attest to branching support.
An incompatible launcher/tool stops before creating claims; upgrade the supported
interface rather than adding a legacy fallback. Discovery observes local refs;
nontrunk start fetches the exact remote branch. Trunk uses local main, which must
be deliberately updated from the fetched main before new work.

For an approved planning-scope rollover on a long-lived integration branch,
finish or explicitly resolve old-scope Stories and retain their evidence/claims.
Approve the next open planning version and scope, then update the root branching
mapping in a separately reviewed change. Never change an active claim's mapping
or rename/rebase its worktree to force adoption. New/resumed starts with a closed
planning version or a different mapping fail without deleting existing claims.
Resolve the live planning/mapping conflict, then resume using the recorded owner
and worktree. Rollover does not close releases, publish, deploy or move main.

The canonical target for every active repository is working branch → development
→ protected main → automatic pinned repository release workflow. Repository type
determines publication outputs, not branching strategy. BR-01 does not migrate
existing repositories: undeclared/explicit trunk and release mappings remain valid
until a deliberate migration. Approved integration scopes use:

```toml
[tool.wood.branching]
strategy = "integration"
release_id = 25 # OpenProject planning version
integration_branch = "development"
scope_reference = "<approved workbook or release scope evidence>"
```

`main` remains stable. Feature Stories start from the fetched integration branch
and target that same branch; `feature` is the default prefix. Integration mappings
require a valid Git branch other than `main`, an open matching planning release,
and approved scope. Start never creates the integration branch. Claims, resume,
PR evidence and delivery retain the same safeguards as release mappings. Story
integration reports `story_complete` without requiring publication or deployment.
Final integration into `main` requires separate release verification and approval;
the approved main merge triggers the pinned repository-specific release workflow.
Configure caller workflows so Story branches and integration merges only validate;
Wood's metadata does not disable publication jobs in GitHub Actions.

An urgent production incident can explicitly use `story start <id>` with both
`--hotfix-incident <incident-or-justification>` and
`--hotfix-approval <authorization-reference>`. This requires adopted integration
metadata and a matching open planning version; it fetches main and creates only
an isolated working branch (default prefix fix), recording a main/main hotfix
plan, the original integration branch and both references in the claim. Resume
with the same references. Missing authority, wrong identity/scope, unsupported
prefix or a changed active mapping fails without discarding work or claims.
These are operator authorization references, not independently verified approvals;
protected-main PR review and required CI remain mandatory. There is no direct-main
push, merge, protection bypass or publication command. Pass both references to
`story evidence`/`delivery status` to verify the actual hotfix PR target and retain
its authority alongside revision-bound CI and merge evidence.

Before subsequent release preparation, reconcile main into development through
a reviewed PR and run `wood repo release-readiness --json`. It fetches both refs
and blocks (exit 3) unless main is an ancestor of integration. The returned
`readiness_file` records exact revisions, observation time and any missing main
commits. This deliberately requires back-merging release metadata too; equivalent
cherry-picks alone are insufficient. Complete history is required; missing refs
or inaccessible history fail closed. This local preparation check never grants
approval or bypasses protection. Wiring it into required GitHub checks, publication
outputs, initialization/enforcement and consumer migrations belong to later work.

Story Primary Repository supports both a repository basename and canonical
`owner/repo`. Canonical start identity checks the GitHub origin (HTTPS or SSH),
including owner, without requiring an upstream or initial commit. Before a remote
exists, bootstrap identity checks the directory basename only; this is not proof
of remote ownership. Evidence and CI verification check the canonical GitHub identity.

Follow the [canonical Codex/Pi policy](https://github.com/SpencerRWood/codex-config/blob/main/docs/branching-policy.md).
`wood repo branching --json` discovers adoption without credentials or a release
contract. An absent `[tool.wood.branching]` preserves existing trunk defaults
(`main` base and PR target, `feature/op-*` naming); it does not classify the repository's purpose.
Adopt applications individually after scope, tooling and validation callers are approved:

```toml
[tool.wood.branching]
strategy = "release"
release_id = 20 # actual OpenProject planning version ID, not inferred from R1
release_branch = "release/v1.2.3" # example; requires explicit approved SemVer mapping
scope_reference = "<approved release scope evidence>"
```

`story start --base-branch release/v1.2.3 --target-branch release/v1.2.3`
confirms selectors against that mapping. `--branch-prefix feat`, `fix` and `feature`
are supported; release defaults to `feat`. Start verifies an open planning release
in the Story project and fetches the exact existing remote branch before creating
the Story worktree at its recorded revision. Preview may update remote tracking
refs but never creates a claim, Story branch or worktree. Wood never creates release
branches. Missing scope/mapping/remote, wrong target and active-claim migration fail
closed. Approved scope references are human approval evidence, not automatic approval.

`story evidence --target-branch release/v1.2.3` verifies the actual merged PR target
against adoption metadata from the validated source commit; it records branch and
release association. Validation, required verification, revision-bound CI and posted
evidence remain required. Publication/deployment are separate release gates and are
not prerequisites for Story closure. Release-target delivery reports return
`story_complete`; trunk delivery reports retain their existing release/deployment
observations. No command here publishes, deploys or merges a release into main.

Existing in-progress work remains intact and may finish its safe current arrangement
with explicit approval. Do not change an active claim's mapping to migrate it.
Recovery Verification R1 fixtures test the first adoption scenario; they do not
modify OP-499 or approve a live release scope.

## Repository initialization and enforcement

Existing repositories use `wood repo migrate assess`, `plan` and `apply`, which
reuse the BR-03 specification, caller generator and separate protection commands.
Assessment saves branches, PRs, claims, worktrees and effective rules without
changing them. A reviewed plan binds the exact baseline, remote/local observations,
destination and proposed file contents. Apply prepares an isolated feature worktree;
it does not commit, merge, change live protection, publish or deploy.

```sh
wood repo migrate assess --json
wood repo migrate plan --baseline <full-approved-SHA> \
  --baseline-approval <scope-reference> --destination /path/to/isolated-worktree \
  --branch feature/op-558-adoption --spec /path/to/spec.json --json
wood repo migrate apply --plan <returned-plan-file> \
  --plan-hash <reviewed-hash> --json
```

If development is missing, select `--initialize-development` on apply. Its approved
baseline must be current main with no unsafe development/create/workflow-run publisher.
Branch creation uses an absent-ref lease; existing development is never advanced or
overwritten. Existing release scopes/branches block automatic transition. Main and
development must both be ancestors of the candidate before planning. Preserve active
claims and dirty source checkouts; changed observations or occupied targets fail closed.
Private durable journals retain partial progress; retry the same plan only while refs,
claims and planned contents still match. Never delete branches/worktrees as automatic recovery.

Publication capabilities and deployment requirements remain in the existing release
contract. `--contract` supplies an explicitly reviewed missing contract, or enables
semantic release in an otherwise identical manual-publisher contract. Reusable workflow
paths keep their call interfaces; `workflow_prefix: "wood-"` gives their repository's
own callers separate names. Unknown additional automatic workflows require review.
Enforcement readback compares YAML semantics and accepts only known non-weakening
GitHub PR defaults; bypass actors, policy drift, wrong checks and incompatible workflows
still fail. Plan live enforcement separately and retain explicit approval for apply.

For an existing rule, `adopt_rulesets: {"main": <observed-rule-ID>}` explicitly
proposes adopting its ID rather than adding a competing rule. The plan may strengthen
review requirements and replace the obsolete BR-02 `validation` context with
`validation / validation` from the same Actions app. It refuses bypass grants,
lost security checks, reduced review requirements or unrelated policy loss.
This is a reviewed transition, not a compatibility status alias.

When old main callers prevent installing the new required checks, use
`repo protection plan --candidate-pr <development-to-main-PR> ...` to bind a bootstrap
plan to the exact open same-repository candidate, current main/development revisions,
passing canonical Actions checks and the reviewed caller content. Pass the same
candidate on separately approved protection apply. Changed candidates/checks fail
before writes. Readback still reports actual main installation as pending; this
bootstrap never merges a PR or grants publication authority.

`wood repo init` is one initializer for prepared, approved source baselines.
React/backend, FastAPI service and Python library layouts share the same
integration strategy. The existing release contract selects the Python working
directory; branching and OpenProject context always live in root `pyproject.toml`.
Perform the template's documented package/project rename first and approve that
source commit. Initialization does not execute template scripts or mutate the source.

The supported specification is JSON. It supplies the new repository identity,
planning scope, exact reviewed workflow/tool SHAs, existing integration/security
commands, GitHub Actions integration ID and baseline commit author. For example:

```json
{
  "repository": "owner/new-repository",
  "project_id": 3,
  "initiative_id": 552,
  "release_id": 26,
  "scope_reference": "<actual approved scope reference>",
  "workflow_revision": "<approved full BR-02-compatible workflows SHA>",
  "wood_revision": "<approved full Wood SHA containing repo route and release-context>",
  "checks_integration_id": 15368,
  "author_name": "<approved author>",
  "author_email": "<approved author email>",
  "required_approvals": 0,
  "images": []
}
```

The SHA placeholders must be replaced; branch names and mutable tags are rejected.
Verify the GitHub Actions App ID before applying enforcement. Required review count
is explicit; the sole-maintainer default retains revision-bound release approval
through BR-02's protected-main authority, without inventing an administrator bypass.
The OpenProject release must remain open for subsequent Story activation; the
initializer records approved scope but does not independently approve it.

Images use existing `image_name`, `dockerfile`, `context` inputs, up to sixteen
distinct declared images. All Dockerfiles/contexts must exist in the source commit.
No template image or deployment identity is copied. Empty images select the shared
tag/GitHub Release workflow; declared images select BR-02's coordinated publication
workflow with one release identity. The existing `release.tag_merged_commit` mode
is enabled so semantic-release tags the validated main revision without pushing
version metadata through protected main. Packaging remains the consumer's existing
capability contract; this command does not publish packages or images.

Optional `deployment` declares `approval_reference`, `automatic` and the existing
`deploy-ansible` `inputs`: inventory/playbook paths, environment name, concurrency
group and health check command, with supported rollback/runner inputs when needed.
Automatic deployment depends on verified publication and its immutable tag;
`automatic = false` generates a separate main-only dispatch for an already
published tag. Existing manual deployment exceptions are retained. Secrets are
referenced through the existing Actions secret inputs; none are written by Wood.

Preview from a task repository, inspect the saved complete plan, then explicitly
apply the same inputs and returned hash:

```sh
wood repo init --source /path/to/prepared-source --baseline <full-source-SHA> \
  --baseline-approval <approval-reference> --destination /path/to/new-repository \
  --spec /path/to/spec.json --json
# After plan review, repeat with --apply --plan-hash <reviewed-hash>.
```

Preview does not create a destination. Apply creates an approved baseline commit
and points both main and development to that exact commit, checking out development.
The tracked `.wood-initialization.json` retains source identity, approval, plan hash
and expected contents/modes. Construction uses disposable staging and an exclusive
destination reservation. Failed construction leaves the source untouched; exact
retries reuse the baseline. Occupied destinations, modified/advanced repositories,
links, submodules, runtime environment files and stale/tampered plans fail closed.
Existing repositories and their branch arrangements/claims are never migrated.

This operation creates only a local repository and an origin URL, without remote
creation, pushing, publishing, deployment or live enforcement. `enforcement_verified`
remains false. Provisioning/pushing a new remote requires separate authorization;
the existing main publisher may run on an initial main push and needs explicit authorization.

Generated validation/routing workflows use JSON, a valid YAML subset, and immutable
workflow/helper pins. Development PRs/pushes validate without publication or deployment.
The existing baseline publisher and deployment controls are preserved, rather than
replaced with a new publishing engine. Main retains the repository's applicable
centralized release machinery, invoked after an explicitly approved release merge.

`pr-routing` checks the exact checkout and ancestry: working branches target
development, development targets main, and current main can reconcile into divergent
development. Release candidates must contain main ancestry, including hotfixes.
Emergency Story PRs retain `Hotfix-Incident:` and `Hotfix-Approval:` evidence.
No hosted OpenProject credentials, release-scope deferral protocol or new CI identity
is required. Wood/Codex use existing local credentials for planning and evidence.

Enforcement is a separate reviewed operation, after installing the workflows on
both remote branches:

```sh
wood repo protection plan --spec /path/to/spec.json --json
wood repo protection apply --spec /path/to/spec.json \
  --plan-hash <reviewed-hash> --approval-reference <separate-approval> --json
wood repo protection readback --spec /path/to/spec.json --json
```

Protection plans bind the exact remote main and development revisions as well as
the specification, observed rulesets and available revision-bound CI check contexts,
application IDs and states. Any branch advance or CI observation change requires a fresh
plan and review before live application. Readback reports missing callers as
failed installation checks so a staged migration cannot appear enforced.

The plan binds observed rulesets and desired no-bypass main/development rules to a
hash. Apply creates missing managed rulesets without rewriting conflicting policy;
partial failure requires fresh readback/replanning, then reuses already correct
rules. Required checks must match installed pinned workflows and the verified
GitHub Actions integration. Both branches require PRs, strict validation and routing,
and resolved conversations, with zero independent approving reviewers, no bypass actors,\nand no direct/force pushes or deletion. Inspect stronger existing controls and\nreport any material security downgrade before applying it.
Archived repositories, inherited-policy ambiguity and truncated authority fail
closed. Application does not attest enforcement: effective readback verifies branch
rules, bypass actors, required-check parameters and revision-bound workflow sources.
It uses GitHub's [active branch rules endpoint](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch),
which excludes disabled/evaluate rulesets. Results and full records are separate
from publication/deployment authority.

No live protection change or consumer/template migration is part of Story 556's
implementation validation. Fixtures cover local Git initialization and mocked
ruleset plan/apply/readback. Generated callers are validated with actionlint when
available, alongside tests of their pins, routing, permissions and output contracts.

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
the token remains secret-backed. Plain `wood` commands automatically fetch missing
URL/token values using the authenticated Infisical CLI, keeping them in memory
for that invocation. Existing injected values take precedence. There is no token
cache, `.env` loader, or credential file.
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

Authenticate Infisical once. For use across repositories and shell sessions, create
`~/.config/wood/infisical.toml` (or `$XDG_CONFIG_HOME/wood/infisical.toml`) with
only these non-secret selectors:

```toml
project_config_dir = "/absolute/path/to/authorized/infisical/context"
environment = "dev"
secret_path = "/openproject"
```

`project_config_dir` must contain the authorized `.infisical.json`. Without this
global file, Wood uses the current directory's Infisical context, environment
`dev`, and path `/openproject`. `INFISICAL_PROJECT_ID` also supports explicit
project selection with Infisical authentication. A malformed global file or a
failed fetch stops with a structured, value-free error; it does not fall back to
another context. Local repository, CI, contract, and help commands do not fetch
OpenProject credentials. Explicit mappings passed to the Python `load_settings`
API remain deterministic and are not supplemented from Infisical.

Run readiness and planning commands directly:

```sh
wood doctor --json
wood story next --json
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
already exist. Example:

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

### Deterministic milestones

`wood story milestones <id> --owner <owner> --json` previews structured observations
from the active primary-repository claim. Add `--validation <saved-validation.json>`
after validation, and `--pr <number>` after PR, review, merge or CI state changes.
Apply the inspected preview with `--apply`. Reporting never runs validation,
requests model inference, opens/merges PRs, closes Stories, publishes or deploys.

Apply saves each event in a private outbox under the shared Git metadata before
submitting any activity through Wood. Each activity has a deterministic identity
and a retained observation time. Exact retries reconcile a prior ambiguous POST
by reading OpenProject before writing. Changed or duplicate remote activities fail
closed. `--retry --apply` submits pending saved evidence without gathering new
observations. A submission failure returns `unavailable`, its pending count and
the outbox path. Keep those files; retries are historical observations, not a new
attestation of current state. Each call submits at most 20 pending events.
Locking covers cooperating worktrees on one host, not independent clones/hosts.

PR selection produces a separately saved delivery snapshot and milestones for PR,
review, CI and merge observations. Release readiness, semantic publication,
immutable artifact identity, deployment, rollback and verification stay separate;
missing and non-applicable authorities remain explicit. An optional
`--readiness <saved-readiness.json>` reports an existing record after checking its
scope and cached remote revisions. Reporting does not refresh those refs or grant
release authority. No rollback evidence provider is currently configured.

Repository Story closure requires generated `story evidence` with matching local
validation, merged PR, current review approval and revision-bound successful CI.
Deployment is not required for ordinary integration Stories. Current independent
GitHub reviews are checked across at most 1000 entries; stale approvals, outstanding
change requests and inaccessible review collections cannot attest approval.
For the explicit human approval in the shared workflow, `story evidence`,
`delivery status` and `story milestones` also accept both `--review-reference <reference>`
and `--reviewed-revision <exact-source-sha>`. This records an operator attestation,
clearly distinguished from independently verified GitHub reviews. Wood cannot
verify the human reference itself; supply it only after actual approval of that
source. It cannot override outstanding GitHub change requests. Reverification
checks the saved source binding and current GitHub state. No raw command output,
credentials or arbitrary activity body is accepted by the milestone command.

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
semantic-release. Run plain `wood` commands, using the checkout's `uv run --active --frozen wood`
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

Run plain Story commands from the target repository using an installed `wood` executable. In the Wood Tools checkout, use `uv run --active --frozen wood` to exercise the checked-out code. The repository root `pyproject.toml` may optionally declare:

```toml
[tool.wood.openproject]
initiative_id = 208
project_id = 3 # optional; verified against the Initiative's OpenProject project
```

`wood story next [ref] --json` and `wood story list [ref] --json` use that Initiative when `ref` is omitted. Pass an explicit project or Initiative reference when the repository has no mapping, or to override one. The CLI finds the Git root from the working directory, regardless of where `wood` or its virtual environment is installed. Missing or malformed context returns an actionable error. `list` filters with `--status` and `--version` and pages with `--offset`. `wood story get <id> --json` returns the implementation packet, relations, and description chunks; use `--offset` to read further chunks. `next` selects a dependency-ready Story in the earliest active R# release, prioritizing an eligible Story already In progress, then the lowest ID. Closed and Rejected Stories are terminal; Rejected predecessors do not satisfy dependencies. Blocked and unversioned Stories are ineligible. An unfinished earlier release holds later releases.

`wood story create` requires project, initiative, Epic, open R# version, subject, goal, requirement IDs, and at least one `--acceptance` value. It previews by default; `--apply` creates the Story. `wood story set-status <id> <status>` checks the live Story, lock version, and allowed transition before preview or apply. `wood story start <id> --owner <session-id> --worktree <absolute-path>` checks the open R# version, Closed predecessors, primary repository and configured project/Initiative, acquires a durable claim, and prepares the standard branch in an isolated worktree outside the primary checkout. `--project` and `--initiative` explicitly override their repository mapping selectors. New worktrees start from local `main`; dirty primary changes stay untouched. Occupied targets, another branch checkout, dirty unclaimed targets and competing owners fail clearly. The matching owner can resume an In progress Story and its dirty worktree without a redundant status write. Blocked/On hold Stories recheck readiness before activation. `wood story block <id> --reason <text>` records a supported blocked state and activity. `wood story complete <id> --evidence <json-file>` validates the supplied repository checks and CI run before closing. Status, block, complete and activity writes require the matching `--owner` while a claim is active. These lifecycle commands preview by default and mutate only with `--apply`.

Claims and the nonblocking OS lock live under `wood-stories` in Git's common metadata directory, shared across agents/worktrees of that local repository. They never expire or release on process exit. This is a cooperating-agent protocol, not authentication, a sandbox, or exclusion across independent clones/hosts. An owner must not execute concurrently in multiple agents. Claims are persisted before Git/remote mutation so interrupted activation can retry with the same owner/path; failed mutation does not silently surrender the claim. Keep the worktree and claim through delivery.

`wood story session get <id> --json` observes local session identity, source revision, bounded dirty paths, saved record paths and their availability without OpenProject credentials. `session checkpoint <id> --owner <id> --phase <implementation|review|delivery|blocked|complete> --next-action <text> [--record <path> ...]` records up to 20 existing evidence/log references. Phase and file presence never attest passed checks. `session handoff <id>` returns that observation; `--apply` also saves a private JSON artifact in Git metadata. `session release <id> --owner <id>` releases ownership explicitly after execution stops and evidence/handoff is retained, preserving worktree and record references. Checkpoint, handoff and release preview by default. Resume from the retained owner, branch and worktree; inspect malformed state or identity conflicts rather than replacing them. Completion still uses the existing evidence contract, independently of session phase.

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

Use `data.evidence_file` from generation for the activity and completion commands. `data.update_file` is the generated implementation-update Markdown; callers can also use the existing activity `--file` option. Evidence generation previews without creating files and `--apply` writes only local files. PR management remains through `gh`.

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

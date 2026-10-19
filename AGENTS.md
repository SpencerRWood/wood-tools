## Development lifecycle

Use root [tool.wood.branching]: feature branches and Story PRs start from and target development. Development validates without publication/deployment. Main requires a separately approved release PR and pinned release workflow. Emergency hotfixes require incident/approval references and reconciliation into development.

# Repository Guidance

## Branching strategy

- This checkout adopts integration through root `[tool.wood.branching]` for OpenProject planning release 24 (Wood Pi Harness), initiative 531. Completed planning release 26 was closed and this scope rollover was approved on 2026-10-19 for OP-537. New Stories start from fetched `development` and target `development`. Preserve existing Story claims and worktrees; do not rewrite their recorded branch arrangements.
- Follow the [canonical branching and delivery policy](https://github.com/SpencerRWood/codex-config/blob/main/docs/branching-policy.md), available through the installed codex-config source path. Verify the strategy, release association, fetched base and PR target before implementation and record them in Story evidence. Story completion does not authorize publication or deployment; retain existing review, CI, security and rollback requirements.

## Scope
- `wood` is the only public executable. The v2 CLI is intentionally breaking; do not add aliases, forwarding wrappers, or legacy configuration loaders.
- Keep domain logic separable from parsing and remote transport. Migrate existing internals by capability, then delete the superseded implementation and update first-party callers in that Story.

## Development
- Use uv and Python 3.14. Run `uv sync --active` in the active environment.
- Run `wood repo validate --json` after edits and fix reported failures before review.
- Keep full command logs on disk and report bounded summaries.

## CLI contract
- All informational commands support `--json` with the v2 envelope and exit-code categories in `wood.output`.
- Bound agent-facing data and errors. Never include secret values in output or audit events.
- Audit logging is optional for runtime correctness.

## Release
- `.github/release.toml` declares validation capabilities. Reusable workflows at `SpencerRWood/workflows@v1` perform validation and release.
- Python semantic-release alone owns version changes, tags, and GitHub Releases. Do not implement release mutation commands in Wood Tools.

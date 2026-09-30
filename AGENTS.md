# Repository Guidance

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

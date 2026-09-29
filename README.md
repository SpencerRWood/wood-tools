# Wood Tools

Wood Tools v2 is a single `wood` command for deterministic agent workflows. This branch establishes the CLI and repository foundation for [WP-396](https://projects.woodhost.cloud/work_packages/396). Project, Story, repository, CI, deployment, and secret capabilities are added in later migration Stories.

## Install and inspect

Use Python 3.14 and uv:

```sh
uv sync --active
uv run --active wood contract --json
```

`wood` is the only installed public executable. The v1 commands are intentionally unavailable. Existing domain packages remain internal while their replacement capabilities are built in later Stories.

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

# WP-402 v2 validation record

This record tracks the final R1 validation gate. It separates checks available on the
local Story branch from evidence that requires a reviewed pull request and merge.

## Local and safe integration evidence

| Acceptance area | Evidence | State |
| --- | --- | --- |
| v2 command surface | `wood --help`, `wood repo info --json`, and `wood repo standards --json` expose the single `wood` entrypoint and declared Python checks. | Passed locally |
| Read-only diagnostics | Under Infisical `/openproject`, `wood doctor`, `project list`, `story next`, `secret status`, and `secret requirements` returned bounded v2 JSON. `wood repo info`, `repo standards`, `ci status`, and `deploy status` also returned bounded v2 JSON. | Passed locally; CI status was stale for this unpushed branch |
| Workbook import | `tests/test_project_cli.py` exercises plan hash preview, stale-plan rejection, and apply through a fake client. `tests/test_implementation_workbook.py` exercises verified writes and repeated reuse without a remote mutation. | Passed locally |
| Story lifecycle | `wood story start 402` was previewed and applied in the authorized OpenProject context. `tests/test_story_cli.py` exercises safe fake API start, status, and completion gates. Live completion remains after merge and required checks. | Start passed; closure pending |
| Secret handling | Diagnostics and audit tests use sentinel credentials and assert they are absent from JSON and audit records. Live diagnostic JSON reports only presence and readiness. | Passed locally |
| Cutover | `tests/test_architecture.py` checks removed modules and the single public entrypoint. A scan of active Wood Tools and codex-config callers found no deprecated executables or resolved-token files. Codex-config Story and workbook instructions now use Infisical for the URL and token. | Local scan passed |
| Release ownership | Wood Tools calls shared `validate.yml@v1` on pull requests and `release.yml@v1` on `main`. The shared release workflow runs validation before semantic-release; Wood Tools has no release mutation command. | Contract inspected; publication pending |
| Applicability | `wood repo info` reports Python applicable; Node and dbt not applicable. `wood deploy status` explicitly returns `not_applicable` because this CLI has no deployment workflow. | Documented |

## Delivery gates

`wood repo validate --json` passed all six declared checks on the Story diff: Ruff,
Ruff format, mypy, pytest, pytest with coverage, and pre-commit. The test suite had
86 passing tests and 85.49% coverage. Full logs are in the local `wood-repo-validate`
run directory reported by the command. After review approval, open the PR and require
centralized validation on its exact head.
After merge, verify the release workflow validation job succeeded before using its
semantic-release result as publication evidence. A release may be legitimately skipped
when the reviewed commit type does not request one. Keep WP-402 open until the required
CI and release evidence has been read back and the implementation update is posted.

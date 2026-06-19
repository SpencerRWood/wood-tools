from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from wood_config.output import blocked_output, error_output, success_output

from .core import ProjectError, init_project, link_repository, show_project, validate_project


def _command_name(args: argparse.Namespace) -> str:
    if args.command == "link" and getattr(args, "link_command", None) == "repo":
        return "link-repo"
    return args.command


def _emit(payload: dict[str, Any], *, json_output: bool) -> int:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for key, value in payload.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for sub_key, sub_value in value.items():
                print(f"  {sub_key}: {sub_value}")
        else:
            print(f"{key}: {value}")
    return 0


def _summarize_json_payload(
    command: str, payload: dict[str, Any], *, apply: bool | None = None
) -> dict[str, Any]:
    if command == "init":
        if apply:
            return success_output(
                command="init",
                mutation="mutating",
                summary=f"Project initialized at {payload['path']}.",
                data=payload,
            )
        return blocked_output(
            command="init",
            summary=f"Project initialization requires approval to write {payload['path']}.",
            data=payload,
            next_actions=["Re-run with --apply to create project.json and .wood metadata."],
        )

    if command == "show":
        return success_output(
            command="show",
            mutation="read-only",
            summary=f"Loaded project metadata from {payload['path']}.",
            data=payload,
        )

    if command == "link-repo":
        if apply:
            return success_output(
                command="link-repo",
                mutation="mutating",
                summary=f"Linked repository {payload['repository']['name']} at {payload['path']}.",
                data=payload,
            )
        return blocked_output(
            command="link-repo",
            summary=(
                f"Linking repository {payload['repository']['name']} requires approval to write "
                f"{payload['path']}."
            ),
            data=payload,
            next_actions=["Re-run with --apply to update project.json."],
        )

    return success_output(
        command="validate",
        mutation="read-only",
        summary=f"Project metadata is valid at {payload['path']}.",
        data=payload,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wood-project", description="Manage workspace metadata")
    parser.add_argument(
        "--project-root",
        type=Path,
        help="Override the project root (defaults to current working directory)",
    )
    parser.add_argument(
        "--project-file",
        type=Path,
        help="Override the project.json path (defaults to <project-root>/project.json)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize project metadata")
    init_parser.add_argument("--project-id", help="Provide an explicit project ID")
    init_parser.add_argument("--project-slug", help="Provide an explicit project slug")
    init_parser.add_argument(
        "--artifact-root",
        type=Path,
        help="Override the artifact root (defaults to <project-root>/.wood/artifacts)",
    )
    init_parser.add_argument("--apply", action="store_true", help="Write project.json")
    init_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    show_parser = subparsers.add_parser("show", help="Show project metadata")
    show_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    validate_parser = subparsers.add_parser("validate", help="Validate project metadata")
    validate_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    link_parser = subparsers.add_parser("link", help="Link project resources")
    link_subparsers = link_parser.add_subparsers(dest="link_command", required=True)

    link_repo_parser = link_subparsers.add_parser("repo", help="Link an implementation repository")
    link_repo_parser.add_argument("repo_path", type=Path, help="Path to the repository to link")
    link_repo_parser.add_argument(
        "--name",
        help="Optional repository name (defaults to the repository directory name)",
    )
    link_repo_parser.add_argument(
        "--role",
        help="Optional repository role stored with the link metadata",
    )
    link_repo_parser.add_argument("--apply", action="store_true", help="Write project.json")
    link_repo_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            payload = init_project(
                project_root=args.project_root,
                artifact_root=args.artifact_root,
                project_id=args.project_id,
                project_slug=args.project_slug,
                apply=args.apply,
            )
            return _emit(
                (
                    _summarize_json_payload("init", payload, apply=args.apply)
                    if args.json
                    else payload
                ),
                json_output=args.json,
            )

        if args.command == "show":
            project = show_project(project_file=args.project_file, project_root=args.project_root)
            payload = {
                "path": str(args.project_file or Path(project["project_root"]) / "project.json"),
                "project": project,
            }
            return _emit(
                _summarize_json_payload("show", payload) if args.json else payload,
                json_output=args.json,
            )

        if args.command == "validate":
            payload = validate_project(
                project_file=args.project_file,
                project_root=args.project_root,
            )
            return _emit(
                _summarize_json_payload("validate", payload) if args.json else payload,
                json_output=args.json,
            )

        if args.command == "link" and args.link_command == "repo":
            payload = link_repository(
                repo_path=args.repo_path,
                repo_name=args.name,
                repo_role=args.role,
                project_file=args.project_file,
                project_root=args.project_root,
                apply=args.apply,
            )
            return _emit(
                (
                    _summarize_json_payload("link-repo", payload, apply=args.apply)
                    if args.json
                    else payload
                ),
                json_output=args.json,
            )

        parser.error("Unknown command")
        return 2
    except ProjectError as exc:
        command_name = _command_name(args)
        if getattr(args, "json", False):
            payload = error_output(
                command=command_name,
                mutation="mutating" if command_name in {"init", "link-repo"} else "read-only",
                summary=str(exc),
                errors=[{"message": str(exc)}],
                next_actions=["Review the project metadata inputs and try again."],
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

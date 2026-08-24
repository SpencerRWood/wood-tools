from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from resources.cli.audit import write_audit_event
from resources.cli.output import blocked_output, error_output, success_output
from resources.packages import ResourceError

from .commands import COMMAND_MODULES
from .core.models import ProjectError
from .implementation import ImplementationWorkflowError
from .openproject.models import OpenProjectError
from .release import ReleaseWorkflowError
from .story import StoryWorkflowError


def _command_name(args: argparse.Namespace) -> str:
    if args.command == "link":
        return f"link-{getattr(args, 'link_command', 'unknown')}"
    if args.command == "resource":
        return f"resource-{getattr(args, 'resource_command', 'unknown')}"
    if args.command == "registry":
        return f"registry-{getattr(args, 'registry_command', 'unknown')}"
    if args.command == "story":
        return f"story-{getattr(args, 'story_command', 'unknown')}"
    if args.command == "release":
        return f"release-{getattr(args, 'release_command', 'unknown')}"
    if args.command == "implementation":
        return f"implementation-{getattr(args, 'implementation_command', 'unknown')}"
    return args.command


def _emit(
    payload: dict[str, Any], *, json_output: bool, command_args: list[str] | None = None
) -> int:
    if {"command", "status", "mutation"}.issubset(payload):
        write_audit_event(payload, cli_name="wood-project", command_args=command_args)
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
                command=command,
                mutation="mutating",
                summary=f"Project initialized at {payload['path']}.",
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=f"Project initialization requires approval to write {payload['path']}.",
            data=payload,
            next_actions=["Re-run with --apply to create project.json and Wood home resources."],
        )
    if command == "link-repo":
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=f"Linked repository {payload['repository']['name']} at {payload['path']}.",
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=f"Linking repository {payload['repository']['name']} requires approval.",
            data=payload,
            next_actions=["Re-run with --apply to update project.json."],
        )
    if command == "link-openproject":
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=("Registered this repository with the global OpenProject registry."),
                data=payload,
            )
        return blocked_output(
            command=command,
            summary="OpenProject project registration requires approval.",
            data=payload,
            next_actions=["Re-run wood-project link openproject with --apply."],
        )
    if command == "resource-install":
        resource = payload["resource"]
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=(
                    f"Installed {resource['kind']} resource {resource['name']} "
                    f"{resource['version']}."
                ),
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=(
                f"Installing {resource['kind']} resource {resource['name']} "
                f"{resource['version']} requires approval."
            ),
            data=payload,
            next_actions=["Re-run with --apply to install the resource into Wood home."],
        )
    if command in {"resource-inspect", "resource-path"}:
        resource = payload["resource"]
        action = "Inspected" if command == "resource-inspect" else "Resolved"
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"{action} {resource['kind']} resource {resource['name']}.",
            data=payload,
        )
    if command == "registry-import":
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary="Imported OpenProject project registry manifest.",
                data=payload,
            )
        return blocked_output(
            command=command,
            summary="OpenProject project registry import requires approval.",
            data=payload,
            next_actions=["Re-run wood-project registry import with --apply."],
        )
    if command in {"user", "project", "story-show"}:
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"OpenProject {command.removeprefix('story-')} inspection completed.",
            data=payload,
        )
    if command == "story-next":
        story = payload.get("story")
        summary = (
            f"Next Story is WP-{story['id']} {story['subject']}."
            if story
            else f"Release {payload.get('release', {}).get('version')} is ready."
        )
        return success_output(
            command=command,
            mutation="read-only",
            summary=summary,
            data=payload,
        )
    if command in {"story-set-status", "story-create-branch"}:
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=f"{command.removeprefix('story-')} completed.",
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=f"{command.removeprefix('story-')} requires approval.",
            data=payload,
            next_actions=[
                f"Re-run wood-project story {command.removeprefix('story-')} with --apply."
            ],
        )
    if command == "release-check":
        return success_output(
            command=command,
            mutation="read-only",
            summary=(
                "Release readiness check passed."
                if payload.get("ready")
                else "Release readiness check found blockers."
            ),
            data=payload,
        )
    if command in {"release-bump", "release-tag", "release-github-create"}:
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=f"{command.removeprefix('release-')} completed.",
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=f"{command.removeprefix('release-')} requires approval.",
            data=payload,
            next_actions=[
                f"Re-run wood-project release {command.removeprefix('release-')} with --apply."
            ],
        )
    if command == "implementation-export":
        return success_output(
            command=command,
            mutation="read-only",
            summary="Implementation workbook exported.",
            data=payload,
        )
    if command == "implementation-plan":
        return success_output(
            command=command,
            mutation="read-only",
            summary="Implementation workbook plan built.",
            data=payload,
        )
    if command == "implementation-apply":
        return success_output(
            command=command,
            mutation="mutating",
            summary="Implementation workbook plan applied.",
            data=payload,
        )
    summary = (
        f"Loaded project metadata from {payload['path']}."
        if command == "show"
        else f"Project metadata is valid at {payload['path']}."
    )
    return success_output(
        command=command,
        mutation="read-only",
        summary=summary,
        data=payload,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wood-project",
        description=(
            "Deterministic Wood Agents execution surface for story, implementation, and release"
        ),
    )
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
    for command_module in COMMAND_MODULES:
        command_module.add_parsers(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(command_args)
    try:
        for command_module in COMMAND_MODULES:
            if command_module.handles(args):
                command, payload, apply = command_module.run(args)
                output = (
                    _summarize_json_payload(command, payload, apply=apply) if args.json else payload
                )
                return _emit(output, json_output=args.json, command_args=command_args)
        parser.error("Unknown command")
        return 2
    except (
        ProjectError,
        ResourceError,
        OpenProjectError,
        StoryWorkflowError,
        ReleaseWorkflowError,
        ImplementationWorkflowError,
    ) as exc:
        command = _command_name(args)
        message = exc.message if isinstance(exc, OpenProjectError) else str(exc)
        if getattr(args, "json", False):
            payload = error_output(
                command=command,
                mutation=(
                    "mutating"
                    if command
                    in {
                        "init",
                        "link-openproject",
                        "link-repo",
                        "registry-import",
                        "resource-install",
                        "story-set-status",
                        "story-create-branch",
                        "release-bump",
                        "release-tag",
                        "release-github-create",
                        "implementation-apply",
                    }
                    else "read-only"
                ),
                summary=message,
                errors=(
                    [{"code": exc.code, "message": message}]
                    if isinstance(
                        exc,
                        OpenProjectError | StoryWorkflowError | ImplementationWorkflowError,
                    )
                    else [{"message": message}]
                ),
                next_actions=[
                    (
                        "Check OpenProject config, secret readiness, and network access."
                        if isinstance(exc, OpenProjectError)
                        else "Review the project metadata inputs and try again."
                    )
                ],
            )
            write_audit_event(payload, cli_name="wood-project", command_args=command_args)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
        print(f"Error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

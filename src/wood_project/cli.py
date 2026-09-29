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
from .openproject.models import OpenProjectError


def _command_name(args: argparse.Namespace) -> str:
    if args.command == "link" and getattr(args, "link_command", None) == "repo":
        return "link-repo"
    if args.command == "resource":
        return f"resource-{getattr(args, 'resource_command', 'unknown')}"
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
    if command in {"user", "project"}:
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"OpenProject {command.removeprefix('story-')} inspection completed.",
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
        description=("Legacy internal project command router"),
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
                        "link-repo",
                        "resource-install",
                    }
                    else "read-only"
                ),
                summary=message,
                errors=(
                    [{"code": exc.code, "message": message}]
                    if isinstance(
                        exc,
                        OpenProjectError,
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

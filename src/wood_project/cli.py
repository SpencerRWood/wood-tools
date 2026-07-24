from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from wood_config.audit import write_audit_event
from wood_config.output import blocked_output, error_output, success_output

from .core import (
    ProjectError,
    init_project,
    inspect_resource,
    install_resource,
    link_repository,
    resolve_resource_path,
    show_project,
    validate_project,
)
from .openproject import OpenProjectClient, OpenProjectError, load_settings


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
                command="init",
                mutation="mutating",
                summary=f"Project initialized at {payload['path']}.",
                data=payload,
            )
        return blocked_output(
            command="init",
            summary=f"Project initialization requires approval to write {payload['path']}.",
            data=payload,
            next_actions=["Re-run with --apply to create project.json and Wood home resources."],
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

    if command == "resource-install":
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=(
                    f"Installed {payload['resource']['kind']} resource "
                    f"{payload['resource']['name']} {payload['resource']['version']}."
                ),
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=(
                f"Installing {payload['resource']['kind']} resource "
                f"{payload['resource']['name']} {payload['resource']['version']} requires approval."
            ),
            data=payload,
            next_actions=["Re-run with --apply to install the resource into Wood home."],
        )

    if command == "resource-inspect":
        resource = payload["resource"]
        return success_output(
            command=command,
            mutation="read-only",
            summary=(
                f"Inspected {resource['kind']} resource {resource['name']} {resource['version']}."
            ),
            data=payload,
        )

    if command == "resource-path":
        resource = payload["resource"]
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"Resolved {resource['kind']} resource path for {resource['name']}.",
            data=payload,
        )

    if command in {"user", "project", "story"}:
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"OpenProject {command} inspection completed.",
            data=payload,
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
        "--wood-home",
        type=Path,
        help="Override the user-global Wood home (defaults to WOOD_HOME or ~/.wood)",
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

    resource_parser = subparsers.add_parser("resource", help="Install and inspect resources")
    resource_subparsers = resource_parser.add_subparsers(dest="resource_command", required=True)

    resource_install_parser = resource_subparsers.add_parser(
        "install",
        help="Install a versioned resource from a directory with wood-resource.json",
    )
    resource_install_parser.add_argument("source_dir", type=Path, help="Resource source directory")
    resource_install_parser.add_argument(
        "--apply",
        action="store_true",
        help="Install into the project Wood home",
    )
    resource_install_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    resource_inspect_parser = resource_subparsers.add_parser(
        "inspect",
        help="Inspect installed resource metadata and verify its digest",
    )
    resource_inspect_parser.add_argument("kind", help="Resource kind")
    resource_inspect_parser.add_argument("name", help="Resource name")
    resource_inspect_parser.add_argument("--version", help="Resource version")
    resource_inspect_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    resource_path_parser = resource_subparsers.add_parser(
        "path",
        help="Resolve a stable installed resource path for consumers",
    )
    resource_path_parser.add_argument("kind", help="Resource kind")
    resource_path_parser.add_argument("name", help="Resource name")
    resource_path_parser.add_argument("--version", help="Resource version")
    resource_path_parser.add_argument(
        "--relative-path",
        help="Optional file or directory within the installed resource",
    )
    resource_path_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    user_parser = subparsers.add_parser(
        "user",
        help="Inspect the authenticated OpenProject user",
    )
    user_parser.add_argument("--config-path", type=Path, help="Override wood-config path")
    user_parser.add_argument("--profile", help="wood-config profile to read")
    user_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    project_parser = subparsers.add_parser(
        "project",
        help="Inspect an OpenProject project",
    )
    project_parser.add_argument("--config-path", type=Path, help="Override wood-config path")
    project_parser.add_argument("--profile", help="wood-config profile to read")
    project_parser.add_argument(
        "openproject_project_id",
        nargs="?",
        help="Project numeric ID or identifier. Defaults to configured project_id.",
    )
    project_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    story_parser = subparsers.add_parser(
        "story",
        help="Inspect a work package plus its relation context",
    )
    story_parser.add_argument("--config-path", type=Path, help="Override wood-config path")
    story_parser.add_argument("--profile", help="wood-config profile to read")
    story_parser.add_argument("work_package_id", type=int)
    story_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    return parser


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(command_args)

    try:
        if args.command == "init":
            payload = init_project(
                project_root=args.project_root,
                wood_home=args.wood_home,
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
                command_args=command_args,
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
                command_args=command_args,
            )

        if args.command == "validate":
            payload = validate_project(
                project_file=args.project_file,
                project_root=args.project_root,
            )
            return _emit(
                _summarize_json_payload("validate", payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
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
                command_args=command_args,
            )

        if args.command == "resource" and args.resource_command == "install":
            payload = install_resource(
                source_dir=args.source_dir,
                project_file=args.project_file,
                project_root=args.project_root,
                apply=args.apply,
            )
            return _emit(
                (
                    _summarize_json_payload("resource-install", payload, apply=args.apply)
                    if args.json
                    else payload
                ),
                json_output=args.json,
                command_args=command_args,
            )

        if args.command == "resource" and args.resource_command == "inspect":
            payload = inspect_resource(
                kind=args.kind,
                name=args.name,
                version=args.version,
                project_file=args.project_file,
                project_root=args.project_root,
            )
            return _emit(
                _summarize_json_payload("resource-inspect", payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command == "resource" and args.resource_command == "path":
            payload = resolve_resource_path(
                kind=args.kind,
                name=args.name,
                version=args.version,
                relative_path=args.relative_path,
                project_file=args.project_file,
                project_root=args.project_root,
            )
            return _emit(
                _summarize_json_payload("resource-path", payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command in {"user", "project", "story"}:
            settings = load_settings(config_path=args.config_path, profile=args.profile)
            client = OpenProjectClient(settings)
            command_name = _command_name(args)

            if args.command == "user":
                payload = client.me()
            elif args.command == "project":
                payload = client.project(args.openproject_project_id)
            elif args.command == "story":
                payload = client.story_context(args.work_package_id)
            else:
                parser.error("Unknown OpenProject command")
                return 2

            return _emit(
                _summarize_json_payload(command_name, payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        parser.error("Unknown command")
        return 2
    except (ProjectError, OpenProjectError) as exc:
        command_name = _command_name(args)
        message = exc.message if isinstance(exc, OpenProjectError) else str(exc)
        if getattr(args, "json", False):
            payload = error_output(
                command=command_name,
                mutation=(
                    "mutating"
                    if command_name in {"init", "link-repo", "resource-install"}
                    else "read-only"
                ),
                summary=message,
                errors=(
                    [{"code": exc.code, "message": message}]
                    if isinstance(exc, OpenProjectError)
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

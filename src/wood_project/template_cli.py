from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from wood_config.audit import write_audit_event
from wood_config.output import error_output, success_output

from .core import (
    ProjectError,
    list_template_packs,
    plan_template_pack,
    render_template_pack,
    show_template_pack,
)


def _emit(
    payload: dict[str, Any], *, json_output: bool, command_args: list[str] | None = None
) -> int:
    if {"command", "status", "mutation"}.issubset(payload):
        write_audit_event(payload, cli_name="wood-template", command_args=command_args)

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


def _summarize_json_payload(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    if command == "list":
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"Resolved {len(payload['template_packs'])} template pack(s).",
            data=payload,
        )

    if command == "generate":
        template = payload["template"]
        return success_output(
            command=command,
            mutation="mutating",
            summary=(f"Generated {template['name']} into {len(payload['files'])} file(s)."),
            data=payload,
        )

    if command == "plan":
        template = payload["template"]
        return success_output(
            command=command,
            mutation="read-only",
            summary=(
                f"Planned {template['name']} into {len(payload['operations'])} operation(s) "
                f"with {len(payload['conflicts'])} conflict(s)."
            ),
            data=payload,
        )

    template_pack = payload["template_pack"]
    return success_output(
        command=command,
        mutation="read-only",
        summary=(
            f"Resolved template pack {template_pack['name']} {template_pack['version']} "
            f"from {template_pack['source']}."
        ),
        data=payload,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wood-template",
        description="Create projects from templates",
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
    parser.add_argument(
        "command",
        help="'generate', 'list', 'plan', or 'show'",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="Template pack name for 'generate', 'plan', or 'show'",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Inspect a template pack source directory before project resolution",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")

    return parser


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(command_args)

    try:
        if args.command == "list":
            if args.name is not None:
                parser.error(f"Unexpected argument for list: {args.name}")
            payload = list_template_packs(
                source_dir=args.source_dir,
                project_file=args.project_file,
                project_root=args.project_root,
            )
            return _emit(
                _summarize_json_payload("list", payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command == "show":
            if args.name is None:
                parser.error("show requires a template name")
            payload = show_template_pack(
                name=args.name,
                source_dir=args.source_dir,
                project_file=args.project_file,
                project_root=args.project_root,
            )
            return _emit(
                _summarize_json_payload("show", payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command == "plan":
            if args.name is None:
                parser.error("plan requires a template name")
            payload = plan_template_pack(
                name=args.name,
                source_dir=args.source_dir,
                project_file=args.project_file,
                project_root=args.project_root,
            )
            return _emit(
                _summarize_json_payload("plan", payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command != "generate":
            parser.error(f"Unknown command: {args.command}")
        if args.name is None:
            parser.error("generate requires a template name")
        payload = render_template_pack(
            name=args.name,
            source_dir=args.source_dir,
            project_file=args.project_file,
            project_root=args.project_root,
        )
        return _emit(
            _summarize_json_payload("generate", payload) if args.json else payload,
            json_output=args.json,
            command_args=command_args,
        )
    except ProjectError as exc:
        if getattr(args, "json", False):
            command = args.command
            payload = error_output(
                command=command,
                mutation="mutating" if command == "generate" else "read-only",
                summary=str(exc),
                errors=[{"message": str(exc)}],
                next_actions=["Review the template pack inputs and try again."],
            )
            write_audit_event(payload, cli_name="wood-template", command_args=command_args)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

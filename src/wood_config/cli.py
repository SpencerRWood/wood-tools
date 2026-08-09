from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from resources.cli.audit import write_audit_event
from resources.cli.output import blocked_output, error_output, success_output, warning_output

from .core import (
    ConfigError,
    build_paths,
    doctor_config,
    get_value,
    init_config,
    load_config,
    resolve_path_aliases,
    save_config,
    set_value,
    show_config,
    validate_config,
)


def _emit(
    payload: dict[str, Any], *, json_output: bool, command_args: list[str] | None = None
) -> int:
    if {"command", "status", "mutation"}.issubset(payload):
        write_audit_event(payload, cli_name="wood-config", command_args=command_args)

    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for key, value in payload.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for sub_key, sub_value in value.items():
                print(f"  {sub_key}={sub_value}")
        else:
            print(f"{key}: {value}")
    return 0


def _emit_validation(payload: dict[str, Any], *, json_output: bool) -> int:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["valid"] else 2

    print(f"profile: {payload['profile']}")
    if payload["valid"]:
        print("status: valid")
        return 0

    print("status: invalid")
    for issue in payload["errors"]:
        print(f"- [{issue['code']}] {issue['field']}: {issue['message']}")
        print(f"  remediation: {issue['remediation']}")
    return 2


def _emit_doctor(payload: dict[str, Any], *, json_output: bool) -> int:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"profile: {payload['profile']}")
    print(f"status: {payload['status']}")
    print(f"checks: {', '.join(check['name'] for check in payload['checks'])}")
    print(f"issues: {payload['summary']['issue_count']}")
    for issue in payload["issues"]:
        print(f"- [{issue['code']}] {issue['field']}: {issue['message']}")
        print(f"  remediation: {issue['remediation']}")
    return 0


def _parse_value(raw_value: str) -> Any:
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value


def _command_mutation(command: str) -> str:
    if command in {"init", "set"}:
        return "mutating"
    return "read-only"


def _summarize_json_payload(
    command: str,
    payload: dict[str, Any],
    *,
    apply: bool | None = None,
) -> dict[str, Any]:
    if command == "init":
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=f"Config initialized at {payload['path']}.",
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=f"Config initialization requires approval to write {payload['path']}.",
            data=payload,
            next_actions=["Re-run with --apply to create the config file."],
        )

    if command == "show":
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"Loaded config for profile {payload['selected_profile']}.",
            data=payload,
        )

    if command == "get":
        return success_output(
            command=command,
            mutation="read-only",
            summary=f"Read config key {payload['key']}.",
            data=payload,
        )

    if command == "set":
        profile = payload["profile"]
        if apply:
            return success_output(
                command=command,
                mutation="mutating",
                summary=f"Updated config key {payload['key']} for profile {profile}.",
                data=payload,
            )
        return blocked_output(
            command=command,
            summary=f"Config update for key {payload['key']} is waiting for approval.",
            data=payload,
            next_actions=["Re-run with --apply to persist the config change."],
        )

    if command == "validate":
        errors = payload["errors"]
        next_actions = [issue["remediation"] for issue in errors if issue.get("remediation")]
        if payload["valid"]:
            return success_output(
                command=command,
                mutation="read-only",
                summary=f"Config profile {payload['profile']} is valid.",
                data=payload,
            )
        return warning_output(
            command=command,
            mutation="read-only",
            summary=f"Config profile {payload['profile']} has validation issues.",
            data=payload,
            errors=errors,
            next_actions=next_actions,
        )

    if command == "doctor":
        issues = payload["issues"]
        next_actions = [issue["remediation"] for issue in issues if issue.get("remediation")]
        if payload["status"] == "ok":
            return success_output(
                command=command,
                mutation="read-only",
                summary=f"Diagnostics passed for profile {payload['profile']}.",
                data=payload,
            )
        return warning_output(
            command=command,
            mutation="read-only",
            summary=f"Diagnostics found issues for profile {payload['profile']}.",
            data=payload,
            warnings=issues,
            next_actions=next_actions,
        )

    return success_output(
        command=command,
        mutation=_command_mutation(command),
        summary=f"Completed {command}.",
        data=payload,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wood-config", description="Manage wood-tools config")
    parser.add_argument("--config-path", type=Path, help="Override config file path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize config file")
    init_parser.add_argument("--apply", action="store_true", help="Write config file")
    init_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    show_parser = subparsers.add_parser("show", help="Show config values")
    show_parser.add_argument("--profile", help="Profile to show")
    show_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    get_parser = subparsers.add_parser("get", help="Get one config value")
    get_parser.add_argument("key", help="Config key name")
    get_parser.add_argument("--profile", help="Profile to read")
    get_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    set_parser = subparsers.add_parser("set", help="Set one config value")
    set_parser.add_argument("key", help="Config key name")
    set_parser.add_argument("value", help="Value (JSON literal or raw string)")
    set_parser.add_argument("--profile", help="Profile to write")
    set_parser.add_argument(
        "--activate-profile",
        action="store_true",
        help="Set selected profile as active after writing",
    )
    set_parser.add_argument("--apply", action="store_true", help="Write config changes")
    set_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    validate_parser = subparsers.add_parser("validate", help="Validate required config settings")
    validate_parser.add_argument("--profile", help="Profile to validate")
    validate_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    doctor_parser = subparsers.add_parser("doctor", help="Run config diagnostics")
    doctor_parser.add_argument("--profile", help="Profile to diagnose")
    doctor_parser.add_argument(
        "--check",
        dest="checks",
        action="append",
        choices=("vaultwarden", "openproject", "ntfy", "scheduler", "agent-readiness"),
        help="Run only the named doctor check (repeatable)",
    )
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    return parser


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(command_args)
    paths = build_paths(args.config_path)

    try:
        if args.command == "init":
            payload = init_config(paths, apply=args.apply)
            return _emit(
                _summarize_json_payload(args.command, payload, apply=args.apply)
                if args.json
                else payload,
                json_output=args.json,
                command_args=command_args,
            )

        document = load_config(paths)

        if args.command == "show":
            payload = show_config(document, profile=args.profile)
            return _emit(
                _summarize_json_payload(args.command, payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command == "get":
            value = get_value(document, args.key, profile=args.profile)
            profile_name = args.profile or document["active_profile"]
            profile_values = document["profiles"][profile_name]
            alias_resolution = resolve_path_aliases(profile_values)
            payload = {
                "key": args.key,
                "profile": profile_name,
                "value": value,
                "alias_resolution": alias_resolution,
            }
            key_parts = args.key.split(".")
            if len(key_parts) >= 3 and key_parts[:2] == ["paths", "project_aliases"]:
                payload["resolved_value"] = alias_resolution["project_aliases"].get(key_parts[2])
            if len(key_parts) >= 3 and key_parts[:2] == ["paths", "artifact_aliases"]:
                payload["resolved_value"] = alias_resolution["artifact_aliases"].get(key_parts[2])
            return _emit(
                _summarize_json_payload(args.command, payload) if args.json else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command == "set":
            parsed_value = _parse_value(args.value)
            updated = set_value(
                document,
                args.key,
                parsed_value,
                profile=args.profile,
                activate_profile=args.activate_profile,
            )

            changed = False
            if args.apply:
                save_config(paths, updated)
                changed = True

            payload = {
                "changed": changed,
                "path": str(paths.file_path),
                "active_profile": updated["active_profile"],
                "key": args.key,
                "value": parsed_value,
                "profile": args.profile or updated["active_profile"],
            }
            return _emit(
                _summarize_json_payload(args.command, payload, apply=args.apply)
                if args.json
                else payload,
                json_output=args.json,
                command_args=command_args,
            )

        if args.command == "validate":
            payload = validate_config(document, profile=args.profile)
            if args.json:
                _emit(
                    _summarize_json_payload(args.command, payload),
                    json_output=True,
                    command_args=command_args,
                )
                return 0 if payload["valid"] else 2
            return _emit_validation(payload, json_output=False)

        if args.command == "doctor":
            payload = doctor_config(document, profile=args.profile, checks=args.checks)
            return (
                _emit(
                    _summarize_json_payload(args.command, payload) if args.json else payload,
                    json_output=args.json,
                    command_args=command_args,
                )
                if args.json
                else _emit_doctor(payload, json_output=False)
            )

        parser.error("Unknown command")
        return 2

    except ConfigError as exc:
        if getattr(args, "json", False):
            payload = error_output(
                command=args.command,
                mutation=_command_mutation(args.command),
                summary=str(exc),
                errors=[{"message": str(exc)}],
                next_actions=["Review the command input and try again."],
            )
            write_audit_event(payload, cli_name="wood-config", command_args=command_args)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

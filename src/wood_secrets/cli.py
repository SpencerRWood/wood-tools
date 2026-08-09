from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from resources.cli.audit import write_audit_event
from resources.cli.output import error_output, success_output, warning_output

from .core import SecretResolver
from .core.providers import SecretProviderError


def _emit(
    payload: dict[str, Any], *, json_output: bool, command_args: list[str] | None = None
) -> int:
    if {"command", "status", "mutation"}.issubset(payload):
        write_audit_event(payload, cli_name="wood-secrets", command_args=command_args)

    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for key, value in payload.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for sub_key, sub_value in value.items():
                print(f"  {sub_key}: {sub_value}")
        elif isinstance(value, list):
            print(f"{key}:")
            for item in value:
                print(f"  - {item}")
        else:
            print(f"{key}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wood-secrets",
        description="Validate and resolve secret references without printing secret values.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check",
        help="Check configured integration secret references or one explicit reference",
    )
    check_parser.add_argument("--ref", help="Secret reference to validate and resolve redacted")
    check_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    providers_parser = subparsers.add_parser("providers", help="List registered secret providers")
    providers_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    status_parser = subparsers.add_parser("status", help="Check one provider status")
    status_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden", "env"),
        help="Provider name to inspect",
    )
    status_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    unlock_parser = subparsers.add_parser("unlock", help="Unlock one provider")
    unlock_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden",),
        help="Provider name to unlock",
    )
    unlock_mode = unlock_parser.add_mutually_exclusive_group()
    unlock_mode.add_argument("--interactive", action="store_true", help="Prompt in the terminal")
    unlock_mode.add_argument("--gui", action="store_true", help="Prompt with a GUI dialog")
    unlock_parser.add_argument(
        "--write-session",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write the unlocked session token to the protected runtime session file",
    )
    unlock_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    lock_parser = subparsers.add_parser("lock", help="Lock one provider")
    lock_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden",),
        help="Provider name to lock",
    )
    lock_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    session_parser = subparsers.add_parser("session", help="Inspect runtime session status")
    session_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden", "env"),
        help="Provider name to inspect",
    )
    session_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    list_parser = subparsers.add_parser(
        "list",
        help="List vault items and field names without exposing secret values",
    )
    list_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden",),
        help="Provider name to inspect",
    )
    list_parser.add_argument("--search", help="Optional provider-native search term")
    list_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    exec_parser = subparsers.add_parser(
        "exec",
        help="Run a command with resolved secrets injected as environment variables",
    )
    exec_parser.add_argument("--env", help="Environment variable name to set")
    exec_parser.add_argument("--ref", help="Secret reference to resolve")
    exec_parser.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="Optional NAME=reference bindings, then '--', then the command to run",
    )

    resolve_parser = subparsers.add_parser("resolve", help="Resolve one secret reference")
    resolve_parser.add_argument("--ref", required=True, help="Secret reference to resolve")
    resolve_parser.add_argument(
        "--redacted",
        action="store_true",
        help="Emit only redacted secret output.",
    )
    resolve_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    doctor_parser = subparsers.add_parser("doctor", help="Diagnose provider readiness")
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(command_args)
    resolver = SecretResolver()

    try:
        if args.command == "check":
            payload = resolver.check(args.ref)
            if args.json:
                envelope_builder = success_output if payload["ok"] else warning_output
                summary = (
                    "Secret reference checks completed."
                    if payload["ok"]
                    else "One or more secret references are not ready."
                )
                envelope = envelope_builder(
                    command="check",
                    mutation="read-only",
                    summary=summary,
                    data=payload,
                    warnings=(
                        payload.get("checks") if not payload["ok"] and args.ref is None else None
                    ),
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "providers":
            payload = resolver.providers()
            if args.json:
                envelope_builder = success_output if payload["ok"] else warning_output
                envelope = envelope_builder(
                    command="providers",
                    mutation="read-only",
                    summary="Secret provider listing completed.",
                    data=payload,
                    warnings=payload["providers"] if not payload["ok"] else None,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "status":
            payload = resolver.status(args.provider)
            if args.json:
                envelope = success_output(
                    command="status",
                    mutation="read-only",
                    summary="Secret provider checks completed.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "unlock":
            interactive = args.interactive or not args.gui
            payload = resolver.unlock(
                args.provider,
                interactive=interactive,
                gui=args.gui,
                write_session=args.write_session,
            )
            if args.json:
                envelope = success_output(
                    command="unlock",
                    mutation="mutating",
                    summary="Unlocked secret provider session.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            print(
                "Unlocked "
                f"{payload['provider']}; session {payload['session']['state']} "
                f"at {payload['session']['path']}."
            )
            return 0

        if args.command == "lock":
            payload = resolver.lock(args.provider)
            if args.json:
                envelope = success_output(
                    command="lock",
                    mutation="mutating",
                    summary="Locked secret provider session.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "session":
            payload = resolver.session(args.provider)
            if args.json:
                envelope = success_output(
                    command="session",
                    mutation="read-only",
                    summary="Secret runtime session status completed.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "list":
            payload = resolver.list_entries(args.provider, search=args.search)
            if args.json:
                envelope = success_output(
                    command="list",
                    mutation="read-only",
                    summary="Listed secret entries without exposing secret values.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "resolve":
            resolved = resolver.resolve(args.ref)
            payload = resolved.to_dict()
            if args.redacted:
                payload["display_value"] = resolved.redacted_value
            if args.json:
                envelope = success_output(
                    command="resolve",
                    mutation="read-only",
                    summary="Resolved secret reference without exposing the secret value.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "exec":
            raw_args = args.command_args
            separator_index = raw_args.index("--") if "--" in raw_args else None
            binding_args = raw_args if separator_index is None else raw_args[:separator_index]
            command = [] if separator_index is None else raw_args[separator_index + 1 :]

            bindings: dict[str, str] = {}
            if args.env or args.ref:
                if not args.env or not args.ref:
                    raise SecretProviderError(
                        "exec requires both --env and --ref when either is used."
                    )
                bindings[args.env] = args.ref
            for binding in binding_args:
                name, separator, reference = binding.partition("=")
                if not separator or not name.strip() or not reference.strip():
                    raise SecretProviderError(
                        "Inline exec bindings must use NAME=reference syntax."
                    )
                bindings[name.strip()] = reference.strip()

            return resolver.exec_with_secrets(bindings, command)

        if args.command == "doctor":
            payload = resolver.doctor()
            if args.json:
                envelope_builder = success_output if payload["status"] == "ok" else warning_output
                envelope = envelope_builder(
                    command="doctor",
                    mutation="read-only",
                    summary="Secret provider diagnostics completed.",
                    data=payload,
                    warnings=payload["issues"] if payload["status"] != "ok" else None,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        parser.error("Unknown command")
        return 2
    except SecretProviderError as exc:
        mutation = "mutating" if args.command in {"unlock", "lock"} else "read-only"
        if getattr(args, "json", False):
            payload = error_output(
                command=args.command,
                mutation=mutation,
                summary=str(exc),
                errors=[{"message": str(exc)}],
                next_actions=[
                    (
                        "Check the secret reference syntax, provider status, "
                        "or configured env fallback."
                    ),
                ],
            )
            write_audit_event(payload, cli_name="wood-secrets", command_args=command_args)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

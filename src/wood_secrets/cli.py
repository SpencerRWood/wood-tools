from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from wood_config.output import error_output, success_output, warning_output

from .core import SecretResolver
from .providers import SecretProviderError


def _emit(payload: dict[str, Any], *, json_output: bool) -> int:
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
    unlock_mode = unlock_parser.add_mutually_exclusive_group(required=True)
    unlock_mode.add_argument("--interactive", action="store_true", help="Prompt in the terminal")
    unlock_mode.add_argument("--gui", action="store_true", help="Prompt with a GUI dialog")
    unlock_parser.add_argument(
        "--write-session",
        action="store_true",
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
    parser = build_parser()
    args = parser.parse_args(argv)
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
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

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
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

        if args.command == "status":
            payload = resolver.status(args.provider)
            if args.json:
                envelope = success_output(
                    command="status",
                    mutation="read-only",
                    summary="Secret provider checks completed.",
                    data=payload,
                )
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

        if args.command == "unlock":
            payload = resolver.unlock(
                args.provider,
                interactive=args.interactive,
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
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

        if args.command == "lock":
            payload = resolver.lock(args.provider)
            if args.json:
                envelope = success_output(
                    command="lock",
                    mutation="mutating",
                    summary="Locked secret provider session.",
                    data=payload,
                )
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

        if args.command == "session":
            payload = resolver.session(args.provider)
            if args.json:
                envelope = success_output(
                    command="session",
                    mutation="read-only",
                    summary="Secret runtime session status completed.",
                    data=payload,
                )
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

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
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

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
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

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
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

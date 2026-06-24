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

    check_parser = subparsers.add_parser("check", help="Check provider readiness or one reference")
    check_parser.add_argument("--ref", help="Secret reference to validate")
    check_parser.add_argument("--json", action="store_true", help="Emit JSON output")

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

    providers_parser = subparsers.add_parser("providers", help="List registered providers")
    providers_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resolver = SecretResolver()

    try:
        if args.command == "check":
            payload = resolver.check(reference=args.ref)
            if args.json:
                envelope = success_output(
                    command="check",
                    mutation="read-only",
                    summary="Secret provider checks completed.",
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

        if args.command == "providers":
            payload = {
                "providers": [status.to_dict() for status in resolver.provider_statuses()],
            }
            if args.json:
                envelope = success_output(
                    command="providers",
                    mutation="read-only",
                    summary="Listed registered secret providers.",
                    data=payload,
                )
                return _emit(envelope, json_output=True)
            return _emit(payload, json_output=False)

        parser.error("Unknown command")
        return 2
    except SecretProviderError as exc:
        if getattr(args, "json", False):
            payload = error_output(
                command=args.command,
                mutation="read-only",
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

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core import (
    ConfigError,
    build_paths,
    get_value,
    init_config,
    load_config,
    save_config,
    set_value,
    show_config,
)


def _emit(payload: dict[str, Any], *, json_output: bool) -> int:
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


def _parse_value(raw_value: str) -> Any:
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = build_paths(args.config_path)

    try:
        if args.command == "init":
            payload = init_config(paths, apply=args.apply)
            return _emit(payload, json_output=args.json)

        document = load_config(paths)

        if args.command == "show":
            payload = show_config(document, profile=args.profile)
            return _emit(payload, json_output=args.json)

        if args.command == "get":
            value = get_value(document, args.key, profile=args.profile)
            payload = {
                "key": args.key,
                "profile": args.profile or document["active_profile"],
                "value": value,
            }
            return _emit(payload, json_output=args.json)

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
            return _emit(payload, json_output=args.json)

        parser.error("Unknown command")
        return 2

    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..openproject import OpenProjectClient, load_settings

COMMANDS = {"user", "project"}


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-path", type=Path, help="Override wood-config path")
    parser.add_argument("--profile", help="wood-config profile to read")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    user_parser = subparsers.add_parser("user", help="Inspect the authenticated OpenProject user")
    _add_connection_options(user_parser)

    project_parser = subparsers.add_parser("project", help="Inspect an OpenProject project")
    project_parser.add_argument(
        "openproject_project_id",
        nargs="?",
        help="Project numeric ID or identifier. Defaults to deprecated project_id or initiative.",
    )
    _add_connection_options(project_parser)


def handles(args: argparse.Namespace) -> bool:
    return args.command in COMMANDS


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    client = OpenProjectClient(load_settings(config_path=args.config_path, profile=args.profile))
    if args.command == "user":
        payload = client.me()
    else:
        payload = client.project(args.openproject_project_id)
    return args.command, payload, None

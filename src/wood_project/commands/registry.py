from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..core.registry import import_openproject_registry

COMMANDS = {"registry"}


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    registry_parser = subparsers.add_parser(
        "registry",
        help="Import editable project registry manifests",
    )
    registry_subparsers = registry_parser.add_subparsers(
        dest="registry_command",
        required=True,
    )

    import_parser = registry_subparsers.add_parser(
        "import",
        help="Preview or import an OpenProject project registry manifest",
    )
    import_parser.add_argument("path", type=Path, help="Path to a JSON or YAML registry manifest")
    import_parser.add_argument(
        "--registry-path",
        help="Global Wood config path. Default: ~/.config/wood-tools/config.json",
    )
    import_parser.add_argument("--apply", action="store_true", help="Write the registry config")
    import_parser.add_argument("--json", action="store_true", help="Emit JSON output")


def handles(args: argparse.Namespace) -> bool:
    return args.command in COMMANDS


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    return (
        "registry-import",
        import_openproject_registry(
            manifest_path=args.path,
            registry_path=args.registry_path,
            apply=args.apply,
        ),
        args.apply,
    )

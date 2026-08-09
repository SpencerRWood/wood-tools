from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from resources.packages import inspect_resource, install_resource, resolve_resource_path

from ..core.documents import load_project_document
from ..core.paths import PROJECT_FILE_NAME, resolve_project_root
from ..core.validation import validate_project_state


def _wood_home(args: argparse.Namespace) -> Path:
    root = resolve_project_root(args.project_root)
    document = load_project_document(args.project_file or root / PROJECT_FILE_NAME)
    validate_project_state(document)
    return Path(document["wood_home"])


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    resource_parser = subparsers.add_parser("resource", help="Install and inspect resources")
    resource_subparsers = resource_parser.add_subparsers(dest="resource_command", required=True)

    install_parser = resource_subparsers.add_parser(
        "install", help="Install a versioned resource from a directory with wood-resource.json"
    )
    install_parser.add_argument("source_dir", type=Path, help="Resource source directory")
    install_parser.add_argument("--apply", action="store_true", help="Install into Wood home")
    install_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    inspect_parser = resource_subparsers.add_parser(
        "inspect", help="Inspect installed resource metadata and verify its digest"
    )
    inspect_parser.add_argument("kind", help="Resource kind")
    inspect_parser.add_argument("name", help="Resource name")
    inspect_parser.add_argument("--version", help="Resource version")
    inspect_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    path_parser = resource_subparsers.add_parser(
        "path", help="Resolve a stable installed resource path for consumers"
    )
    path_parser.add_argument("kind", help="Resource kind")
    path_parser.add_argument("name", help="Resource name")
    path_parser.add_argument("--version", help="Resource version")
    path_parser.add_argument("--relative-path", help="Path within the installed resource")
    path_parser.add_argument("--json", action="store_true", help="Emit JSON output")


def handles(args: argparse.Namespace) -> bool:
    return args.command == "resource"


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    if args.resource_command == "install":
        return (
            "resource-install",
            install_resource(
                source_dir=args.source_dir,
                wood_home=_wood_home(args),
                apply=args.apply,
            ),
            args.apply,
        )
    if args.resource_command == "inspect":
        return (
            "resource-inspect",
            inspect_resource(
                kind=args.kind,
                name=args.name,
                version=args.version,
                wood_home=_wood_home(args),
            ),
            None,
        )
    return (
        "resource-path",
        resolve_resource_path(
            kind=args.kind,
            name=args.name,
            version=args.version,
            relative_path=args.relative_path,
            wood_home=_wood_home(args),
        ),
        None,
    )

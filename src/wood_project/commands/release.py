from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..release import bump_version, check_release, create_github_release, create_tag

COMMAND = "release"


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit JSON output")


def _add_pyproject_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml. Default: pyproject.toml.",
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("release", help="Manage deterministic release workflows")
    release_subparsers = parser.add_subparsers(dest="release_command", required=True)

    check_parser = release_subparsers.add_parser(
        "check", help="Run read-only deterministic release readiness checks"
    )
    check_parser.add_argument(
        "--version", help="Explicit X.Y.Z version. Defaults to pyproject.toml."
    )
    check_parser.add_argument("--root-work-package-id", type=int)
    check_parser.add_argument("--openproject-version")
    check_parser.add_argument("--config-path", type=Path, help="Override wood-config path")
    check_parser.add_argument("--profile", help="wood-config profile to read")
    check_parser.add_argument("--type", default="Story")
    check_parser.add_argument("--page-size", type=int, default=1000)
    _add_pyproject_option(check_parser)
    _add_common_options(check_parser)

    bump_parser = release_subparsers.add_parser(
        "bump", help="Preview or apply a pyproject.toml version bump"
    )
    bump_parser.add_argument("bump", help="patch, minor, major, or an explicit X.Y.Z version")
    bump_parser.add_argument("--apply", action="store_true", help="Apply the version mutation")
    _add_pyproject_option(bump_parser)
    _add_common_options(bump_parser)

    tag_parser = release_subparsers.add_parser("tag", help="Preview or create a local release tag")
    tag_parser.add_argument("--version", help="Explicit X.Y.Z version. Defaults to pyproject.toml.")
    tag_parser.add_argument("--apply", action="store_true", help="Apply the git mutation")
    tag_parser.add_argument("--allow-dirty", action="store_true")
    _add_pyproject_option(tag_parser)
    _add_common_options(tag_parser)

    github_parser = release_subparsers.add_parser(
        "github-create", help="Preview or create a GitHub release from an existing tag"
    )
    github_parser.add_argument(
        "--version", help="Explicit X.Y.Z version. Defaults to pyproject.toml."
    )
    github_parser.add_argument("--generate-notes", action="store_true")
    github_parser.add_argument("--notes-from-history", action="store_true")
    github_parser.add_argument("--apply", action="store_true", help="Apply the GitHub mutation")
    _add_pyproject_option(github_parser)
    _add_common_options(github_parser)


def handles(args: argparse.Namespace) -> bool:
    return args.command == COMMAND


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    command = f"release-{args.release_command}"
    if args.release_command == "check":
        return (
            command,
            check_release(
                version=args.version,
                pyproject=args.pyproject,
                root_work_package_id=args.root_work_package_id,
                openproject_version=args.openproject_version,
                config_path=args.config_path,
                profile=args.profile,
                story_type=args.type,
                page_size=args.page_size,
            ),
            None,
        )
    if args.release_command == "bump":
        return (
            command,
            bump_version(bump=args.bump, pyproject=args.pyproject, apply=args.apply),
            args.apply,
        )
    if args.release_command == "tag":
        return (
            command,
            create_tag(
                version=args.version,
                pyproject=args.pyproject,
                apply=args.apply,
                allow_dirty=args.allow_dirty,
            ),
            args.apply,
        )
    if args.release_command == "github-create":
        return (
            command,
            create_github_release(
                version=args.version,
                pyproject=args.pyproject,
                generate_notes=args.generate_notes,
                notes_from_history=args.notes_from_history,
                apply=args.apply,
            ),
            args.apply,
        )
    raise AssertionError(f"Unknown release command: {args.release_command}")

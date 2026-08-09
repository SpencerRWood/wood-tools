from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..core.project import init_project, link_repository, show_project, validate_project

COMMANDS = {"init", "show", "validate", "link"}


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    init_parser = subparsers.add_parser("init", help="Initialize project metadata")
    init_parser.add_argument("--project-id", help="Provide an explicit project ID")
    init_parser.add_argument("--project-slug", help="Provide an explicit project slug")
    init_parser.add_argument(
        "--wood-home",
        type=Path,
        help="Override the user-global Wood home (defaults to WOOD_HOME or ~/.wood)",
    )
    init_parser.add_argument("--apply", action="store_true", help="Write project.json")
    init_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    show_parser = subparsers.add_parser("show", help="Show project metadata")
    show_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    validate_parser = subparsers.add_parser("validate", help="Validate project metadata")
    validate_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    link_parser = subparsers.add_parser("link", help="Link project resources")
    link_subparsers = link_parser.add_subparsers(dest="link_command", required=True)
    link_repo_parser = link_subparsers.add_parser("repo", help="Link an implementation repository")
    link_repo_parser.add_argument("repo_path", type=Path, help="Path to the repository to link")
    link_repo_parser.add_argument("--name", help="Optional repository name")
    link_repo_parser.add_argument("--role", help="Optional repository role")
    link_repo_parser.add_argument("--apply", action="store_true", help="Write project.json")
    link_repo_parser.add_argument("--json", action="store_true", help="Emit JSON output")


def handles(args: argparse.Namespace) -> bool:
    return args.command in COMMANDS


def run(args: argparse.Namespace) -> tuple[str, dict[str, Any], bool | None]:
    if args.command == "init":
        return (
            "init",
            init_project(
                project_root=args.project_root,
                wood_home=args.wood_home,
                project_id=args.project_id,
                project_slug=args.project_slug,
                apply=args.apply,
            ),
            args.apply,
        )
    if args.command == "show":
        project = show_project(project_file=args.project_file, project_root=args.project_root)
        return (
            "show",
            {
                "path": str(args.project_file or Path(project["project_root"]) / "project.json"),
                "project": project,
            },
            None,
        )
    if args.command == "validate":
        return (
            "validate",
            validate_project(project_file=args.project_file, project_root=args.project_root),
            None,
        )
    return (
        "link-repo",
        link_repository(
            repo_path=args.repo_path,
            repo_name=args.name,
            repo_role=args.role,
            project_file=args.project_file,
            project_root=args.project_root,
            apply=args.apply,
        ),
        args.apply,
    )

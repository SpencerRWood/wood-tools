"""Public adapters for repository, CI, and deployment inspection."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from . import operations
from .output import Status, envelope


def add_operations_parsers(commands: argparse._SubParsersAction[Any]) -> None:
    repo = commands.add_parser("repo", help="Inspect repository standards and validation")
    repo_actions = repo.add_subparsers(dest="repo_command", required=True)
    for name in ("info", "standards", "validate"):
        parser = repo_actions.add_parser(name)
        parser.add_argument("--json", dest="operations_json", action="store_true")
    ci = commands.add_parser("ci", help="Inspect centralized validation")
    ci_actions = ci.add_subparsers(dest="ci_command", required=True)
    for name in ("status", "failures"):
        parser = ci_actions.add_parser(name)
        parser.add_argument("--json", dest="operations_json", action="store_true")
    deploy = commands.add_parser("deploy", help="Inspect deployment evidence")
    deploy_actions = deploy.add_subparsers(dest="deploy_command", required=True)
    status = deploy_actions.add_parser("status")
    status.add_argument("--environment")
    status.add_argument("--json", dest="operations_json", action="store_true")


def run_operations_command(args: argparse.Namespace, cwd: Path) -> dict[str, object]:
    group = args.command
    action = getattr(args, f"{group}_command")
    command = f"{group} {action}"
    try:
        root = operations.repository_root(cwd)
        status: Status = "success"
        if group == "repo":
            if action == "info":
                data = operations.repo_info(root)
            elif action == "standards":
                data = operations.repo_standards(root)
                if not data["conformant"]:
                    status = "invalid"
            else:
                data = operations.repo_validate(root)
                if not data["passed"]:
                    checks = cast(list[dict[str, object]], data["checks"])
                    states = {item["state"] for item in checks}
                    status = (
                        "error"
                        if "failed" in states
                        else "unavailable"
                        if "unavailable" in states
                        else "unsupported"
                    )
        elif group == "ci":
            status, data = (
                operations.ci_status(root) if action == "status" else operations.ci_failures(root)
            )
        else:
            status, data = operations.deploy_status(root, args.environment)
        return envelope(command=command, status=status, summary=f"{command}: {status}.", data=data)
    except operations.OperationsError as exc:
        return envelope(
            command=command,
            status=exc.status,
            summary=str(exc),
            errors=[{"code": exc.code, "message": str(exc)}],
        )

"""Public repo initialization, routing and enforcement adapters."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import initialization, migration, protection, repository_routing
from .output import Mutation, Status, envelope
from .workflow_files import read_json, run_directory, write_json


def add_parsers(actions: argparse._SubParsersAction[Any]) -> None:
    migrate = actions.add_parser(
        "migrate", help="Assess/prepare existing repositories without publication"
    )
    migrations = migrate.add_subparsers(dest="migration_command", required=True)
    for action in ("assess", "plan", "apply"):
        parser = migrations.add_parser(action)
        parser.add_argument("--json", dest="operations_json", action="store_true")
        if action == "plan":
            parser.add_argument("--baseline", required=True)
            parser.add_argument("--baseline-approval", required=True)
            parser.add_argument("--destination", type=Path, required=True)
            parser.add_argument("--branch", required=True)
            parser.add_argument("--spec", type=Path, required=True)
            parser.add_argument("--contract", type=Path)
        elif action == "apply":
            parser.add_argument("--plan", type=Path, required=True)
            parser.add_argument("--plan-hash", required=True)
            parser.add_argument("--initialize-development", action="store_true")
    init = actions.add_parser("init", help="Plan/apply one approved local baseline initialization")
    init.add_argument("--source", type=Path, required=True)
    init.add_argument("--baseline", required=True)
    init.add_argument("--baseline-approval", required=True)
    init.add_argument("--destination", type=Path, required=True)
    init.add_argument("--spec", type=Path, required=True)
    init.add_argument("--plan-hash")
    init.add_argument("--apply", action="store_true")
    init.add_argument("--json", dest="operations_json", action="store_true")
    protect = actions.add_parser("protection", help="Plan/apply and read back GitHub enforcement")
    protection_actions = protect.add_subparsers(dest="protection_command", required=True)
    for action in ("plan", "apply", "readback"):
        parser = protection_actions.add_parser(action)
        parser.add_argument("--spec", type=Path, required=True)
        parser.add_argument("--json", dest="operations_json", action="store_true")
        if action == "apply":
            parser.add_argument("--plan-hash", required=True)
            parser.add_argument("--approval-reference", required=True)
        if action in {"plan", "apply"}:
            parser.add_argument("--candidate-pr", type=int)
    route = actions.add_parser("route", help="Verify exact-candidate PR routing and ancestry")
    route.add_argument("--event-file", type=Path, required=True)
    route.add_argument("--github-output", type=Path)
    route.add_argument("--json", dest="operations_json", action="store_true")
    release = actions.add_parser("release-context", help="Extract merged PR hotfix references")
    release.add_argument("--revision", required=True)
    release.add_argument("--github-output", type=Path)
    release.add_argument("--json", dest="operations_json", action="store_true")


def run(args: argparse.Namespace, root: Path) -> dict[str, object]:
    action = args.repo_command
    command = f"repo {action}"
    mutation: Mutation = "read-only"
    status: Status = "success"
    if action == "migrate":
        command += f" {args.migration_command}"
        if args.migration_command == "assess":
            record = migration.assess(root)
        elif args.migration_command == "plan":
            record = migration.plan(
                root,
                args.baseline,
                args.baseline_approval,
                args.destination,
                args.branch,
                read_json(args.spec),
                args.contract.read_text() if args.contract else None,
            )
            mutation = "preview"
        else:
            data = migration.apply(
                root, read_json(args.plan), args.plan_hash, args.initialize_development
            )
            return envelope(
                command=command,
                status="success",
                mutation="mutating",
                data=data,
                summary="Migration prepared; validation and separate enforcement remain.",
            )
        path = run_directory(root, "wood-migration-") / f"{args.migration_command}.json"
        write_json(path, record)
        observed = record.get("observed", record)
        data = {
            "record_file": str(path),
            "plan_hash": record.get("plan_hash"),
            "repository": observed["repository"],
            "remote_branches": observed["remote_branches"],
            "active_claim_count": len(observed["claims"]),
            "open_pr_count": len(observed["open_prs"]),
            "changed_file_count": len(record.get("contents", {})),
            "enforcement_verified": False,
            "publication_authorized": False,
        }
    elif action == "init":
        record, files = initialization.plan(
            args.source,
            args.baseline,
            args.baseline_approval,
            args.destination,
            read_json(args.spec),
        )
        path = run_directory(root, "wood-repo-init-") / "plan.json"
        write_json(path, record)
        data = {
            "plan_file": str(path),
            "plan_hash": record["plan_hash"],
            "baseline_source_revision": record["source_revision"],
            "destination": record["destination"],
            "branches": ["main", "development"],
            "enforcement_verified": False,
            "file_count": len(files),
        }
        if args.apply:
            data.update(initialization.apply(record, files, args.plan_hash or ""))
        mutation = "mutating" if args.apply else "preview"
    elif action == "protection":
        subcommand = args.protection_command
        command += f" {subcommand}"
        spec = read_json(args.spec)
        if subcommand == "plan":
            record = protection.plan(root, spec, args.candidate_pr)
            mutation = "preview"
        elif subcommand == "apply":
            record = protection.apply(
                root, spec, args.plan_hash, args.approval_reference, args.candidate_pr
            )
            mutation = "mutating"
            status = "success" if record["readback"]["passed"] else "blocked"
        else:
            record = protection.readback(root, spec)
            status = "success" if record["passed"] else "blocked"
        path = run_directory(root, "wood-protection-") / f"{subcommand}.json"
        write_json(path, record)
        data = {**record, "record_file": str(path)}
    else:
        data = (
            repository_routing.route(root, read_json(args.event_file))
            if action == "route"
            else repository_routing.release_context(root, args.revision)
        )
        if args.github_output:
            repository_routing.github_outputs(args.github_output, data)
    return envelope(
        command=command,
        status=status,
        mutation=mutation,
        data=data,
        summary=f"{command}: {status}.",
    )

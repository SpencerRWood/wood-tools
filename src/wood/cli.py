"""The only installed Wood Tools v2 command."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Never

from resources.cli.audit import write_audit_event

from .diagnostics import doctor, secret_command
from .output import EXIT_CODES, envelope, exit_code, render
from .project import run_project_command


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise _ArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="wood", description="Wood Tools v2 agent CLI")
    parser.add_argument("--json", action="store_true", help="Emit the structured v2 envelope")
    commands = parser.add_subparsers(dest="command")
    contract = commands.add_parser(
        "contract", help="Show the CLI contract and available capabilities"
    )
    contract.add_argument(
        "--json", dest="contract_json", action="store_true", help="Emit the structured v2 envelope"
    )
    secret = commands.add_parser("secret", help="Inspect injected secret readiness")
    secret_commands = secret.add_subparsers(dest="secret_command", required=True)
    for name in ("status", "check", "requirements"):
        action = secret_commands.add_parser(name)
        action.add_argument("--json", dest="secret_json", action="store_true")
        if name in {"check", "requirements"}:
            action.add_argument("--name", action="append", default=[])
    doctor_parser = commands.add_parser("doctor", help="Inspect aggregate readiness")
    doctor_parser.add_argument("--json", dest="doctor_json", action="store_true")
    project = commands.add_parser("project", help="Discover projects and import planning workbooks")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    projects = project_commands.add_parser("list", help="List accessible projects")
    projects.add_argument("--initiative")
    projects.add_argument("--offset", type=int, default=0)
    projects.add_argument("--json", dest="project_json", action="store_true")
    status = project_commands.add_parser("status", help="Show project planning and Story status")
    status.add_argument("ref")
    status.add_argument("--initiative")
    status.add_argument("--json", dest="project_json", action="store_true")
    importer = project_commands.add_parser("import-workbook", help="Plan or apply a workbook")
    importer.add_argument("path", type=Path)
    importer.add_argument("--project")
    importer.add_argument("--initiative")
    importer.add_argument("--sheet-name", default="Implementation")
    importer.add_argument("--apply", action="store_true")
    importer.add_argument("--plan-hash")
    importer.add_argument("--operation-offset", type=int, default=0)
    importer.add_argument("--json", dest="project_json", action="store_true")
    return parser


def _contract() -> dict[str, object]:
    return envelope(
        command="contract",
        status="success",
        summary="Wood Tools v2 CLI foundation is ready.",
        data={
            "public_executable": "wood",
            "capabilities": [
                "contract",
                "secret status",
                "secret check",
                "secret requirements",
                "doctor",
                "project list",
                "project status",
                "project import-workbook",
            ],
            "exit_codes": EXIT_CODES,
            "mutation_kinds": ["read-only", "preview", "mutating"],
        },
        next_actions=["Use a capability command when its migration Story is implemented."],
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    try:
        parsed = build_parser().parse_args(args)
        as_json = parsed.json or any(
            getattr(parsed, name, False)
            for name in ("contract_json", "secret_json", "doctor_json", "project_json")
        )
        if parsed.command is None:
            if not as_json:
                build_parser().print_help()
                return 0
            payload = _contract()
        elif parsed.command == "secret":
            payload = secret_command(
                parsed.secret_command,
                root=Path.cwd(),
                environ=os.environ,
                names=getattr(parsed, "name", []),
            )
        elif parsed.command == "doctor":
            payload = doctor(Path.cwd(), os.environ)
        elif parsed.command == "project":
            payload = run_project_command(
                parsed.project_command,
                ref=getattr(parsed, "ref", None),
                initiative=getattr(parsed, "initiative", None),
                offset=getattr(parsed, "offset", 0),
                path=getattr(parsed, "path", None),
                sheet_name=getattr(parsed, "sheet_name", "Implementation"),
                project=getattr(parsed, "project", None),
                apply=getattr(parsed, "apply", False),
                plan_hash=getattr(parsed, "plan_hash", None),
                operation_offset=getattr(parsed, "operation_offset", 0),
            )
        else:
            payload = _contract()
    except _ArgumentError:
        payload = envelope(
            command="wood",
            status="invalid",
            summary="Invalid command or argument.",
            errors=[{"code": "INVALID_INPUT", "message": "Use a supported command and option."}],
            next_actions=["Run wood --help for available commands."],
        )
    try:
        # The v2 audit record keeps the command outcome, not raw arguments.
        # Future capabilities may accept sensitive positional values.
        write_audit_event(payload, cli_name="wood")
    except OSError:
        pass
    print(render(payload, as_json=as_json))
    return exit_code(payload)


if __name__ == "__main__":
    raise SystemExit(main())

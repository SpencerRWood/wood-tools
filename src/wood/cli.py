"""The only installed Wood Tools v2 command."""

from __future__ import annotations

import argparse
import sys
from typing import Never

from resources.cli.audit import write_audit_event

from .output import EXIT_CODES, envelope, exit_code, render


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
    return parser


def _contract() -> dict[str, object]:
    return envelope(
        command="contract",
        status="success",
        summary="Wood Tools v2 CLI foundation is ready.",
        data={
            "public_executable": "wood",
            "capabilities": ["contract"],
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
        as_json = parsed.json or getattr(parsed, "contract_json", False)
        if parsed.command is None:
            if not as_json:
                build_parser().print_help()
                return 0
            payload = _contract()
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

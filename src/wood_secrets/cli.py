from __future__ import annotations

import argparse
import json
import sys
from getpass import getpass
from pathlib import Path
from typing import Any

from resources.cli.audit import write_audit_event
from resources.cli.output import error_output, success_output, warning_output
from wood_config.core import ConfigError, build_paths, load_config, save_config

from .core import SecretResolver
from .core.env_files import resolve_env_file, write_resolved_env_file
from .core.materialization import (
    DEFAULT_MATERIALIZED_SECRET_MANIFEST,
    MaterializationError,
    load_materialized_secret_manifest,
    materialized_secret_definitions,
    materialized_secret_identity,
)
from .core.providers import SecretProviderError


def _emit(
    payload: dict[str, Any], *, json_output: bool, command_args: list[str] | None = None
) -> int:
    if {"command", "status", "mutation"}.issubset(payload):
        write_audit_event(payload, cli_name="wood-secrets", command_args=command_args)

    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for key, value in payload.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for sub_key, sub_value in value.items():
                print(f"  {sub_key}: {sub_value}")
        elif isinstance(value, list):
            print(f"{key}:")
            for item in value:
                print(f"  - {item}")
        else:
            print(f"{key}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wood-secrets",
        description="Validate and resolve secret references without printing secret values.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check",
        help="Check configured integration secret references or one explicit reference",
    )
    check_parser.add_argument("--ref", help="Secret reference to validate and resolve redacted")
    check_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    providers_parser = subparsers.add_parser("providers", help="List registered secret providers")
    providers_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    status_parser = subparsers.add_parser("status", help="Check one provider status")
    status_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden", "env"),
        help="Provider name to inspect",
    )
    status_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    unlock_parser = subparsers.add_parser("unlock", help="Unlock one provider")
    unlock_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden",),
        help="Provider name to unlock",
    )
    unlock_mode = unlock_parser.add_mutually_exclusive_group()
    unlock_mode.add_argument("--interactive", action="store_true", help="Prompt in the terminal")
    unlock_mode.add_argument("--gui", action="store_true", help="Prompt with a GUI dialog")
    unlock_parser.add_argument(
        "--write-session",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write the unlocked session token to the protected runtime session file",
    )
    unlock_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    lock_parser = subparsers.add_parser("lock", help="Lock one provider")
    lock_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden",),
        help="Provider name to lock",
    )
    lock_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    session_parser = subparsers.add_parser("session", help="Inspect runtime session status")
    session_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden", "env"),
        help="Provider name to inspect",
    )
    session_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    list_parser = subparsers.add_parser(
        "list",
        help="List vault items and field names without exposing secret values",
    )
    list_parser.add_argument(
        "--provider",
        default="vaultwarden",
        choices=("vaultwarden",),
        help="Provider name to inspect",
    )
    list_parser.add_argument("--search", help="Optional provider-native search term")
    list_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    add_parser = subparsers.add_parser(
        "add",
        help="Preview or create a Wood-managed Vaultwarden secret",
    )
    add_parser.add_argument("--service", required=True, help="Credential-owning service")
    add_parser.add_argument("--principal", required=True, help="Credential consumer")
    add_parser.add_argument("--credential", required=True, help="Credential purpose")
    add_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the secret value from stdin instead of prompting interactively",
    )
    add_parser.add_argument(
        "--apply",
        action="store_true",
        help="Create the Vaultwarden item. Without --apply, only preview.",
    )
    add_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    exec_parser = subparsers.add_parser(
        "exec",
        help="Run a command with resolved secrets injected as environment variables",
    )
    exec_parser.add_argument("--env", help="Environment variable name to set")
    exec_parser.add_argument("--ref", help="Secret reference to resolve")
    exec_parser.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="Optional NAME=reference bindings, then '--', then the command to run",
    )

    resolve_parser = subparsers.add_parser("resolve", help="Resolve one secret reference")
    resolve_parser.add_argument("--ref", required=True, help="Secret reference to resolve")
    resolve_parser.add_argument(
        "--redacted",
        action="store_true",
        help="Emit only redacted secret output.",
    )
    resolve_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    resolve_env_parser = subparsers.add_parser(
        "resolve-env",
        help="Resolve *_REF values from an env file into a local resolved env file",
    )
    resolve_env_parser.add_argument("--input", default=".env", help="Path to input env file")
    resolve_env_parser.add_argument(
        "--output",
        default=".env.resolved",
        help="Path to write resolved env values when --apply is used",
    )
    resolve_env_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the resolved env file. Without --apply, only preview the resolution.",
    )
    resolve_env_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file when used with --apply",
    )
    resolve_env_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    file_parser = subparsers.add_parser(
        "file",
        help="Register, inspect, or write materialized secret files",
    )
    file_subparsers = file_parser.add_subparsers(dest="file_command", required=True)

    file_add_parser = file_subparsers.add_parser(
        "add",
        help="Preview or register one Vaultwarden reference for file materialization",
    )
    file_add_parser.add_argument("name", help="Materialized secret name")
    file_add_parser.add_argument("ref", help="Secret reference to write to a file")
    file_add_parser.add_argument("--target", help="Optional relative target under secrets_root")
    file_add_parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the registration. Without --apply, only preview.",
    )
    file_add_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    file_import_parser = file_subparsers.add_parser(
        "import",
        help="Preview or import materialized secret file registrations from JSON/YAML",
    )
    file_import_parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_MATERIALIZED_SECRET_MANIFEST),
        help=(
            "Path to a JSON or YAML secret file manifest. "
            f"Default: {DEFAULT_MATERIALIZED_SECRET_MANIFEST}"
        ),
    )
    file_import_parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist imported registrations. Without --apply, only preview.",
    )
    file_import_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    file_write_parser = file_subparsers.add_parser(
        "write",
        help="Preview or write configured materialized secret files",
    )
    file_write_parser.add_argument("name", nargs="?", help="Optional materialized secret name")
    file_write_parser.add_argument(
        "--manifest",
        help=(
            "Materialized secret manifest to use instead of the default "
            f"{DEFAULT_MATERIALIZED_SECRET_MANIFEST}"
        ),
    )
    file_write_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write configured materialized secrets. Without --apply, only preview.",
    )
    file_write_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    file_status_parser = file_subparsers.add_parser(
        "status",
        help="Inspect configured materialized secret file status without writing files",
    )
    file_status_parser.add_argument("name", nargs="?", help="Optional materialized secret name")
    file_status_parser.add_argument(
        "--manifest",
        help=(
            "Materialized secret manifest to use instead of the default "
            f"{DEFAULT_MATERIALIZED_SECRET_MANIFEST}"
        ),
    )
    file_status_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    doctor_parser = subparsers.add_parser("doctor", help="Diagnose provider readiness")
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser


def _read_add_secret_value(*, stdin: bool) -> str:
    if stdin:
        value = sys.stdin.read().rstrip("\r\n")
        if not value:
            raise SecretProviderError("Secret value must be non-empty.")
        return value

    value = getpass("Enter secret value: ")
    if not value:
        raise SecretProviderError("Secret value must be non-empty.")
    confirmation = getpass("Confirm secret value: ")
    if value != confirmation:
        raise SecretProviderError("Secret confirmation did not match.")
    return value


def _register_materialized_file(
    *,
    name: str,
    reference: str,
    target: str | None,
    apply: bool,
) -> dict[str, Any]:
    if not name.strip():
        raise SecretProviderError("Materialized secret name must be non-empty.")
    definition = {"ref": reference.strip()}
    if not definition["ref"]:
        raise SecretProviderError("Secret reference must be non-empty.")
    if target is not None:
        if not target.strip():
            raise SecretProviderError("Materialized secret target must be non-empty.")
        definition["target"] = target.strip()

    try:
        paths = build_paths()
        document = load_config(paths)
        active_profile = str(document["active_profile"])
        profile = document["profiles"].setdefault(active_profile, {})
        integrations = profile.setdefault("integrations", {})
        vaultwarden = integrations.setdefault("vaultwarden", {})
        materialized = vaultwarden.setdefault("materialized_secrets", {})
        if not isinstance(materialized, dict):
            raise SecretProviderError(
                "integrations.vaultwarden.materialized_secrets must be an object."
            )
        previous = materialized.get(name)
        materialized[name] = definition
        materialized_secret_definitions(profile)
        changed = previous != definition
        if apply:
            save_config(paths, document)
    except ConfigError as exc:
        raise SecretProviderError(f"Unable to update wood-config: {exc}") from exc
    except OSError as exc:
        raise SecretProviderError(f"Unable to update wood-config: {exc}") from exc

    return {
        "ok": True,
        "apply": apply,
        "changed": changed if apply else False,
        "path": str(paths.file_path),
        "file": {
            "name": name,
            "ref": definition["ref"],
            "target": definition.get("target"),
        },
    }


def _load_materialized_file_manifest(path: Path) -> dict[str, Any]:
    try:
        return load_materialized_secret_manifest(path)
    except MaterializationError as exc:
        raise SecretProviderError(str(exc)) from exc


def _import_materialized_files(*, path: Path, apply: bool) -> dict[str, Any]:
    definitions = _load_materialized_file_manifest(path)

    try:
        paths = build_paths()
        document = load_config(paths)
        active_profile = str(document["active_profile"])
        profile = document["profiles"].setdefault(active_profile, {})
        integrations = profile.setdefault("integrations", {})
        vaultwarden = integrations.setdefault("vaultwarden", {})
        materialized = vaultwarden.setdefault("materialized_secrets", {})
        if not isinstance(materialized, dict):
            raise SecretProviderError(
                "integrations.vaultwarden.materialized_secrets must be an object."
            )

        imported = []
        for name, definition in definitions.items():
            previous = materialized.get(name)
            state = (
                "added"
                if previous is None
                else "unchanged"
                if previous == definition
                else "updated"
            )
            materialized[name] = definition
            imported.append(
                {
                    "name": name,
                    "ref": definition["ref"],
                    "target": definition.get("target"),
                    "state": state,
                }
            )

        materialized_secret_definitions(profile)
        if apply:
            save_config(paths, document)
    except ConfigError as exc:
        raise SecretProviderError(f"Unable to update wood-config: {exc}") from exc
    except OSError as exc:
        raise SecretProviderError(f"Unable to update wood-config: {exc}") from exc

    states = [item["state"] for item in imported]
    return {
        "ok": True,
        "apply": apply,
        "path": str(paths.file_path),
        "manifest_path": str(path),
        "selected_count": len(imported),
        "added_count": states.count("added"),
        "updated_count": states.count("updated"),
        "unchanged_count": states.count("unchanged"),
        "changed": apply and any(state != "unchanged" for state in states),
        "imported": imported,
    }


def main(argv: list[str] | None = None) -> int:
    command_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(command_args)
    resolver = SecretResolver()

    try:
        if args.command == "check":
            payload = resolver.check(args.ref)
            if args.json:
                envelope_builder = success_output if payload["ok"] else warning_output
                summary = (
                    "Secret reference checks completed."
                    if payload["ok"]
                    else "One or more secret references are not ready."
                )
                envelope = envelope_builder(
                    command="check",
                    mutation="read-only",
                    summary=summary,
                    data=payload,
                    warnings=(
                        payload.get("checks") if not payload["ok"] and args.ref is None else None
                    ),
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "providers":
            payload = resolver.providers()
            if args.json:
                envelope_builder = success_output if payload["ok"] else warning_output
                envelope = envelope_builder(
                    command="providers",
                    mutation="read-only",
                    summary="Secret provider listing completed.",
                    data=payload,
                    warnings=payload["providers"] if not payload["ok"] else None,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "status":
            payload = resolver.status(args.provider)
            if args.json:
                envelope = success_output(
                    command="status",
                    mutation="read-only",
                    summary="Secret provider checks completed.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "unlock":
            interactive = args.interactive or not args.gui
            payload = resolver.unlock(
                args.provider,
                interactive=interactive,
                gui=args.gui,
                write_session=args.write_session,
            )
            if args.json:
                envelope = success_output(
                    command="unlock",
                    mutation="mutating",
                    summary="Unlocked secret provider session.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            print(
                "Unlocked "
                f"{payload['provider']}; session {payload['session']['state']} "
                f"at {payload['session']['path']}."
            )
            return 0

        if args.command == "lock":
            payload = resolver.lock(args.provider)
            if args.json:
                envelope = success_output(
                    command="lock",
                    mutation="mutating",
                    summary="Locked secret provider session.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "session":
            payload = resolver.session(args.provider)
            if args.json:
                envelope = success_output(
                    command="session",
                    mutation="read-only",
                    summary="Secret runtime session status completed.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "list":
            payload = resolver.list_entries(args.provider, search=args.search)
            if args.json:
                envelope = success_output(
                    command="list",
                    mutation="read-only",
                    summary="Listed secret entries without exposing secret values.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "add":
            materialized_secret_identity(
                service=args.service,
                principal=args.principal,
                credential=args.credential,
            )
            secret_value = _read_add_secret_value(stdin=args.stdin)
            try:
                payload = resolver.add_secret(
                    service=args.service,
                    principal=args.principal,
                    credential=args.credential,
                    value=secret_value,
                    apply=args.apply,
                )
            finally:
                secret_value = ""
            if args.json:
                envelope = success_output(
                    command="add",
                    mutation="mutating" if args.apply else "read-only",
                    summary=(
                        "Wood-managed Vaultwarden secret created."
                        if args.apply
                        else "Wood-managed Vaultwarden secret preview completed."
                    ),
                    data=payload,
                    next_actions=(
                        ["Re-run wood-secrets add with --apply to create the Vaultwarden item."]
                        if not args.apply
                        else None
                    ),
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            item = payload["secret"]
            verb = "created" if args.apply else "would create"
            print(
                f"{verb} {item['canonical_identity']} -> {item['target']} ({item['target_source']})"
            )
            return 0

        if args.command == "resolve":
            resolved = resolver.resolve(args.ref)
            payload = resolved.to_dict()
            if args.redacted:
                payload["display_value"] = resolved.redacted_value
            if args.json:
                envelope = success_output(
                    command="resolve",
                    mutation="read-only",
                    summary="Resolved secret reference without exposing the secret value.",
                    data=payload,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        if args.command == "resolve-env":
            if args.force and not args.apply:
                raise SecretProviderError("--force can only be used with --apply.")
            result = resolve_env_file(
                resolver=resolver,
                input_path=Path(args.input),
                output_path=Path(args.output),
            )
            if args.apply:
                write_resolved_env_file(result, force=args.force)
                payload = result.preview(apply=True, wrote=True)
                if args.json:
                    envelope = success_output(
                        command="resolve-env",
                        mutation="mutating",
                        summary="Resolved env references and wrote the output file.",
                        data=payload,
                    )
                    return _emit(envelope, json_output=True, command_args=command_args)
                for item in result.resolved:
                    print(f"resolved {item.output_key} from {item.reference}")
                print(f"wrote {result.output_path}")
                return 0

            payload = result.preview(apply=False)
            if args.json:
                envelope = success_output(
                    command="resolve-env",
                    mutation="read-only",
                    summary="Resolved env references preview completed without writing files.",
                    data=payload,
                    next_actions=[
                        "Re-run wood-secrets resolve-env with --apply to write the output file."
                    ],
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            for item in result.resolved:
                print(f"would resolve {item.output_key} from {item.reference}")
            print("preview complete; no files were written")
            return 0

        if args.command == "file":
            if args.file_command == "add":
                payload = _register_materialized_file(
                    name=args.name,
                    reference=args.ref,
                    target=args.target,
                    apply=args.apply,
                )
                if args.json:
                    envelope_builder = success_output if args.apply else warning_output
                    envelope = envelope_builder(
                        command="file-add",
                        mutation="mutating" if args.apply else "read-only",
                        summary=(
                            "Materialized secret file registration saved."
                            if args.apply
                            else "Materialized secret file registration preview completed."
                        ),
                        data=payload,
                        next_actions=(
                            ["Re-run wood-secrets file add with --apply to save this registration."]
                            if not args.apply
                            else None
                        ),
                    )
                    return _emit(envelope, json_output=True, command_args=command_args)
                verb = "registered" if args.apply else "would register"
                target = payload["file"].get("target") or "(derived)"
                print(f"{verb} {payload['file']['name']} -> {target}")
                return 0

            if args.file_command == "import":
                payload = _import_materialized_files(path=Path(args.path), apply=args.apply)
                if args.json:
                    envelope_builder = success_output if args.apply else warning_output
                    envelope = envelope_builder(
                        command="file-import",
                        mutation="mutating" if args.apply else "read-only",
                        summary=(
                            "Materialized secret file registrations imported."
                            if args.apply
                            else "Materialized secret file import preview completed."
                        ),
                        data=payload,
                        next_actions=(
                            [
                                "Re-run wood-secrets file import with --apply to save "
                                "these registrations."
                            ]
                            if not args.apply
                            else None
                        ),
                    )
                    return _emit(envelope, json_output=True, command_args=command_args)
                verb = "imported" if args.apply else "would import"
                for item in payload["imported"]:
                    target = item.get("target") or "(derived)"
                    print(f"{verb} {item['name']} -> {target} ({item['state']})")
                return 0

            if args.file_command == "write":
                manifest_path = Path(args.manifest) if args.manifest else None
                payload = resolver.materialize(
                    args.name,
                    apply=args.apply,
                    manifest_path=manifest_path,
                )
                if args.json:
                    envelope_builder = success_output if payload["ok"] else warning_output
                    envelope = envelope_builder(
                        command="file-write",
                        mutation="mutating" if args.apply else "read-only",
                        summary=(
                            "Materialized secret file write completed."
                            if args.apply and payload["ok"]
                            else "Materialized secret file write preview completed."
                            if payload["ok"]
                            else "One or more materialized secret files were not changed."
                        ),
                        data=payload,
                        warnings=payload["errors"] if not payload["ok"] else None,
                        next_actions=(
                            ["Re-run wood-secrets file write with --apply to write files."]
                            if not args.apply and payload["ok"]
                            else None
                        ),
                    )
                    return _emit(envelope, json_output=True, command_args=command_args)
                for item in payload["materialized"]:
                    verb = "wrote" if args.apply else "would write"
                    print(f"{verb} {item['name']} -> {item['target']} ({item['state']})")
                for item in payload["errors"]:
                    print(f"skipped {item['name']}: {item['message']}", file=sys.stderr)
                return 0 if payload["ok"] else 1

            if args.file_command == "status":
                manifest_path = Path(args.manifest) if args.manifest else None
                payload = resolver.materialize_status(args.name, manifest_path=manifest_path)
                if args.json:
                    envelope_builder = success_output if payload["ok"] else warning_output
                    envelope = envelope_builder(
                        command="file-status",
                        mutation="read-only",
                        summary=(
                            "Materialized secret file status completed."
                            if payload["ok"]
                            else "One or more materialized secret files need attention."
                        ),
                        data=payload,
                        warnings=payload["needs_attention"] if not payload["ok"] else None,
                    )
                    return _emit(envelope, json_output=True, command_args=command_args)
                for item in payload["materialized"]:
                    print(f"{item['name']} -> {item['target']} ({item['state']})")
                return 0 if payload["ok"] else 1

        if args.command == "exec":
            raw_args = args.command_args
            separator_index = raw_args.index("--") if "--" in raw_args else None
            binding_args = raw_args if separator_index is None else raw_args[:separator_index]
            command = [] if separator_index is None else raw_args[separator_index + 1 :]

            bindings: dict[str, str] = {}
            if args.env or args.ref:
                if not args.env or not args.ref:
                    raise SecretProviderError(
                        "exec requires both --env and --ref when either is used."
                    )
                bindings[args.env] = args.ref
            for binding in binding_args:
                name, separator, reference = binding.partition("=")
                if not separator or not name.strip() or not reference.strip():
                    raise SecretProviderError(
                        "Inline exec bindings must use NAME=reference syntax."
                    )
                bindings[name.strip()] = reference.strip()

            return resolver.exec_with_secrets(bindings, command)

        if args.command == "doctor":
            payload = resolver.doctor()
            if args.json:
                envelope_builder = success_output if payload["status"] == "ok" else warning_output
                envelope = envelope_builder(
                    command="doctor",
                    mutation="read-only",
                    summary="Secret provider diagnostics completed.",
                    data=payload,
                    warnings=payload["issues"] if payload["status"] != "ok" else None,
                )
                return _emit(envelope, json_output=True, command_args=command_args)
            return _emit(payload, json_output=False, command_args=command_args)

        parser.error("Unknown command")
        return 2
    except SecretProviderError as exc:
        mutation = (
            "mutating"
            if args.command in {"unlock", "lock"}
            or (args.command == "resolve-env" and args.apply)
            or (args.command == "add" and args.apply)
            or (
                args.command == "file"
                and getattr(args, "file_command", None) in {"add", "import", "write"}
                and args.apply
            )
            else "read-only"
        )
        if getattr(args, "json", False):
            payload = error_output(
                command=args.command,
                mutation=mutation,
                summary=str(exc),
                errors=[{"message": str(exc)}],
                next_actions=[
                    (
                        "Check the secret reference syntax, provider status, "
                        "or configured env fallback."
                    ),
                ],
            )
            write_audit_event(payload, cli_name="wood-secrets", command_args=command_args)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 2
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

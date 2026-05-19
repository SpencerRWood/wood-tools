#!/usr/bin/env python3
"""Resolve Vaultwarden-backed *_REF values from .env into .env.resolved.

Default behavior expects BW_SESSION to already be set:

    export BW_SESSION="$(bw unlock --raw)"
    python scripts/resolve_env_refs.py --apply

On macOS, you can optionally prompt for the Vaultwarden/Bitwarden master password
without storing it in .env, shell history, or project files:

    python scripts/resolve_env_refs.py --apply --prompt-unlock

The prompt-unlock mode passes the password directly to `bw unlock --raw`, keeps the
resulting BW_SESSION only in this process environment, and never prints the password
or resolved secret values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvLine:
    raw: str
    key: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class VaultRef:
    raw: str
    parts: tuple[str, ...]

    @property
    def full_item_name(self) -> str:
        return " / ".join(self.parts)

    @property
    def search_term(self) -> str:
        return self.parts[-1]

    @property
    def field_hint(self) -> str:
        return self.parts[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing output."
    )
    mode.add_argument("--apply", action="store_true", help="Write .env.resolved output file.")
    parser.add_argument("--input", default=".env", help="Path to input .env file.")
    parser.add_argument("--output", default=".env.resolved", help="Path to output resolved file.")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite output if it already exists."
    )
    parser.add_argument(
        "--prompt-unlock",
        action="store_true",
        help=(
            "Prompt for the Vaultwarden/Bitwarden master password using a macOS hidden "
            "dialog if BW_SESSION is not already set. The password is not stored."
        ),
    )
    args = parser.parse_args()
    if args.force and not args.apply:
        parser.error("--force can only be used with --apply")
    return args


def parse_env_lines(content: str) -> list[EnvLine]:
    lines: list[EnvLine] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            lines.append(EnvLine(raw=raw_line))
            continue
        key, value = raw_line.split("=", 1)
        lines.append(EnvLine(raw=raw_line, key=key.strip(), value=value.strip()))
    return lines


def prompt_for_password_macos() -> str:
    if sys.platform != "darwin":
        raise RuntimeError("--prompt-unlock currently supports macOS only via osascript.")

    script = (
        'display dialog "Vaultwarden master password" '
        'default answer "" with hidden answer '
        'buttons {"Cancel", "OK"} default button "OK"'
    )

    try:
        proc = subprocess.run(
            ["osascript", "-e", script, "-e", "text returned of result"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as err:
        raise RuntimeError("osascript is required for --prompt-unlock but was not found.") from err
    except subprocess.CalledProcessError as err:
        raise RuntimeError("Vaultwarden password prompt was cancelled or failed.") from err

    password = proc.stdout.rstrip("\n")
    if not password:
        raise RuntimeError("Vaultwarden password prompt returned an empty password.")
    return password


def unlock_bw_session_with_password(password: str) -> str:
    try:
        proc = subprocess.run(
            ["bw", "unlock", "--raw"],
            input=password,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as err:
        raise RuntimeError("'bw' CLI is required but was not found on PATH.") from err
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.strip()
        raise RuntimeError(f"bw unlock failed: {stderr}") from err

    session = proc.stdout.strip()
    if not session:
        raise RuntimeError("bw unlock did not return a session token.")
    return session


def ensure_bw_session(prompt_unlock: bool) -> None:
    if os.environ.get("BW_SESSION"):
        return

    if prompt_unlock:
        password = prompt_for_password_macos()
        try:
            session = unlock_bw_session_with_password(password)
        finally:
            # Best-effort removal of the password reference from this scope.
            password = ""
        os.environ["BW_SESSION"] = session
        print("unlocked Vaultwarden/Bitwarden session for this process")
        return

    raise RuntimeError(
        "BW_SESSION is not set. Unlock Bitwarden/Vaultwarden first:\n\n"
        '  export BW_SESSION="$(bw unlock --raw)"\n\n'
        "Or on macOS, rerun with:\n\n"
        "  python scripts/resolve_env_refs.py --apply --prompt-unlock"
    )


def run_bw_json(args: list[str]) -> object:
    command = ["bw", *args]
    session = os.environ.get("BW_SESSION")
    if session:
        command.extend(["--session", session])

    try:
        proc = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as err:
        raise RuntimeError("'bw' CLI is required but was not found on PATH.") from err
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.strip()
        raise RuntimeError(f"bw command failed: {' '.join(args)} ({stderr})") from err

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"bw returned invalid JSON for: {' '.join(args)}") from err


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def parse_vault_ref(reference: str) -> VaultRef:
    if not reference.startswith("vaultwarden://"):
        raise RuntimeError(f"Unsupported reference: {reference}")

    path = reference[len("vaultwarden://") :].strip("/")
    parts = tuple(part.strip() for part in path.split("/") if part.strip())
    if len(parts) < 2:
        raise RuntimeError(f"Invalid vaultwarden reference: {reference}")

    return VaultRef(raw=reference, parts=parts)


def candidate_item_names(ref: VaultRef) -> list[str]:
    """Return likely Bitwarden item names for a vaultwarden:// reference.

    For the convention:
        vaultwarden://wood/openproject/prod/api-token

    The preferred item name is:
        wood / openproject / prod / api-token
    """

    slash_spaced = " / ".join(ref.parts)
    slash = "/".join(ref.parts)
    last = ref.parts[-1]

    candidates = [slash_spaced, slash, last]

    # Some vaults may store the item without the last path segment and use the last
    # segment as a field/purpose hint.
    if len(ref.parts) > 2:
        candidates.append(" / ".join(ref.parts[:-1]))
        candidates.append("/".join(ref.parts[:-1]))

    deduped: list[str] = []
    for item in candidates:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def extract_custom_field(item: dict, candidates: list[str]) -> str | None:
    fields = item.get("fields") or []
    candidate_names = {normalize_token(name) for name in candidates if name}

    for field in fields:
        name = str(field.get("name") or "")
        if normalize_token(name) in candidate_names:
            value = field.get("value")
            if isinstance(value, str) and value:
                return value
    return None


def choose_vault_item(items: list[dict], ref: VaultRef, key: str) -> dict:
    names = candidate_item_names(ref)

    for expected_name in names:
        matches = [item for item in items if str(item.get("name") or "").strip() == expected_name]
        if len(matches) == 1:
            return matches[0]

    if len(items) == 1:
        return items[0]

    item_names = ", ".join(str(item.get("name") or "<unnamed>") for item in items[:10])
    raise RuntimeError(
        f"Ambiguous or missing vault item for {key}: {ref.raw}. "
        f"Expected one of: {', '.join(names)}. "
        f"Search returned: {item_names or 'no items'}. "
        "Use a more specific reference."
    )


def resolve_vault_ref(key: str, reference: str) -> str:
    ref = parse_vault_ref(reference)

    # Search by the most specific visible term first, normally "api-token".
    items = run_bw_json(["list", "items", "--search", ref.search_term])
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected bw output while resolving {key}")

    item = choose_vault_item(items, ref, key)

    field_candidates = [
        key,
        key.removesuffix("_REF"),
        ref.field_hint,
        ref.field_hint.replace("-", "_"),
        normalize_token(ref.field_hint),
        "api_token",
        "token",
        "password",
    ]

    resolved = extract_custom_field(item, field_candidates)
    if resolved:
        return resolved

    login = item.get("login") or {}
    password = login.get("password")
    if isinstance(password, str) and password:
        return password

    raise RuntimeError(
        f"Unable to resolve secret value for {key}: {reference}. "
        "Add a matching custom field or login.password. Notes are intentionally ignored."
    )


def check_output_not_staged(output_path: Path) -> None:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return

    staged = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    output_candidates = {output_path.as_posix(), output_path.name}
    if staged.intersection(output_candidates):
        raise RuntimeError(f"Refusing to continue: {output_path} is staged for commit.")


def build_resolved_content(lines: list[EnvLine]) -> tuple[str, list[str]]:
    output_lines: list[str] = []
    actions: list[str] = []

    for line in lines:
        if line.key is None:
            output_lines.append(line.raw)
            continue

        key = line.key
        value = line.value or ""

        if key.endswith("_REF") and value.startswith("vaultwarden://"):
            resolved_key = key[: -len("_REF")]
            secret_value = resolve_vault_ref(key, value)
            output_lines.append(f"{resolved_key}={secret_value}")
            actions.append(f"resolved {resolved_key} from {value}")
            continue

        output_lines.append(f"{key}={value}")

    return "\n".join(output_lines) + "\n", actions


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        ensure_bw_session(prompt_unlock=args.prompt_unlock)
        check_output_not_staged(output_path)
        lines = parse_env_lines(input_path.read_text(encoding="utf-8"))
        content, actions = build_resolved_content(lines)
    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    for action in actions:
        print(action)

    if args.dry_run:
        print("dry-run complete; no files were written")
        return 0

    if output_path.exists() and not args.force:
        print(f"Error: output exists ({output_path}); use --force to overwrite", file=sys.stderr)
        return 2

    output_path.write_text(content, encoding="utf-8")
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

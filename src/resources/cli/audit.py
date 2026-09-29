from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_MAX_FILES = 5
MODE_ENV = "WOOD_AUDIT_LOG"
PATH_ENV = "WOOD_AUDIT_LOG_PATH"
MAX_BYTES_ENV = "WOOD_AUDIT_LOG_MAX_BYTES"
MAX_FILES_ENV = "WOOD_AUDIT_LOG_MAX_FILES"
WOOD_HOME_ENV = "WOOD_HOME"
DEFAULT_CLI_NAME = "wood-tools"

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"[a-z0-9_]*token"
    r"|[a-z0-9_]*secret"
    r"|[a-z0-9_]*password"
    r"|[a-z0-9_]*credential"
    r"|[a-z0-9_]*api[_-]?key"
    r")\s*[:=]\s*([^\s,;.]+)"
)
_SENSITIVE_NAME_RE = re.compile(r"(?i)(token|secret|password|credential|api[_-]?key)")


def _wood_home() -> Path:
    configured = os.environ.get(WOOD_HOME_ENV)
    return Path(configured).expanduser() if configured else Path.home() / ".wood"


def default_audit_log_path() -> Path:
    return _wood_home() / "state" / "logs" / "audit.jsonl"


def _audit_log_path() -> Path:
    configured = os.environ.get(PATH_ENV)
    return Path(configured).expanduser() if configured else default_audit_log_path()


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _redact_text(value: str) -> str:
    return _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)


def _is_sensitive_name(value: str) -> bool:
    normalized = value.lstrip("-").split("=", 1)[0]
    return bool(_SENSITIVE_NAME_RE.search(normalized))


def _marks_next_arg_sensitive(value: str) -> bool:
    return (value.startswith("-") or "." in value) and _is_sensitive_name(value)


def _redact_command_args(args: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False

    for arg in args:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue

        if "=" in arg:
            name, _, value = arg.partition("=")
            if _is_sensitive_name(name):
                redacted.append(f"{name}=<redacted>")
                continue
            redacted.append(_redact_text(arg))
            continue

        redacted_arg = _redact_text(arg)
        redacted.append(redacted_arg)
        if _marks_next_arg_sensitive(arg):
            redact_next = True

    return redacted


def _full_command(cli_name: str, command_args: Sequence[str] | None) -> str | None:
    if command_args is None:
        return None
    return shlex.join([cli_name, *_redact_command_args(command_args)])


def _safe_messages(messages: Any) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    if not isinstance(messages, list):
        return safe

    for item in messages:
        if isinstance(item, dict):
            entry: dict[str, str] = {}
            for key in ("code", "field", "message", "remediation"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    entry[key] = _redact_text(value)
            if entry:
                safe.append(entry)
        elif isinstance(item, str) and item:
            safe.append({"message": _redact_text(item)})
    return safe


def _infer_target_type(cli_name: str, command: str, data: Any) -> str:
    if isinstance(data, dict):
        if "resource" in data:
            return "resource"
        if "repository" in data or "project" in data:
            return "project"
        if "infisical" in data or "presence" in data:
            return "secret-readiness"

    if command.startswith("secret "):
        return "secret-readiness"
    if command == "doctor":
        return "readiness"
    if command.startswith("project "):
        return "project"

    return "command"


def _target_path(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    path = data.get("path")
    return path if isinstance(path, str) and path else None


def _approval_reference(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("approval_reference", "approval_ref"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return _redact_text(value)
    return None


def _report_paths(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    paths = data.get("generated_report_paths") or data.get("report_paths")
    if isinstance(paths, list):
        return [value for value in paths if isinstance(value, str) and value]
    if isinstance(paths, str) and paths:
        return [paths]
    return []


def _command_summary(cli_name: str, command: str, target_type: str) -> str:
    summaries = {
        ("wood", "secret status"): "Inspect Infisical readiness",
        ("wood", "secret check"): "Check injected variable presence",
        ("wood", "secret requirements"): "List required variable names",
        ("wood", "doctor"): "Inspect aggregate readiness",
        ("wood", "project list"): "List OpenProject projects",
        ("wood", "project status"): "Inspect OpenProject planning status",
        ("wood", "project import-workbook"): "Plan or apply implementation workbook",
    }
    return summaries.get((cli_name, command), f"Run {target_type} command")


def build_audit_event(
    envelope: dict[str, Any],
    *,
    cli_name: str = DEFAULT_CLI_NAME,
    command_args: Sequence[str] | None = None,
) -> dict[str, Any]:
    command = str(envelope.get("command") or "unknown")
    data = envelope.get("data")
    target_path = _target_path(data)
    target: dict[str, str] = {"type": _infer_target_type(cli_name, command, data)}
    if target_path:
        target["path"] = target_path

    event: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "cli": cli_name,
        "command": command,
        "full_command": _full_command(cli_name, command_args),
        "summary": _command_summary(cli_name, command, target["type"]),
        "outcome": str(envelope.get("status") or "unknown"),
        "mutation_status": str(envelope.get("mutation") or "unknown"),
        "mutation": envelope.get("mutation") == "mutating",
        "requires_approval": bool(envelope.get("requires_approval")),
        "target": target,
    }
    if event["full_command"] is None:
        del event["full_command"]

    approval_reference = _approval_reference(data)
    if approval_reference:
        event["approval_reference"] = approval_reference

    report_paths = _report_paths(data)
    if report_paths:
        event["generated_report_paths"] = report_paths

    summary = envelope.get("summary")
    if isinstance(summary, str) and summary:
        event["reason"] = _redact_text(summary)

    errors = _safe_messages(envelope.get("errors"))
    if errors:
        event["errors"] = errors

    warnings = _safe_messages(envelope.get("warnings"))
    if warnings:
        event["warnings"] = warnings

    next_actions = envelope.get("next_actions")
    if isinstance(next_actions, list):
        event["next_actions_count"] = len([action for action in next_actions if action])

    event["actor"] = getpass.getuser()
    event["schema_version"] = 1
    return event


def _rotate_log(path: Path, *, max_bytes: int, max_files: int) -> None:
    if not path.exists() or path.stat().st_size <= max_bytes:
        return

    oldest = path.with_name(f"{path.name}.{max_files}")
    if oldest.exists():
        oldest.unlink()

    for index in range(max_files - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))

    path.replace(path.with_name(f"{path.name}.1"))


def write_audit_event(
    envelope: dict[str, Any],
    *,
    cli_name: str = DEFAULT_CLI_NAME,
    command_args: Sequence[str] | None = None,
) -> None:
    mode = os.environ.get(MODE_ENV, "file").strip().lower()
    if mode in {"0", "false", "off", "disabled", "none"}:
        return

    event = build_audit_event(envelope, cli_name=cli_name, command_args=command_args)
    line = json.dumps(event) + "\n"

    if mode == "console":
        print(line, end="", file=sys.stderr)
        return

    path = _audit_log_path()
    max_bytes = _env_int(MAX_BYTES_ENV, DEFAULT_MAX_BYTES)
    max_files = _env_int(MAX_FILES_ENV, DEFAULT_MAX_FILES)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log(path, max_bytes=max_bytes, max_files=max_files)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        return

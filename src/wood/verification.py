"""Repository-owned verification commands and content-bound result records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .workflow_files import (
    WorkflowFilesError,
    output_directory,
    read_json,
    run_directory,
    snapshot_fingerprint,
    write_json,
)

KIND = "wood-repository-verification"


def _invalid(message: str) -> WorkflowFilesError:
    return WorkflowFilesError("VERIFICATION_CONTRACT_INVALID", message)


def load_contract(
    root: Path, *, optional: bool = False, source_text: str | None = None
) -> dict[str, Any] | None:
    """Validate the complete contract before executing any command."""
    path = root / "pyproject.toml"
    document: dict[str, Any]
    try:
        if source_text is not None:
            document = tomllib.loads(source_text)
        elif not path.exists():
            document = {}
        else:
            if path.stat().st_size > 1_000_000:
                raise ValueError("oversized TOML")
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        section = document.get("tool", {}).get("wood", {}).get("verify")
    except (OSError, ValueError, AttributeError, UnicodeError) as exc:
        raise _invalid("Cannot read [tool.wood.verify] in pyproject.toml.") from exc
    if section is None:
        if optional:
            return None
        raise WorkflowFilesError(
            "VERIFICATION_CONTRACT_MISSING", "Declare [tool.wood.verify] in pyproject.toml."
        )
    if (
        not isinstance(section, dict)
        or set(section) != {"version", "retry_safe", "checks"}
        or type(section.get("version")) is not int
        or section["version"] != 1
        or section.get("retry_safe") is not True
    ):
        raise _invalid("Verification requires version = 1, retry_safe = true, and checks only.")
    checks = section["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 20:
        raise _invalid("Verification must declare between 1 and 20 named checks.")
    normalized: list[dict[str, Any]] = []
    names = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) - {
            "name",
            "argv",
            "required",
            "timeout_seconds",
            "directory",
        }:
            raise _invalid("Check contains unsupported fields.")
        name, argv = check.get("name"), check.get("argv")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}", name)
            or name in names
        ):
            raise _invalid("Check names must be unique identifiers of at most 60 characters.")
        names.add(name)
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 50
            or any(
                not isinstance(arg, str) or not arg or len(arg) > 500 or "\x00" in arg
                for arg in argv
            )
        ):
            raise _invalid(
                "Check argv must contain 1–50 nonempty strings of at most 500 characters."
            )
        required = check.get("required", True)
        timeout = check.get("timeout_seconds", 30)
        if type(required) is not bool or type(timeout) is not int or not 1 <= timeout <= 60:
            raise _invalid("Check required must be boolean; timeout_seconds must be 1–60.")
        relative = check.get("directory", ".")
        if not isinstance(relative, str) or not relative or "\x00" in relative:
            raise _invalid("Check directory must be a relative path within the repository.")
        try:
            directory = (root / relative).resolve()
        except (OSError, RuntimeError) as exc:
            raise _invalid("Check directory cannot be resolved.") from exc
        if (
            Path(relative).is_absolute()
            or not directory.is_relative_to(root.resolve())
            or not directory.is_dir()
        ):
            raise _invalid("Check directory must exist within the repository.")
        normalized.append(
            {
                "name": name,
                "argv": argv,
                "required": required,
                "timeout_seconds": timeout,
                "directory": relative,
            }
        )
    if sum(check["timeout_seconds"] for check in normalized) > 300:
        raise _invalid("Combined check timeouts must not exceed 300 seconds.")
    return {"version": 1, "retry_safe": True, "checks": normalized}


def _contract_hash(contract: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _execute(root: Path, check: dict[str, Any], log: Path) -> dict[str, Any]:
    result = {
        "name": check["name"],
        "required": check["required"],
        "state": "command_error",
        "exit_code": None,
        "log_path": str(log),
    }
    try:
        with log.open("xb") as stream:
            log.chmod(0o600)
            try:
                process = subprocess.Popen(
                    check["argv"],
                    cwd=(root / check["directory"]).resolve(),
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                try:
                    code = process.wait(timeout=check["timeout_seconds"])
                    result.update(state="passed" if code == 0 else "failed", exit_code=code)
                except subprocess.TimeoutExpired:
                    # Stop children too: retrying must not leave the previous check running.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    result["state"] = "timed_out"
            except OSError:
                # Never include argv, environment values, or raw exception text in results.
                stream.write(b"Verification command could not be started.\n")
        result["log_sha256"] = _file_hash(log)
    except OSError as exc:
        raise WorkflowFilesError(
            "VERIFICATION_LOG_UNWRITABLE", "Cannot retain verification log."
        ) from exc
    return result


def repo_verify(root: Path) -> dict[str, Any]:
    contract = load_contract(root)
    assert contract is not None
    directory = run_directory(root, "wood-repo-verify-")
    fingerprint = snapshot_fingerprint(root, root, output_directory(root))
    checks = [
        _execute(root, check, directory / f"{index:02d}-{check['name']}.log")
        for index, check in enumerate(contract["checks"], 1)
    ]
    # A check which changes repository source cannot attest to the original implementation.
    unchanged = fingerprint == snapshot_fingerprint(root, root, output_directory(root))
    passed = unchanged and all(check["state"] == "passed" for check in checks if check["required"])
    record = {
        "schema_version": 1,
        "kind": KIND,
        "repository_root": str(root.resolve()),
        "source_fingerprint": fingerprint,
        "contract_sha256": _contract_hash(contract),
        "source_unchanged": unchanged,
        "checks": checks,
        "passed": passed,
        "log_dir": str(directory),
    }
    path = directory / "verification.json"
    write_json(path, record)
    return {**record, "verification_file": str(path)}


def verify_record(
    root: Path, path: Path, fingerprint: str, *, contract: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Verify the saved result and logs without executing repository commands again."""
    contract = contract if contract is not None else load_contract(root)
    assert contract is not None
    record = read_json(path)
    invalid = WorkflowFilesError(
        "VERIFICATION_EVIDENCE_INVALID",
        "Verification record or logs do not match passed repository checks.",
    )
    if (
        record.get("schema_version") != 1
        or record.get("kind") != KIND
        or record.get("repository_root") != str(root.resolve())
        or record.get("source_fingerprint") != fingerprint
        or record.get("contract_sha256") != _contract_hash(contract)
        or record.get("source_unchanged") is not True
        or record.get("passed") is not True
        or not isinstance(record.get("checks"), list)
        or len(record["checks"]) != len(contract["checks"])
    ):
        raise invalid
    for actual, declared in zip(record["checks"], contract["checks"], strict=True):
        if (
            not isinstance(actual, dict)
            or actual.get("name") != declared["name"]
            or actual.get("required") is not declared["required"]
            or actual.get("state") not in {"passed", "failed", "command_error", "timed_out"}
            or (declared["required"] and actual.get("state") != "passed")
            or (
                actual.get("state") == "passed"
                and (type(actual.get("exit_code")) is not int or actual["exit_code"] != 0)
            )
            or not isinstance(actual.get("log_path"), str)
        ):
            raise invalid
        try:
            if _file_hash(Path(actual["log_path"])) != actual.get("log_sha256"):
                raise invalid
        except OSError as exc:
            raise invalid from exc
    return {
        "path": str(path.resolve()),
        "sha256": _file_hash(path),
        "checks": [
            {"name": check["name"], "required": check["required"], "state": check["state"]}
            for check in record["checks"]
        ],
    }

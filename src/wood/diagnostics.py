"""Shared, bounded readiness checks for secret commands and doctor."""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError

from wood_project.openproject.credentials import resolve_environment
from wood_project.openproject.models import OpenProjectError

from .output import envelope
from .runtime import (
    REQUIRED_VARIABLES,
    infisical_readiness,
    openproject_prerequisites,
    variable_presence,
)

NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")


def secret_command(
    action: str, *, root: Path, environ: Mapping[str, str], names: list[str]
) -> dict[str, object]:
    if len(names) > 30 or any(not NAME_PATTERN.fullmatch(name) for name in names):
        return envelope(
            command=f"secret {action}",
            status="invalid",
            summary="Variable names must be uppercase identifiers; at most 30 are allowed.",
            errors=[{"code": "INVALID_VARIABLE_NAME", "message": "Use uppercase variable names."}],
        )
    selected = tuple(sorted(set(names))) if names else REQUIRED_VARIABLES
    requirements = openproject_prerequisites(environ)
    if action == "requirements":
        return envelope(
            command="secret requirements",
            status="success",
            summary="Required variable names and presence are available.",
            data={
                "requirements": requirements,
                "selected_presence": variable_presence(environ, selected),
            },
        )
    if action == "check":
        present = variable_presence(environ, selected)
        missing = [name for name, found in present.items() if not found]
        return envelope(
            command="secret check",
            status="unavailable" if missing else "success",
            summary="Required variables are missing."
            if missing
            else "Required variables are present.",
            data={"required_names": list(selected), "presence": present, "missing_names": missing},
            next_actions=["Run this command under infisical run with the required variables."]
            if missing
            else [],
        )
    infisical = infisical_readiness(root, environ)
    ready = infisical["state"] == "ready" and requirements["state"] == "ready"
    actions = [
        str(action) for action in (infisical["next_action"], requirements["next_action"]) if action
    ]
    return envelope(
        command="secret status",
        status="success" if ready else "unavailable",
        summary="Secret runtime is ready." if ready else "Secret runtime needs attention.",
        data={"infisical": infisical, "openproject": requirements},
        next_actions=actions,
    )


def repository_readiness(root: Path) -> dict[str, object]:
    if not (root / ".git").exists():
        return {"state": "not_applicable", "next_action": None}
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return {"state": "unavailable", "next_action": "Install git and inspect the repository."}
    if result.returncode:
        return {"state": "unavailable", "next_action": "Inspect git status in this repository."}
    return {"state": "ready", "clean": not bool(result.stdout), "next_action": None}


def openproject_connectivity(environ: Mapping[str, str]) -> dict[str, object]:
    prerequisites = openproject_prerequisites(environ)
    if prerequisites["state"] != "ready":
        return {"state": "unavailable", "next_action": prerequisites["next_action"]}
    url = environ["OPENPROJECT_URL"].rstrip("/") + "/api/v3/users/me"
    token = environ["OPENPROJECT_API_TOKEN"]
    if not url.startswith("https://"):
        return {"state": "unavailable", "next_action": "Set OPENPROJECT_URL to an HTTPS URL."}
    credentials = base64.b64encode(f"apikey:{token}".encode()).decode("ascii")
    req = request.Request(url, headers={"Authorization": f"Basic {credentials}"})
    try:
        with request.urlopen(req, timeout=5) as response:
            response.read(1)
    except HTTPError, URLError, OSError, TimeoutError, ValueError:
        return {
            "state": "unavailable",
            "next_action": "Check OpenProject URL, credentials, and connectivity.",
        }
    return {"state": "ready", "next_action": None}


def doctor(root: Path, environ: Mapping[str, str]) -> dict[str, object]:
    credential_error = None
    try:
        environ = resolve_environment(root, environ)
    except OpenProjectError as exc:
        credential_error = exc
    checks: dict[str, dict[str, object]] = {
        "repository": repository_readiness(root),
        "infisical": infisical_readiness(root, environ),
        "openproject_prerequisites": openproject_prerequisites(environ),
        "openproject_connectivity": openproject_connectivity(environ),
        "tools": {
            "state": "ready"
            if all(shutil.which(name) for name in ("git", "uv", "infisical"))
            else "unavailable",
            "presence": {
                name: shutil.which(name) is not None for name in ("git", "uv", "infisical")
            },
            "next_action": "Install missing required tools."
            if not all(shutil.which(name) for name in ("git", "uv", "infisical"))
            else None,
        },
        "runtime": {
            "state": "ready" if sys.version_info >= (3, 14) else "unavailable",
            "python_3_14_or_newer": sys.version_info >= (3, 14),
            "next_action": "Use Python 3.14 or newer." if sys.version_info < (3, 14) else None,
        },
    }
    unavailable = [name for name, check in checks.items() if check["state"] == "unavailable"]
    actions = [
        str(checks[name]["next_action"]) for name in unavailable if checks[name]["next_action"]
    ]
    if credential_error is not None:
        actions.insert(0, credential_error.message)
    return envelope(
        command="doctor",
        status="unavailable" if unavailable else "success",
        summary="Readiness checks need attention." if unavailable else "Readiness checks passed.",
        data={"checks": checks, "unavailable": unavailable},
        next_actions=list(dict.fromkeys(actions)),
        errors=[{"code": credential_error.code, "message": credential_error.message}]
        if credential_error is not None
        else [],
    )

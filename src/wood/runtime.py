"""Value-free inspection of the environment injected by Infisical."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TypedDict

from wood_project.openproject.credentials import infisical_settings
from wood_project.openproject.models import OpenProjectError

type State = Literal["ready", "unavailable", "not_applicable"]

REQUIRED_VARIABLES = ("OPENPROJECT_URL", "OPENPROJECT_API_TOKEN")


class Check(TypedDict):
    state: State
    next_action: str | None


def variable_presence(environ: Mapping[str, str], names: tuple[str, ...]) -> dict[str, bool]:
    """Never copy variable values into diagnostic data."""
    return {name: bool(environ.get(name)) for name in names}


def infisical_context(root: Path, environ: Mapping[str, str]) -> dict[str, object]:
    try:
        directory, _, _ = infisical_settings(root, environ)
    except OpenProjectError:
        return {"configured": False}
    path = directory / ".infisical.json"
    configured = False
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            configured = isinstance(document, dict) and bool(document.get("workspaceId"))
        except OSError, ValueError:
            pass
    configured = configured or bool(environ.get("INFISICAL_PROJECT_ID"))
    return {"configured": configured}


def infisical_readiness(root: Path, environ: Mapping[str, str]) -> dict[str, object]:
    """Inspect CLI, context, and auth; discard all subprocess output."""
    cli = shutil.which("infisical") is not None
    context = infisical_context(root, environ)
    auth = bool(environ.get("INFISICAL_TOKEN"))
    if cli and not auth:
        try:
            result = subprocess.run(
                ["infisical", "user", "get"],
                cwd=root,
                env=dict(environ),
                capture_output=True,
                timeout=5,
                check=False,
            )
            auth = result.returncode == 0
        except OSError, subprocess.TimeoutExpired:
            pass
    ready = cli and bool(context["configured"]) and auth
    return {
        "state": "ready" if ready else "unavailable",
        "cli_available": cli,
        "context": context,
        "auth_ready": auth,
        "next_action": (
            None
            if ready
            else "Install and authenticate Infisical, then configure its project context."
        ),
    }


def openproject_prerequisites(environ: Mapping[str, str]) -> dict[str, object]:
    present = variable_presence(environ, REQUIRED_VARIABLES)
    missing = [name for name in REQUIRED_VARIABLES if not present[name]]
    return {
        "state": "ready" if not missing else "unavailable",
        "required_names": list(REQUIRED_VARIABLES),
        "presence": present,
        "next_action": (
            None if not missing else "Run wood under Infisical with OpenProject variables injected."
        ),
    }

"""Acquire OpenProject credentials in memory from the authenticated Infisical CLI."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path

from .models import OpenProjectError

REQUIRED_NAMES = ("OPENPROJECT_URL", "OPENPROJECT_API_TOKEN")
SETUP_ACTION = (
    "Authenticate Infisical and configure ~/.config/wood/infisical.toml "
    "with project_config_dir, environment, and secret_path; then retry plain wood."
)


def infisical_settings(root: Path, environ: Mapping[str, str]) -> tuple[Path, str, str]:
    """Read only non-secret selectors; never read credentials from configuration files."""
    config_home = Path(environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    config_file = config_home / "wood" / "infisical.toml"
    if not config_file.exists():
        return root, "dev", "/openproject"
    try:
        settings = tomllib.loads(config_file.read_text(encoding="utf-8"))
        if set(settings) != {"project_config_dir", "environment", "secret_path"}:
            raise ValueError
        if any(not isinstance(value, str) or not value.strip() for value in settings.values()):
            raise ValueError
        directory = Path(settings["project_config_dir"]).expanduser()
        if not directory.is_absolute() or not settings["secret_path"].startswith("/"):
            raise ValueError
        return directory, settings["environment"], settings["secret_path"]
    except OSError, ValueError:
        raise OpenProjectError("INFISICAL_CONFIG_INVALID", SETUP_ACTION) from None


def resolve_environment(root: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Keep existing injected values; fetch missing values without modifying the environment."""
    values = dict(environ)
    missing = [name for name in REQUIRED_NAMES if not values.get(name)]
    if not missing:
        return values
    executable = shutil.which("infisical")
    if executable is None:
        raise OpenProjectError("INFISICAL_CLI_UNAVAILABLE", SETUP_ACTION)
    directory, environment, secret_path = infisical_settings(root, environ)
    if not (directory / ".infisical.json").is_file() and not environ.get("INFISICAL_PROJECT_ID"):
        raise OpenProjectError("INFISICAL_CONTEXT_UNAVAILABLE", SETUP_ACTION)
    for name in missing:
        command = [
            executable,
            "secrets",
            "get",
            name,
            "--plain",
            "--silent",
            f"--env={environment}",
            f"--path={secret_path}",
        ]
        if project_id := environ.get("INFISICAL_PROJECT_ID"):
            command.append(f"--projectId={project_id}")
        try:
            result = subprocess.run(
                command,
                cwd=directory,
                env=dict(environ),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except OSError, subprocess.TimeoutExpired, UnicodeError:
            raise OpenProjectError("INFISICAL_CREDENTIALS_UNAVAILABLE", SETUP_ACTION) from None
        # Never report stdout, stderr, or exception text: all may contain credentials.
        value = result.stdout.rstrip("\r\n")
        if result.returncode or not value.strip() or "\n" in value or "\r" in value:
            raise OpenProjectError("INFISICAL_CREDENTIALS_UNAVAILABLE", SETUP_ACTION)
        values[name] = value
    return values


def credential_next_actions(code: str) -> list[str]:
    return [SETUP_ACTION] if code.startswith("INFISICAL_") else []

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .providers import (
    MissingSecretError,
    ProviderLockedError,
    ProviderStatus,
    ProviderUnavailableError,
    SecretProvider,
    SecretProviderError,
    choose_vaultwarden_item,
    extract_custom_field,
    normalize_token,
    parse_vaultwarden_reference,
)

SESSION_SCHEMA_VERSION = 1
DEFAULT_SESSION_FILE = Path.home() / ".wood" / "runtime" / "secrets" / "vaultwarden-session.json"
DEFAULT_APPDATA_DIR = Path.home() / ".wood" / "runtime" / "bitwarden-cli"
BITWARDEN_DATA_FILE = "data.json"
SESSION_PROBE_SEARCH = "__wood_tools_session_probe__"
UNLOCK_PASSWORD_ENV = "WOOD_SECRETS_BW_UNLOCK_PASSWORD"
APPDATA_ENV = "BITWARDENCLI_APPDATA_DIR"
GUI_UNLOCK_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class VaultwardenSessionStatus:
    provider: str
    path: str
    exists: bool
    protected: bool
    source: str
    usable: bool
    state: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "path": self.path,
            "exists": self.exists,
            "protected": self.protected,
            "source": self.source,
            "usable": self.usable,
            "state": self.state,
            "detail": self.detail,
        }


class VaultwardenSessionStore:
    def __init__(self, path: Path | None = None, *, project_root: Path | None = None) -> None:
        self.path = (path or DEFAULT_SESSION_FILE).expanduser()
        self._project_root = project_root.resolve() if project_root is not None else None

    def _validate_runtime_path(self) -> None:
        if self._project_root is None:
            return
        resolved_path = self.path.resolve(strict=False)
        try:
            resolved_path.relative_to(self._project_root)
        except ValueError:
            return
        raise ProviderUnavailableError(
            "Vaultwarden session files must be stored outside the current project root."
        )

    def _is_protected(self) -> bool:
        if not self.path.exists():
            return False
        mode = stat.S_IMODE(self.path.stat().st_mode)
        return mode == 0o600

    def read_token(self) -> str | None:
        if not self.path.exists():
            return None
        self._validate_runtime_path()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SecretProviderError(
                f"Vaultwarden session file at {self.path} contains invalid JSON."
            ) from exc

        token = payload.get("session_token")
        if not isinstance(token, str) or not token.strip():
            raise SecretProviderError(
                f"Vaultwarden session file at {self.path} is missing a token."
            )
        return token.strip()

    def write_token(self, token: str) -> None:
        self._validate_runtime_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        payload = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "provider": "vaultwarden",
            "created_at": datetime.now(UTC).isoformat(),
            "session_token": token,
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(self.path, 0o600)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def status(self, *, env_token_present: bool, usable: bool = False) -> VaultwardenSessionStatus:
        self._validate_runtime_path()
        if env_token_present:
            return VaultwardenSessionStatus(
                provider="vaultwarden",
                path=str(self.path),
                exists=self.path.exists(),
                protected=self._is_protected(),
                source="environment",
                usable=usable,
                state="ready" if usable else "available",
                detail=(
                    "Vaultwarden session token is available from the current process environment."
                ),
            )

        if not self.path.exists():
            return VaultwardenSessionStatus(
                provider="vaultwarden",
                path=str(self.path),
                exists=False,
                protected=False,
                source="file",
                usable=False,
                state="missing",
                detail="No Vaultwarden runtime session file is present.",
            )

        protected = self._is_protected()
        if not protected:
            return VaultwardenSessionStatus(
                provider="vaultwarden",
                path=str(self.path),
                exists=True,
                protected=False,
                source="file",
                usable=False,
                state="unprotected",
                detail="Vaultwarden runtime session file exists but is not mode 0600.",
            )

        return VaultwardenSessionStatus(
            provider="vaultwarden",
            path=str(self.path),
            exists=True,
            protected=True,
            source="file",
            usable=usable,
            state="ready" if usable else "available",
            detail="Vaultwarden runtime session file is present and access is restricted.",
        )


def prompt_for_password_macos() -> str:
    script = (
        'display dialog "Unlock Vaultwarden" '
        'with title "wood-secrets" '
        'default answer "" with hidden answer '
        'buttons {"Cancel", "OK"} default button "OK" '
        f"giving up after {GUI_UNLOCK_TIMEOUT_SECONDS}"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script, "-e", "text returned of result"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ProviderUnavailableError(
            "GUI unlock requires osascript, but it was not found on PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SecretProviderError("Vaultwarden GUI unlock was cancelled or failed.") from exc

    password = proc.stdout.rstrip("\n")
    if not password:
        raise SecretProviderError("Vaultwarden GUI unlock timed out or did not provide a password.")
    return password


class VaultwardenSecretProvider(SecretProvider):
    scheme = "vaultwarden"
    name = "vaultwarden"

    def __init__(
        self,
        *,
        executable: str = "bw",
        server_url: str | None = None,
        appdata_dir: Path | None = None,
        appdata_source_dir: Path | None = None,
        runner: Callable[..., str] | None = None,
        which: Callable[[str], str | None] | None = None,
        session_store: VaultwardenSessionStore | None = None,
        password_prompt: Callable[[], str] | None = None,
        stdin_isatty: Callable[[], bool] | None = None,
        system_name: str | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.executable = executable
        self.server_url = self._normalize_server_url(server_url)
        self._system_name = system_name or platform.system()
        self.appdata_dir = (appdata_dir or DEFAULT_APPDATA_DIR).expanduser()
        self.appdata_source_dir = (
            appdata_source_dir.expanduser()
            if appdata_source_dir is not None
            else self._default_appdata_source_dir()
        )
        self._runner = runner or self._run_command
        self._which = which or shutil.which
        self._session_store = session_store or VaultwardenSessionStore()
        self._password_prompt = password_prompt or prompt_for_password_macos
        self._stdin_isatty = stdin_isatty or sys.stdin.isatty
        self._environ = environ if environ is not None else os.environ

    @staticmethod
    def _normalize_server_url(server_url: str | None) -> str | None:
        if server_url is None:
            return None
        value = server_url.strip()
        if not value:
            return None
        parsed = urlsplit(value)
        normalized = parsed.geturl().rstrip("/")
        return normalized or None

    def _configured_server(self) -> str:
        return self._runner(["config", "server"]).strip()

    def _default_appdata_source_dir(self) -> Path:
        if self._system_name == "Darwin":
            return Path.home() / "Library" / "Application Support" / "Bitwarden CLI"
        if self._system_name == "Windows":
            appdata = self._environ.get("APPDATA")
            if appdata:
                return Path(appdata) / "Bitwarden CLI"
        xdg_config_home = self._environ.get("XDG_CONFIG_HOME")
        if xdg_config_home:
            return Path(xdg_config_home) / "Bitwarden CLI"
        return Path.home() / ".config" / "Bitwarden CLI"

    def _ensure_appdata_dir(self) -> None:
        self.appdata_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.appdata_dir, 0o700)
        self._bootstrap_appdata_file()

    def _bootstrap_appdata_file(self) -> None:
        target = self.appdata_dir / BITWARDEN_DATA_FILE
        source = self.appdata_source_dir / BITWARDEN_DATA_FILE
        if not source.exists():
            return
        try:
            source.resolve(strict=False).relative_to(self.appdata_dir.resolve(strict=False))
            return
        except ValueError:
            pass
        if target.exists() and self._data_file_has_login_state(target):
            return
        if target.exists() and not self._data_file_has_login_state(source):
            return
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)

    def _data_file_has_login_state(self, path: Path) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        return any(
            bool(payload.get(key))
            for key in (
                "authenticatedAccounts",
                "global_account_accounts",
                "global_account_activeAccountId",
            )
        )

    def _command_env(self) -> dict[str, str]:
        self._ensure_appdata_dir()
        env = dict(self._environ)
        env.setdefault(APPDATA_ENV, str(self.appdata_dir))
        return env

    def _ensure_server_configured(self) -> None:
        if not self.server_url:
            return
        current_server = self._normalize_server_url(self._configured_server())
        if current_server == self.server_url:
            return
        try:
            self._runner(["config", "server", self.server_url])
        except SecretProviderError as exc:
            # Bitwarden CLI requires a logout before changing the configured server.
            if "Logout required before server config update." not in str(exc):
                raise
            self._runner(["logout"])
            self._environ.pop("BW_SESSION", None)
            self._session_store.clear()
            self._runner(["config", "server", self.server_url])

    def _server_mismatch_detail(self) -> str | None:
        if not self.server_url:
            return None
        current_server = self._normalize_server_url(self._configured_server())
        if current_server == self.server_url:
            return None
        current_display = current_server or "<unset>"
        return (
            "Vaultwarden CLI server does not match wood-config. "
            f"Configured {self.server_url}; active CLI server {current_display}. "
            "Run 'wood-secrets unlock' or 'wood-secrets unlock --gui' to apply the "
            "configured server."
        )

    def _run_command(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        session_token: str | None = None,
    ) -> str:
        command = [self.executable, *args]
        if session_token:
            command.extend(["--session", session_token])
        try:
            proc = subprocess.run(
                command,
                input=input_text,
                env=self._command_env(),
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ProviderUnavailableError(
                f"Vaultwarden CLI '{self.executable}' was not found on PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            raise SecretProviderError(
                f"Vaultwarden CLI command failed: {self.executable} {' '.join(args)} ({stderr})"
            ) from exc
        return proc.stdout

    def _run_json(self, args: list[str], *, session_token: str | None = None) -> Any:
        output = self._runner(args, session_token=session_token)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise SecretProviderError(
                f"Vaultwarden CLI returned invalid JSON for: {self.executable} {' '.join(args)}"
            ) from exc

    def _current_session_token(self) -> str | None:
        env_token = self._environ.get("BW_SESSION")
        if env_token:
            return env_token
        return self._session_store.read_token()

    def _session_usable(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            self._ensure_server_configured()
            payload = self._run_json(
                ["list", "items", "--search", SESSION_PROBE_SEARCH],
                session_token=token,
            )
        except SecretProviderError:
            return False
        return isinstance(payload, list)

    def status(self) -> ProviderStatus:
        if self._which(self.executable) is None:
            return ProviderStatus(
                name=self.name,
                scheme=self.scheme,
                available=False,
                unlocked=False,
                configured=False,
                state="unavailable",
                detail=f"Vaultwarden CLI '{self.executable}' is not available on PATH.",
            )

        payload = self._run_json(["status"])
        cli_status = str(payload.get("status") or "unknown").lower()
        mismatch_detail = self._server_mismatch_detail()
        if mismatch_detail:
            return ProviderStatus(
                name=self.name,
                scheme=self.scheme,
                available=True,
                unlocked=False,
                configured=False,
                state="misconfigured",
                detail=mismatch_detail,
            )
        token = self._current_session_token()
        session_usable = self._session_usable(token)

        if cli_status == "unlocked" or session_usable:
            detail = (
                "Vaultwarden CLI is unlocked and can resolve references."
                if cli_status == "unlocked"
                else "Vaultwarden runtime session is available for authenticated commands."
            )
            return ProviderStatus(
                name=self.name,
                scheme=self.scheme,
                available=True,
                unlocked=True,
                configured=True,
                state="ready",
                detail=detail,
            )

        if cli_status in {"locked", "unauthenticated"}:
            state = "locked"
            detail = f"Vaultwarden CLI reported status '{cli_status}'."
        else:
            state = "degraded"
            detail = f"Vaultwarden CLI reported unexpected status '{cli_status}'."

        return ProviderStatus(
            name=self.name,
            scheme=self.scheme,
            available=True,
            unlocked=False,
            configured=True,
            state=state,
            detail=detail,
        )

    def unlock(
        self,
        *,
        interactive: bool = False,
        gui: bool = False,
        write_session: bool = False,
    ) -> ProviderStatus:
        if self._which(self.executable) is None:
            raise ProviderUnavailableError(
                f"Vaultwarden CLI '{self.executable}' was not found on PATH."
            )
        if gui and interactive:
            raise SecretProviderError("Choose either interactive unlock or GUI unlock, not both.")
        if not gui and not interactive:
            raise SecretProviderError("Unlock requires either --interactive or --gui.")
        if interactive and not self._stdin_isatty():
            raise ProviderLockedError(
                "Interactive Vaultwarden unlock requires a local terminal TTY."
            )
        if gui and self._system_name != "Darwin":
            raise ProviderUnavailableError("GUI unlock is only available on macOS.")

        self._ensure_server_configured()
        password = self._password_prompt() if gui else getpass("Vaultwarden master password: ")
        if not password:
            raise SecretProviderError("Vaultwarden unlock requires a non-empty password.")
        previous_password = self._environ.get(UNLOCK_PASSWORD_ENV)
        self._environ[UNLOCK_PASSWORD_ENV] = password
        try:
            token = self._runner(["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV]).strip()
        finally:
            password = ""
            if previous_password is None:
                self._environ.pop(UNLOCK_PASSWORD_ENV, None)
            else:
                self._environ[UNLOCK_PASSWORD_ENV] = previous_password
        if not token:
            raise SecretProviderError("Vaultwarden unlock did not return a session token.")
        self._environ["BW_SESSION"] = token
        if write_session:
            self._session_store.write_token(token)
        return self.status()

    def lock(self) -> ProviderStatus:
        if self._which(self.executable) is None:
            raise ProviderUnavailableError(
                f"Vaultwarden CLI '{self.executable}' was not found on PATH."
            )
        token = self._current_session_token()
        self._runner(["lock"], session_token=token)
        self._environ.pop("BW_SESSION", None)
        self._session_store.clear()
        return self.status()

    def session_status(self) -> dict[str, Any]:
        env_token = self._environ.get("BW_SESSION")
        token = env_token or self._session_store.read_token()
        usable = self._session_usable(token)
        return self._session_store.status(
            env_token_present=bool(env_token),
            usable=usable,
        ).to_dict()

    def resolve(self, reference: str) -> str:
        status = self.status()
        if not status.available:
            raise ProviderUnavailableError(status.detail)
        if not status.configured:
            raise ProviderUnavailableError(status.detail)
        if not status.unlocked:
            raise ProviderLockedError(
                "Vaultwarden is not unlocked. Unlock it before resolving secrets."
            )

        session_token = self._current_session_token()
        ref = parse_vaultwarden_reference(reference)
        items = self._run_json(
            ["list", "items", "--search", ref.search_term],
            session_token=session_token,
        )
        if not isinstance(items, list):
            raise SecretProviderError("Unexpected Vaultwarden CLI output while resolving secret.")

        item = choose_vaultwarden_item(items, ref)
        field_candidates = [
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

        raise MissingSecretError(
            "Vaultwarden item did not contain a matching custom field or login.password."
        )

    def list_entries(self, *, search: str | None = None) -> dict[str, Any]:
        status = self.status()
        if not status.available:
            raise ProviderUnavailableError(status.detail)
        if not status.configured:
            raise ProviderUnavailableError(status.detail)
        if not status.unlocked:
            raise ProviderLockedError(
                "Vaultwarden is not unlocked. Unlock it before listing secret entries."
            )

        session_token = self._current_session_token()
        args = ["list", "items"]
        if search and search.strip():
            args.extend(["--search", search.strip()])
        payload = self._run_json(args, session_token=session_token)
        if not isinstance(payload, list):
            raise SecretProviderError("Unexpected Vaultwarden CLI output while listing secrets.")

        items: list[dict[str, Any]] = []
        for item in payload:
            fields = item.get("fields") or []
            field_names = [
                str(field.get("name") or "").strip()
                for field in fields
                if str(field.get("name") or "").strip()
            ]
            login = item.get("login") or {}
            has_login_password = isinstance(login.get("password"), str) and bool(
                login.get("password")
            )
            items.append(
                {
                    "name": str(item.get("name") or "<unnamed>").strip() or "<unnamed>",
                    "field_names": field_names,
                    "has_login_password": has_login_password,
                }
            )

        return {
            "provider": self.name,
            "search": search.strip() if search and search.strip() else None,
            "count": len(items),
            "items": items,
        }

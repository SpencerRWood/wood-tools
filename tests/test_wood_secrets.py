from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from wood_secrets.cli import main
from wood_secrets.core import SecretResolver
from wood_secrets.providers import (
    EnvironmentSecretProvider,
    MissingSecretError,
    ProviderLockedError,
    ProviderStatus,
    ProviderUnavailableError,
    SecretProvider,
    SecretProviderError,
    normalize_env_fallback_name,
)
from wood_secrets.vaultwarden import (
    UNLOCK_PASSWORD_ENV,
    VaultwardenSecretProvider,
    VaultwardenSessionStore,
)


class StubProvider(SecretProvider):
    scheme = "vaultwarden"
    name = "vaultwarden"

    def __init__(
        self,
        *,
        status: ProviderStatus | None = None,
        value: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self._status = status or ProviderStatus(
            name="vaultwarden",
            scheme="vaultwarden",
            available=True,
            unlocked=True,
            configured=True,
            state="ready",
            detail="Stub provider ready.",
        )
        self._value = value
        self._error = error
        self._session = {
            "provider": "vaultwarden",
            "path": str(Path.home() / ".wood" / "runtime" / "secrets" / "vaultwarden-session.json"),
            "exists": True,
            "protected": True,
            "source": "file",
            "usable": True,
            "state": "ready",
            "detail": "Stub session ready.",
        }

    def status(self) -> ProviderStatus:
        return self._status

    def unlock(
        self,
        *,
        interactive: bool = False,
        gui: bool = False,
        write_session: bool = False,
    ) -> ProviderStatus:
        return self._status

    def lock(self) -> ProviderStatus:
        return self._status

    def session_status(self) -> dict[str, object]:
        return self._session

    def resolve(self, reference: str) -> str:
        if self._error is not None:
            raise self._error
        if self._value is None:
            raise MissingSecretError(f"Missing stub secret for {reference}")
        return self._value

    def list_entries(self, *, search: str | None = None) -> dict[str, object]:
        if self._error is not None:
            raise self._error
        return {
            "provider": "vaultwarden",
            "search": search,
            "count": 1,
            "items": [
                {
                    "name": "wood / openproject / prod",
                    "field_names": ["api-token", "username"],
                    "has_login_password": True,
                }
            ],
        }


def make_config_document(
    *,
    openproject_ref: str | None = None,
    ntfy_ref: str | None = None,
    vaultwarden_url: str | None = None,
) -> dict[str, object]:
    return {
        "version": 1,
        "active_profile": "default",
        "profiles": {
            "default": {
                "integrations": {
                    "openproject": {
                        "token_ref": openproject_ref,
                    },
                    "ntfy": {
                        "token_ref": ntfy_ref,
                    },
                    "vaultwarden": {
                        "url": vaultwarden_url,
                        "session_file": None,
                        "cli": {"executable": "bw"},
                    },
                }
            }
        },
    }


@pytest.fixture
def install_stub_resolver(monkeypatch: pytest.MonkeyPatch):
    def _install(
        *,
        provider: SecretProvider | None = None,
        command_runner: object | None = None,
        environ: dict[str, str] | None = None,
        config_document: dict[str, object] | None = None,
    ) -> None:
        monkeypatch.setattr(
            "wood_secrets.core.load_config",
            lambda _: config_document if config_document is not None else make_config_document(),
        )
        env_provider = EnvironmentSecretProvider(environ=environ or {})
        providers = {
            "env": env_provider,
            "vaultwarden": provider or StubProvider(value="super-secret-token"),
        }
        monkeypatch.setattr(
            "wood_secrets.cli.SecretResolver",
            lambda: SecretResolver(
                providers=providers,
                command_runner=command_runner,
                environ=environ or {},
            ),
        )

    return _install


def test_resolve_redacted_success_path(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["resolve", "--ref", "vaultwarden://wood/prod/api-token", "--redacted"])

    assert code == 0
    out = capsys.readouterr().out
    assert "[REDACTED]" in out
    assert "super-secret-token" not in out


def test_resolve_redacted_success_path_with_explicit_field_selector(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(
        [
            "resolve",
            "--ref",
            "vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN",
            "--redacted",
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "[REDACTED]" in out
    assert "super-secret-token" not in out


def test_resolve_uses_environment_fallback_without_printing_secret(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    reference = "vaultwarden://wood/prod/api-token"
    fallback_name = normalize_env_fallback_name(reference)
    install_stub_resolver(
        provider=StubProvider(error=MissingSecretError("provider unavailable")),
        environ={fallback_name: "fallback-secret"},
    )

    code = main(["resolve", "--ref", reference, "--redacted", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["from_env_fallback"] is True
    assert payload["data"]["redacted_value"] == "[REDACTED]"
    assert "fallback-secret" not in json.dumps(payload)


def test_status_json_lists_provider_status(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["status", "--provider", "vaultwarden", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "status"
    assert payload["data"]["ok"] is True
    assert payload["data"]["provider"] == "vaultwarden"
    assert payload["data"]["status"]["scheme"] == "vaultwarden"


def test_providers_json_lists_registered_provider_statuses(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["providers", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "providers"
    assert payload["data"]["ok"] is True
    assert {provider["scheme"] for provider in payload["data"]["providers"]} == {
        "env",
        "vaultwarden",
    }


def test_check_json_reports_configured_openproject_and_ntfy_without_printing_secret(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver(
        environ={
            "OPENPROJECT_TOKEN": "openproject-secret",
            "NTFY_TOKEN": "ntfy-secret",
        },
        config_document=make_config_document(
            openproject_ref="env://OPENPROJECT_TOKEN",
            ntfy_ref="env://NTFY_TOKEN",
        ),
    )

    code = main(["check", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["status"] == "success"
    assert payload["data"]["ok"] is True
    assert {check["integration"] for check in payload["data"]["checks"]} == {"openproject", "ntfy"}
    assert all(check["redacted_value"] == "[REDACTED]" for check in payload["data"]["checks"])
    assert "openproject-secret" not in rendered
    assert "ntfy-secret" not in rendered


def test_check_json_reports_missing_reference_configuration(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver(
        config_document=make_config_document(
            openproject_ref="env://OPENPROJECT_TOKEN",
            ntfy_ref=None,
        ),
    )

    code = main(["check", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    ntfy_check = next(
        check for check in payload["data"]["checks"] if check["integration"] == "ntfy"
    )
    assert ntfy_check["configured"] is False
    assert ntfy_check["ok"] is False


def test_resolve_invalid_reference_returns_error(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["resolve", "--ref", "not-a-reference", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "scheme" in payload["summary"]


def test_resolve_invalid_explicit_field_reference_returns_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["resolve", "--ref", "vaultwarden://#FIELD_NAME", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "at least two path segments" in payload["summary"]


def test_doctor_reports_locked_provider(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver(
        provider=StubProvider(
            status=ProviderStatus(
                name="vaultwarden",
                scheme="vaultwarden",
                available=True,
                unlocked=False,
                configured=True,
                state="locked",
                detail="Vaultwarden CLI reported status 'locked'.",
            ),
            error=MissingSecretError("locked"),
        ),
        environ={
            "OPENPROJECT_TOKEN": "openproject-secret",
            "NTFY_TOKEN": "ntfy-secret",
        },
        config_document=make_config_document(
            openproject_ref="env://OPENPROJECT_TOKEN",
            ntfy_ref="env://NTFY_TOKEN",
        ),
    )

    code = main(["doctor", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    assert any(issue["code"] == "provider_locked" for issue in payload["data"]["issues"])


def test_doctor_reports_misconfigured_provider(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver(
        provider=StubProvider(
            status=ProviderStatus(
                name="vaultwarden",
                scheme="vaultwarden",
                available=True,
                unlocked=False,
                configured=False,
                state="misconfigured",
                detail="Vaultwarden CLI server does not match wood-config.",
            ),
            error=ProviderUnavailableError("server mismatch"),
        )
    )

    code = main(["doctor", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    assert any(issue["code"] == "provider_misconfigured" for issue in payload["data"]["issues"])


def test_doctor_reports_integration_secret_issue_without_printing_secret(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver(
        environ={"OPENPROJECT_TOKEN": "openproject-secret"},
        config_document=make_config_document(
            openproject_ref="env://OPENPROJECT_TOKEN",
            ntfy_ref="env://NTFY_TOKEN",
        ),
    )

    code = main(["doctor", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["status"] == "warning"
    assert any(issue["code"] == "integration_secret_unready" for issue in payload["data"]["issues"])
    assert "openproject-secret" not in rendered


def test_unlock_json_reports_redacted_session_metadata(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(
        [
            "unlock",
            "--provider",
            "vaultwarden",
            "--interactive",
            "--write-session",
            "--json",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["command"] == "unlock"
    assert payload["data"]["session"]["protected"] is True
    assert payload["data"]["write_session"] is True
    assert "super-secret-token" not in rendered


def test_session_json_reports_runtime_session_without_secret_value(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["session", "--provider", "vaultwarden", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["command"] == "session"
    assert payload["data"]["session"]["state"] == "ready"
    assert "super-secret-token" not in rendered


def test_list_json_reports_item_and_field_names_without_secret_values(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["list", "--provider", "vaultwarden", "--search", "openproject", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["command"] == "list"
    assert payload["data"]["provider"] == "vaultwarden"
    assert payload["data"]["search"] == "openproject"
    assert payload["data"]["count"] == 1
    assert payload["data"]["items"][0]["field_names"] == ["api-token", "username"]
    assert "super-secret-token" not in rendered


def test_exec_injects_secret_into_child_process_without_printing_secret(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command: list[str], *, env: dict[str, str]) -> int:
        calls.append((command, env))
        return 0

    install_stub_resolver(command_runner=runner)

    code = main(
        [
            "exec",
            "--env",
            "OPENPROJECT_TOKEN",
            "--ref",
            "vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN",
            "--",
            "env",
        ]
    )

    assert code == 0
    assert calls[0][0] == ["env"]
    assert calls[0][1]["OPENPROJECT_TOKEN"] == "super-secret-token"
    assert "super-secret-token" not in capsys.readouterr().out


def test_exec_supports_inline_binding_syntax(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command: list[str], *, env: dict[str, str]) -> int:
        calls.append((command, env))
        return 0

    install_stub_resolver(command_runner=runner)

    code = main(
        [
            "exec",
            "OPENPROJECT_TOKEN=vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN",
            "--",
            "env",
        ]
    )

    assert code == 0
    assert calls[0][0] == ["env"]
    assert calls[0][1]["OPENPROJECT_TOKEN"] == "super-secret-token"
    assert "super-secret-token" not in capsys.readouterr().out


def test_exec_supports_multiple_inline_bindings(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command: list[str], *, env: dict[str, str]) -> int:
        calls.append((command, env))
        return 0

    install_stub_resolver(command_runner=runner)

    code = main(
        [
            "exec",
            "OPENPROJECT_TOKEN=vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN",
            "SECOND_TOKEN=vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN",
            "--",
            "env",
        ]
    )

    assert code == 0
    assert calls[0][1]["OPENPROJECT_TOKEN"] == "super-secret-token"
    assert calls[0][1]["SECOND_TOKEN"] == "super-secret-token"
    assert "super-secret-token" not in capsys.readouterr().out


def test_lock_json_reports_locked_state(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver(
        provider=StubProvider(
            status=ProviderStatus(
                name="vaultwarden",
                scheme="vaultwarden",
                available=True,
                unlocked=False,
                configured=True,
                state="locked",
                detail="Vaultwarden CLI reported status 'locked'.",
            ),
        )
    )

    code = main(["lock", "--provider", "vaultwarden", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "lock"
    assert payload["data"]["ok"] is True
    assert payload["data"]["status"]["state"] == "locked"


def test_vaultwarden_unlock_writes_protected_runtime_session_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_file = tmp_path / "runtime" / "vaultwarden-session.json"
    calls: list[tuple[list[str], str | None, str | None]] = []
    environ: dict[str, str] = {}

    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        calls.append((args, input_text, session_token))
        if args == ["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV]:
            assert environ[UNLOCK_PASSWORD_ENV] == "master-password"
            return "session-token\n"
        if args == ["status"]:
            return json.dumps({"status": "locked"})
        if args[:3] == ["list", "items", "--search"]:
            return "[]"
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=session_file, project_root=tmp_path / "project"),
        environ=environ,
    )
    monkeypatch.setattr("wood_secrets.vaultwarden.getpass", lambda _: "master-password")

    status = provider.unlock(interactive=True, write_session=True)

    assert status.unlocked is True
    assert session_file.exists()
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
    assert "session-token" not in provider.session_status()["detail"]
    assert UNLOCK_PASSWORD_ENV not in environ
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["session_token"] == "session-token"
    assert calls[0] == (["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV], None, None)


def test_vaultwarden_status_reports_configured_server_mismatch(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args == ["config", "server"]:
            return "https://vault.other.example\n"
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        server_url="https://vault.example.test/",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={},
    )

    status = provider.status()

    assert status.available is True
    assert status.configured is False
    assert status.unlocked is False
    assert status.state == "misconfigured"
    assert "https://vault.example.test" in status.detail
    assert "https://vault.other.example" in status.detail


def test_vaultwarden_unlock_applies_configured_server_before_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], str | None, str | None]] = []
    current_server = "https://vault.other.example"
    environ: dict[str, str] = {}

    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        nonlocal current_server
        calls.append((args, input_text, session_token))
        if args == ["config", "server"]:
            return f"{current_server}\n"
        if args == ["config", "server", "https://vault.example.test"]:
            current_server = "https://vault.example.test"
            return "https://vault.example.test\n"
        if args == ["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV]:
            assert environ[UNLOCK_PASSWORD_ENV] == "master-password"
            return "session-token\n"
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args[:3] == ["list", "items", "--search"]:
            return "[]"
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        server_url="https://vault.example.test",
        session_store=VaultwardenSessionStore(
            path=tmp_path / "runtime" / "vaultwarden-session.json"
        ),
        environ=environ,
    )
    monkeypatch.setattr("wood_secrets.vaultwarden.getpass", lambda _: "master-password")

    status = provider.unlock(interactive=True, write_session=False)

    assert status.unlocked is True
    assert status.configured is True
    assert calls[0] == (["config", "server"], None, None)
    assert calls[1] == (["config", "server", "https://vault.example.test"], None, None)
    assert calls[2] == (["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV], None, None)
    assert UNLOCK_PASSWORD_ENV not in environ


def test_vaultwarden_unlock_logs_out_before_reconfiguring_server_when_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], str | None, str | None]] = []
    configured_once = False
    current_server = "https://vault.other.example"
    environ = {"BW_SESSION": "stale-session"}

    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        nonlocal configured_once
        nonlocal current_server
        calls.append((args, input_text, session_token))
        if args == ["config", "server"]:
            return f"{current_server}\n"
        if args == ["config", "server", "https://vault.example.test"]:
            if not configured_once:
                configured_once = True
                raise SecretProviderError(
                    "Vaultwarden CLI command failed: bw config server https://vault.example.test "
                    "(Logout required before server config update.)"
                )
            current_server = "https://vault.example.test"
            return "https://vault.example.test\n"
        if args == ["logout"]:
            return ""
        if args == ["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV]:
            assert environ[UNLOCK_PASSWORD_ENV] == "master-password"
            return "session-token\n"
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args[:3] == ["list", "items", "--search"]:
            return "[]"
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        server_url="https://vault.example.test",
        session_store=VaultwardenSessionStore(
            path=tmp_path / "runtime" / "vaultwarden-session.json"
        ),
        environ=environ,
    )
    monkeypatch.setattr("wood_secrets.vaultwarden.getpass", lambda _: "master-password")

    status = provider.unlock(interactive=True, write_session=False)

    assert status.unlocked is True
    assert calls[0] == (["config", "server"], None, None)
    assert calls[1] == (["config", "server", "https://vault.example.test"], None, None)
    assert calls[2] == (["logout"], None, None)
    assert calls[3] == (["config", "server", "https://vault.example.test"], None, None)
    assert calls[4] == (["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV], None, None)
    assert UNLOCK_PASSWORD_ENV not in environ


def test_vaultwarden_unlock_restores_existing_password_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = {UNLOCK_PASSWORD_ENV: "previous-value"}

    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["unlock", "--raw", "--passwordenv", UNLOCK_PASSWORD_ENV]:
            assert environ[UNLOCK_PASSWORD_ENV] == "master-password"
            return "session-token\n"
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args[:3] == ["list", "items", "--search"]:
            return "[]"
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ=environ,
    )
    monkeypatch.setattr("wood_secrets.vaultwarden.getpass", lambda _: "master-password")

    status = provider.unlock(interactive=True, write_session=False)

    assert status.unlocked is True
    assert environ[UNLOCK_PASSWORD_ENV] == "previous-value"


def test_vaultwarden_session_file_must_live_outside_project_root(tmp_path: Path) -> None:
    session_store = VaultwardenSessionStore(
        path=tmp_path / "project" / ".wood" / "vaultwarden-session.json",
        project_root=tmp_path / "project",
    )

    with pytest.raises(ProviderUnavailableError, match="outside the current project root"):
        session_store.write_token("session-token")


def test_vaultwarden_session_file_allows_default_runtime_path_outside_explicit_project_context(
    tmp_path: Path,
) -> None:
    session_store = VaultwardenSessionStore(path=tmp_path / "runtime" / "vaultwarden-session.json")

    session_store.write_token("session-token")

    assert session_store.read_token() == "session-token"


def test_vaultwarden_resolve_fails_closed_when_locked_and_reference_missing_session(
    tmp_path: Path,
) -> None:
    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "locked"})
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={},
    )

    with pytest.raises(ProviderLockedError, match="not unlocked"):
        provider.resolve("vaultwarden://wood/prod/api-token")


def test_vaultwarden_resolve_fails_closed_when_configured_server_mismatches(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args == ["config", "server"]:
            return "https://vault.other.example\n"
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        server_url="https://vault.example.test",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={},
    )

    with pytest.raises(ProviderUnavailableError, match="does not match wood-config"):
        provider.resolve("vaultwarden://wood/prod/api-token")


def test_vaultwarden_resolve_supports_explicit_field_selector(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args == ["list", "items", "--search", "__wood_tools_session_probe__"]:
            return "[]"
        if args == ["list", "items", "--search", "api-token"]:
            return json.dumps(
                [
                    {
                        "name": "wood / openproject / prod / api-token",
                        "fields": [
                            {"name": "OPENPROJECT_API_TOKEN", "value": "secret-token"},
                            {"name": "username", "value": "spencer"},
                        ],
                        "login": {"password": ""},
                    }
                ]
            )
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={"BW_SESSION": "active-session"},
    )

    resolved = provider.resolve(
        "vaultwarden://wood/openproject/prod/api-token#OPENPROJECT_API_TOKEN"
    )

    assert resolved == "secret-token"


def test_vaultwarden_list_entries_returns_names_only(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args == ["list", "items", "--search", "__wood_tools_session_probe__"]:
            return "[]"
        if args == ["list", "items", "--search", "openproject"]:
            return json.dumps(
                [
                    {
                        "name": "wood / openproject / prod",
                        "fields": [
                            {"name": "api-token", "value": "secret-token"},
                            {"name": "username", "value": "spencer"},
                        ],
                        "login": {"password": "super-secret"},
                    }
                ]
            )
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={"BW_SESSION": "active-session"},
    )

    payload = provider.list_entries(search="openproject")

    assert payload["count"] == 1
    assert payload["items"] == [
        {
            "name": "wood / openproject / prod",
            "field_names": ["api-token", "username"],
            "has_login_password": True,
        }
    ]

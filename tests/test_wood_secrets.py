from __future__ import annotations

import json

import pytest

from wood_secrets.cli import main
from wood_secrets.core import SecretResolver
from wood_secrets.providers import (
    EnvironmentSecretProvider,
    MissingSecretError,
    ProviderStatus,
    SecretProvider,
    normalize_env_fallback_name,
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

    def status(self) -> ProviderStatus:
        return self._status

    def unlock(self) -> ProviderStatus:
        return self._status

    def lock(self) -> ProviderStatus:
        return self._status

    def resolve(self, reference: str) -> str:
        if self._error is not None:
            raise self._error
        if self._value is None:
            raise MissingSecretError(f"Missing stub secret for {reference}")
        return self._value


@pytest.fixture
def install_stub_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    def _install(
        *,
        provider: SecretProvider | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        env_provider = EnvironmentSecretProvider(environ=environ or {})
        providers = {
            "env": env_provider,
            "vaultwarden": provider or StubProvider(value="super-secret-token"),
        }
        monkeypatch.setattr(
            "wood_secrets.cli.SecretResolver",
            lambda: SecretResolver(providers=providers, environ=environ or {}),
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


def test_check_json_lists_provider_status(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["check", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "check"
    assert payload["data"]["ok"] is True
    assert {provider["scheme"] for provider in payload["data"]["providers"]} == {
        "env",
        "vaultwarden",
    }


def test_resolve_invalid_reference_returns_error(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["resolve", "--ref", "not-a-reference", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "scheme" in payload["summary"]


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
        )
    )

    code = main(["doctor", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    assert payload["data"]["issues"][0]["code"] == "provider_locked"


def test_providers_output_is_redacted_by_design(
    install_stub_resolver, capsys: pytest.CaptureFixture[str]
) -> None:
    install_stub_resolver()

    code = main(["providers", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert "super-secret-token" not in rendered
    assert payload["data"]["providers"][0]["supports_env_fallback"] is True

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from wood_secrets.cli import main
from wood_secrets.core import SecretResolver
from wood_secrets.core.providers import (
    CreatedSecret,
    EnvironmentSecretProvider,
    MissingSecretError,
    ProviderLockedError,
    ProviderStatus,
    ProviderUnavailableError,
    SecretProvider,
    SecretProviderError,
    normalize_env_fallback_name,
)
from wood_secrets.core.vaultwarden import (
    APPDATA_ENV,
    UNLOCK_PASSWORD_ENV,
    VaultwardenSecretProvider,
    VaultwardenSessionStore,
    prompt_for_password_macos,
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
        canonical_item_exists: bool = False,
        create_error: Exception | None = None,
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
        self._canonical_item_exists = canonical_item_exists
        self._create_error = create_error
        self.unlock_calls: list[dict[str, bool]] = []
        self.exists_checks: list[dict[str, str]] = []
        self.create_calls: list[dict[str, object]] = []
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
        self.unlock_calls.append(
            {
                "interactive": interactive,
                "gui": gui,
                "write_session": write_session,
            }
        )
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
                    "name": "openproject / wood-tools",
                    "field_names": ["api-token", "username"],
                    "has_login_password": True,
                }
            ],
        }

    def canonical_item_exists(self, *, item_name: str, search: str) -> bool:
        if self._error is not None:
            raise self._error
        self.exists_checks.append({"item_name": item_name, "search": search})
        return self._canonical_item_exists

    def create_canonical_secret(
        self,
        *,
        item_name: str,
        field_name: str,
        value: str,
        metadata: dict[str, str],
    ) -> CreatedSecret:
        if self._create_error is not None:
            raise self._create_error
        self.create_calls.append(
            {
                "item_name": item_name,
                "field_name": field_name,
                "value_length": len(value),
                "metadata": metadata,
            }
        )
        return CreatedSecret(
            provider="vaultwarden",
            item_id="vaultwarden-item-id",
            item_name=item_name,
        )


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


def make_materialization_config(
    *,
    secrets_root: Path,
    definitions: dict[str, object],
) -> dict[str, object]:
    document = make_config_document()
    profile = document["profiles"]["default"]  # type: ignore[index]
    profile["paths"] = {  # type: ignore[index]
        "project_root": "./projects",
        "project_aliases": {},
        "secrets_root": str(secrets_root),
        "scheduler_root": "./scheduler",
        "template_search_paths": ["./templates"],
    }
    profile["integrations"]["vaultwarden"]["materialized_secrets"] = definitions  # type: ignore[index]
    return document


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
            "wood_secrets.core.resolver.load_config",
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

    code = main(
        ["resolve", "--ref", "vaultwarden://openproject/wood-tools/api-token", "--redacted"]
    )

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
            "vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN",
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
    reference = "vaultwarden://openproject/wood-tools/api-token"
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


def test_resolve_env_json_previews_without_writing_or_leaking_secret(
    install_stub_resolver,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    output_file = tmp_path / ".env.resolved"
    env_file.write_text(
        "\n".join(
            [
                "# local development",
                "OPENPROJECT_TOKEN_REF=vaultwarden://openproject/wood-tools/api-token",
                "PLAIN_VALUE=kept",
            ]
        ),
        encoding="utf-8",
    )
    install_stub_resolver()

    code = main(
        [
            "resolve-env",
            "--input",
            str(env_file),
            "--output",
            str(output_file),
            "--json",
        ]
    )

    assert code == 0
    assert not output_file.exists()
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["command"] == "resolve-env"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["resolved_count"] == 1
    assert payload["data"]["resolved"][0]["output_key"] == "OPENPROJECT_TOKEN"
    assert payload["data"]["resolved"][0]["redacted_value"] == "[REDACTED]"
    assert "super-secret-token" not in rendered


def test_resolve_env_apply_writes_file_without_printing_secret(
    install_stub_resolver,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    output_file = tmp_path / ".env.resolved"
    env_file.write_text(
        "\n".join(
            [
                "OPENPROJECT_TOKEN_REF=vaultwarden://openproject/wood-tools/api-token",
                "NTFY_TOKEN_REF=env://NTFY_TOKEN",
            ]
        ),
        encoding="utf-8",
    )
    install_stub_resolver(environ={"NTFY_TOKEN": "ntfy-secret"})

    code = main(
        [
            "resolve-env",
            "--input",
            str(env_file),
            "--output",
            str(output_file),
            "--apply",
        ]
    )

    assert code == 0
    assert output_file.read_text(encoding="utf-8") == (
        "OPENPROJECT_TOKEN=super-secret-token\nNTFY_TOKEN=ntfy-secret\n"
    )
    out = capsys.readouterr().out
    assert "resolved OPENPROJECT_TOKEN" in out
    assert "resolved NTFY_TOKEN" in out
    assert "super-secret-token" not in out
    assert "ntfy-secret" not in out


def test_resolve_env_force_requires_apply(
    install_stub_resolver,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TOKEN_REF=vaultwarden://openproject/wood-tools/api-token\n",
        encoding="utf-8",
    )
    install_stub_resolver()

    code = main(["resolve-env", "--input", str(env_file), "--force", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "--force can only be used with --apply" in payload["summary"]


def test_materialize_json_previews_without_writing_or_leaking_secret(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "--json"])

    assert code == 0
    assert not (secrets_root / "openproject" / "api-token").exists()
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["command"] == "materialize"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["materialized"][0]["state"] == "missing"
    assert payload["data"]["materialized"][0]["redacted_value"] == "[REDACTED]"
    assert "super-secret-token" not in rendered


def test_materialize_json_derives_target_from_vaultwarden_identity(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token#API_TOKEN",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "openproject-token", "--json"])

    assert code == 0
    assert not (secrets_root / "openproject" / "wood-tools" / "api-token").exists()
    payload = json.loads(capsys.readouterr().out)
    item = payload["data"]["materialized"][0]
    rendered = json.dumps(payload)
    assert item["target"] == str(secrets_root / "openproject" / "wood-tools" / "api-token")
    assert item["service"] == "openproject"
    assert item["principal"] == "wood-tools"
    assert item["credential"] == "api-token"
    assert item["state"] == "missing"
    assert "super-secret-token" not in rendered


def test_materialize_apply_writes_protected_file_without_printing_secret(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "openproject-token", "--apply", "--json"])

    assert code == 0
    target = secrets_root / "openproject" / "api-token"
    assert target.read_text(encoding="utf-8") == "super-secret-token"
    assert stat.S_IMODE(secrets_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((secrets_root / "openproject").stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["data"]["materialized"][0]["state"] == "created"
    assert "super-secret-token" not in rendered


def test_materialize_apply_writes_derived_target_without_printing_secret(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "postgres-password": {
                    "ref": "vaultwarden://postgres/wood-events/password#PASSWORD",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "postgres-password", "--apply", "--json"])

    assert code == 0
    target = secrets_root / "postgres" / "wood-events" / "password"
    assert target.read_text(encoding="utf-8") == "super-secret-token"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["data"]["materialized"][0]["target"] == str(target)
    assert payload["data"]["materialized"][0]["state"] == "created"
    assert "super-secret-token" not in rendered


def test_materialize_explicit_target_overrides_vaultwarden_identity(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token#API_TOKEN",
                    "target": "custom/openproject-token",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "openproject-token", "--apply", "--json"])

    assert code == 0
    explicit_target = secrets_root / "custom" / "openproject-token"
    derived_target = secrets_root / "openproject" / "wood-tools" / "api-token"
    assert explicit_target.read_text(encoding="utf-8") == "super-secret-token"
    assert not derived_target.exists()
    payload = json.loads(capsys.readouterr().out)
    item = payload["data"]["materialized"][0]
    assert item["target"] == str(explicit_target)
    assert "service" not in item


def test_materialize_apply_skips_unchanged_file(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    target = secrets_root / "openproject" / "api-token"
    target.parent.mkdir(parents=True)
    target.write_text("super-secret-token", encoding="utf-8")
    before = target.stat().st_mtime_ns
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "openproject-token", "--apply", "--json"])

    assert code == 0
    assert target.stat().st_mtime_ns == before
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["materialized"][0]["state"] == "current"
    assert payload["data"]["materialized"][0]["changed"] is False


def test_materialize_provider_failure_preserves_existing_file_without_leak(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    target = secrets_root / "openproject" / "api-token"
    target.parent.mkdir(parents=True)
    target.write_text("previous-valid-value", encoding="utf-8")
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    install_stub_resolver(provider=StubProvider(error=ProviderUnavailableError("secret boom")))

    code = main(["materialize", "openproject-token", "--apply", "--json"])

    assert code == 0
    assert target.read_text(encoding="utf-8") == "previous-valid-value"
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["status"] == "warning"
    assert payload["data"]["errors"][0]["error_type"] == "ProviderUnavailableError"
    assert "secret boom" not in rendered
    assert "previous-valid-value" not in rendered


def test_materialize_rejects_unsafe_target_without_writing(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "bad-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "../outside",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "bad-token", "--apply", "--json"])

    assert code == 0
    assert not (tmp_path / "outside").exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    assert payload["data"]["errors"][0]["error_type"] == "MaterializationError"


def test_materialize_rejects_malformed_derived_identity_without_writing(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "bad-token": {
                    "ref": "vaultwarden://postgres/password#PASSWORD",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "bad-token", "--apply", "--json"])

    assert code == 0
    assert not secrets_root.exists()
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["status"] == "warning"
    assert payload["data"]["errors"][0]["error_type"] == "MaterializationError"
    assert "super-secret-token" not in rendered


def test_materialize_rejects_symlink_parent_escape(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    outside = tmp_path / "outside"
    outside.mkdir()
    secrets_root.mkdir()
    (secrets_root / "linked").symlink_to(outside)
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "bad-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "linked/api-token",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize", "bad-token", "--apply", "--json"])

    assert code == 0
    assert not (outside / "api-token").exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"


def test_materialize_atomic_write_failure_preserves_existing_file(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    target = secrets_root / "openproject" / "api-token"
    target.parent.mkdir(parents=True)
    target.write_text("previous-valid-value", encoding="utf-8")
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    monkeypatch.setattr(
        "wood_secrets.core.resolver.write_secret_atomic",
        lambda *_: (_ for _ in ()).throw(SecretProviderError("write failed with secret")),
    )
    install_stub_resolver()

    code = main(["materialize", "openproject-token", "--apply", "--json"])

    assert code == 0
    assert target.read_text(encoding="utf-8") == "previous-valid-value"
    rendered = capsys.readouterr().out
    assert "write failed with secret" not in rendered
    assert "previous-valid-value" not in rendered


def test_materialize_status_reports_missing_without_writing(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize-status", "openproject-token", "--json"])

    assert code == 0
    assert not secrets_root.exists()
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["command"] == "materialize-status"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["materialized"][0]["state"] == "missing"
    assert payload["data"]["materialized"][0]["exists"] is False
    assert "super-secret-token" not in rendered


def test_materialize_status_reports_derived_identity_metadata(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "cloudflare-token": {
                    "ref": "vaultwarden://cloudflare/caddy/api-token#API_TOKEN",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize-status", "cloudflare-token", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    item = payload["data"]["materialized"][0]
    rendered = json.dumps(payload)
    assert item["target"] == str(secrets_root / "cloudflare" / "caddy" / "api-token")
    assert item["service"] == "cloudflare"
    assert item["principal"] == "caddy"
    assert item["credential"] == "api-token"
    assert item["state"] == "missing"
    assert "super-secret-token" not in rendered


def test_materialize_status_reports_current_and_refresh_needed(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    current = secrets_root / "current"
    stale = secrets_root / "stale"
    secrets_root.mkdir()
    current.write_text("super-secret-token", encoding="utf-8")
    stale.write_text("old-secret-token", encoding="utf-8")
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "current-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "current",
                },
                "stale-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "stale",
                },
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize-status", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    states = {item["name"]: item["state"] for item in payload["data"]["materialized"]}
    assert states == {
        "current-token": "current",
        "stale-token": "refresh-needed",
    }
    rendered = json.dumps(payload)
    assert "super-secret-token" not in rendered
    assert "old-secret-token" not in rendered


def test_materialize_status_reports_provider_error_without_leaking_existing_file(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    target = secrets_root / "openproject" / "api-token"
    target.parent.mkdir(parents=True)
    target.write_text("previous-valid-value", encoding="utf-8")
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    install_stub_resolver(provider=StubProvider(error=ProviderLockedError("locked secret value")))

    code = main(["materialize-status", "openproject-token", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["data"]["materialized"][0]["state"] == "provider-error"
    assert payload["data"]["materialized"][0]["error_type"] == "ProviderLockedError"
    assert payload["data"]["materialized"][0]["exists"] is True
    assert "locked secret value" not in rendered
    assert "previous-valid-value" not in rendered


def test_materialize_status_reports_invalid_unsafe_path_and_symlink_target(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    outside = tmp_path / "outside"
    outside.mkdir()
    secrets_root.mkdir()
    (secrets_root / "linked").symlink_to(outside)
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "bad-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "linked",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize-status", "bad-token", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["materialized"][0]["state"] == "invalid"
    assert payload["data"]["materialized"][0]["is_symlink"] is True


def test_materialize_status_reports_unsafe_permissions(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    target = secrets_root / "openproject" / "api-token"
    target.parent.mkdir(parents=True)
    target.write_text("super-secret-token", encoding="utf-8")
    target.chmod(0o644)
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    install_stub_resolver()

    code = main(["materialize-status", "openproject-token", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    item = payload["data"]["materialized"][0]
    assert item["state"] == "current"
    assert item["file_permissions_safe"] is False
    assert item["permissions_safe"] is False
    assert item["mode"] == "0o644"


def test_materialize_status_reports_present_unverified_when_file_cannot_be_read(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_root = tmp_path / "secrets"
    target = secrets_root / "openproject" / "api-token"
    target.parent.mkdir(parents=True)
    target.write_text("super-secret-token", encoding="utf-8")
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(
            secrets_root=secrets_root,
            definitions={
                "openproject-token": {
                    "ref": "vaultwarden://openproject/wood-tools/api-token",
                    "target": "openproject/api-token",
                }
            },
        ),
    )
    monkeypatch.setattr(
        "wood_secrets.core.materialization.Path.read_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    install_stub_resolver()

    code = main(["materialize-status", "openproject-token", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    assert payload["data"]["materialized"][0]["state"] == "present-unverified"
    assert "super-secret-token" not in rendered
    assert "denied" not in rendered


def test_add_requires_service_before_prompt(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "wood_secrets.cli.getpass",
        lambda _: (_ for _ in ()).throw(AssertionError("prompted")),
    )
    install_stub_resolver()

    with pytest.raises(SystemExit) as exc:
        main(["add", "--principal", "wood-events", "--credential", "password", "--json"])

    assert exc.value.code == 2
    rendered = capsys.readouterr().err
    assert "super-secret-token" not in rendered


def test_add_rejects_invalid_identity_before_prompt(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "wood_secrets.cli.getpass",
        lambda _: (_ for _ in ()).throw(AssertionError("prompted")),
    )
    install_stub_resolver()

    code = main(
        [
            "add",
            "--service",
            "postgres",
            "--principal",
            "../wood-events",
            "--credential",
            "password",
            "--json",
        ]
    )

    assert code == 2
    rendered = capsys.readouterr().out
    assert "super-secret-token" not in rendered


def test_add_prompt_preview_does_not_create_or_leak_secret(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider()
    prompts: list[str] = []
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(secrets_root=secrets_root, definitions={}),
    )
    monkeypatch.setattr(
        "wood_secrets.cli.getpass",
        lambda prompt: prompts.append(prompt) or "new-secret-token",
    )
    install_stub_resolver(provider=provider)

    code = main(
        [
            "add",
            "--service",
            "postgres",
            "--principal",
            "wood-events",
            "--credential",
            "password",
            "--json",
        ]
    )

    assert code == 0
    assert prompts == ["Enter secret value: ", "Confirm secret value: "]
    assert provider.exists_checks == [
        {"item_name": "postgres / wood-events / password", "search": "password"}
    ]
    assert provider.create_calls == []
    assert not (secrets_root / "postgres" / "wood-events" / "password").exists()
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    item = payload["data"]["secret"]
    assert payload["command"] == "add"
    assert payload["mutation"] == "read-only"
    assert item["canonical_identity"] == "postgres / wood-events / password"
    assert item["reference"] == "vaultwarden://postgres/wood-events/password#PASSWORD"
    assert item["target"] == str(secrets_root / "postgres" / "wood-events" / "password")
    assert item["target_source"] == "identity-derived"
    assert item["redacted_value"] == "[REDACTED]"
    assert "new-secret-token" not in rendered


def test_add_apply_creates_vaultwarden_item_without_leaking_secret(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider()
    secrets_root = tmp_path / "secrets"
    monkeypatch.setattr(
        "wood_secrets.core.materialization.load_config",
        lambda _: make_materialization_config(secrets_root=secrets_root, definitions={}),
    )
    monkeypatch.setattr("wood_secrets.cli.getpass", lambda _: "new-secret-token")
    install_stub_resolver(provider=provider)

    code = main(
        [
            "add",
            "--service",
            "openproject",
            "--principal",
            "wood-tools",
            "--credential",
            "api-token",
            "--apply",
            "--json",
        ]
    )

    assert code == 0
    assert provider.create_calls == [
        {
            "item_name": "openproject / wood-tools / api-token",
            "field_name": "API_TOKEN",
            "value_length": len("new-secret-token"),
            "metadata": {
                "wood.credential": "api-token",
                "wood.managed": "true",
                "wood.principal": "wood-tools",
                "wood.reference": "vaultwarden://openproject/wood-tools/api-token#API_TOKEN",
                "wood.service": "openproject",
            },
        }
    ]
    assert not (secrets_root / "openproject" / "wood-tools" / "api-token").exists()
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload)
    item = payload["data"]["secret"]
    assert payload["mutation"] == "mutating"
    assert item["outcome"] == "created"
    assert item["vaultwarden_item_id"] == "vaultwarden-item-id"
    assert "new-secret-token" not in rendered


def test_add_confirmation_mismatch_fails_without_mutation(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider()
    values = iter(["first-secret", "second-secret"])
    monkeypatch.setattr("wood_secrets.cli.getpass", lambda _: next(values))
    install_stub_resolver(provider=provider)

    code = main(
        [
            "add",
            "--service",
            "postgres",
            "--principal",
            "wood-events",
            "--credential",
            "password",
            "--apply",
            "--json",
        ]
    )

    assert code == 2
    assert provider.exists_checks == []
    assert provider.create_calls == []
    rendered = capsys.readouterr().out
    assert "first-secret" not in rendered
    assert "second-secret" not in rendered


def test_add_empty_secret_rejected_without_mutation(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = StubProvider()
    monkeypatch.setattr("wood_secrets.cli.getpass", lambda _: "")
    install_stub_resolver(provider=provider)

    code = main(
        [
            "add",
            "--service",
            "postgres",
            "--principal",
            "wood-events",
            "--credential",
            "password",
        ]
    )

    assert code == 2
    assert provider.exists_checks == []
    assert provider.create_calls == []


def test_add_stdin_apply_reads_secret_without_confirmation(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider()
    monkeypatch.setattr("wood_secrets.cli.sys.stdin.read", lambda: "stdin-secret\n")
    monkeypatch.setattr(
        "wood_secrets.cli.getpass",
        lambda _: (_ for _ in ()).throw(AssertionError("prompted")),
    )
    install_stub_resolver(provider=provider)

    code = main(
        [
            "add",
            "--service",
            "postgres",
            "--principal",
            "wood-events",
            "--credential",
            "password",
            "--stdin",
            "--apply",
            "--json",
        ]
    )

    assert code == 0
    assert provider.create_calls[0]["value_length"] == len("stdin-secret")
    rendered = capsys.readouterr().out
    assert "stdin-secret" not in rendered


def test_add_duplicate_identity_rejected_without_create(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider(canonical_item_exists=True)
    monkeypatch.setattr("wood_secrets.cli.getpass", lambda _: "new-secret-token")
    install_stub_resolver(provider=provider)

    code = main(
        [
            "add",
            "--service",
            "postgres",
            "--principal",
            "wood-events",
            "--credential",
            "password",
            "--apply",
            "--json",
        ]
    )

    assert code == 2
    assert provider.create_calls == []
    rendered = capsys.readouterr().out
    assert "already exists" in rendered
    assert "new-secret-token" not in rendered


def test_add_provider_error_is_redacted(
    install_stub_resolver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider(error=ProviderLockedError("locked with secret text"))
    monkeypatch.setattr("wood_secrets.cli.getpass", lambda _: "new-secret-token")
    install_stub_resolver(provider=provider)

    code = main(
        [
            "add",
            "--service",
            "postgres",
            "--principal",
            "wood-events",
            "--credential",
            "password",
            "--apply",
            "--json",
        ]
    )

    assert code == 2
    rendered = capsys.readouterr().out
    assert "new-secret-token" not in rendered
    assert "locked with secret text" not in rendered


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


def test_unlock_defaults_to_interactive_write_session_with_concise_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = StubProvider(value="super-secret-token")
    env_provider = EnvironmentSecretProvider(environ={})
    providers = {"env": env_provider, "vaultwarden": provider}
    monkeypatch.setattr(
        "wood_secrets.cli.SecretResolver",
        lambda: SecretResolver(providers=providers, environ={}),
    )

    code = main(["unlock"])

    assert code == 0
    assert provider.unlock_calls == [{"interactive": True, "gui": False, "write_session": True}]
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert "Unlocked vaultwarden" in out
    assert "super-secret-token" not in out


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
            "vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN",
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
            "OPENPROJECT_TOKEN=vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN",
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
            "OPENPROJECT_TOKEN=vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN",
            "SECOND_TOKEN=vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN",
            "--",
            "env",
        ]
    )

    assert code == 0
    assert calls[0][1]["OPENPROJECT_TOKEN"] == "super-secret-token"
    assert calls[0][1]["SECOND_TOKEN"] == "super-secret-token"
    assert "super-secret-token" not in capsys.readouterr().out


def test_resolver_resolves_configured_integration_reference_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "wood_secrets.core.resolver.load_config",
        lambda _: make_config_document(
            openproject_ref="vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN"
        ),
    )
    resolver = SecretResolver(
        providers={
            "env": EnvironmentSecretProvider(environ={}),
            "vaultwarden": StubProvider(value="super-secret-token"),
        },
        environ={},
    )

    resolved = resolver.resolve_configured("openproject")

    assert resolved.value == "super-secret-token"
    assert resolved.reference == (
        "vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN"
    )


def test_resolver_reports_configured_integration_status_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "wood_secrets.core.resolver.load_config",
        lambda _: make_config_document(
            openproject_ref="vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN"
        ),
    )
    resolver = SecretResolver(
        providers={
            "env": EnvironmentSecretProvider(environ={}),
            "vaultwarden": StubProvider(value="super-secret-token"),
        },
        environ={},
    )

    payload = resolver.configured_status("openproject")

    assert payload["ok"] is True
    assert payload["redacted_value"] == "[REDACTED]"
    assert "super-secret-token" not in json.dumps(payload)


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
        stdin_isatty=lambda: True,
    )
    monkeypatch.setattr("wood_secrets.core.vaultwarden.getpass", lambda _: "master-password")

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
        stdin_isatty=lambda: True,
    )
    monkeypatch.setattr("wood_secrets.core.vaultwarden.getpass", lambda _: "master-password")

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
        stdin_isatty=lambda: True,
    )
    monkeypatch.setattr("wood_secrets.core.vaultwarden.getpass", lambda _: "master-password")

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
        stdin_isatty=lambda: True,
    )
    monkeypatch.setattr("wood_secrets.core.vaultwarden.getpass", lambda _: "master-password")

    status = provider.unlock(interactive=True, write_session=False)

    assert status.unlocked is True
    assert environ[UNLOCK_PASSWORD_ENV] == "previous-value"


def test_vaultwarden_unlock_fails_closed_without_tty(tmp_path: Path) -> None:
    provider = VaultwardenSecretProvider(
        runner=lambda *_, **__: "",
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={},
        stdin_isatty=lambda: False,
    )

    with pytest.raises(ProviderLockedError, match="requires a local terminal TTY"):
        provider.unlock(interactive=True, write_session=True)


def test_vaultwarden_gui_prompt_empty_result_is_controlled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("wood_secrets.core.vaultwarden.subprocess.run", fake_run)

    with pytest.raises(SecretProviderError, match="timed out"):
        prompt_for_password_macos()


def test_vaultwarden_subprocess_uses_writable_appdata_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    appdata_dir = tmp_path / "bw-appdata"
    calls: list[dict[str, object]] = []

    def fake_run(
        command: list[str],
        *,
        input: str | None = None,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {
                "command": command,
                "input": input,
                "env": env,
                "check": check,
                "capture_output": capture_output,
                "text": text,
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr("wood_secrets.core.vaultwarden.subprocess.run", fake_run)
    provider = VaultwardenSecretProvider(
        executable="bw",
        appdata_dir=appdata_dir,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={},
    )

    assert provider._run_command(["status"]) == "{}\n"

    assert calls[0]["command"] == ["bw", "status"]
    assert calls[0]["env"][APPDATA_ENV] == str(appdata_dir)
    assert appdata_dir.is_dir()
    assert stat.S_IMODE(appdata_dir.stat().st_mode) == 0o700


def test_vaultwarden_appdata_bootstraps_existing_login_state(tmp_path: Path) -> None:
    source_dir = tmp_path / "source-appdata"
    source_dir.mkdir()
    (source_dir / "data.json").write_text('{"authenticatedAccounts":{}}\n', encoding="utf-8")
    appdata_dir = tmp_path / "runtime-appdata"

    provider = VaultwardenSecretProvider(
        executable="bw",
        appdata_dir=appdata_dir,
        appdata_source_dir=source_dir,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={},
    )

    provider._ensure_appdata_dir()

    copied_data = appdata_dir / "data.json"
    assert copied_data.read_text(encoding="utf-8") == '{"authenticatedAccounts":{}}\n'
    assert stat.S_IMODE(copied_data.stat().st_mode) == 0o600


def test_vaultwarden_appdata_repairs_empty_runtime_state(tmp_path: Path) -> None:
    source_dir = tmp_path / "source-appdata"
    source_dir.mkdir()
    source_payload = '{"global_account_activeAccountId":"account-id"}\n'
    (source_dir / "data.json").write_text(source_payload, encoding="utf-8")
    appdata_dir = tmp_path / "runtime-appdata"
    appdata_dir.mkdir()
    (appdata_dir / "data.json").write_text("{}\n", encoding="utf-8")

    provider = VaultwardenSecretProvider(
        executable="bw",
        appdata_dir=appdata_dir,
        appdata_source_dir=source_dir,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={},
    )

    provider._ensure_appdata_dir()

    assert (appdata_dir / "data.json").read_text(encoding="utf-8") == source_payload


def test_vaultwarden_resolve_uses_protected_session_file_from_new_process(
    tmp_path: Path,
) -> None:
    session_file = tmp_path / "runtime" / "session.json"
    VaultwardenSessionStore(path=session_file).write_token("session-token")

    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "locked"})
        if args == ["list", "items", "--search", "__wood_tools_session_probe__"]:
            assert session_token == "session-token"
            return "[]"
        if args == ["list", "items", "--search", "api-token"]:
            assert session_token == "session-token"
            return json.dumps(
                [
                    {
                        "name": "openproject / wood-tools / api-token",
                        "fields": [
                            {"name": "OPENPROJECT_API_TOKEN", "value": "secret-token"},
                        ],
                        "login": {"password": ""},
                    }
                ]
            )
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=session_file),
        environ={},
    )

    assert (
        provider.resolve("vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN")
        == "secret-token"
    )


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
        provider.resolve("vaultwarden://openproject/wood-tools/api-token")


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
        provider.resolve("vaultwarden://openproject/wood-tools/api-token")


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
                        "name": "openproject / wood-tools / api-token",
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
        "vaultwarden://openproject/wood-tools/api-token#OPENPROJECT_API_TOKEN"
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
                        "name": "openproject / wood-tools",
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
            "name": "openproject / wood-tools",
            "field_names": ["api-token", "username"],
            "has_login_password": True,
        }
    ]


def test_vaultwarden_canonical_item_exists_checks_exact_identity(tmp_path: Path) -> None:
    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args == ["list", "items", "--search", "__wood_tools_session_probe__"]:
            return "[]"
        if args == ["list", "items", "--search", "password"]:
            assert session_token == "active-session"
            return json.dumps(
                [
                    {"name": "postgres / other / password"},
                    {"name": "postgres / wood-events / password"},
                ]
            )
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={"BW_SESSION": "active-session"},
    )

    assert provider.canonical_item_exists(
        item_name="postgres / wood-events / password",
        search="password",
    )


def test_vaultwarden_create_canonical_secret_uses_redactable_item_payload(tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None, str | None]] = []

    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        calls.append((args, input_text, session_token))
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args == ["list", "items", "--search", "__wood_tools_session_probe__"]:
            return "[]"
        if args == ["get", "template", "item"]:
            return json.dumps({"type": 1, "name": "", "notes": "", "fields": []})
        if args == ["encode"]:
            assert input_text is not None
            item = json.loads(input_text)
            assert item["name"] == "postgres / wood-events / password"
            assert item["fields"] == [
                {"name": "PASSWORD", "value": "new-secret-token", "type": 1}
            ]
            assert "wood.service: postgres" in item["notes"]
            return "encoded-item"
        if args == ["create", "item", "encoded-item"]:
            return json.dumps({"id": "created-id", "name": "postgres / wood-events / password"})
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={"BW_SESSION": "active-session"},
    )

    created = provider.create_canonical_secret(
        item_name="postgres / wood-events / password",
        field_name="PASSWORD",
        value="new-secret-token",
        metadata={
            "wood.credential": "password",
            "wood.managed": "true",
            "wood.principal": "wood-events",
            "wood.reference": "vaultwarden://postgres/wood-events/password#PASSWORD",
            "wood.service": "postgres",
        },
    )

    assert created.item_id == "created-id"
    assert calls[-1] == (["create", "item", "encoded-item"], None, "active-session")


def test_vaultwarden_create_canonical_secret_reports_malformed_response_safely(
    tmp_path: Path,
) -> None:
    def runner(
        args: list[str], *, input_text: str | None = None, session_token: str | None = None
    ) -> str:
        if args == ["status"]:
            return json.dumps({"status": "unlocked"})
        if args == ["list", "items", "--search", "__wood_tools_session_probe__"]:
            return "[]"
        if args == ["get", "template", "item"]:
            return json.dumps({"type": 1, "name": "", "fields": []})
        if args == ["encode"]:
            return "encoded-secret"
        if args == ["create", "item", "encoded-secret"]:
            return json.dumps({"name": "postgres / wood-events / password"})
        raise AssertionError(f"Unexpected command: {args}")

    provider = VaultwardenSecretProvider(
        runner=runner,
        which=lambda _: "/usr/bin/bw",
        session_store=VaultwardenSessionStore(path=tmp_path / "runtime" / "session.json"),
        environ={"BW_SESSION": "active-session"},
    )

    with pytest.raises(SecretProviderError, match="Vaultwarden item creation failed") as exc:
        provider.create_canonical_secret(
            item_name="postgres / wood-events / password",
            field_name="PASSWORD",
            value="new-secret-token",
            metadata={"wood.service": "postgres"},
        )

    assert "new-secret-token" not in str(exc.value)

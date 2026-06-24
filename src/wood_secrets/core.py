from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wood_config.core import ConfigError, build_paths, load_config

from .providers import (
    EnvironmentSecretProvider,
    InvalidSecretReferenceError,
    MissingSecretError,
    ProviderLockedError,
    ProviderStatus,
    ProviderUnavailableError,
    SecretProvider,
    SecretProviderError,
    normalize_env_fallback_name,
    parse_reference_scheme,
    parse_vaultwarden_reference,
)
from .vaultwarden import VaultwardenSecretProvider, VaultwardenSessionStore


@dataclass(frozen=True)
class ResolvedSecret:
    reference: str
    provider: str
    value: str
    from_env_fallback: bool = False

    @property
    def redacted_value(self) -> str:
        return "[REDACTED]"

    def to_dict(self, *, include_value: bool = False) -> dict[str, Any]:
        payload = {
            "reference": self.reference,
            "provider": self.provider,
            "from_env_fallback": self.from_env_fallback,
            "redacted_value": self.redacted_value,
        }
        if include_value:
            payload["value"] = self.value
        return payload


def build_default_registry(
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, SecretProvider]:
    env = environ if environ is not None else os.environ
    executable = "bw"
    session_file: str | None = None

    try:
        config = load_config(build_paths())
        active_profile = str(config["active_profile"])
        profile = config["profiles"].get(active_profile, {})
        integrations = profile.get("integrations", {}) if isinstance(profile, dict) else {}
        vaultwarden = integrations.get("vaultwarden", {}) if isinstance(integrations, dict) else {}
        if isinstance(vaultwarden, dict):
            cli = vaultwarden.get("cli", {})
            if isinstance(cli, dict):
                executable_value = cli.get("executable")
                if isinstance(executable_value, str) and executable_value.strip():
                    executable = executable_value
            session_value = vaultwarden.get("session_file")
            if isinstance(session_value, str) and session_value.strip():
                session_file = session_value
    except (ConfigError, OSError, KeyError, TypeError, ValueError):
        pass

    return {
        "env": EnvironmentSecretProvider(environ=env),
        "vaultwarden": VaultwardenSecretProvider(
            executable=executable,
            environ=env,
            session_store=VaultwardenSessionStore(
                path=None if session_file is None else Path(os.path.expanduser(session_file))
            ),
        ),
    }


class SecretResolver:
    def __init__(
        self,
        providers: dict[str, SecretProvider] | None = None,
        *,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._providers = (
            providers if providers is not None else build_default_registry(environ=self._environ)
        )

    def provider_statuses(self) -> list[ProviderStatus]:
        return [provider.status() for provider in self._providers.values()]

    def provider_names(self) -> list[str]:
        return sorted(self._providers)

    def get_provider(self, scheme: str) -> SecretProvider:
        provider = self._providers.get(scheme)
        if provider is None:
            raise InvalidSecretReferenceError(f"No provider is registered for scheme '{scheme}'.")
        return provider

    def validate_reference(self, reference: str) -> dict[str, Any]:
        scheme = parse_reference_scheme(reference)
        provider = self.get_provider(scheme)
        provider.status()
        if scheme == "vaultwarden":
            parse_vaultwarden_reference(reference)
        return {
            "reference": reference,
            "scheme": scheme,
            "provider": provider.name,
            "env_fallback_variable": normalize_env_fallback_name(reference),
        }

    def resolve(self, reference: str) -> ResolvedSecret:
        scheme = parse_reference_scheme(reference)
        provider = self.get_provider(scheme)

        try:
            value = provider.resolve(reference)
            return ResolvedSecret(reference=reference, provider=provider.name, value=value)
        except (ProviderUnavailableError, ProviderLockedError, MissingSecretError) as exc:
            fallback_name = normalize_env_fallback_name(reference)
            fallback_value = self._environ.get(fallback_name)
            if fallback_value:
                return ResolvedSecret(
                    reference=reference,
                    provider=provider.name,
                    value=fallback_value,
                    from_env_fallback=True,
                )
            raise exc

    def check(self, reference: str | None = None) -> dict[str, Any]:
        if reference is None:
            statuses = [status.to_dict() for status in self.provider_statuses()]
            ready = all(status["available"] for status in statuses)
            return {
                "ok": ready,
                "providers": statuses,
            }

        validated = self.validate_reference(reference)
        provider_status = self.get_provider(validated["scheme"]).status().to_dict()
        validated["provider_status"] = provider_status
        validated["ok"] = provider_status["available"]
        return validated

    def status(self, provider_name: str) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        status = provider.status().to_dict()
        return {
            "ok": status["available"] and status["unlocked"],
            "provider": provider_name,
            "status": status,
        }

    def unlock(
        self,
        provider_name: str,
        *,
        interactive: bool = False,
        gui: bool = False,
        write_session: bool = False,
    ) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        status = provider.unlock(
            interactive=interactive,
            gui=gui,
            write_session=write_session,
        ).to_dict()
        return {
            "ok": status["available"] and status["unlocked"],
            "provider": provider_name,
            "status": status,
            "session": provider.session_status(),
            "write_session": write_session,
        }

    def lock(self, provider_name: str) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        status = provider.lock().to_dict()
        return {
            "ok": status["available"] and not status["unlocked"],
            "provider": provider_name,
            "status": status,
            "session": provider.session_status(),
        }

    def session(self, provider_name: str) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        return {
            "provider": provider_name,
            "session": provider.session_status(),
        }

    def doctor(self) -> dict[str, Any]:
        statuses = [status.to_dict() for status in self.provider_statuses()]
        issues: list[dict[str, str]] = []
        for status in statuses:
            if not status["available"]:
                issues.append(
                    {
                        "code": "provider_unavailable",
                        "provider": status["name"],
                        "message": status["detail"],
                    }
                )
            elif not status["unlocked"]:
                issues.append(
                    {
                        "code": "provider_locked",
                        "provider": status["name"],
                        "message": status["detail"],
                    }
                )
        return {
            "status": "ok" if not issues else "warning",
            "providers": statuses,
            "issues": issues,
        }


__all__ = [
    "ResolvedSecret",
    "SecretProviderError",
    "SecretResolver",
    "build_default_registry",
]

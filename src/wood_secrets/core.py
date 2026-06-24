from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .providers import (
    EnvironmentSecretProvider,
    InvalidSecretReferenceError,
    MissingSecretError,
    ProviderLockedError,
    ProviderStatus,
    ProviderUnavailableError,
    SecretProvider,
    SecretProviderError,
    VaultwardenSecretProvider,
    normalize_env_fallback_name,
    parse_reference_scheme,
    parse_vaultwarden_reference,
)


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
    return {
        "env": EnvironmentSecretProvider(environ=env),
        "vaultwarden": VaultwardenSecretProvider(),
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

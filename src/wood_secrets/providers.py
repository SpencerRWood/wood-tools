from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

ENV_FALLBACK_PREFIX = "WOOD_SECRETS_REF_"


class SecretProviderError(RuntimeError):
    """Base error for provider failures."""


class InvalidSecretReferenceError(SecretProviderError):
    """Raised when a secret reference does not match provider syntax."""


class MissingSecretError(SecretProviderError):
    """Raised when a secret value cannot be found."""


class ProviderUnavailableError(SecretProviderError):
    """Raised when a provider cannot be used in the current environment."""


class ProviderLockedError(SecretProviderError):
    """Raised when a provider requires an unlock step."""


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    scheme: str
    available: bool
    unlocked: bool
    configured: bool
    state: str
    detail: str
    supports_env_fallback: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VaultwardenReference:
    raw: str
    parts: tuple[str, ...]

    @property
    def search_term(self) -> str:
        return self.parts[-1]

    @property
    def field_hint(self) -> str:
        return self.parts[-1]


def normalize_env_fallback_name(reference: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", reference).strip("_").upper()
    return f"{ENV_FALLBACK_PREFIX}{normalized}"


def parse_reference_scheme(reference: str) -> str:
    scheme, separator, _ = reference.partition("://")
    if not separator or not scheme:
        raise InvalidSecretReferenceError("Secret reference must use '<scheme>://<value>' syntax.")
    return scheme.lower()


def parse_vaultwarden_reference(reference: str) -> VaultwardenReference:
    if parse_reference_scheme(reference) != "vaultwarden":
        raise InvalidSecretReferenceError(f"Unsupported secret reference: {reference}")

    path = reference[len("vaultwarden://") :].strip("/")
    parts = tuple(part.strip() for part in path.split("/") if part.strip())
    if len(parts) < 2:
        raise InvalidSecretReferenceError(
            "Vaultwarden references must include at least two path segments."
        )
    return VaultwardenReference(raw=reference, parts=parts)


def candidate_item_names(reference: VaultwardenReference) -> list[str]:
    slash_spaced = " / ".join(reference.parts)
    slash = "/".join(reference.parts)
    last = reference.parts[-1]
    names = [slash_spaced, slash, last]
    if len(reference.parts) > 2:
        names.extend((" / ".join(reference.parts[:-1]), "/".join(reference.parts[:-1])))

    deduped: list[str] = []
    for name in names:
        if name and name not in deduped:
            deduped.append(name)
    return deduped


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def extract_custom_field(item: dict[str, Any], candidates: list[str]) -> str | None:
    fields = item.get("fields") or []
    candidate_names = {normalize_token(name) for name in candidates if name}

    for field in fields:
        name = str(field.get("name") or "")
        if normalize_token(name) in candidate_names:
            value = field.get("value")
            if isinstance(value, str) and value:
                return value
    return None


def choose_vaultwarden_item(
    items: list[dict[str, Any]], reference: VaultwardenReference
) -> dict[str, Any]:
    for expected_name in candidate_item_names(reference):
        matches = [item for item in items if str(item.get("name") or "").strip() == expected_name]
        if len(matches) == 1:
            return matches[0]

    if len(items) == 1:
        return items[0]

    item_names = ", ".join(str(item.get("name") or "<unnamed>") for item in items[:10])
    raise MissingSecretError(
        "Ambiguous or missing vault item for reference "
        f"{reference.raw}. Search returned: {item_names or 'no items'}."
    )


class SecretProvider(ABC):
    scheme: str
    name: str

    @abstractmethod
    def status(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def unlock(
        self,
        *,
        interactive: bool = False,
        gui: bool = False,
        write_session: bool = False,
    ) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def lock(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def session_status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def resolve(self, reference: str) -> str:
        raise NotImplementedError


class EnvironmentSecretProvider(SecretProvider):
    scheme = "env"
    name = "environment"

    def __init__(self, *, environ: dict[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            scheme=self.scheme,
            available=True,
            unlocked=True,
            configured=True,
            state="ready",
            detail="Reads secret values directly from process environment variables.",
        )

    def unlock(
        self,
        *,
        interactive: bool = False,
        gui: bool = False,
        write_session: bool = False,
    ) -> ProviderStatus:
        return self.status()

    def lock(self) -> ProviderStatus:
        return self.status()

    def session_status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "path": None,
            "exists": False,
            "protected": False,
            "source": "environment",
            "usable": True,
            "state": "not-applicable",
            "detail": "Environment provider does not use a runtime session file.",
        }

    def resolve(self, reference: str) -> str:
        if parse_reference_scheme(reference) != self.scheme:
            raise InvalidSecretReferenceError(f"Unsupported secret reference: {reference}")

        variable = reference[len("env://") :].strip()
        if not variable:
            raise InvalidSecretReferenceError(
                "Environment references must include a variable name."
            )

        value = self._environ.get(variable)
        if not value:
            raise MissingSecretError(f"Environment variable {variable} is not set.")
        return value

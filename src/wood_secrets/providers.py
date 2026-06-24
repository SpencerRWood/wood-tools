from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
    def unlock(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def lock(self) -> ProviderStatus:
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

    def unlock(self) -> ProviderStatus:
        return self.status()

    def lock(self) -> ProviderStatus:
        return self.status()

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


class VaultwardenSecretProvider(SecretProvider):
    scheme = "vaultwarden"
    name = "vaultwarden"

    def __init__(
        self,
        *,
        executable: str = "bw",
        runner: Any | None = None,
        which: Any | None = None,
    ) -> None:
        self.executable = executable
        self._runner = runner or self._run_command
        self._which = which or shutil.which

    def _run_command(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                [self.executable, *args],
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

    def _run_json(self, args: list[str]) -> Any:
        output = self._runner(args)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise SecretProviderError(
                f"Vaultwarden CLI returned invalid JSON for: {self.executable} {' '.join(args)}"
            ) from exc

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
        unlocked = cli_status == "unlocked"
        if cli_status == "unlocked":
            state = "ready"
            detail = "Vaultwarden CLI is unlocked and can resolve references."
        elif cli_status in {"locked", "unauthenticated"}:
            state = "locked"
            detail = f"Vaultwarden CLI reported status '{cli_status}'."
        else:
            state = "degraded"
            detail = f"Vaultwarden CLI reported unexpected status '{cli_status}'."

        return ProviderStatus(
            name=self.name,
            scheme=self.scheme,
            available=True,
            unlocked=unlocked,
            configured=True,
            state=state,
            detail=detail,
        )

    def unlock(self) -> ProviderStatus:
        if self._which(self.executable) is None:
            raise ProviderUnavailableError(
                f"Vaultwarden CLI '{self.executable}' was not found on PATH."
            )
        self._runner(["unlock", "--check"])
        return self.status()

    def lock(self) -> ProviderStatus:
        if self._which(self.executable) is None:
            raise ProviderUnavailableError(
                f"Vaultwarden CLI '{self.executable}' was not found on PATH."
            )
        self._runner(["lock"])
        return self.status()

    def resolve(self, reference: str) -> str:
        status = self.status()
        if not status.available:
            raise ProviderUnavailableError(status.detail)
        if not status.unlocked:
            raise ProviderLockedError(
                "Vaultwarden is not unlocked. Unlock it before resolving secrets."
            )

        ref = parse_vaultwarden_reference(reference)
        items = self._run_json(["list", "items", "--search", ref.search_term])
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

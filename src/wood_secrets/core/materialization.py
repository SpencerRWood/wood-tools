from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from wood_config.core import ConfigError, build_paths, load_config

from .providers import SecretProviderError, parse_reference_scheme, parse_vaultwarden_reference


class MaterializationError(SecretProviderError):
    """Raised when materialized secret configuration or writes are unsafe."""


@dataclass(frozen=True)
class MaterializedSecretDefinition:
    name: str
    reference: str
    target: str | None


@dataclass(frozen=True)
class MaterializedSecretIdentity:
    service: str
    principal: str
    credential: str

    def to_relative_target(self) -> str:
        return f"{self.service}/{self.principal}/{self.credential}"

    @property
    def canonical_identity(self) -> str:
        return f"{self.service} / {self.principal} / {self.credential}"

    @property
    def field_name(self) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", self.credential).strip("_").upper()

    @property
    def reference(self) -> str:
        return (
            f"vaultwarden://{self.service}/{self.principal}/"
            f"{self.credential}#{self.field_name}"
        )


def active_profile_values() -> dict[str, Any]:
    try:
        config = load_config(build_paths())
        active_profile = str(config["active_profile"])
        values = config["profiles"].get(active_profile, {})
    except (ConfigError, OSError, KeyError, TypeError, ValueError) as exc:
        raise MaterializationError(f"Unable to load wood-config: {exc}") from exc
    if not isinstance(values, dict):
        raise MaterializationError("Active wood-config profile is not an object.")
    return values


def configured_secrets_root(values: dict[str, Any]) -> Path:
    paths = values.get("paths")
    paths = paths if isinstance(paths, dict) else {}
    configured = paths.get("secrets_root")
    root = configured if isinstance(configured, str) and configured.strip() else "~/.wood/secrets"
    return Path(os.path.expanduser(root))


def materialized_secret_definitions(values: dict[str, Any]) -> list[MaterializedSecretDefinition]:
    integrations = values.get("integrations")
    integrations = integrations if isinstance(integrations, dict) else {}
    vaultwarden = integrations.get("vaultwarden")
    vaultwarden = vaultwarden if isinstance(vaultwarden, dict) else {}
    definitions = vaultwarden.get("materialized_secrets")
    if definitions is None:
        definitions = {}
    if not isinstance(definitions, dict):
        raise MaterializationError(
            "integrations.vaultwarden.materialized_secrets must be an object."
        )

    materializations: list[MaterializedSecretDefinition] = []
    for name, definition in sorted(definitions.items()):
        if not isinstance(name, str) or not name.strip():
            raise MaterializationError("Materialized secret names must be non-empty strings.")
        if not isinstance(definition, dict):
            raise MaterializationError(f"Materialized secret {name!r} must be an object.")
        reference = definition.get("ref")
        target = definition.get("target")
        if not isinstance(reference, str) or not reference.strip():
            raise MaterializationError(f"Materialized secret {name!r} must include ref.")
        if target is not None and (not isinstance(target, str) or not target.strip()):
            raise MaterializationError(
                f"Materialized secret {name!r} target must be a non-empty string when provided."
            )
        if parse_reference_scheme(reference) == "vaultwarden":
            parse_vaultwarden_reference(reference)
        materializations.append(
            MaterializedSecretDefinition(
                name=name.strip(),
                reference=reference.strip(),
                target=target.strip() if isinstance(target, str) else None,
            )
        )
    return materializations


def selected_definitions(
    definitions: list[MaterializedSecretDefinition],
    name: str | None,
) -> list[MaterializedSecretDefinition]:
    if name is None:
        return definitions
    selected = [definition for definition in definitions if definition.name == name]
    if not selected:
        known = ", ".join(definition.name for definition in definitions) or "none configured"
        raise MaterializationError(f"Unknown materialized secret {name!r}. Known: {known}.")
    return selected


def _validate_identity_component(value: str) -> str:
    component = value.strip()
    if not component or component in {".", ".."}:
        raise MaterializationError(
            "Vaultwarden materialization identity must include service, principal, and credential."
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", component):
        raise MaterializationError(
            "Vaultwarden materialization identity components must contain only "
            "letters, numbers, '.', '_', and '-'."
        )
    if "/" in component or "\\" in component:
        raise MaterializationError(
            "Vaultwarden materialization identity components cannot contain path separators."
        )
    if Path(component).is_absolute():
        raise MaterializationError(
            "Vaultwarden materialization identity components cannot be absolute paths."
        )
    return component


def vaultwarden_materialization_identity(reference: str) -> MaterializedSecretIdentity:
    if parse_reference_scheme(reference) != "vaultwarden":
        raise MaterializationError(
            "Materialized secrets without explicit targets require vaultwarden:// references."
        )

    raw_path = reference[len("vaultwarden://") :].partition("#")[0]
    if raw_path != raw_path.strip("/"):
        raise MaterializationError(
            "Vaultwarden materialization identity cannot start or end with '/'."
        )
    raw_parts = raw_path.split("/")
    if len(raw_parts) != 3:
        raise MaterializationError(
            "Vaultwarden materialization identity must be service/principal/credential."
        )
    service, principal, credential = (
        _validate_identity_component(part) for part in raw_parts
    )
    return MaterializedSecretIdentity(
        service=service,
        principal=principal,
        credential=credential,
    )


def materialized_secret_identity(
    *,
    service: str,
    principal: str,
    credential: str,
) -> MaterializedSecretIdentity:
    return MaterializedSecretIdentity(
        service=_validate_identity_component(service),
        principal=_validate_identity_component(principal),
        credential=_validate_identity_component(credential),
    )


def materialized_secret_target(
    definition: MaterializedSecretDefinition,
) -> tuple[str, MaterializedSecretIdentity | None]:
    if definition.target is not None:
        return definition.target, None
    identity = vaultwarden_materialization_identity(definition.reference)
    return identity.to_relative_target(), identity


def safe_target_path(root: Path, target: str) -> Path:
    target_path = Path(target)
    if target_path.is_absolute():
        raise MaterializationError("Materialized secret targets must be relative paths.")
    if any(part in {"", ".", ".."} for part in target_path.parts):
        raise MaterializationError("Materialized secret targets cannot contain traversal segments.")

    root_resolved = root.expanduser().resolve(strict=False)
    candidate = root_resolved / target_path
    current = root_resolved
    for part in target_path.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise MaterializationError("Materialized secret target parent cannot be a symlink.")
    if candidate.exists() and candidate.is_symlink():
        raise MaterializationError("Materialized secret target cannot be a symlink.")

    resolved_parent = candidate.parent.resolve(strict=False)
    if root_resolved != resolved_parent and root_resolved not in resolved_parent.parents:
        raise MaterializationError(
            "Materialized secret target escapes the configured secrets root."
        )
    return candidate


def file_mode(path: Path) -> str | None:
    try:
        return oct(stat.S_IMODE(path.stat().st_mode))
    except OSError:
        return None


def owner_only_permissions(path: Path, *, directory: bool) -> bool | None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    allowed = 0o700 if directory else 0o600
    return mode & ~allowed == 0


def path_metadata(root: Path, path: Path) -> dict[str, Any]:
    root_expanded = root.expanduser()
    target_exists = path.exists()
    parent_exists = path.parent.exists()
    file_safe = owner_only_permissions(path, directory=False) if target_exists else None
    parent_safe = owner_only_permissions(path.parent, directory=True) if parent_exists else None
    root_safe = (
        owner_only_permissions(root_expanded, directory=True)
        if root_expanded.exists()
        else None
    )
    permission_safe = all(
        item is not False for item in (root_safe, parent_safe, file_safe)
    )
    return {
        "exists": target_exists,
        "parent_exists": parent_exists,
        "is_file": path.is_file() if target_exists else False,
        "is_symlink": path.is_symlink(),
        "mode": file_mode(path) if target_exists else None,
        "parent_mode": file_mode(path.parent) if parent_exists else None,
        "root_mode": file_mode(root_expanded) if root_expanded.exists() else None,
        "permissions_safe": permission_safe,
        "root_permissions_safe": root_safe,
        "parent_permissions_safe": parent_safe,
        "file_permissions_safe": file_safe,
    }


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def current_state(path: Path, value: str) -> str:
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "invalid-target"
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        return "unreadable"
    return "current" if current == value else "refresh-needed"


def status_state(path: Path, value: str) -> str:
    if not path.exists():
        return "missing"
    if path.is_symlink() or not path.is_file():
        return "invalid"
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        return "present-unverified"
    return "current" if current == value else "refresh-needed"


def write_secret_atomic(path: Path, value: str) -> None:
    ensure_private_directory(path.parent)
    temp_name: str | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
            temp_name = tmp.name
            os.chmod(temp_name, 0o600)
            tmp.write(value)
            tmp.flush()
            os.fsync(tmp.fileno())
        Path(temp_name).replace(path)
        os.chmod(path, 0o600)
    except OSError as exc:
        if temp_name is not None:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise MaterializationError("Unable to write materialized secret atomically.") from exc


def materialization_result(
    *,
    name: str,
    reference: str,
    path: Path,
    state: str,
    apply: bool,
    changed: bool,
    identity: MaterializedSecretIdentity | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "reference": reference,
        "target": str(path),
        "state": state,
        "changed": changed,
        "applied": apply,
        "redacted_value": "[REDACTED]",
    }
    if identity is not None:
        payload.update(
            {
                "service": identity.service,
                "principal": identity.principal,
                "credential": identity.credential,
            }
        )
    return payload


def materialization_status_result(
    *,
    name: str,
    reference: str,
    path: Path,
    root: Path,
    state: str,
    provider: str | None,
    from_env_fallback: bool,
    error_type: str | None = None,
    identity: MaterializedSecretIdentity | None = None,
) -> dict[str, Any]:
    metadata = path_metadata(root, path)
    payload = {
        "name": name,
        "reference": reference,
        "target": str(path),
        "state": state,
        "provider": provider,
        "from_env_fallback": from_env_fallback,
        "error_type": error_type,
        "redacted_value": "[REDACTED]",
        **metadata,
    }
    if identity is not None:
        payload.update(
            {
                "service": identity.service,
                "principal": identity.principal,
                "credential": identity.credential,
            }
        )
    return payload

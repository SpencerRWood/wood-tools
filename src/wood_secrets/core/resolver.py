from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wood_config.core import ConfigError, build_paths, load_config

from .materialization import (
    MaterializationError,
    active_profile_values,
    configured_secrets_root,
    current_state,
    ensure_private_directory,
    materialization_result,
    materialization_status_result,
    materialized_secret_definitions,
    materialized_secret_identity,
    materialized_secret_target,
    safe_target_path,
    selected_definitions,
    status_state,
    write_secret_atomic,
)
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


@dataclass(frozen=True)
class IntegrationSecretTarget:
    name: str
    field: str
    reference: str | None


def build_default_registry(
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, SecretProvider]:
    env = environ if environ is not None else os.environ
    executable = "bw"
    appdata_dir: str | None = None
    session_file: str | None = None
    server_url: str | None = None

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
                appdata_value = cli.get("appdata_dir")
                if isinstance(appdata_value, str) and appdata_value.strip():
                    appdata_dir = appdata_value
            url_value = vaultwarden.get("url")
            if isinstance(url_value, str) and url_value.strip():
                server_url = url_value.strip()
            session_value = vaultwarden.get("session_file")
            if isinstance(session_value, str) and session_value.strip():
                session_file = session_value
    except (ConfigError, OSError, KeyError, TypeError, ValueError):
        pass

    return {
        "env": EnvironmentSecretProvider(environ=env),
        "vaultwarden": VaultwardenSecretProvider(
            executable=executable,
            server_url=server_url,
            appdata_dir=None if appdata_dir is None else Path(os.path.expanduser(appdata_dir)),
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
        command_runner: Any | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._command_runner = command_runner or self._run_command
        self._providers = (
            providers if providers is not None else build_default_registry(environ=self._environ)
        )
        self._integration_targets, self._config_error = self._load_integration_targets()

    @staticmethod
    def _run_command(command: list[str], *, env: dict[str, str]) -> int:
        try:
            proc = subprocess.run(command, env=env, check=False)
        except FileNotFoundError as exc:
            raise SecretProviderError(f"Command not found: {command[0]}") from exc
        return proc.returncode

    @staticmethod
    def _openproject_registry(integration: dict[str, Any], active_profile: str) -> dict[str, Any]:
        projects = integration.get("projects")
        if isinstance(projects, dict) and projects:
            return integration

        registry_path = integration.get("registry_path")
        if not isinstance(registry_path, str) or not registry_path.strip():
            return integration

        registry_file = Path(registry_path).expanduser()
        if not registry_file.is_absolute():
            registry_file = Path.cwd() / registry_file
        registry = load_config(build_paths(registry_file))
        profiles = registry.get("profiles", {})
        profile = profiles.get(active_profile, {}) if isinstance(profiles, dict) else {}
        integrations = profile.get("integrations", {}) if isinstance(profile, dict) else {}
        openproject = integrations.get("openproject", {}) if isinstance(integrations, dict) else {}
        return openproject if isinstance(openproject, dict) else integration

    def _load_integration_targets(self) -> tuple[list[IntegrationSecretTarget], str | None]:
        targets = [
            IntegrationSecretTarget(
                name="openproject",
                field='integrations.openproject.projects["."].token_ref',
                reference=None,
            ),
            IntegrationSecretTarget(
                name="ntfy",
                field="integrations.ntfy.token_ref",
                reference=None,
            ),
        ]
        try:
            config = load_config(build_paths())
            active_profile = str(config["active_profile"])
            profile = config["profiles"].get(active_profile, {})
            integrations = profile.get("integrations", {}) if isinstance(profile, dict) else {}
            if not isinstance(integrations, dict):
                return targets, "Active profile integrations are not configured."

            resolved_targets: list[IntegrationSecretTarget] = []
            for target in targets:
                integration = integrations.get(target.name, {})
                if target.name == "openproject" and isinstance(integration, dict):
                    integration = self._openproject_registry(integration, active_profile)
                    projects = integration.get("projects")
                    if isinstance(projects, dict) and projects:
                        for project_path, metadata in sorted(projects.items()):
                            reference = None
                            if isinstance(metadata, dict):
                                candidate = metadata.get("token_ref")
                                if isinstance(candidate, str) and candidate.strip():
                                    reference = candidate.strip()
                            integration_name = (
                                "openproject"
                                if project_path == "."
                                else f"openproject:{project_path}"
                            )
                            resolved_targets.append(
                                IntegrationSecretTarget(
                                    name=integration_name,
                                    field=(
                                        "integrations.openproject.projects."
                                        f"{project_path}.token_ref"
                                    ),
                                    reference=reference,
                                )
                            )
                        continue
                reference = None
                if isinstance(integration, dict):
                    candidate = integration.get("token_ref")
                    if isinstance(candidate, str) and candidate.strip():
                        reference = candidate.strip()
                resolved_targets.append(
                    IntegrationSecretTarget(
                        name=target.name,
                        field=target.field,
                        reference=reference,
                    )
                )
            return resolved_targets, None
        except (ConfigError, OSError, KeyError, TypeError, ValueError) as exc:
            return targets, str(exc)

    def provider_statuses(self) -> list[ProviderStatus]:
        return [provider.status() for provider in self._providers.values()]

    def provider_names(self) -> list[str]:
        return sorted(self._providers)

    def configured_reference(self, integration_name: str) -> str:
        for target in self._integration_targets:
            if target.name == integration_name:
                if target.reference is None:
                    raise MissingSecretError(
                        f"Secret reference is not configured for integration '{integration_name}'."
                    )
                return target.reference
        known = ", ".join(sorted(target.name for target in self._integration_targets))
        raise InvalidSecretReferenceError(
            f"Unknown integration '{integration_name}'. Known integrations: {known}."
        )

    def resolve_configured(self, integration_name: str) -> ResolvedSecret:
        return self.resolve(self.configured_reference(integration_name))

    def configured_status(self, integration_name: str) -> dict[str, Any]:
        reference = self.configured_reference(integration_name)
        return self.inspect_reference(reference)

    def providers(self) -> dict[str, Any]:
        statuses = [status.to_dict() for status in self.provider_statuses()]
        return {
            "ok": all(status["available"] for status in statuses),
            "providers": statuses,
        }

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

    def inspect_reference(self, reference: str) -> dict[str, Any]:
        validated = self.validate_reference(reference)
        provider_status = self.get_provider(validated["scheme"]).status().to_dict()
        payload = {
            **validated,
            "provider_status": provider_status,
            "ok": False,
        }

        try:
            resolved = self.resolve(reference)
        except SecretProviderError as exc:
            payload["error"] = str(exc)
            return payload

        payload.update(
            {
                "ok": True,
                "provider": resolved.provider,
                "from_env_fallback": resolved.from_env_fallback,
                "redacted_value": resolved.redacted_value,
            }
        )
        return payload

    def integration_checks(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for target in self._integration_targets:
            if target.reference is None:
                checks.append(
                    {
                        "integration": target.name,
                        "field": target.field,
                        "configured": False,
                        "ok": False,
                        "error": "Secret reference is not configured.",
                    }
                )
                continue

            try:
                inspected = self.inspect_reference(target.reference)
            except SecretProviderError as exc:
                inspected = {
                    "reference": target.reference,
                    "configured": True,
                    "ok": False,
                    "error": str(exc),
                }
            checks.append(
                {
                    "integration": target.name,
                    "field": target.field,
                    "configured": True,
                    **inspected,
                }
            )
        return checks

    def resolve(self, reference: str) -> ResolvedSecret:
        scheme = parse_reference_scheme(reference)
        if scheme == "vaultwarden":
            parse_vaultwarden_reference(reference)
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
            checks = self.integration_checks()
            ready = all(check["ok"] for check in checks)
            return {
                "ok": ready,
                "checks": checks,
                "config_error": self._config_error,
            }

        return self.inspect_reference(reference)

    def status(self, provider_name: str) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        status = provider.status().to_dict()
        return {
            "ok": status["available"] and status["configured"] and status["unlocked"],
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
            "ok": status["available"] and status["configured"] and status["unlocked"],
            "provider": provider_name,
            "status": status,
            "session": provider.session_status(),
            "write_session": write_session,
        }

    def lock(self, provider_name: str) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        status = provider.lock().to_dict()
        return {
            "ok": status["available"] and status["configured"] and not status["unlocked"],
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

    def list_entries(self, provider_name: str, *, search: str | None = None) -> dict[str, Any]:
        provider = self.get_provider(provider_name)
        return provider.list_entries(search=search)

    def exec_with_secrets(self, bindings: dict[str, str], command: list[str]) -> int:
        if not bindings:
            raise SecretProviderError("exec requires at least one NAME=reference binding.")
        if not command:
            raise SecretProviderError("exec requires a command after '--'.")

        child_env = dict(self._environ)
        for env_name, reference in bindings.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                raise SecretProviderError(
                    "Environment variable names must match [A-Za-z_][A-Za-z0-9_]*."
                )
            resolved = self.resolve(reference)
            child_env[env_name] = resolved.value
        return self._command_runner(command, env=child_env)

    def doctor(self) -> dict[str, Any]:
        statuses = [status.to_dict() for status in self.provider_statuses()]
        checks = self.integration_checks()
        issues: list[dict[str, str]] = []

        if self._config_error:
            issues.append(
                {
                    "code": "config_unavailable",
                    "provider": "config",
                    "message": self._config_error,
                }
            )

        for status in statuses:
            if not status["available"]:
                issues.append(
                    {
                        "code": "provider_unavailable",
                        "provider": status["name"],
                        "message": status["detail"],
                    }
                )
            elif not status["configured"]:
                issues.append(
                    {
                        "code": "provider_misconfigured",
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
        for check in checks:
            if check["ok"]:
                continue
            if not check.get("configured"):
                issues.append(
                    {
                        "code": "integration_reference_missing",
                        "provider": check["integration"],
                        "message": f"{check['field']}: {check['error']}",
                    }
                )
                continue
            issues.append(
                {
                    "code": "integration_secret_unready",
                    "provider": check["integration"],
                    "message": f"{check['reference']}: {check['error']}",
                }
            )
        return {
            "status": "ok" if not issues else "warning",
            "providers": statuses,
            "checks": checks,
            "issues": issues,
        }

    def materialize(self, name: str | None = None, *, apply: bool = False) -> dict[str, Any]:
        values = active_profile_values()
        root = configured_secrets_root(values)
        definitions = selected_definitions(materialized_secret_definitions(values), name)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for definition in definitions:
            try:
                target_value, identity = materialized_secret_target(definition)
                target = safe_target_path(root, target_value)
                resolved = self.resolve(definition.reference)
                state = current_state(target, resolved.value)
                changed = state in {"missing", "refresh-needed"}
                output_state = state
                if apply and changed:
                    ensure_private_directory(root)
                    write_secret_atomic(target, resolved.value)
                    output_state = "created" if state == "missing" else "updated"
                results.append(
                    materialization_result(
                        name=definition.name,
                        reference=definition.reference,
                        path=target,
                        state=output_state,
                        apply=apply,
                        changed=changed if apply else False,
                        identity=identity,
                    )
                )
            except (SecretProviderError, MaterializationError) as exc:
                errors.append(
                    {
                        "name": definition.name,
                        "reference": definition.reference,
                        "error_type": type(exc).__name__,
                        "message": "Materialized secret was not changed.",
                    }
                )

        return {
            "ok": not errors,
            "apply": apply,
            "secrets_root": str(root.expanduser()),
            "selected_count": len(definitions),
            "materialized": results,
            "errors": errors,
        }

    def materialize_status(self, name: str | None = None) -> dict[str, Any]:
        values = active_profile_values()
        root = configured_secrets_root(values)
        definitions = selected_definitions(materialized_secret_definitions(values), name)
        results: list[dict[str, Any]] = []

        for definition in definitions:
            try:
                target_value, identity = materialized_secret_target(definition)
                target = safe_target_path(root, target_value)
            except MaterializationError as exc:
                target_value = definition.target or definition.reference
                target = root.expanduser() / target_value
                results.append(
                    materialization_status_result(
                        name=definition.name,
                        reference=definition.reference,
                        path=target,
                        root=root,
                        state="invalid",
                        provider=None,
                        from_env_fallback=False,
                        error_type=type(exc).__name__,
                    )
                )
                continue

            try:
                resolved = self.resolve(definition.reference)
                state = status_state(target, resolved.value)
                results.append(
                    materialization_status_result(
                        name=definition.name,
                        reference=definition.reference,
                        path=target,
                        root=root,
                        state=state,
                        provider=resolved.provider,
                        from_env_fallback=resolved.from_env_fallback,
                        identity=identity,
                    )
                )
            except SecretProviderError as exc:
                results.append(
                    materialization_status_result(
                        name=definition.name,
                        reference=definition.reference,
                        path=target,
                        root=root,
                        state="provider-error",
                        provider=None,
                        from_env_fallback=False,
                        error_type=type(exc).__name__,
                        identity=identity,
                    )
                )

        unhealthy_states = {
            "invalid",
            "provider-error",
            "present-unverified",
            "refresh-needed",
        }
        needs_attention = [
            item
            for item in results
            if item["state"] in unhealthy_states or item["permissions_safe"] is False
        ]
        return {
            "ok": not needs_attention,
            "secrets_root": str(root.expanduser()),
            "selected_count": len(definitions),
            "materialized": results,
            "needs_attention": needs_attention,
        }

    def add_secret(
        self,
        *,
        service: str,
        principal: str,
        credential: str,
        value: str,
        apply: bool = False,
    ) -> dict[str, Any]:
        if not value:
            raise SecretProviderError("Secret value must be non-empty.")

        values = active_profile_values()
        root = configured_secrets_root(values)
        identity = materialized_secret_identity(
            service=service,
            principal=principal,
            credential=credential,
        )
        target = safe_target_path(root, identity.to_relative_target())
        provider = self.get_provider("vaultwarden")
        try:
            exists = provider.canonical_item_exists(
                item_name=identity.canonical_identity,
                search=identity.credential,
            )
        except ProviderLockedError as exc:
            raise ProviderLockedError(
                "Vaultwarden is not unlocked. Unlock it before creating secrets."
            ) from exc
        except ProviderUnavailableError as exc:
            raise ProviderUnavailableError(
                "Vaultwarden is unavailable or misconfigured for secret creation."
            ) from exc
        except SecretProviderError as exc:
            raise SecretProviderError("Vaultwarden duplicate check failed.") from exc
        if exists:
            raise SecretProviderError(
                "A Vaultwarden item already exists for this canonical identity."
            )

        secret = {
            "service": identity.service,
            "principal": identity.principal,
            "credential": identity.credential,
            "canonical_identity": identity.canonical_identity,
            "reference": identity.reference,
            "target": str(target),
            "target_source": "identity-derived",
            "provider": "vaultwarden",
            "outcome": "planned" if not apply else "created",
            "applied": apply,
            "redacted_value": "[REDACTED]",
        }

        if apply:
            try:
                created = provider.create_canonical_secret(
                    item_name=identity.canonical_identity,
                    field_name=identity.field_name,
                    value=value,
                    metadata={
                        "wood.credential": identity.credential,
                        "wood.managed": "true",
                        "wood.principal": identity.principal,
                        "wood.reference": identity.reference,
                        "wood.service": identity.service,
                    },
                )
            except ProviderLockedError as exc:
                raise ProviderLockedError(
                    "Vaultwarden is not unlocked. Unlock it before creating secrets."
                ) from exc
            except ProviderUnavailableError as exc:
                raise ProviderUnavailableError(
                    "Vaultwarden is unavailable or misconfigured for secret creation."
                ) from exc
            except SecretProviderError as exc:
                raise SecretProviderError("Vaultwarden item creation failed.") from exc
            secret["vaultwarden_item_id"] = created.item_id
            secret["vaultwarden_item_name"] = created.item_name

        return {
            "ok": True,
            "apply": apply,
            "secrets_root": str(root.expanduser()),
            "secret": secret,
        }


__all__ = [
    "ResolvedSecret",
    "SecretProviderError",
    "SecretResolver",
    "build_default_registry",
]

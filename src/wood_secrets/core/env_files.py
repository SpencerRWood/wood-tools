from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .providers import InvalidSecretReferenceError, SecretProviderError, parse_reference_scheme


class SecretValueResolver(Protocol):
    def resolve(self, reference: str): ...


@dataclass(frozen=True)
class EnvLine:
    raw: str
    key: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class ResolvedEnvReference:
    key: str
    output_key: str
    reference: str
    provider: str
    from_env_fallback: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "output_key": self.output_key,
            "reference": self.reference,
            "provider": self.provider,
            "from_env_fallback": self.from_env_fallback,
            "redacted_value": "[REDACTED]",
        }


@dataclass(frozen=True)
class ResolvedEnvFile:
    input_path: Path
    output_path: Path
    content: str
    resolved: tuple[ResolvedEnvReference, ...]

    def preview(self, *, apply: bool, wrote: bool = False) -> dict[str, object]:
        return {
            "input": str(self.input_path),
            "output": str(self.output_path),
            "apply": apply,
            "wrote": wrote,
            "resolved_count": len(self.resolved),
            "resolved": [item.to_dict() for item in self.resolved],
        }


def parse_env_lines(content: str) -> list[EnvLine]:
    lines: list[EnvLine] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            lines.append(EnvLine(raw=raw_line))
            continue
        key, value = raw_line.split("=", 1)
        lines.append(EnvLine(raw=raw_line, key=key.strip(), value=value.strip()))
    return lines


def is_secret_reference(value: str) -> bool:
    try:
        parse_reference_scheme(value)
    except InvalidSecretReferenceError:
        return False
    return True


def check_output_not_staged(output_path: Path) -> None:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError, subprocess.CalledProcessError:
        return

    staged = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    output_candidates = {output_path.as_posix(), output_path.name}
    if staged.intersection(output_candidates):
        raise SecretProviderError(f"Refusing to continue: {output_path} is staged for commit.")


def resolve_env_file(
    *,
    resolver: SecretValueResolver,
    input_path: Path,
    output_path: Path,
) -> ResolvedEnvFile:
    if not input_path.exists():
        raise SecretProviderError(f"Input file not found: {input_path}")
    if not input_path.is_file():
        raise SecretProviderError(f"Input path is not a file: {input_path}")

    lines = parse_env_lines(input_path.read_text(encoding="utf-8"))
    output_lines: list[str] = []
    resolved: list[ResolvedEnvReference] = []

    for line in lines:
        if line.key is None:
            output_lines.append(line.raw)
            continue

        key = line.key
        value = line.value or ""
        if key.endswith("_REF") and is_secret_reference(value):
            output_key = key[: -len("_REF")]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", output_key):
                raise SecretProviderError(
                    f"Resolved environment variable name is invalid: {output_key}"
                )
            secret = resolver.resolve(value)
            output_lines.append(f"{output_key}={secret.value}")
            resolved.append(
                ResolvedEnvReference(
                    key=key,
                    output_key=output_key,
                    reference=value,
                    provider=secret.provider,
                    from_env_fallback=secret.from_env_fallback,
                )
            )
            continue

        output_lines.append(f"{key}={value}")

    return ResolvedEnvFile(
        input_path=input_path,
        output_path=output_path,
        content="\n".join(output_lines) + "\n",
        resolved=tuple(resolved),
    )


def write_resolved_env_file(result: ResolvedEnvFile, *, force: bool = False) -> None:
    check_output_not_staged(result.output_path)
    if result.output_path.exists() and not force:
        raise SecretProviderError(
            f"Output exists ({result.output_path}); use --force to overwrite."
        )
    result.output_path.write_text(result.content, encoding="utf-8")

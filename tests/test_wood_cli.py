from __future__ import annotations

import json
from pathlib import Path

import pytest

from wood.cli import main
from wood.output import EXIT_CODES, envelope, exit_code


def test_contract_is_bounded_and_self_describing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["contract", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert payload["command"] == "contract"
    assert payload["status"] == "success"
    assert payload["mutation"] == "read-only"
    assert payload["data"]["public_executable"] == "wood"
    assert payload["data"]["exit_codes"] == EXIT_CODES


def test_json_option_works_before_and_after_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "contract"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"
    assert main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["command"] == "contract"


def test_invalid_command_has_structured_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["story", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "invalid"
    assert payload["errors"][0]["code"] == "INVALID_INPUT"
    assert payload["next_actions"]


def test_exit_categories_and_bounds() -> None:
    for status, expected in EXIT_CODES.items():
        payload = envelope(
            command="test",
            status=status,
            summary="x" * 600,
            data={"items": list(range(100))},
        )
        assert exit_code(payload) == expected
        assert len(payload["summary"]) == 500
        assert len(payload["data"]["items"]) == 50  # type: ignore[index]
    with pytest.raises(ValueError, match="blocked"):
        envelope(command="test", status="success", summary="invalid", requires_approval=True)


def test_audit_is_redacted_and_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("WOOD_AUDIT_LOG_PATH", str(audit_path))
    assert main(["contract", "--json", "--token=private-value"]) == 2
    capsys.readouterr()
    event = json.loads(audit_path.read_text().splitlines()[0])
    assert event["cli"] == "wood"
    assert "full_command" not in event
    assert "private-value" not in json.dumps(event)
    monkeypatch.setenv("WOOD_AUDIT_LOG_PATH", str(tmp_path))
    assert main(["contract", "--json"]) == 0

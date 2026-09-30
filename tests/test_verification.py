from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wood import operations, verification
from wood.cli import main
from wood.workflow_files import WorkflowFilesError, read_json, write_json


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    return root


def contract(root, checks, **values):
    header = {"version": 1, "retry_safe": True, **values}
    lines = ["[tool.wood.verify]"]
    for key, value in header.items():
        lines.append(f"{key} = {json.dumps(value)}")
    for check in checks:
        lines.append("[[tool.wood.verify.checks]]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in check.items())
    (root / "pyproject.toml").write_text("\n".join(lines) + "\n")


def command(code="pass", **values):
    return {"name": "runtime", "argv": [sys.executable, "-c", code], **values}


def test_named_required_optional_checks_and_safe_retry(repository):
    contract(
        repository, [command(), command("raise SystemExit(1)", name="advisory", required=False)]
    )
    first = verification.repo_verify(repository)
    second = verification.repo_verify(repository)
    assert first["passed"] is second["passed"] is True
    assert [check["state"] for check in first["checks"]] == ["passed", "failed"]
    assert first["verification_file"] != second["verification_file"]
    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert Path(first["verification_file"]).stat().st_mode & 0o777 == 0o600
    result = verification.verify_record(
        repository, Path(first["verification_file"]), first["source_fingerprint"]
    )
    assert result["checks"][1]["required"] is False


@pytest.mark.parametrize(
    "values", [{"version": 2}, {"version": True}, {"retry_safe": False}, {"unknown": 1}]
)
def test_invalid_contract_header(repository, values):
    contract(repository, [command()], **values)
    with pytest.raises(WorkflowFilesError):
        verification.repo_verify(repository)


@pytest.mark.parametrize(
    "check",
    [
        {},
        command(name="../escape"),
        command(argv=[]),
        command(argv=["x", ""]),
        command(argv=["x", "a" * 501]),
        command(argv=["x"] * 51),
        command(argv=["x\x00"]),
        command(required="yes"),
        command(timeout_seconds=True),
        command(timeout_seconds=61),
        command(directory=".."),
        command(directory="/tmp"),
        command(directory="missing"),
        command(directory=""),
        command(unknown=True),
    ],
)
def test_schema_errors_before_execution(repository, check, monkeypatch):
    contract(repository, [command(), check])

    def execute(*_args):
        pytest.fail("Malformed contracts must not execute any check")

    monkeypatch.setattr(verification, "_execute", execute)
    with pytest.raises(WorkflowFilesError):
        verification.repo_verify(repository)


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [command(), command()],
        [command(name=f"check{i}") for i in range(21)],
        [command(name=f"check{i}", timeout_seconds=60) for i in range(6)],
    ],
)
def test_check_count_unique_names_and_total_timeout(repository, checks):
    contract(repository, checks)
    with pytest.raises(WorkflowFilesError):
        verification.load_contract(repository)


def test_missing_and_malformed_contract(repository):
    assert verification.load_contract(repository, optional=True) is None
    with pytest.raises(WorkflowFilesError, match="Declare"):
        verification.repo_verify(repository)
    (repository / "pyproject.toml").write_text("malformed [")
    with pytest.raises(WorkflowFilesError, match="Cannot read"):
        verification.repo_verify(repository)
    (repository / "pyproject.toml").write_text("[tool.wood]\nverify = 'bad'\n")
    with pytest.raises(WorkflowFilesError):
        verification.repo_verify(repository)


def test_directory_and_shell_free_arguments(repository):
    (repository / "checks").mkdir()
    contract(
        repository,
        [
            {
                "name": "literal",
                "argv": [
                    sys.executable,
                    "-c",
                    "import sys; assert sys.argv[1] == '$(false); value'",
                    "$(false); value",
                ],
                "directory": "checks",
            }
        ],
    )
    assert verification.repo_verify(repository)["passed"]


def test_failure_and_unstartable_command(repository):
    contract(
        repository,
        [command("raise SystemExit(3)"), command(argv=["/missing/executable"], name="missing")],
    )
    result = verification.repo_verify(repository)
    assert not result["passed"]
    assert [check["state"] for check in result["checks"]] == ["failed", "command_error"]
    assert result["checks"][0]["exit_code"] == 3


def test_timeout_terminates_child_process_and_allows_retry(repository, tmp_path):
    marker = tmp_path / "child-finished"
    child = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).touch()"
    parent = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "time.sleep(10)"
    )
    contract(repository, [command(parent, timeout_seconds=1)])
    result = verification.repo_verify(repository)
    assert result["checks"][0]["state"] == "timed_out"
    assert not result["passed"]
    time.sleep(1.2)
    assert not marker.exists()
    contract(repository, [command()])
    assert verification.repo_verify(repository)["passed"]


def test_source_mutation_cannot_attest_to_source(repository):
    contract(
        repository, [command("from pathlib import Path; Path('changed.txt').write_text('changed')")]
    )
    result = verification.repo_verify(repository)
    assert not result["source_unchanged"]
    assert not result["passed"]


@pytest.mark.parametrize(
    "tamper",
    ["fingerprint", "required", "name", "state", "exit_code", "log", "missing_log", "checks"],
)
def test_record_and_log_tampering_rejected(repository, tamper):
    contract(repository, [command()])
    result = verification.repo_verify(repository)
    path = Path(result["verification_file"])
    record = read_json(path)
    if tamper == "fingerprint":
        record["source_fingerprint"] = "wrong"
    elif tamper == "checks":
        record["checks"] = []
    elif tamper == "log":
        Path(record["checks"][0]["log_path"]).write_text("changed")
    elif tamper == "missing_log":
        Path(record["checks"][0]["log_path"]).unlink()
    else:
        record["checks"][0][tamper] = {
            "required": False,
            "name": "other",
            "state": "failed",
            "exit_code": 1,
        }[tamper]
    write_json(path, record)
    with pytest.raises(WorkflowFilesError):
        verification.verify_record(repository, path, result["source_fingerprint"])


def test_changed_contract_and_missing_record(repository):
    contract(repository, [command()])
    result = verification.repo_verify(repository)
    contract(repository, [command("raise SystemExit(1)")])
    with pytest.raises(WorkflowFilesError):
        verification.verify_record(
            repository, Path(result["verification_file"]), result["source_fingerprint"]
        )
    with pytest.raises(WorkflowFilesError):
        verification.verify_record(repository, repository / "missing.json", "fingerprint")


def test_bounded_cli_retains_full_logs_without_command_or_output(repository, monkeypatch, capsys):
    contract(repository, [command("print('sensitive-output-' * 10000)")])
    monkeypatch.setattr(operations, "repository_root", lambda _cwd: repository)
    assert main(["repo", "verify", "--json"]) == 0
    output = capsys.readouterr().out
    assert len(output) < 3000
    assert "sensitive-output" not in output
    assert "argv" not in output
    result = json.loads(output)["data"]
    log = Path(result["checks"][0]["log_path"])
    assert log.stat().st_size > 100000
    assert log.stat().st_mode & 0o777 == 0o600


def test_optional_failure_cli_warning(repository, monkeypatch, capsys):
    contract(repository, [command("raise SystemExit(1)", required=False)])
    monkeypatch.setattr(operations, "repository_root", lambda _cwd: repository)
    assert main(["repo", "verify", "--json"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["warnings"][0]["code"] == "OPTIONAL_VERIFICATION_FAILED"
    )


def test_source_changed_cli_failure(repository, monkeypatch, capsys):
    contract(repository, [command("from pathlib import Path; Path('changed.txt').touch()")])
    monkeypatch.setattr(operations, "repository_root", lambda _cwd: repository)
    assert main(["repo", "verify", "--json"]) == 1
    assert (
        json.loads(capsys.readouterr().out)["warnings"][0]["code"] == "VERIFICATION_SOURCE_CHANGED"
    )


def test_log_io_failure(repository, monkeypatch):
    contract(repository, [command()])
    monkeypatch.setattr(verification, "_file_hash", lambda _path: (_ for _ in ()).throw(OSError()))
    with pytest.raises(WorkflowFilesError, match="retain verification log"):
        verification.repo_verify(repository)


def test_symlink_directory_escape(repository, tmp_path):
    (repository / "outside").symlink_to(tmp_path, target_is_directory=True)
    contract(repository, [command(directory="outside")])
    with pytest.raises(WorkflowFilesError):
        verification.repo_verify(repository)

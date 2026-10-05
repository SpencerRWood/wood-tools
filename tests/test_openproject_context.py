from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from wood.cli import main
from wood.runtime import openproject_prerequisites
from wood_project.openproject import OpenProjectClient, OpenProjectError, load_settings
from wood_project.openproject.context import RepositoryContextError, repository_context
from wood_project.story.models import StoryWorkflowError
from wood_project.story.repository_context import story_reference


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENPROJECT_PROJECT_ID", raising=False)
    monkeypatch.delenv("OPENPROJECT_INITIATIVE_ID", raising=False)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    return tmp_path


def test_mapping_ignores_environment_ids(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "pyproject.toml").write_text(
        "[tool.wood.openproject]\nproject_id = 45\ninitiative_id = 812\n"
    )
    monkeypatch.setenv("OPENPROJECT_PROJECT_ID", "invalid-legacy-value")
    monkeypatch.setenv("OPENPROJECT_INITIATIVE_ID", "999")
    child = repo / "child"
    child.mkdir()
    context = repository_context(cwd=child)
    assert (context.project_id, context.initiative_id) == (45, 812)
    assert story_reference(None, cwd=child) == ("812", 45)
    assert story_reference("999", cwd=child) == ("999", None)


def test_environment_ids_do_not_fill_missing_keys(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENPROJECT_PROJECT_ID", "45")
    monkeypatch.setenv("OPENPROJECT_INITIATIVE_ID", "812")
    context = repository_context()
    assert context.project_id is None and context.initiative_id is None
    with pytest.raises(StoryWorkflowError, match="Configure initiative_id"):
        story_reference(None)
    (repo / "pyproject.toml").write_text("[tool.wood.openproject]\nproject_id = 46\n")
    context = repository_context()
    assert context.project_id == 46 and context.initiative_id is None
    with pytest.raises(StoryWorkflowError, match="Configure initiative_id"):
        story_reference(None)


def test_only_requested_mapping_is_required(repo: Path) -> None:
    (repo / "pyproject.toml").write_text(
        "[tool.wood.openproject]\nproject_id = 45\ninitiative_id = 'bad'\n"
    )
    assert repository_context(keys=("project_id",)).project_id == 45
    with pytest.raises(RepositoryContextError, match="initiative_id"):
        repository_context()


@pytest.mark.parametrize("value", ["45", "bad-token-value", "0", "-3"])
def test_environment_ids_are_never_parsed(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("OPENPROJECT_PROJECT_ID", value)
    monkeypatch.setenv("OPENPROJECT_INITIATIVE_ID", value)
    context = repository_context()
    assert context.project_id is None and context.initiative_id is None
    with pytest.raises(StoryWorkflowError) as error:
        story_reference(None)
    assert value not in str(error.value)


def test_runtime_prerequisites_exclude_repository_ids() -> None:
    values = {"OPENPROJECT_URL": "https://example.test", "OPENPROJECT_API_TOKEN": "secret"}
    result = openproject_prerequisites(values)
    assert result["state"] == "ready"
    assert set(result["presence"]) == set(values)
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["epic", "release"])
@pytest.mark.parametrize("source", ["repository", "environment_only", "explicit"])
def test_planning_project_context_without_initiative(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    source: str,
) -> None:
    if source == "repository":
        (repo / "pyproject.toml").write_text("[tool.wood.openproject]\nproject_id = 45\n")
        monkeypatch.setenv("OPENPROJECT_PROJECT_ID", "99")
    elif source == "environment_only":
        monkeypatch.setenv("OPENPROJECT_PROJECT_ID", "45")
    else:
        (repo / "pyproject.toml").write_text("[tool.wood.openproject\n")
    monkeypatch.setenv("OPENPROJECT_URL", "https://example.test")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "test-secret")
    requests: list[tuple[str, object]] = []

    def request_json(
        _self: object,
        method: str,
        path: str,
        *,
        query: object = None,
        body: object = None,
    ) -> dict[str, object]:
        assert method == "GET" and body is None
        requests.append((path, query))
        return {"total": 0, "_embedded": {"elements": []}}

    monkeypatch.setattr(OpenProjectClient, "request_json", request_json)
    selectors = ["--project", "45"] if source == "explicit" else []
    if source == "environment_only":
        assert main([kind, "list", "--json"]) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["errors"][0]["code"] == "INVALID_CONTEXT"
        assert not requests
        return
    assert main([kind, "list", *selectors, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["project_id"] == 45
    if kind == "release":
        assert requests[0][0] == "/api/v3/projects/45/versions"
    else:
        assert "45" in json.dumps(requests[0][1])


def test_missing_mapping_and_diagnostics_are_token_safe(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENPROJECT_URL", "https://example.test")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "test-secret")
    assert main(["story", "next", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["errors"][0]["code"] == "REPOSITORY_CONTEXT_INVALID"
    assert "test-secret" not in json.dumps(result)
    assert main(["secret", "requirements", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    requirements = result["data"]["requirements"]
    assert set(requirements["required_names"]) == {"OPENPROJECT_URL", "OPENPROJECT_API_TOKEN"}
    assert "OPENPROJECT_PROJECT_ID" not in json.dumps(result)
    assert "OPENPROJECT_INITIATIVE_ID" not in json.dumps(result)
    assert "test-secret" not in json.dumps(result)


@pytest.mark.parametrize("mapping", [None, "[tool.wood.openproject\n"])
def test_project_discovery_needs_only_connection(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mapping: str | None,
) -> None:
    if mapping:
        (repo / "pyproject.toml").write_text(mapping)
    monkeypatch.setenv("OPENPROJECT_URL", "https://example.test")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "test-secret")
    monkeypatch.setattr(
        OpenProjectClient,
        "get_json",
        lambda *_, **__: {
            "_embedded": {"elements": []},
            "total": 0,
        },
    )
    assert main(["project", "list", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["projects"] == []
    assert "test-secret" not in json.dumps(result)


def test_file_token_is_never_loaded(repo: Path) -> None:
    (repo / ".env").write_text("OPENPROJECT_API_TOKEN=file-secret\n")
    (repo / "pyproject.toml").write_text("[tool.wood.openproject]\napi_token = 'file-secret'\n")
    with pytest.raises(OpenProjectError) as error:
        load_settings({"OPENPROJECT_URL": "https://example.test"})
    assert "file-secret" not in str(error.value)


@pytest.mark.parametrize("failure", ["http", "url", "json"])
def test_transport_errors_never_echo_token(failure: str) -> None:
    settings = load_settings(
        {
            "OPENPROJECT_URL": "https://example.test",
            "OPENPROJECT_API_TOKEN": "test-secret",
        }
    )

    def transport(*_: object, **__: object) -> object:
        if failure == "http":
            raise HTTPError(
                "https://example.test", 401, "test-secret", {}, io.BytesIO(b"test-secret")
            )
        if failure == "url":
            raise URLError("test-secret")
        return io.BytesIO(b"test-secret")

    with pytest.raises(OpenProjectError) as error:
        OpenProjectClient(settings, transport=transport).get_json("/api/v3/test-secret")
    assert "test-secret" not in str(error.value)

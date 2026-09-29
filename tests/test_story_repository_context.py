from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from wood.cli import main
from wood_project.story import workflow
from wood_project.story.models import StoryWorkflowError
from wood_project.story.repository_context import story_reference


def repository(tmp_path: Path, pyproject: str | None) -> Path:
    root = tmp_path / "target-repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if pyproject is not None:
        (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    child = root / "src"
    child.mkdir()
    return child


def test_explicit_reference_overrides_missing_or_malformed_repository(tmp_path: Path) -> None:
    child = repository(tmp_path, "[tool.wood.openproject]\ninitiative_id = 'bad'\n")
    assert story_reference("987", cwd=child) == ("987", None)
    assert story_reference("A project", cwd=tmp_path) == ("A project", None)


@pytest.mark.parametrize(
    ("pyproject", "expected_project"),
    [
        ("[tool.wood.openproject]\ninitiative_id = 812\n", None),
        ("[tool.wood.openproject]\ninitiative_id = 812\nproject_id = 45\n", 45),
    ],
)
def test_repository_mapping_from_git_root(
    tmp_path: Path, pyproject: str, expected_project: int | None
) -> None:
    child = repository(tmp_path, pyproject)
    assert story_reference(None, cwd=child) == ("812", expected_project)


@pytest.mark.parametrize(
    "pyproject",
    [
        None,
        "[project]\nname = 'sample'\n",
        "[tool.wood.openproject]\ninitiative_id = '812'\n",
        "[tool.wood.openproject]\ninitiative_id = true\n",
        "[tool.wood.openproject]\ninitiative_id = 812\nproject_id = 0\n",
        "[tool.wood.openproject\n",
    ],
)
def test_missing_or_malformed_mapping_asks_for_explicit_reference(
    tmp_path: Path, pyproject: str | None
) -> None:
    child = repository(tmp_path, pyproject)
    with pytest.raises(StoryWorkflowError, match="Pass an explicit"):
        story_reference(None, cwd=child)


def test_non_repository_asks_for_explicit_reference(tmp_path: Path) -> None:
    with pytest.raises(StoryWorkflowError, match="Pass an explicit"):
        story_reference(None, cwd=tmp_path)


def test_cli_reports_bounded_context_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    child = repository(tmp_path, "[project]\nname = 'sample'\n")
    monkeypatch.chdir(child)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    monkeypatch.setattr("wood.story.load_settings", lambda: object())
    assert main(["story", "next", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["errors"][0]["code"] == "REPOSITORY_CONTEXT_INVALID"
    assert "Pass an explicit" in result["summary"]
    assert "sample" not in json.dumps(result)


def test_executable_outside_target_repository_finds_mapping(tmp_path: Path) -> None:
    child = repository(tmp_path, "[tool.wood.openproject]\ninitiative_id = 913\n")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from wood_project.story.repository_context import story_reference; "
            "print(story_reference(None))",
        ],
        cwd=child,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "('913', None)"


@pytest.mark.parametrize("action", ["list", "next"])
def test_cli_uses_mapping_and_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    child = repository(tmp_path, "[tool.wood.openproject]\ninitiative_id = 812\nproject_id = 45\n")
    monkeypatch.chdir(child)
    monkeypatch.setenv("WOOD_AUDIT_LOG", "off")
    monkeypatch.setattr("wood.story.load_settings", lambda: object())
    received: list[tuple[str, int | None]] = []

    def fake_operation(_client: object, ref: str, **kwargs: object) -> dict[str, object]:
        received.append((ref, kwargs.get("configured_project_id")))  # type: ignore[arg-type]
        return {}

    operation = "list_stories" if action == "list" else "next_story"
    monkeypatch.setattr(workflow, operation, fake_operation)
    assert main(["story", action, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"
    assert main(["story", action, "999", "--json"]) == 0
    capsys.readouterr()
    assert received == [("812", 45), ("999", None)]


@pytest.mark.parametrize("action", ["list", "next"])
def test_configured_project_is_checked_against_initiative(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    monkeypatch.setattr(workflow, "_root", lambda _client, _ref: (812, 45))
    with pytest.raises(StoryWorkflowError, match="Configured project_id"):
        if action == "list":
            workflow.list_stories(object(), "812", configured_project_id=46)  # type: ignore[arg-type]
        else:
            workflow.next_story(object(), "812", configured_project_id=46)  # type: ignore[arg-type]

"""Public Story activity command behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wood.cli import main
from wood_project.openproject import OpenProjectClient, OpenProjectError


def _activity(activity_id: int, story_id: int, comment: str) -> dict[str, Any]:
    return {
        "id": activity_id,
        "comment": {"raw": comment},
        "_links": {"workPackage": {"href": f"/api/v3/work_packages/{story_id}"}},
    }


def _api(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, Any]]]) -> list[str]:
    calls: list[str] = []
    activities = {item["id"]: item for page in pages for item in page}

    def request(self, method, path, *, query=None, body=None):
        calls.append(f"{method} {path}")
        if path == "/api/v3/work_packages/301":
            return {"id": 301, "_links": {"type": {"title": "Story"}}}
        if path == "/api/v3/work_packages/301/activities":
            if method == "POST":
                activities[900] = _activity(900, 301, body["comment"]["raw"])
                return activities[900]
            index = int(query["offset"]) - 1
            return {
                "total": sum(len(page) for page in pages),
                "_embedded": {"elements": pages[index] if index < len(pages) else []},
            }
        if path.startswith("/api/v3/activities/"):
            return activities[int(path.rsplit("/", 1)[1])]
        raise AssertionError((method, path))

    monkeypatch.setattr(OpenProjectClient, "request_json", request)
    monkeypatch.setattr("wood.story.load_settings", lambda: object())
    return calls


def _run(path: Path, *, apply: bool = False) -> int:
    args = ["story", "activity", "add", "301", "--file", str(path), "--json"]
    if apply:
        args.append("--apply")
    return main(args)


def test_preview_and_apply_verify_readback(monkeypatch, tmp_path, capsys) -> None:
    calls = _api(monkeypatch, [[]])
    path = tmp_path / "comment.md"
    path.write_text("A UTF-8 comment: café\n", encoding="utf-8")
    assert _run(path) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mutation"] == "preview"
    assert preview["data"]["comment"] == "A UTF-8 comment: café\n"
    assert not any(call.startswith("POST ") for call in calls)
    assert _run(path, apply=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["activity_id"] == 900
    assert result["data"]["reused"] is False
    assert "GET /api/v3/activities/900" in calls


def test_duplicate_retry_and_conflict_across_pages(monkeypatch, tmp_path, capsys) -> None:
    comment = "Implementation update (WP-301)\nShipped."
    pages = [[_activity(1, 301, "")], [_activity(2, 301, comment)]]
    calls = _api(monkeypatch, pages)
    path = tmp_path / "comment.md"
    path.write_text(comment, encoding="utf-8")
    assert _run(path, apply=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["activity_id"] == 2
    assert result["data"]["reused"] is True
    assert not any(call.startswith("POST ") for call in calls)
    path.write_text("Implementation update (WP-301)\nDifferent.", encoding="utf-8")
    assert _run(path, apply=True) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["errors"][0]["code"] == "ACTIVITY_CONFLICT"
    assert not any(call.startswith("POST ") for call in calls)


@pytest.mark.parametrize("content", [b"", b"  \n", b"\xff", b"x" * 4097])
def test_invalid_comment_file(monkeypatch, tmp_path, capsys, content) -> None:
    calls = _api(monkeypatch, [[]])
    path = tmp_path / "comment.md"
    path.write_bytes(content)
    assert _run(path) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "INVALID_COMMENT"
    assert calls == []


def test_missing_file_and_non_story(monkeypatch, tmp_path, capsys) -> None:
    calls = _api(monkeypatch, [[]])
    assert _run(tmp_path / "missing.md") == 2
    capsys.readouterr()
    path = tmp_path / "comment.md"
    path.write_text("Hello", encoding="utf-8")
    monkeypatch.setattr(
        OpenProjectClient,
        "request_json",
        lambda *_args, **_kwargs: {"id": 301, "_links": {"type": {"title": "Task"}}},
    )
    assert _run(path) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "NOT_A_STORY"
    assert calls == []


def test_failed_readback_and_openproject_failure(monkeypatch, tmp_path, capsys) -> None:
    path = tmp_path / "comment.md"
    path.write_text("Hello", encoding="utf-8")
    calls = _api(monkeypatch, [[]])
    original = OpenProjectClient.request_json

    def wrong_readback(self, method, endpoint, *, query=None, body=None):
        if endpoint == "/api/v3/activities/900":
            return _activity(900, 302, "Hello")
        return original(self, method, endpoint, query=query, body=body)

    monkeypatch.setattr(OpenProjectClient, "request_json", wrong_readback)
    assert _run(path, apply=True) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "ACTIVITY_READBACK_FAILED"
    assert "POST /api/v3/work_packages/301/activities" in calls

    def unavailable(self, method, endpoint, *, query=None, body=None):
        raise OpenProjectError("OPENPROJECT_LOOKUP_FAILED", "private server response")

    monkeypatch.setattr(OpenProjectClient, "request_json", unavailable)
    assert _run(path, apply=True) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["errors"][0]["code"] == "ACTIVITY_UNAVAILABLE"
    assert "private server response" not in json.dumps(failed)

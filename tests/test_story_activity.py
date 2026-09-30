"""Public Story activity command behavior."""

from __future__ import annotations

import hashlib
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
        "_links": {
            "workPackage": {"href": f"/api/v3/work_packages/{story_id}"},
            "update": {"href": f"/api/v3/activities/{activity_id}"},
        },
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
            if method == "PATCH":
                activities[int(path.rsplit("/", 1)[1])]["comment"]["raw"] = body["comment"]["raw"]
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


def test_inspection_classifies_summaries_across_pages(monkeypatch, capsys):
    comment = "Final summary (WP-301)\n" + "x" * 800
    _api(monkeypatch, [[_activity(1, 301, "Progress")], [_activity(2, 301, comment)]])
    assert main(["story", "activity", "list", "301", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == "story activity list"
    assert result["mutation"] == "read-only"
    assert result["data"]["summary_activity_id"] == 2
    assert [a["role"] for a in result["data"]["activities"]] == [
        "progress",
        "implementation_summary",
    ]
    summary = result["data"]["activities"][1]
    assert summary["comment_truncated"]
    assert summary["sha256"] == hashlib.sha256(comment.encode()).hexdigest()


def test_activity_api_offsets_are_page_numbers(monkeypatch, capsys):
    _api(
        monkeypatch,
        [
            [_activity(i, 301, "Progress") for i in range(1, 101)],
            [_activity(101, 301, "Final summary (WP-301)\nDone")],
        ],
    )
    assert main(["story", "activity", "list", "301", "--offset", "100", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["summary_activity_id"] == 101
    assert result["total"] == 101
    assert result["activities"][0]["id"] == 101


def test_summary_update_preview_apply_and_retry(monkeypatch, tmp_path, capsys):
    old = "Implementation update (WP-301)\nBefore"
    calls = _api(monkeypatch, [[_activity(2, 301, old)]])
    path = tmp_path / "summary.md"
    path.write_text("Implementation update (WP-301)\nAfter")
    args = [
        "story",
        "activity",
        "summary",
        "301",
        "--file",
        str(path),
        "--expected-sha256",
        hashlib.sha256(old.encode()).hexdigest(),
        "--json",
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["data"]["action"] == "update"
    assert not any(c.startswith("PATCH") for c in calls)
    assert main([*args, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["activity_id"] == 2
    assert main([*args, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["action"] == "reuse"
    assert calls.count("PATCH /api/v3/activities/2") == 1


@pytest.mark.parametrize(
    "case",
    ["stale", "ambiguous", "wrong_heading", "not_editable", "changed_on_read", "invalid_hash"],
)
def test_summary_refuses_unsafe_mutation(monkeypatch, tmp_path, capsys, case):
    old = "Final summary (WP-301)\nBefore"
    activity = _activity(2, 301, old)
    if case == "not_editable":
        del activity["_links"]["update"]
    pages = [[activity]]
    if case == "ambiguous":
        pages.append([_activity(3, 301, old)])
    calls = _api(monkeypatch, pages)
    path = tmp_path / "summary.md"
    path.write_text(
        "Final summary (WP-302)\nAfter"
        if case == "wrong_heading"
        else "Final summary (WP-301)\nAfter"
    )
    expected = hashlib.sha256(old.encode()).hexdigest()
    if case == "stale":
        expected = "0" * 64
    elif case == "invalid_hash":
        expected = "bad"
    if case == "changed_on_read":
        original = OpenProjectClient.request_json

        def changed(self, method, endpoint, **kwargs):
            result = original(self, method, endpoint, **kwargs)
            if endpoint == "/api/v3/activities/2":
                return {**result, "comment": {"raw": "New progress comment"}}
            return result

        monkeypatch.setattr(OpenProjectClient, "request_json", changed)
    assert (
        main(
            [
                "story",
                "activity",
                "summary",
                "301",
                "--file",
                str(path),
                "--expected-sha256",
                expected,
                "--apply",
                "--json",
            ]
        )
        == 2
    )
    capsys.readouterr()
    assert not any(c.startswith(("POST", "PATCH")) for c in calls)


def test_summary_create_and_missing_expected_summary(monkeypatch, tmp_path, capsys):
    calls = _api(monkeypatch, [[]])
    path = tmp_path / "summary.md"
    path.write_text("Final summary (WP-301)\nShipped")
    args = [
        "story",
        "activity",
        "summary",
        "301",
        "--file",
        str(path),
        "--json",
        "--expected-sha256",
    ]
    assert main([*args, "0" * 64, "--apply"]) == 2
    capsys.readouterr()
    assert not any(c.startswith("POST") for c in calls)
    assert main([*args, "absent"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["action"] == "create"
    assert main([*args, "absent", "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["activity_id"] == 900


@pytest.mark.parametrize(
    "case", ["negative", "duplicate", "foreign", "early_stop", "changing_total", "invalid_total"]
)
def test_inspection_refuses_incomplete_collection(monkeypatch, capsys, case):
    _api(monkeypatch, [[_activity(1, 301, "Progress")]])
    original = OpenProjectClient.request_json

    def malformed(self, method, endpoint, **kwargs):
        result = original(self, method, endpoint, **kwargs)
        if endpoint.endswith("/activities"):
            if case == "invalid_total":
                result["total"] = True
            elif case in {"early_stop", "changing_total"}:
                result["total"] = (
                    2 if kwargs["query"]["offset"] == "1" else 3 if case == "changing_total" else 2
                )
            elif case == "duplicate":
                result["total"] = 2
                result["_embedded"]["elements"] *= 2
            elif case == "foreign":
                result["_embedded"]["elements"][0]["_links"]["workPackage"]["href"] = (
                    "/api/v3/work_packages/302"
                )
        return result

    monkeypatch.setattr(OpenProjectClient, "request_json", malformed)
    args = ["story", "activity", "list", "301", "--json"]
    if case == "negative":
        args += ["--offset", "-1"]
    assert main(args) == 2
    capsys.readouterr()

from __future__ import annotations

import html
import re


def normalize_packet_heading(line: str) -> str:
    heading = line.strip()
    heading = re.sub(r"^#+\s*", "", heading)
    heading = re.sub(r"^\s*(?:[-*]\s*)+", "", heading)
    heading = heading.strip("*_` ")
    heading = heading.removesuffix(":").strip()
    return re.sub(r"\s+", " ", heading).lower()


def append_section_text(existing: str, heading: str, body: str, *, include_heading: bool) -> str:
    body = body.strip()
    if not body:
        return existing
    addition = f"{heading}\n{body}" if include_heading else body
    return f"{existing}\n\n{addition}".strip() if existing else addition


def extract_labeled_value(description: str, label: str) -> str:
    pattern = re.compile(
        rf"^\s*(?:[-*]\s*)?{re.escape(label)}\s*:\s*(.+?)\s*$",
        flags=re.IGNORECASE,
    )
    for line in description.splitlines():
        match = pattern.match(line)
        if match:
            return strip_wrapping_backticks(clean_packet_text(match.group(1)))
    return ""


def clean_packet_text(value: str) -> str:
    cleaned = html.unescape(value)
    cleaned = cleaned.replace("\\_", "_")
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"(?m)^\s*(?:[-*]\s*)+", "* ", cleaned)
    cleaned = re.sub(r"(?m)^\s*#+\s*", "", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = cleaned.strip()
    return cleaned


def strip_wrapping_backticks(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`"):
        return stripped[1:-1].strip()
    return stripped


def is_packet_title(line: str) -> bool:
    return normalize_packet_heading(line) == "codex implementation packet"


def parse_description_packet(description: str) -> dict[str, str]:
    sections = {
        "story_id": extract_labeled_value(description, "External story ID"),
        "branch_name": extract_labeled_value(description, "Branch"),
        "requirement_ids": extract_labeled_value(description, "Requirement IDs"),
        "primary_repository": extract_labeled_value(description, "Primary Repository"),
        "affected_repositories": extract_labeled_value(description, "Affected Repositories"),
        "released_in": extract_labeled_value(description, "Released In"),
        "goal": "",
        "acceptance_criteria": "",
        "non_goals": "",
        "implementation_notes": "",
        "notes": "",
    }
    current: str | None = None
    current_heading = ""
    current_lines: list[str] = []
    heading_map = {
        "goal": "goal",
        "acceptance criteria": "acceptance_criteria",
        "acceptance": "acceptance_criteria",
        "non-goals": "non_goals",
        "non goals": "non_goals",
        "non-goal": "non_goals",
        "implementation notes": "implementation_notes",
        "implementation requirements": "implementation_notes",
        "expected commands": "implementation_notes",
        "expected files / modules": "implementation_notes",
        "expected files/modules": "implementation_notes",
        "expected files": "implementation_notes",
        "test requirements": "implementation_notes",
        "dependencies": "implementation_notes",
        "package": "implementation_notes",
        "requirement ids": "requirement_ids",
        "notes": "implementation_notes",
    }

    def flush_current() -> None:
        nonlocal current, current_heading, current_lines
        if current is None:
            current_lines = []
            return
        body = "\n".join(current_lines).strip()
        if current == "requirement_ids":
            if body and not sections["requirement_ids"]:
                sections["requirement_ids"] = body
        else:
            sections[current] = append_section_text(
                sections[current],
                current_heading,
                clean_packet_text(body),
                include_heading=current == "implementation_notes"
                and current_heading.lower() != "implementation notes",
            )
        current = None
        current_heading = ""
        current_lines = []

    for raw_line in description.splitlines():
        heading = normalize_packet_heading(raw_line)
        if heading in heading_map:
            flush_current()
            current = heading_map[heading]
            current_heading = raw_line.strip().strip("# ").removesuffix(":").strip()
            continue
        if current:
            current_lines.append(raw_line)
    flush_current()

    if not sections["goal"]:
        lines = [
            clean_packet_text(line)
            for line in description.splitlines()
            if line.strip() and not is_packet_title(line)
        ]
        sections["goal"] = lines[0] if lines else ""
    return sections

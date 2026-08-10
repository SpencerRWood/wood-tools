from __future__ import annotations

from . import openproject, project, release, resources, story, story_backlog

COMMAND_MODULES = (project, resources, openproject, story, release, story_backlog)

__all__ = [
    "COMMAND_MODULES",
    "openproject",
    "project",
    "release",
    "resources",
    "story",
    "story_backlog",
]

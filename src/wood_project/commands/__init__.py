from __future__ import annotations

from . import openproject, project, resources, story

COMMAND_MODULES = (story, openproject, resources, project)

__all__ = [
    "COMMAND_MODULES",
    "openproject",
    "project",
    "resources",
    "story",
]

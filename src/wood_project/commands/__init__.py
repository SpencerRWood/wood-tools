from __future__ import annotations

from . import implementation, openproject, project, resources, story

COMMAND_MODULES = (story, implementation, openproject, resources, project)

__all__ = [
    "COMMAND_MODULES",
    "implementation",
    "openproject",
    "project",
    "resources",
    "story",
]

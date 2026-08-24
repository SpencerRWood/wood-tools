from __future__ import annotations

from . import implementation, openproject, project, registry, release, resources, story

COMMAND_MODULES = (story, implementation, release, openproject, resources, registry, project)

__all__ = [
    "COMMAND_MODULES",
    "implementation",
    "openproject",
    "project",
    "registry",
    "release",
    "resources",
    "story",
]

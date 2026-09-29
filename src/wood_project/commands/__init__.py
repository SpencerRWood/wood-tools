from __future__ import annotations

from . import openproject, project, resources

COMMAND_MODULES = (openproject, resources, project)

__all__ = [
    "COMMAND_MODULES",
    "openproject",
    "project",
    "resources",
]

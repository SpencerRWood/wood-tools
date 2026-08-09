from __future__ import annotations

from .aliases import resolve_path_aliases
from .diagnostics import doctor_config
from .document import (
    build_paths,
    default_config_path,
    get_active_profile,
    get_value,
    init_config,
    load_config,
    save_config,
    set_active_profile,
    set_value,
    show_config,
    validate_document,
)
from .models import ConfigError, ConfigPaths
from .validation import validate_config, validate_profile

__all__ = [
    "ConfigError",
    "ConfigPaths",
    "build_paths",
    "default_config_path",
    "doctor_config",
    "get_active_profile",
    "get_value",
    "init_config",
    "load_config",
    "resolve_path_aliases",
    "save_config",
    "set_active_profile",
    "set_value",
    "show_config",
    "validate_config",
    "validate_document",
    "validate_profile",
]

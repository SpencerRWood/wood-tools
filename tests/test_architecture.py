from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from wood.cli import build_parser
from wood_project.commands import COMMAND_MODULES

SRC_ROOT = Path(__file__).parents[1] / "src"
PROJECT_ROOT = SRC_ROOT.parent

ALLOWED_PACKAGE_DEPENDENCIES = {
    "resources": set(),
    "wood": {"resources", "wood_project"},
    "wood_project": {"resources"},
    "wood_templates": {"resources"},
}


def _python_files(package: str | None = None) -> list[Path]:
    root = SRC_ROOT / package if package else SRC_ROOT
    return sorted(root.rglob("*.py"))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_legacy_implementation_modules_are_absent() -> None:
    assert not list(SRC_ROOT.rglob("_impl.py"))
    assert not (SRC_ROOT / "wood_tools").exists()
    assert not (SRC_ROOT / "wood_story").exists()
    assert not (SRC_ROOT / "wood_resources").exists()
    assert not (SRC_ROOT / "wood_cli").exists()
    assert not (SRC_ROOT / "woodlib").exists()
    assert (SRC_ROOT / "wood" / "cli.py").is_file()
    assert (SRC_ROOT / "resources" / "cli" / "__init__.py").is_file()
    assert (SRC_ROOT / "resources" / "packages" / "__init__.py").is_file()


def test_shared_namespace_initializer_has_no_eager_imports() -> None:
    assert _imports(SRC_ROOT / "resources" / "__init__.py") == []


def test_packages_do_not_import_private_implementation_modules() -> None:
    violations = [
        f"{path.relative_to(SRC_ROOT)}: {module}"
        for path in _python_files()
        for module in _imports(path)
        if "._impl" in module or module.endswith("_impl")
    ]
    assert violations == []


def test_packages_do_not_import_legacy_shared_namespaces() -> None:
    violations = [
        f"{path.relative_to(SRC_ROOT)}: {module}"
        for path in _python_files()
        for module in _imports(path)
        if module in {"wood_story", "wood_tools"}
        or module.startswith(("wood_story.", "wood_tools."))
    ]
    assert violations == []


def test_templates_do_not_depend_on_wood_project() -> None:
    violations = [
        f"{path.relative_to(SRC_ROOT)}: {module}"
        for path in _python_files("wood_templates")
        for module in _imports(path)
        if module == "wood_project" or module.startswith("wood_project.")
    ]
    assert violations == []


def test_package_dependencies_follow_the_architecture() -> None:
    violations: list[str] = []
    for owner, allowed_dependencies in ALLOWED_PACKAGE_DEPENDENCIES.items():
        for path in _python_files(owner):
            for module in _imports(path):
                dependency = module.split(".", maxsplit=1)[0]
                if dependency == owner or dependency not in ALLOWED_PACKAGE_DEPENDENCIES:
                    continue
                if dependency not in allowed_dependencies:
                    violations.append(f"{path.relative_to(SRC_ROOT)}: {module}")
    assert violations == []


def test_core_modules_do_not_import_cli_layers() -> None:
    violations = [
        f"{path.relative_to(SRC_ROOT)}: {module}"
        for path in SRC_ROOT.glob("wood_*/core/**/*.py")
        for module in _imports(path)
        if ".cli." in module or module.endswith(".cli")
    ]
    assert violations == []


def test_wood_project_canonical_command_modules_are_authoritative() -> None:
    module_names = tuple(module.__name__.rsplit(".", maxsplit=1)[-1] for module in COMMAND_MODULES)

    assert module_names == (
        "story",
        "openproject",
        "resources",
        "project",
    )


def test_legacy_loop_and_backlog_routes_are_absent() -> None:
    legacy_paths = [
        SRC_ROOT / "wood_project" / "commands" / "story_backlog.py",
        SRC_ROOT / "wood_project" / "story_backlog",
        SRC_ROOT / "wood_project" / "backlog",
        SRC_ROOT / "wood_project" / "story_loop",
        SRC_ROOT / "wood_project" / "release_loop",
        SRC_ROOT / "wood_project" / "implementation" / "_next_story.py",
    ]

    assert [path for path in legacy_paths if path.exists()] == []


def test_wood_help_exposes_diagnostics() -> None:
    help_text = build_parser().format_help()

    assert "Wood Tools v2 agent CLI" in help_text
    assert "{contract,secret,doctor,project}" in help_text
    assert "release" not in help_text


def test_single_public_entrypoint_and_no_manual_release_package() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {"wood": "wood.cli:main"}
    assert not list((SRC_ROOT / "wood_project" / "release").glob("*.py"))
    assert not (SRC_ROOT / "wood_project" / "commands" / "release.py").exists()

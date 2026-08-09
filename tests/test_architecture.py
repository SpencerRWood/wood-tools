from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parents[1] / "src"

ALLOWED_PACKAGE_DEPENDENCIES = {
    "resources": set(),
    "wood_config": {"resources"},
    "wood_secrets": {"resources", "wood_config"},
    "wood_project": {"resources", "wood_config", "wood_secrets"},
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
    assert not (SRC_ROOT / "wood").exists()
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

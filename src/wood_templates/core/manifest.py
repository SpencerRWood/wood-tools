from __future__ import annotations

from pathlib import Path
from typing import Any

from resources.packages import (
    RESOURCE_SCHEMA_VERSION,
    ResourceError,
    validate_resource_digest,
    validate_slug,
    validate_version,
)


class TemplateError(ResourceError):
    """Raised when a template pack or generated project is invalid."""


TEMPLATE_PACK_RESOURCE_KIND = "template"
TEMPLATE_PACK_CONTRACT_FIELD = "template_pack"


def _validate_template_variable(name: str, raw_variable: object) -> dict[str, Any]:
    validate_slug("template_pack.variables key", name)
    if not isinstance(raw_variable, dict):
        raise TemplateError(f"template_pack.variables.{name} must be an object.")
    variable_type = raw_variable.get("type")
    if variable_type not in {"string", "boolean", "integer", "path"}:
        raise TemplateError(
            f"template_pack.variables.{name}.type must be one of: boolean, integer, path, string."
        )
    required = raw_variable.get("required", False)
    if not isinstance(required, bool):
        raise TemplateError(f"template_pack.variables.{name}.required must be a boolean.")
    variable = {
        "type": variable_type,
        "required": required,
    }
    description = raw_variable.get("description")
    if description is not None:
        if not isinstance(description, str) or not description.strip():
            raise TemplateError(
                f"template_pack.variables.{name}.description must be a non-empty string."
            )
        variable["description"] = description
    default = raw_variable.get("default")
    if default is not None:
        variable["default"] = default
    return variable


def _validate_template_operation(index: int, raw_operation: object) -> dict[str, Any]:
    if not isinstance(raw_operation, dict):
        raise TemplateError(f"template_pack.operations[{index}] must be an object.")
    operation_type = raw_operation.get("type", "render")
    if operation_type != "render":
        raise TemplateError(f"template_pack.operations[{index}].type must be render.")
    template = raw_operation.get("template")
    output = raw_operation.get("output")
    if not isinstance(template, str) or not template.strip():
        raise TemplateError(f"template_pack.operations[{index}].template must be non-empty.")
    if Path(template).is_absolute() or ".." in Path(template).parts:
        raise TemplateError(f"template_pack.operations[{index}].template must be relative.")
    if not isinstance(output, str) or not output.strip():
        raise TemplateError(f"template_pack.operations[{index}].output must be non-empty.")
    if Path(output).is_absolute() or ".." in Path(output).parts:
        raise TemplateError(f"template_pack.operations[{index}].output must be relative.")
    overwrite = raw_operation.get("overwrite", "never")
    if overwrite not in {"never", "safe", "always"}:
        raise TemplateError(
            f"template_pack.operations[{index}].overwrite must be one of: always, never, safe."
        )
    operation = {
        "type": operation_type,
        "template": template,
        "output": output,
        "overwrite": overwrite,
    }
    safe_overwrite = raw_operation.get("safe_overwrite")
    if safe_overwrite is not None:
        if not isinstance(safe_overwrite, dict):
            raise TemplateError(
                f"template_pack.operations[{index}].safe_overwrite must be an object."
            )
        strategy = safe_overwrite.get("strategy")
        if strategy not in {"if-unchanged", "if-missing"}:
            raise TemplateError(
                f"template_pack.operations[{index}].safe_overwrite.strategy must be one of: "
                "if-missing, if-unchanged."
            )
        operation["safe_overwrite"] = {"strategy": strategy}
    return operation


def _validate_template_operations(field: str, raw_operations: object) -> list[dict[str, Any]]:
    if not isinstance(raw_operations, list) or not raw_operations:
        raise TemplateError(f"{field} must be a non-empty array.")
    return [
        _validate_template_operation(index, operation)
        for index, operation in enumerate(raw_operations)
    ]


def _validate_template_expected_tree(field: str, raw_tree: object) -> list[str]:
    if not isinstance(raw_tree, list) or not raw_tree:
        raise TemplateError(f"{field} must be a non-empty array.")
    expected_tree: list[str] = []
    seen: set[str] = set()
    for index, raw_path in enumerate(raw_tree):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise TemplateError(f"{field}[{index}] must be a non-empty string.")
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            raise TemplateError(f"{field}[{index}] must be relative.")
        if raw_path in seen:
            raise TemplateError(f"{field} contains duplicate path '{raw_path}'.")
        seen.add(raw_path)
        expected_tree.append(raw_path)
    return expected_tree


def _validate_template_features(raw_features: object) -> dict[str, Any]:
    if raw_features is None:
        return {}
    if not isinstance(raw_features, dict):
        raise TemplateError("template_pack.features must be an object.")

    features: dict[str, Any] = {}
    for feature_name, raw_feature in sorted(raw_features.items()):
        validate_slug("template_pack.features key", feature_name)
        if not isinstance(raw_feature, dict):
            raise TemplateError(f"template_pack.features.{feature_name} must be an object.")
        description = raw_feature.get("description")
        if not isinstance(description, str) or not description.strip():
            raise TemplateError(
                f"template_pack.features.{feature_name}.description must be non-empty."
            )
        conflicts_with_raw = raw_feature.get("conflicts_with", [])
        if not isinstance(conflicts_with_raw, list):
            raise TemplateError(
                f"template_pack.features.{feature_name}.conflicts_with must be an array."
            )
        conflicts_with: list[str] = []
        for index, conflict in enumerate(conflicts_with_raw):
            conflict_name = validate_slug(
                f"template_pack.features.{feature_name}.conflicts_with[{index}]",
                conflict,
            )
            conflicts_with.append(conflict_name)
        operations = _validate_template_operations(
            f"template_pack.features.{feature_name}.operations",
            raw_feature.get("operations"),
        )
        expected_tree = _validate_template_expected_tree(
            f"template_pack.features.{feature_name}.expected_tree",
            raw_feature.get("expected_tree"),
        )
        features[feature_name] = {
            "description": description,
            "conflicts_with": conflicts_with,
            "operations": operations,
            "expected_tree": expected_tree,
        }
    for feature_name, feature in features.items():
        for conflict_name in feature["conflicts_with"]:
            if conflict_name not in features:
                raise TemplateError(
                    f"template_pack.features.{feature_name}.conflicts_with references "
                    f"unknown feature '{conflict_name}'."
                )
    return features


def _validate_template_pack_contract(
    kind: str,
    raw_contract: object,
    *,
    manifest_name: str,
    manifest_version: str,
) -> dict[str, Any] | None:
    if kind != TEMPLATE_PACK_RESOURCE_KIND:
        if raw_contract is not None and not isinstance(raw_contract, dict):
            raise TemplateError("template_pack must be an object when provided.")
        return raw_contract if isinstance(raw_contract, dict) else None

    if not isinstance(raw_contract, dict):
        raise TemplateError("template resources must define template_pack.")
    if raw_contract.get("schema_version") != RESOURCE_SCHEMA_VERSION:
        raise TemplateError(f"template_pack.schema_version must be {RESOURCE_SCHEMA_VERSION}.")
    name = validate_slug("template_pack.name", raw_contract.get("name"))
    version = validate_version("template_pack.version", raw_contract.get("version"))
    if name != manifest_name:
        raise TemplateError("template_pack.name must match resource name.")
    if version != manifest_version:
        raise TemplateError("template_pack.version must match resource version.")
    implementation_stack = raw_contract.get("implementation_stack")
    if not isinstance(implementation_stack, str) or not implementation_stack.strip():
        raise TemplateError("template_pack.implementation_stack must be non-empty.")

    raw_variables = raw_contract.get("variables", {})
    if not isinstance(raw_variables, dict):
        raise TemplateError("template_pack.variables must be an object.")
    variables = {
        key: _validate_template_variable(key, value) for key, value in sorted(raw_variables.items())
    }

    operations = _validate_template_operations(
        "template_pack.operations",
        raw_contract.get("operations"),
    )
    expected_tree = _validate_template_expected_tree(
        "template_pack.expected_tree",
        raw_contract.get("expected_tree"),
    )
    operation_outputs = {operation["output"] for operation in operations}
    missing_outputs = sorted(set(expected_tree) - operation_outputs)
    if missing_outputs:
        raise TemplateError(
            "template_pack.expected_tree entries must have matching operations: "
            + ", ".join(missing_outputs)
        )
    features = _validate_template_features(raw_contract.get("features"))

    raw_validation = raw_contract.get("validation", [])
    if not isinstance(raw_validation, list):
        raise TemplateError("template_pack.validation must be an array.")
    validation: list[dict[str, str]] = []
    for index, raw_rule in enumerate(raw_validation):
        if not isinstance(raw_rule, dict):
            raise TemplateError(f"template_pack.validation[{index}] must be an object.")
        rule = raw_rule.get("rule")
        message = raw_rule.get("message")
        if not isinstance(rule, str) or not rule.strip():
            raise TemplateError(f"template_pack.validation[{index}].rule must be non-empty.")
        if not isinstance(message, str) or not message.strip():
            raise TemplateError(f"template_pack.validation[{index}].message must be non-empty.")
        validation.append({"rule": rule, "message": message})

    return {
        "schema_version": RESOURCE_SCHEMA_VERSION,
        "name": name,
        "version": version,
        "implementation_stack": implementation_stack,
        "variables": variables,
        "operations": operations,
        "expected_tree": expected_tree,
        "features": features,
        "validation": validation,
    }


def validate_template_pack_contract(
    raw_contract: object,
    *,
    manifest_name: str,
    manifest_version: str,
) -> dict[str, Any]:
    contract = _validate_template_pack_contract(
        TEMPLATE_PACK_RESOURCE_KIND,
        raw_contract,
        manifest_name=manifest_name,
        manifest_version=manifest_version,
    )
    if contract is None:
        raise TemplateError("template resources must define template_pack.")
    return contract


def validate_project_template_packs(document: dict[str, Any]) -> None:
    raw_template_packs = document.get("template_packs", [])
    if raw_template_packs is None:
        raw_template_packs = []
    if not isinstance(raw_template_packs, list):
        raise TemplateError("template_packs must be an array when provided.")

    seen_names: set[str] = set()
    for index, entry in enumerate(raw_template_packs):
        if not isinstance(entry, dict):
            raise TemplateError(f"template_packs[{index}] must be an object.")
        name = validate_slug(f"template_packs[{index}].name", entry.get("name"))
        validate_version(f"template_packs[{index}].version", entry.get("version"))
        validate_resource_digest(entry.get("digest"))
        if entry.get("kind", TEMPLATE_PACK_RESOURCE_KIND) != TEMPLATE_PACK_RESOURCE_KIND:
            raise TemplateError(f"template_packs[{index}].kind must be template.")
        if "source" in entry:
            source = entry["source"]
            if not isinstance(source, str) or not source.strip():
                raise TemplateError(f"template_packs[{index}].source must be a non-empty string.")
            if Path(source).is_absolute():
                raise TemplateError(f"template_packs[{index}].source must not be an absolute path.")
        if name in seen_names:
            raise TemplateError(f"template_packs contains duplicate name '{name}'.")
        seen_names.add(name)


__all__ = [
    "TemplateError",
    "TEMPLATE_PACK_CONTRACT_FIELD",
    "TEMPLATE_PACK_RESOURCE_KIND",
    "validate_project_template_packs",
    "validate_template_pack_contract",
]

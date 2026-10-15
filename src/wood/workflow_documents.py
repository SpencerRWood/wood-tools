"""Read Actions YAML with string event names and reject ambiguous mappings."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import yaml

from .workflow_files import WorkflowFilesError


class ActionsLoader(yaml.SafeLoader):
    pass


ActionsLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for initial, resolvers in ActionsLoader.yaml_implicit_resolvers.items():
    ActionsLoader.yaml_implicit_resolvers[initial] = [
        (tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"
    ]
ActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"), list("tTfF")
)


def _mapping(loader: ActionsLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise WorkflowFilesError("WORKFLOW_INVALID", "Duplicate workflow mapping key.")
        result[key] = loader.construct_object(value_node)
    return result


ActionsLoader.add_constructor("tag:yaml.org,2002:map", _mapping)


def document(text: str) -> dict[str, Any]:
    try:
        value = yaml.load(text, Loader=ActionsLoader)
    except yaml.YAMLError as exc:
        raise WorkflowFilesError("WORKFLOW_INVALID", "Cannot parse workflow YAML.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("jobs"), dict):
        raise WorkflowFilesError("WORKFLOW_INVALID", "Workflow requires a jobs mapping.")
    return value


def events(value: dict[str, Any]) -> dict[str, Any]:
    trigger = value.get("on", {})
    if isinstance(trigger, str):
        return {trigger: None}
    if isinstance(trigger, list) and all(isinstance(item, str) for item in trigger):
        return dict.fromkeys(trigger)
    if isinstance(trigger, dict):
        return trigger
    raise WorkflowFilesError("WORKFLOW_INVALID", "Invalid workflow events.")


def with_events(text: str, trigger: dict[str, Any]) -> str:
    """Change the trigger block while retaining reusable jobs and their comments verbatim."""
    if text.lstrip().startswith("{"):
        value = document(text)
        value["on"] = trigger
        return json.dumps(value, indent=2) + "\n"
    tree = yaml.compose(text, Loader=ActionsLoader)
    if not isinstance(tree, yaml.MappingNode):
        raise WorkflowFilesError("WORKFLOW_INVALID", "Workflow requires a mapping.")
    for index, (key, _value) in enumerate(tree.value):
        if key.value == "on":
            stop = (
                tree.value[index + 1][0].start_mark.index
                if index + 1 < len(tree.value)
                else len(text)
            )
            return (
                text[: key.start_mark.index]
                + yaml.safe_dump({"on": trigger}, sort_keys=False)
                + "\n"
                + text[stop:]
            )
    raise WorkflowFilesError("WORKFLOW_INVALID", "Workflow has no trigger block.")


def reusable_only(value: dict[str, Any]) -> bool:
    return set(events(value)) == {"workflow_call"}


def read_only(value: dict[str, Any]) -> bool:
    """Conservative extra-workflow gate; unknown credentials/permissions need review."""
    defaults = value.get("permissions")
    if "secrets" in str(value.get("env", {})):
        return False
    if defaults is not None and (
        not isinstance(defaults, dict) or any(v not in {"read", "none"} for v in defaults.values())
    ):
        return False
    for job in value["jobs"].values():
        if job.get("runs-on") not in {"ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"}:
            return False
        permissions = job.get("permissions", defaults)
        if (
            not isinstance(permissions, dict)
            or any(v not in {"read", "none"} for v in permissions.values())
            or "secrets" in str(job)
        ):
            return False
        # An unreviewed reusable job may publish even if its caller requests read access.
        if "uses" in job:
            return False
    return True

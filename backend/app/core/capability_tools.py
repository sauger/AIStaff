"""Compile a capability manifest into provider-native tool specs.

The manifest remains the authorization source of truth.  This module only
decides which capabilities are exposed to the model on a given step and how
their JSON schema is rendered, so that a provider can enforce the protocol
instead of the prompt asking the model to imitate it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from app.config import get_settings
from app.core.task_request_compiler import CapabilityDescriptor, CapabilityManifest

TOOL_NAME_MAX_CHARS = 64
# Only structural keywords survive the compaction.  Validation constraints such
# as minimum/maxLength are enforced server-side when the capability is invoked,
# so shipping them to the provider would spend tokens on a schema the model is
# not able to honour reliably anyway.
_STRUCTURAL_KEYS = {"type", "properties", "required", "enum", "items"}
_DEGRADED_SUFFIX = "（参数 schema 过大，已省略；调用前请先用 capability_describe 查看参数）"


def model_tool_name(capability_name: str) -> str:
    """Return a provider-safe function name for one capability.

    OpenAI-compatible providers accept ``[A-Za-z0-9_-]{1,64}``.  Dots are the
    common separator in capability names (``tokenop.get_org``), so they are
    mapped to ``_`` deterministically instead of being dropped.
    """

    dotted = str(capability_name or "").replace(".", "__")
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", dotted).strip("_")
    if not cleaned:
        cleaned = "capability"
    if cleaned[0].isdigit():
        cleaned = f"tool_{cleaned}"
    return cleaned[:TOOL_NAME_MAX_CHARS]


def compact_parameters(schema: Any) -> dict[str, Any]:
    """Project a JSON schema onto the structural keywords only."""

    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    compacted = _compact_schema(schema)
    if compacted.get("type") != "object":
        return {"type": "object", "properties": {}}
    compacted.setdefault("properties", {})
    return compacted


def build_tool_specs(
    manifest: CapabilityManifest,
    *,
    activated: Iterable[str],
    max_description_chars: int | None = None,
    schema_budget_chars: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return ``(tools, names)`` for one model call.

    ``names`` maps the provider-facing function name back to the capability
    name accepted by :meth:`HarnessCapabilityInvoker.invoke`.  Capabilities are
    sorted by name so the rendered tool list is byte-stable across iterations,
    which keeps provider prompt caches warm.
    """

    settings = get_settings()
    description_budget = max(
        8,
        int(
            max_description_chars
            if max_description_chars is not None
            else settings.agent_tools_max_description_chars
        ),
    )
    schema_budget = max(
        128,
        int(
            schema_budget_chars
            if schema_budget_chars is not None
            else settings.agent_tools_schema_budget_chars
        ),
    )
    wanted = {str(name).strip() for name in activated if str(name or "").strip()}
    if not wanted:
        return [], {}
    descriptors = sorted(
        (
            descriptor
            for descriptor in manifest.available
            if descriptor.available and descriptor.name in wanted
        ),
        key=lambda item: (item.kind, item.name, item.capability_id),
    )
    tools: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for descriptor in descriptors:
        function_name = _unique_tool_name(descriptor, names)
        parameters = compact_parameters(descriptor.input_schema)
        description = _tool_description(descriptor, description_budget)
        if len(_serialize(parameters)) > schema_budget:
            parameters = {"type": "object", "properties": {}}
            description = f"{description}{_DEGRADED_SUFFIX}"
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": function_name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
        names[function_name] = descriptor.name
    return tools, names


def build_tools_for_manifest(
    manifest: CapabilityManifest,
    *,
    activated: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Convenience wrapper exposing every available capability."""

    if activated is None:
        activated = manifest.allowed_names()
    return build_tool_specs(manifest, activated=activated)


def _unique_tool_name(
    descriptor: CapabilityDescriptor,
    taken: dict[str, str],
) -> str:
    base = model_tool_name(descriptor.name)
    candidate = base
    suffix = 2
    while candidate in taken:
        tail = f"_{suffix}"
        candidate = f"{base[: TOOL_NAME_MAX_CHARS - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _tool_description(descriptor: CapabilityDescriptor, budget: int) -> str:
    text = " ".join(
        str(
            descriptor.description
            or descriptor.metadata.get("description")
            or descriptor.name
        ).split()
    )
    if not text:
        text = descriptor.name
    if len(text) > budget:
        text = f"{text[: max(0, budget - 1)]}…"
    return text


def _compact_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    compacted: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _STRUCTURAL_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            compacted["properties"] = {
                str(prop): _compact_schema(sub)
                for prop, sub in value.items()
                if isinstance(sub, dict)
            }
        elif key == "required" and isinstance(value, list):
            compacted["required"] = [str(item) for item in value]
        elif key == "enum" and isinstance(value, list):
            compacted["enum"] = value
        elif key == "items":
            compacted["items"] = _compact_schema(value)
        elif key == "type" and isinstance(value, str):
            compacted["type"] = value
    return compacted


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


__all__ = [
    "build_tool_specs",
    "build_tools_for_manifest",
    "compact_parameters",
    "model_tool_name",
]

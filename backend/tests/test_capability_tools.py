from __future__ import annotations

import json

from app.core.capability_tools import (
    build_tool_specs,
    compact_parameters,
    model_tool_name,
)
from app.core.task_request_compiler import CapabilityDescriptor, CapabilityManifest


def _descriptor(
    name: str,
    *,
    schema: dict | None = None,
    description: str = "do something",
    kind: str = "tool",
    available: bool = True,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=f"cap_{name}",
        name=name,
        kind=kind,
        description=description,
        input_schema=schema or {},
        available=available,
    )


def _manifest(*descriptors: CapabilityDescriptor) -> CapabilityManifest:
    return CapabilityManifest(available=list(descriptors))


def test_model_tool_name_is_provider_safe_and_stable():
    assert model_tool_name("tokenop.get_organization_account") == (
        "tokenop__get_organization_account"
    )
    assert model_tool_name("9lives") == "tool_9lives"
    assert model_tool_name("...") == "capability"
    assert len(model_tool_name("x" * 200)) == 64


def test_build_tool_specs_exposes_only_activated_available_capabilities():
    manifest = _manifest(
        _descriptor("zeta.read", schema={"type": "object"}),
        _descriptor("alpha.read"),
        _descriptor("hidden.read", available=False),
    )

    tools, names = build_tool_specs(
        manifest,
        activated={"zeta.read", "alpha.read", "hidden.read", "missing"},
    )

    assert [tool["function"]["name"] for tool in tools] == [
        "alpha__read",
        "zeta__read",
    ]
    assert names == {"alpha__read": "alpha.read", "zeta__read": "zeta.read"}
    assert tools[0]["type"] == "function"


def test_compact_parameters_keeps_structure_and_drops_validation_noise():
    schema = {
        "type": "object",
        "title": "ignored",
        "properties": {
            "path": {
                "type": "string",
                "description": "file path",
                "minLength": 1,
            },
            "mode": {"type": "string", "enum": ["read", "write"]},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    assert compact_parameters(schema) == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "mode": {"type": "string", "enum": ["read", "write"]},
        },
        "required": ["path"],
    }
    assert compact_parameters({}) == {"type": "object", "properties": {}}


def test_description_is_truncated():
    manifest = _manifest(_descriptor("alpha.read", description="x" * 200))

    tools, _names = build_tool_specs(
        manifest,
        activated={"alpha.read"},
        max_description_chars=20,
    )

    description = tools[0]["function"]["description"]
    assert len(description) == 20
    assert description.endswith("…")


def test_oversized_schema_is_collapsed_and_marked():
    huge = {
        "type": "object",
        "properties": {
            f"field_{index}": {"type": "string", "description": "y" * 80}
            for index in range(80)
        },
    }
    manifest = _manifest(_descriptor("big.read", schema=huge))

    tools, _names = build_tool_specs(
        manifest,
        activated={"big.read"},
        schema_budget_chars=200,
    )

    function = tools[0]["function"]
    assert function["parameters"] == {"type": "object", "properties": {}}
    assert "capability_describe" in function["description"]


def test_name_collisions_are_deduplicated():
    manifest = _manifest(_descriptor("a.b"), _descriptor("a_b"))

    tools, names = build_tool_specs(manifest, activated={"a.b", "a_b"})

    rendered = [tool["function"]["name"] for tool in tools]
    assert len(set(rendered)) == 2
    assert set(names.values()) == {"a.b", "a_b"}


def test_specs_are_byte_stable_across_calls_and_activation_order():
    manifest = _manifest(
        _descriptor("b.read"),
        _descriptor("a.read"),
        _descriptor("c.write", kind="internal"),
    )

    first = build_tool_specs(manifest, activated={"a.read", "b.read", "c.write"})
    second = build_tool_specs(manifest, activated={"c.write", "b.read", "a.read"})

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

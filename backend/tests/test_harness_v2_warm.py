from __future__ import annotations

from app.core.capability_discovery import (
    project_capability_manifest,
    warm_capability_manifest,
)
from app.core.harness_v2_engine import (
    _merge_warm_capabilities,
    _warm_capability_names,
)
from app.core.task_request_compiler import CapabilityDescriptor, CapabilityManifest


def _descriptor(name: str, **overrides) -> CapabilityDescriptor:
    payload = {
        "capability_id": f"cap_{name}",
        "name": name,
        "kind": "tool",
        "description": f"{name} capability",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    }
    payload.update(overrides)
    return CapabilityDescriptor(**payload)


def test_merge_keeps_newest_order_and_skips_internal_actions():
    merged = _merge_warm_capabilities(
        ["alpha.read", "beta.read"],
        [
            {"tool_name": "beta.read"},
            {"tool_name": "capability_describe"},
            {"tool_name": "gamma.read"},
        ],
    )

    assert merged == ["alpha.read", "beta.read", "gamma.read"]


def test_merge_respects_limit():
    existing = [f"cap.{index}" for index in range(45)]

    merged = _merge_warm_capabilities(existing, [{"tool_name": "zzz.cap"}], limit=40)

    assert len(merged) == 40
    assert merged[-1] == "zzz.cap"
    assert "cap.0" not in merged
    assert merged == sorted(merged)


def test_warm_names_reads_checkpoint_and_dedupes():
    checkpoint = {"warm_capabilities": ["alpha.read", "alpha.read", "", "beta.read"]}

    assert _warm_capability_names(checkpoint) == ["alpha.read", "beta.read"]
    assert _warm_capability_names({}) == []
    assert _warm_capability_names({"warm_capabilities": "oops"}) == []


def test_warm_capability_manifest_promotes_schema_and_drops_catalog_entry():
    full = CapabilityManifest(
        available=[
            _descriptor("alpha.read"),
            _descriptor("beta.read"),
        ]
    )
    projected = project_capability_manifest(full)
    assert all(item.name != "alpha.read" for item in projected.available)

    warmed = warm_capability_manifest(projected, full, ["alpha.read"])

    assert any(item.name == "alpha.read" for item in warmed.available)
    assert all(entry.name != "alpha.read" for entry in warmed.catalog)
    promoted = next(item for item in warmed.available if item.name == "alpha.read")
    assert promoted.input_schema["properties"]["query"]["type"] == "string"


def test_warm_capability_manifest_ignores_unknown_and_unauthorized_names():
    full = CapabilityManifest(
        available=[
            _descriptor("alpha.read"),
            _descriptor("hidden.read", available=False),
        ]
    )
    projected = project_capability_manifest(full)

    warmed = warm_capability_manifest(
        projected,
        full,
        ["missing.read", "hidden.read"],
    )

    assert warmed.available == projected.available


def test_warm_capability_manifest_is_identity_without_names():
    full = CapabilityManifest(available=[_descriptor("alpha.read")])
    projected = project_capability_manifest(full)

    assert warm_capability_manifest(projected, full, []) is projected

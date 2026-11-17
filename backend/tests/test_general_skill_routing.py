from __future__ import annotations

from app.core.general_skill_routing import apply_general_skill_routes
from app.core.task_request_compiler import (
    CapabilityDescriptor,
    CapabilityManifest,
    TaskRequirement,
)


def _skill(name: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=name,
        name=f"general_skill.{name}",
        kind="general_skill",
        description=f"{name} description",
    )


def _tool(name: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=name,
        name=name,
        kind="tool",
        description=name,
        read_only=True,
        metadata={"method": "GET"},
    )


def test_refuse_route_removes_tokenop_tools_and_requires_skill() -> None:
    skill = _skill("tokenop-ops-analysis-report")
    tool = _tool("tokenop.export_platform_usage_details")
    full = CapabilityManifest(available=[skill, tool], snapshot_revision="rev")
    projected = CapabilityManifest(available=[tool], snapshot_revision="rev")
    requirement = TaskRequirement(
        task_frame_id="task-route-refuse",
        kind="conversation",
        goal="客户费用加渠道成本一共多少钱",
        capability_manifest=projected,
    )
    routes = [
        {
            "id": "refuse",
            "priority": 100,
            "mode": "refuse",
            "patterns": [r"客户费用.*渠道成本"],
            "skills": ["tokenop-ops-analysis-report"],
            "reason": "口径不同",
        }
    ]

    applied = apply_general_skill_routes(requirement, projected, full, routes)

    assert applied == ["general_skill.tokenop-ops-analysis-report"]
    assert "general_skill.tokenop-ops-analysis-report" in requirement.required_capability_names
    assert "general_skill.tokenop-ops-analysis-report" in {
        item.name for item in requirement.capability_manifest.available
    }
    assert "tokenop.export_platform_usage_details" not in {
        item.name for item in requirement.capability_manifest.available
    }
    assert "tokenop." in requirement.blocked_tool_prefixes
    assert any("不得输出跨口径合计" in item for item in requirement.completion_criteria)


def test_normal_route_requires_skill_without_removing_tools() -> None:
    skill = _skill("tokenop-customer-billing")
    tool = _tool("tokenop.export_platform_usage_details")
    full = CapabilityManifest(available=[skill, tool], snapshot_revision="rev")
    projected = CapabilityManifest(available=[tool], snapshot_revision="rev")
    requirement = TaskRequirement(
        task_frame_id="task-route-normal",
        kind="conversation",
        goal="算一下客户费用",
        capability_manifest=projected,
    )
    routes = [
        {
            "id": "customer",
            "priority": 80,
            "mode": "normal",
            "patterns": [r"客户费用"],
            "skills": ["tokenop-customer-billing"],
        }
    ]

    applied = apply_general_skill_routes(requirement, projected, full, routes)

    assert applied == ["general_skill.tokenop-customer-billing"]
    assert "general_skill.tokenop-customer-billing" in requirement.required_capability_names
    assert "tokenop.export_platform_usage_details" in {
        item.name for item in requirement.capability_manifest.available
    }


def test_no_route_match_is_a_noop() -> None:
    tool = _tool("tokenop.export_platform_usage_details")
    full = CapabilityManifest(available=[tool], snapshot_revision="rev")
    projected = CapabilityManifest(available=[tool], snapshot_revision="rev")
    requirement = TaskRequirement(
        task_frame_id="task-route-none",
        kind="conversation",
        goal="你好",
        capability_manifest=projected,
    )

    applied = apply_general_skill_routes(
        requirement,
        projected,
        full,
        [{"patterns": [r"客户费用"], "skills": ["tokenop-customer-billing"]}],
    )

    assert applied == []
    assert requirement.required_capability_names == []

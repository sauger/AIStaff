from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "ULTRARAG_DATA_DIR", tempfile.mkdtemp(prefix="staffdeck-workflow-policy-")
)

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.workflow_packs import get_workflow_pack
from app.core.capability_manifest import CapabilityManifestBuilder
from app.core.harness_v2_engine import _turn_stop_reason
from app.core.task_request_compiler import TaskExecutionResult
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    MCPServer,
    Skill,
    Tenant,
    Tool,
)
from app.llm.call_policy import policy_for
from app.workflow_packs.screen.sop import build_screen_sop

TENANT = "tenant-policy"
AGENT = "agent-screen"

WORKFLOW_METADATA = {
    "workflow": {
        "runtime": "workflow_v1",
        "pack": "screen.change",
        "tool_policy": {
            "conversation_allowlist": [
                "get_screen",
                "describe_dataset",
                "list_component_types",
                "knowledge_search",
            ]
        },
    }
}


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(metadata: dict | None) -> Session:
    db = Session(_engine())
    db.add(Tenant(id=TENANT, name="Policy Tenant"))
    db.add(MCPServer(id="mcp-1", tenant_id=TENANT, name="ai_screen", enabled=True))
    db.add(
        AgentProfile(
            id=AGENT,
            tenant_id=TENANT,
            name="数字大屏助手",
            metadata_json=metadata or {},
        )
    )
    for index, (name, scope) in enumerate(
        [
            ("get_screen", "general"),
            ("describe_dataset", "general"),
            ("list_component_types", "general"),
            ("create_screen", "sop_specific"),
            ("plan_screen", "sop_specific"),
            ("apply_screen_operations", "sop_specific"),
        ]
    ):
        tool = Tool(
            id=f"tool-{index}",
            tenant_id=TENANT,
            name=name,
            display_name=name,
            bucket="AI 大屏",
            tool_type="mcp",
            method="POST",
            url="http://mcp.test",
            mcp_server_id="mcp-1",
            capability_scope=scope,
            capability_scope_inherited=False,
            enabled=True,
        )
        db.add(tool)
        db.add(
            AgentResourceBinding(
                id=f"bind-{index}",
                tenant_id=TENANT,
                agent_id=AGENT,
                resource_type="tool",
                resource_id=tool.id,
                status="active",
                metadata_json={"scope": "agent_private"},
            )
        )
    db.commit()
    return db


def _names(manifest) -> set[str]:
    return {item.name for item in manifest.available}


def test_conversation_surface_is_allowlisted():
    db = _seed(WORKFLOW_METADATA)
    manifest = CapabilityManifestBuilder(db).build(TENANT, AGENT, None, None)
    names = _names(manifest)
    assert {"get_screen", "describe_dataset", "list_component_types"} <= names
    assert "create_screen" not in names
    assert "apply_screen_operations" not in names
    assert "read_file" not in names
    assert "capability_describe" not in names


def test_workflow_sop_only_exposes_node_tools():
    db = _seed(WORKFLOW_METADATA)
    skill = Skill(
        id="skill-1",
        tenant_id=TENANT,
        skill_id="screen.change",
        name="大屏变更",
        status="published",
        content_json=build_screen_sop(),
    )
    apply_names = _names(
        CapabilityManifestBuilder(db).build(TENANT, AGENT, skill, "apply")
    )
    assert "apply_screen_operations" in apply_names
    assert "create_screen" not in apply_names

    plan_names = _names(
        CapabilityManifestBuilder(db).build(TENANT, AGENT, skill, "submit_plan")
    )
    assert "plan_screen" in plan_names
    assert "create_screen" not in plan_names
    assert "apply_screen_operations" not in plan_names


def test_legacy_agent_keeps_full_surface():
    db = _seed(None)
    names = _names(CapabilityManifestBuilder(db).build(TENANT, AGENT, None, None))
    assert "get_screen" in names
    assert "describe_dataset" in names
    assert "read_file" in names


def test_turn_stop_reason_priority():
    completed = TaskExecutionResult(task_frame_id="f1", status="completed", reply_fragment="ok")
    assert _turn_stop_reason([completed]) == "completed"
    assert (
        _turn_stop_reason(
            [completed, TaskExecutionResult(task_frame_id="f2", status="awaiting_user")]
        )
        == "awaiting_user"
    )
    assert (
        _turn_stop_reason([TaskExecutionResult(task_frame_id="f3", status="action_budget")])
        == "budget_exhausted"
    )
    assert _turn_stop_reason([]) == "no_task"


def test_workflow_structured_step_is_bounded():
    policy = policy_for("workflow.structured_step")
    assert policy is not None
    assert policy.max_tokens <= 1200
    assert policy.timeout_seconds is not None
    assert policy.timeout_seconds <= 30.0


def test_planner_call_is_bounded():
    policy = policy_for("turn_planner.plan")
    assert policy is not None
    assert policy.max_tokens <= 300
    assert policy.timeout_seconds is not None
    assert policy.timeout_seconds <= 8.0


def test_workflow_pack_endpoint_returns_publishable_card():
    payload = get_workflow_pack("screen")
    assert payload["pack_id"] == "screen"
    assert payload["content"]["skill_id"] == "screen.change"
    assert payload["content"]["metadata"]["workflow"]["runtime"] == "workflow_v1"

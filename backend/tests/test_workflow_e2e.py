from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

os.environ.setdefault(
    "ULTRARAG_DATA_DIR", tempfile.mkdtemp(prefix="staffdeck-workflow-e2e-")
)

from app.core import harness_v2_engine as engine_module
from app.core.agent_loop import AgentLoop
from app.core.capability_manifest import CapabilityManifestBuilder
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.core.task_request_compiler import (
    CapabilityDescriptor,
    CapabilityManifest,
)
from app.core.turn_planner import TurnPlanner
from app.db.models import (
    AgentEvent,
    AgentProfile,
    ChatSession,
    ModelConfig,
    Skill,
    Tenant,
)
from app.llm import LLMClient
from app.session.session_schema import ChatTurnRequest, PlannedTaskFrame, TurnPlan
from app.session.turn_context import ScreenTurnContext, SelectedComponent
from app.workflow_packs.screen.sop import build_screen_sop

MODEL = ModelConfig(
    id="model-1",
    tenant_id="tenant-demo",
    name="test-model",
    api_key_encrypted="unused",
    model="test-model",
)
SOP = build_screen_sop()
SKILL = Skill(
    id="skill-1",
    tenant_id="tenant-demo",
    skill_id="screen.change",
    name="大屏变更",
    status="published",
    content_json=SOP,
)

MANIFEST = CapabilityManifest(
    available=[
        CapabilityDescriptor(
            capability_id="cap-get",
            name="get_screen",
            kind="tool",
            input_schema={
                "type": "object",
                "properties": {
                    "screenId": {"type": "string"},
                    "includeDocument": {"type": "boolean"},
                },
            },
        ),
        CapabilityDescriptor(
            capability_id="cap-apply",
            name="apply_screen_operations",
            kind="tool",
            input_schema={
                "type": "object",
                "properties": {
                    "requestId": {"type": "string"},
                    "screenId": {"type": "string"},
                    "expectedRevision": {"type": "integer"},
                    "operations": {"type": "array"},
                },
            },
        ),
        CapabilityDescriptor(
            capability_id="cap-validate",
            name="validate_screen",
            kind="tool",
            input_schema={
                "type": "object",
                "properties": {"document": {"type": "object"}},
            },
        ),
        CapabilityDescriptor(
            capability_id="cap-create",
            name="create_screen",
            kind="tool",
            input_schema={
                "type": "object",
                "properties": {
                    "requestId": {"type": "string"},
                    "mode": {"type": "string"},
                    "template": {"type": "string"},
                    "name": {"type": "string"},
                    "screenId": {"type": "string"},
                    "expectedRevision": {"type": "integer"},
                },
            },
        ),
        CapabilityDescriptor(
            capability_id="cap-describe",
            name="describe_dataset",
            kind="tool",
            input_schema={"type": "object", "properties": {}},
        ),
        CapabilityDescriptor(
            capability_id="cap-plan",
            name="plan_screen",
            kind="tool",
            input_schema={
                "type": "object",
                "properties": {
                    "requestId": {"type": "string"},
                    "planId": {"type": "string"},
                    "sessionId": {"type": "string"},
                    "screenId": {"type": "string"},
                    "projectId": {"type": "string"},
                    "expectedRevision": {"type": "integer"},
                    "confirm": {"type": "boolean"},
                    "draft": {"type": "object"},
                },
                "required": ["requestId"],
            },
        ),
    ]
)


def _turn_plan() -> TurnPlan:
    return TurnPlan(
        decision="start_new_task",
        confidence=0.95,
        user_intent="大屏变更",
        task_frames=[
            PlannedTaskFrame(
                task_id="t1",
                kind="sop",
                decision="start_new_task",
                target_skill_id="screen.change",
                target_step_id="bind",
                requirements=["完成大屏变更"],
            )
        ],
    )


class FakeScreen:
    def __init__(self, *, components=None, revision=11, new_screen_id="scr_new"):
        self.components = list(components or [])
        self.revision = revision
        self.new_screen_id = new_screen_id
        self.calls: list[tuple[str, dict]] = []
        self.plan_calls = 0
        self.next_plan: dict = {}

    def invoke(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "get_screen":
            data = {
                "screenId": arguments.get("screenId") or "scr_1",
                "revision": self.revision,
                "components": self.components,
            }
            if arguments.get("includeDocument"):
                data["document"] = {
                    "schemaVersion": "1.0",
                    "components": [
                        {
                            "id": item["componentId"],
                            "type": item["type"],
                            "name": item.get("name"),
                        }
                        for item in self.components
                    ],
                }
            return {"success": True, "data": data}
        if name == "apply_screen_operations":
            changed = [
                operation.get("componentId")
                for operation in arguments.get("operations") or []
                if operation.get("componentId")
            ]
            self.revision += 1
            return {
                "success": True,
                "data": {
                    "screenId": arguments.get("screenId"),
                    "revision": self.revision,
                    "changedComponentIds": changed,
                },
            }
        if name == "validate_screen":
            return {"success": True, "data": {"valid": True, "errors": []}}
        if name == "describe_dataset":
            return {
                "success": True,
                "data": {
                    "datasets": [
                        {
                            "slug": "sales_demo",
                            "name": "销售示例数据",
                            "datasetVersionId": "ver_sales",
                        }
                    ],
                    "sources": [],
                },
            }
        if name == "plan_screen":
            draft = arguments.get("draft") or {}
            return {
                "success": True,
                "data": {
                    "planId": "plan_1",
                    "status": "compiled" if arguments.get("confirm") else "needs_confirmation",
                    "goal": draft.get("goal") or "销售经营大屏",
                    "summary": "方案已提交",
                },
            }
        if name == "create_screen":
            return {
                "success": True,
                "data": {
                    "screenId": self.new_screen_id,
                    "revision": 1,
                    "name": arguments.get("name"),
                },
            }
        return {
            "success": False,
            "error": {"code": "UNKNOWN_TOOL", "message": name},
        }

    def generate_json(self, _system_prompt, _payload):
        self.plan_calls += 1
        return self.next_plan



@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-demo", name="Demo"))
        session.add(AgentProfile(id="agent-1", tenant_id="tenant-demo", name="数字大屏助手"))
        session.add(
            ChatSession(
                id="sess-1",
                tenant_id="tenant-demo",
                agent_id="agent-1",
                user_id="user-1",
            )
        )
        session.commit()
        yield session


def _run_turn(db, screen: FakeScreen, *, message: str, context: ScreenTurnContext):
    request = ChatTurnRequest(
        tenant_id="tenant-demo",
        session_id="sess-1",
        agent_id="agent-1",
        user_id="user-1",
        client_turn_id="turn-1",
        message=message,
        screen_context=context,
    )
    with (
        patch.object(AgentLoop, "_get_request_model", lambda self, req, agent_id: MODEL),
        patch.object(
            AgentLoop,
            "_list_published_skills",
            lambda self, tenant_id, agent_id: [SKILL],
        ),
        patch.object(TurnPlanner, "plan", lambda self, *args, **kwargs: _turn_plan()),
        patch.object(CapabilityManifestBuilder, "build", lambda self, *args, **kwargs: MANIFEST),
        patch.object(HarnessCapabilityInvoker, "invoke", screen.invoke),
        patch.object(
            engine_module,
            "get_settings",
            lambda: SimpleNamespace(workflow_runtime_enabled=True),
        ),
        patch.object(LLMClient, "__init__", lambda self, config: None),
        patch.object(LLMClient, "generate_json", lambda self, system, payload: screen.generate_json(system, payload)),
    ):
        response = AgentLoop(db).handle_turn(request)
    return response


def _events(db) -> list[AgentEvent]:
    return list(
        db.exec(
            select(AgentEvent).where(AgentEvent.session_id == "sess-1")
        ).all()
    )


def _step_ids(db) -> list[str]:
    ids = []
    for event in _events(db):
        if event.event_type == "workflow_step_started":
            ids.append(str((event.payload_json or {}).get("step_id")))
    return ids


def _tool_names(screen: FakeScreen) -> list[str]:
    return [name for name, _arguments in screen.calls]


def test_t1_creates_a_sales_screen_through_the_sop(db):
    screen = FakeScreen(components=[], revision=1)
    screen.next_plan = {
        "confirm": True,
        "draft": {
            "goal": "销售经营大屏",
            "sources": ["销售示例数据"],
            "slots": [
                {
                    "title": "收入",
                    "role": "metric",
                    "datasetSlug": "sales_demo",
                    "measures": ["收入"],
                    "chartType": "metric",
                }
            ],
        },
    }

    response = _run_turn(
        db,
        screen,
        message="帮我做一个销售经营大屏",
        context=ScreenTurnContext(
            project_id="proj_1",
            screen_id=None,
            screen_name=None,
            revision=None,
            component_count=None,
        ),
    )

    assert "方案" in response.reply
    assert "销售经营大屏" in response.reply
    assert _tool_names(screen) == ["describe_dataset", "plan_screen"]
    draft = screen.calls[1][1]["draft"]
    assert "frame" not in draft and "operations" not in draft
    assert screen.calls[1][1]["confirm"] is True
    assert screen.plan_calls == 1
    assert _step_ids(db) == [
        "bind",
        "describe_catalog",
        "draft_plan",
        "submit_plan",
        "reply_plan",
    ]


def test_t2_fills_an_empty_current_screen(db):
    screen = FakeScreen(components=[], revision=10)
    screen.next_plan = {
        "confirm": True,
        "draft": {
            "goal": "销售看板",
            "slots": [
                {
                    "title": "销售看板",
                    "role": "trend",
                    "measures": ["收入"],
                    "dimensions": ["月份"],
                    "chartType": "line",
                }
            ],
        },
    }

    response = _run_turn(
        db,
        screen,
        message="在这个大屏上生成一个销售看板",
        context=ScreenTurnContext(
            project_id="proj_1",
            screen_id="scr_1",
            screen_name="销售大屏",
            revision=10,
            component_count=0,
        ),
    )

    assert "方案" in response.reply
    assert _tool_names(screen) == ["describe_dataset", "plan_screen"]
    arguments = screen.calls[1][1]
    assert arguments["screenId"] == "scr_1"
    assert arguments["expectedRevision"] == 10
    assert "frame" not in arguments["draft"]
    assert "operations" not in arguments["draft"]
    assert screen.plan_calls == 1


def test_t3_edits_only_the_selected_component(db):
    screen = FakeScreen(
        components=[{"componentId": "kpi_3", "name": "合同总额", "type": "metric"}],
        revision=11,
    )
    screen.next_plan = {
        "change_kind": "update_component",
        "target_component_ids": ["kpi_3"],
        "patches": {"kpi_3": {"props": {"title": "合同总额"}}},
    }

    response = _run_turn(
        db,
        screen,
        message="把这个指标改成合同总额",
        context=ScreenTurnContext(
            project_id="proj_1",
            screen_id="scr_1",
            screen_name="销售大屏",
            revision=11,
            component_count=1,
            selected=[
                SelectedComponent(
                    component_id="kpi_3",
                    name="合同总额",
                    component_type="metric",
                )
            ],
        ),
    )

    assert "已完成修改" in response.reply
    assert "kpi_3" in response.reply
    assert screen.plan_calls == 1
    apply_args = screen.calls[1][1]
    assert apply_args["screenId"] == "scr_1"
    assert apply_args["expectedRevision"] == 11
    assert apply_args["operations"] == [
        {
            "op": "update_component",
            "componentId": "kpi_3",
            "changes": {"props": {"title": "合同总额"}},
        }
    ]
    assert screen.calls[0][1]["screenId"] == "scr_1"
    assert "includeDocument" not in screen.calls[0][1]
    stopped = db.exec(
        select(AgentEvent).where(
            AgentEvent.session_id == "sess-1",
            AgentEvent.event_type == "turn_stopped",
        )
    ).all()
    assert stopped and stopped[-1].payload_json["stop_reason"] == "completed"


def test_t4_asks_before_any_write_when_target_is_missing(db):
    screen = FakeScreen(
        components=[{"componentId": f"cmp_{index}", "name": f"组件{index}", "type": "text"} for index in range(5)],
        revision=11,
    )
    screen.next_plan = {}

    response = _run_turn(
        db,
        screen,
        message="把这个改一下",
        context=ScreenTurnContext(
            project_id="proj_1",
            screen_id="scr_1",
            screen_name="销售大屏",
            revision=11,
            component_count=5,
        ),
    )

    assert screen.calls == []
    assert screen.plan_calls == 0
    assert "你想改哪一个" in response.reply
    assert _step_ids(db) == ["bind"]
    stored = db.get(ChatSession, "sess-1")
    assert stored is not None
    assert stored.awaiting_input_json

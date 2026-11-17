from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core import harness_v2_engine as engine_module
from app.core.agent_loop import AgentLoop
from app.core.harness_v2_engine import _step_result, _workflow_frame_result
from app.core.task_request_compiler import CapabilityDescriptor, CapabilityManifest
from app.db.models import ChatSession, Skill
from app.session.session_schema import ChatTurnRequest, StepAgentResult
from app.session.turn_context import ScreenTurnContext
from app.workflows import (
    ExpressionScopes,
    UnresolvedExpression,
    WorkflowPack,
    WorkflowPackError,
    WorkflowRuntime,
    WorkflowRuntimeContext,
    render_template,
    resolve_arguments,
)

SKILL_ID = "screen_ops"
TASK_FRAME_ID = "task-1"


def _workflow_skill(
    *,
    runtime: str = "workflow_v1",
    with_workflow: bool = True,
    node_metadata: dict | None = None,
) -> Skill:
    metadata: dict = {}
    if with_workflow:
        metadata["workflow"] = {
            "runtime": runtime,
            "binder": "app.workflow_packs.screen.binder:bind",
            "tool_policy": {"conversation_allow": ["get_screen"]},
            "stop": {"max_llm_calls": 2, "max_tool_calls": 6},
        }
    return Skill(
        id="skill-screen",
        tenant_id="tenant-demo",
        skill_id=SKILL_ID,
        name="大屏岗位 SOP",
        status="published",
        content_json={
            "start_node_id": "resolve",
            "metadata": metadata,
            "nodes": [
                {
                    "node_id": "resolve",
                    "name": "读取大屏",
                    "instruction": "",
                    "capability_refs": {"tool_ids": ["get_screen"]},
                    "metadata": node_metadata
                    if node_metadata is not None
                    else {
                        "runtime_action": "tool",
                        "tool": "get_screen",
                        "arguments": {"screen_id": "$runtime.screen_id"},
                        "next_step_id": "say",
                    },
                },
                {
                    "node_id": "say",
                    "name": "回复",
                    "instruction": "",
                    "metadata": {
                        "runtime_action": "reply",
                        "template": "已读取大屏 $runtime.screen_name（revision=$out.resolve.data.revision）。",
                    },
                },
            ],
            "terminal_node_ids": ["say"],
        },
    )


def _manifest(*extra: CapabilityDescriptor) -> CapabilityManifest:
    get_screen = CapabilityDescriptor(
        capability_id="cap-get-screen",
        name="get_screen",
        kind="tool",
        input_schema={
            "type": "object",
            "required": ["screen_id"],
            "properties": {
                "screen_id": {"type": "string"},
                "revision": {"type": "integer"},
            },
        },
    )
    return CapabilityManifest(available=[get_screen, *extra])


def _descriptor(name: str, capability_id: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        name=name,
        kind="tool",
        input_schema={"type": "object", "properties": {}},
    )


class FakeInvoker:
    def __init__(self, outcome: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.outcome = outcome or {
            "success": True,
            "data": {"screen_id": "scr_456", "revision": 12},
        }

    def invoke(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, dict(arguments)))
        return self.outcome

    def read_tool_result_reference(self, reference: dict) -> dict:
        return {"document": {"screenId": "scr_456"}, "resolved_from": reference["kind"]}


class TraceRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, dict(payload)))

    def by_type(self, event_type: str) -> list[dict]:
        return [payload for name, payload in self.events if name == event_type]


def _turn_context() -> ScreenTurnContext:
    return ScreenTurnContext(
        schema_id="screen/v1",
        project_id="proj_123",
        screen_id="scr_456",
        screen_name="销售大屏",
        revision=11,
        component_count=5,
        selected=[
            {"component_id": "kpi_3", "name": "合同总额", "component_type": "metric"}
        ],
    )


def _runtime(
    skill: Skill,
    *,
    invoker: FakeInvoker | None = None,
    trace: TraceRecorder | None = None,
    turn_context: ScreenTurnContext | None = None,
    checkpoint: dict | None = None,
    allowed_tool_refs: set[str] | None = None,
    manifest: CapabilityManifest | None = None,
) -> WorkflowRuntime:
    return WorkflowRuntime(
        pack=WorkflowPack.from_skill(skill) or pytest.fail("workflow pack missing"),
        invoker=invoker or FakeInvoker(),
        manifest=manifest or _manifest(),
        trace=trace or TraceRecorder(),
        task_frame_id=TASK_FRAME_ID,
        skill_id=skill.skill_id,
        allowed_tool_refs=(
            allowed_tool_refs if allowed_tool_refs is not None else {"get_screen"}
        ),
        turn_context=turn_context or _turn_context(),
        checkpoint=checkpoint or {},
        slots={"topic": "销售"},
    )


def _node(skill: Skill, node_id: str) -> dict:
    return next(
        node
        for node in skill.content_json["nodes"]
        if node["node_id"] == node_id
    )


def _workflow_state(**overrides: object) -> dict:
    state: dict[str, object] = {
        "skill_id": SKILL_ID,
        "task_frame_id": TASK_FRAME_ID,
        "bind": {},
        "outputs": {},
        "llm_calls": 0,
        "tool_calls": 0,
    }
    state.update(overrides)
    return {"workflow_state": state}


def test_tool_node_executes_with_zero_llm_calls() -> None:
    invoker = FakeInvoker()
    trace = TraceRecorder()
    runtime = _runtime(_workflow_skill(), invoker=invoker, trace=trace)

    result = runtime.run_step(
        node_id="resolve",
        node=_node(_workflow_skill(), "resolve"),
    )

    assert result.status == "completed"
    assert result.next_step_id == "say"
    assert result.capability_results == [
        {
            "tool_name": "get_screen",
            "success": True,
            "data": {"screen_id": "scr_456", "revision": 12},
        }
    ]
    # 零 LLM：参数由 runtime 表达式解析，没有任何模型调用。
    assert runtime.context.llm_calls == 0
    assert runtime.context.tool_calls == 1
    assert invoker.calls == [("get_screen", {"screen_id": "scr_456"})]
    # 引擎侧的 StepAgentResult 映射（单轨：由 _step_result 派生）。
    step = _step_result(result)
    assert isinstance(step, StepAgentResult)
    assert step.action == "advance"
    assert step.is_step_completed is True
    assert step.next_step_id == "say"
    # 输出进入 workflow_state，供后续节点用 $out.resolve.* 引用。
    assert result.loop_checkpoint["workflow_state"]["outputs"]["resolve"]["data"][
        "revision"
    ] == 12
    started = trace.by_type("workflow_step_started")
    completed = trace.by_type("workflow_step_completed")
    assert started[0]["step_id"] == "resolve"
    assert started[0]["runtime_action"] == "tool"
    assert started[0]["llm_calls"] == 0
    assert started[0]["tool_calls"] == 0
    assert completed[0]["status"] == "completed"
    assert completed[0]["tool_calls"] == 1
    assert completed[0]["stop_reason"] == "completed"


def test_slot_updates_survive_the_engine_step_result_mapping() -> None:
    runtime = _runtime(_workflow_skill())
    result = runtime._result(
        status="completed",
        slot_updates={"screen": {"mode": "create"}},
    )

    assert result.slot_updates == {"screen": {"mode": "create"}}
    assert _step_result(result).slot_updates == {"screen": {"mode": "create"}}


def test_reply_node_renders_template_from_outputs() -> None:
    skill = _workflow_skill()
    checkpoint = _workflow_state(
        outputs={"resolve": {"status": "ok", "data": {"revision": 12}}},
        tool_calls=1,
    )
    runtime = _runtime(skill, checkpoint=checkpoint)

    result = runtime.run_step(node_id="say", node=_node(skill, "say"))

    assert result.status == "completed"
    assert result.reply_fragment == "已读取大屏 销售大屏（revision=12）。"
    assert _step_result(result).action == "advance"
    assert runtime.context.llm_calls == 0


def test_ask_node_returns_awaiting_user_with_declared_slots() -> None:
    skill = _workflow_skill(
        node_metadata={
            "runtime_action": "ask",
            "question": "要修改哪个组件？当前选中：$runtime.selected.0.name",
        }
    )
    skill.content_json["nodes"][0]["expected_user_info"] = ["target_component"]
    runtime = _runtime(skill)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "awaiting_user"
    assert result.reply_fragment == "要修改哪个组件？当前选中：合同总额"
    assert result.next_step_id is None
    # ask 的 slot_updates 必须为空：字段要等用户回答后再写。
    assert result.slot_updates == {}
    step = _step_result(result)
    assert step.action == "ask_user"
    assert step.is_step_completed is False


def test_ask_node_without_expected_user_info_fails() -> None:
    skill = _workflow_skill(
        node_metadata={"runtime_action": "ask", "question": "改哪个？"}
    )
    runtime = _runtime(skill)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "failed"
    assert result.error["code"] == "WORKFLOW_ASK_SLOTS_MISSING"
    # 未渲染问题：不能把无法持久化的停点抛给用户。
    assert result.reply_fragment.startswith("ask 节点")


def test_awaiting_input_persists_missing_fields_in_agent_loop() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        loop = AgentLoop(db)
        session = ChatSession(
            id="session-1",
            tenant_id="tenant-demo",
            active_skill_id=SKILL_ID,
            active_step_id="resolve",
        )
        skill = _workflow_skill()
        skill.content_json["nodes"][0]["expected_user_info"] = ["target_component"]
        step = StepAgentResult(
            action="ask_user",
            reply="要修改哪个组件？",
            next_step_id=None,
            is_step_completed=False,
        )

        loop._sync_awaiting_input_from_step_result(
            session,
            step,
            skill,
            source_skill_id=SKILL_ID,
            source_step_id="resolve",
        )

        awaiting = session.awaiting_input_json
        assert awaiting is not None
        assert awaiting["skill_id"] == SKILL_ID
        assert awaiting["step_id"] == "resolve"
        assert awaiting["expected_fields"] == ["target_component"]
        assert awaiting["question_summary"] == "要修改哪个组件？"
        assert session.last_agent_question == "要修改哪个组件？"


def test_unknown_runtime_action_fails_closed() -> None:
    skill = _workflow_skill(
        node_metadata={"runtime_action": "teleport", "tool": "get_screen"}
    )
    trace = TraceRecorder()
    runtime = _runtime(skill, trace=trace)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "failed"
    assert result.error["code"] == "WORKFLOW_ACTION_UNKNOWN"
    assert trace.by_type("workflow_step_completed")[0]["stop_reason"] == "failed"


def test_unknown_bind_and_structured_handlers_fail_closed() -> None:
    cases = [
        (
            {"runtime_action": "bind", "binder": "missing/binder"},
            "WORKFLOW_BINDER_UNKNOWN",
        ),
        (
            {"runtime_action": "structured_llm", "handler": "missing/handler"},
            "WORKFLOW_HANDLER_UNKNOWN",
        ),
    ]
    for node_metadata, expected_code in cases:
        skill = _workflow_skill(node_metadata=node_metadata)
        runtime = _runtime(skill)
        result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))
        assert result.status == "failed"
        assert result.error["code"] == expected_code


def test_tool_result_sandbox_reference_is_resolved_before_outputs() -> None:
    invoker = FakeInvoker(
        {
            "success": True,
            "data": {"kind": "sandbox_json_file", "sandbox_path": "/workspace/x.json"},
        }
    )
    runtime = _runtime(_workflow_skill(), invoker=invoker)

    result = runtime.run_step(
        node_id="resolve",
        node=_node(_workflow_skill(), "resolve"),
    )

    assert result.status == "completed"
    stored = result.loop_checkpoint["workflow_state"]["outputs"]["resolve"]["data"]
    assert stored["document"] == {"screenId": "scr_456"}
    assert stored["resolved_from"] == "sandbox_json_file"


def test_node_tool_refs_narrow_the_descriptor_surface() -> None:
    manifest = _manifest(
        _descriptor("apply_screen_operations", "cap-apply"),
    )
    refs = {"get_screen"}
    invoker = FakeInvoker()

    allowed = _runtime(
        _workflow_skill(),
        invoker=invoker,
        allowed_tool_refs=refs,
        manifest=manifest,
    )
    ok = allowed.run_step(
        node_id="resolve",
        node=_node(_workflow_skill(), "resolve"),
    )
    assert ok.status == "completed"
    assert invoker.calls == [("get_screen", {"screen_id": "scr_456"})]

    skill = _workflow_skill(
        node_metadata={
            "runtime_action": "tool",
            "tool": "apply_screen_operations",
            "arguments": {"screenId": "scr_456"},
        }
    )
    denied = _runtime(
        skill,
        invoker=invoker,
        allowed_tool_refs=refs,
        manifest=manifest,
    )
    blocked = denied.run_step(node_id="resolve", node=_node(skill, "resolve"))
    assert blocked.status == "failed"
    assert blocked.error["code"] == "TOOL_NOT_ALLOWED"
    assert "未在当前 SOP 节点声明" in blocked.error["message"]
    # 被拒绝的工具从未到达 invoker。
    assert [name for name, _ in invoker.calls] == ["get_screen"]


def test_tool_refs_match_by_capability_id_and_source_tool_name() -> None:
    sourced = CapabilityDescriptor(
        capability_id="cap-row-7",
        name="mcp_get_screen",
        kind="tool",
        metadata={"source_tool_name": "get_screen"},
        input_schema={"type": "object", "required": ["screen_id"]},
    )
    runtime = _runtime(
        _workflow_skill(),
        manifest=CapabilityManifest(available=[sourced]),
        allowed_tool_refs={"cap-row-7", "get_screen"},
    )
    # 节点声明的是 mcp_get_screen（descriptor.name），三种引用方式都放行。
    result = runtime.run_step(
        node_id="resolve",
        node=_node(_workflow_skill(), "resolve"),
    )
    assert result.status == "completed"


def test_empty_tool_refs_block_every_tool() -> None:
    runtime = _runtime(_workflow_skill(), allowed_tool_refs=set())

    result = runtime.run_step(
        node_id="resolve",
        node=_node(_workflow_skill(), "resolve"),
    )

    assert result.status == "failed"
    assert result.error["code"] == "TOOL_NOT_ALLOWED"


def test_unresolved_expression_fails_the_step() -> None:
    skill = _workflow_skill(
        node_metadata={
            "runtime_action": "tool",
            "tool": "get_screen",
            "arguments": {"screen_id": "$runtime.screen_id", "extra": "$out.missing.data"},
        }
    )
    runtime = _runtime(skill)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "failed"
    assert result.error["code"] == "EXPRESSION_UNRESOLVED"
    assert "$out.missing.data" in result.error["message"]


def test_missing_required_tool_argument_is_invalid() -> None:
    skill = _workflow_skill(
        node_metadata={
            "runtime_action": "tool",
            "tool": "get_screen",
            "arguments": {"revision": "$runtime.revision"},
        }
    )
    invoker = FakeInvoker()
    runtime = _runtime(skill, invoker=invoker)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "failed"
    assert result.error["code"] == "TOOL_ARGUMENTS_INVALID"
    assert "screen_id" in result.error["message"]
    assert invoker.calls == []


def test_basic_type_mismatch_is_invalid() -> None:
    skill = _workflow_skill(
        node_metadata={
            "runtime_action": "tool",
            "tool": "get_screen",
            "arguments": {"screen_id": "$runtime.revision"},
        }
    )
    runtime = _runtime(skill)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "failed"
    assert result.error["code"] == "TOOL_ARGUMENTS_INVALID"


def test_tool_failure_propagates_error_code() -> None:
    skill = _workflow_skill()
    invoker = FakeInvoker(
        outcome={"success": False, "error": {"code": "MCP_TIMEOUT", "message": "上游超时"}}
    )
    runtime = _runtime(skill, invoker=invoker)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "failed"
    assert result.error["code"] == "MCP_TIMEOUT"


def test_stop_policy_budget_blocks_tool_step() -> None:
    skill = _workflow_skill()
    checkpoint = _workflow_state(tool_calls=6)
    invoker = FakeInvoker()
    runtime = _runtime(skill, invoker=invoker, checkpoint=checkpoint)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "failed"
    assert result.error["code"] == "WORKFLOW_BUDGET_EXCEEDED"
    assert result.error["stop_reason"] == "budget"
    assert invoker.calls == []


# ---------------------------------------------------------------- F5 scoping


def test_workflow_state_is_scoped_to_skill_and_frame() -> None:
    first = WorkflowRuntimeContext(
        skill_id="screen_ops",
        task_frame_id=TASK_FRAME_ID,
        bind={"targets": ["kpi_3"]},
        outputs={"resolve": {"status": "ok", "data": {}}},
    )
    checkpoint = {"workflow_state": first.to_checkpoint()}

    same = WorkflowRuntimeContext.from_checkpoint(
        checkpoint,
        skill_id="screen_ops",
        task_frame_id=TASK_FRAME_ID,
    )
    assert same.bind == {"targets": ["kpi_3"]}
    assert "resolve" in same.outputs

    other_skill = WorkflowRuntimeContext.from_checkpoint(
        checkpoint,
        skill_id="report_ops",
        task_frame_id=TASK_FRAME_ID,
    )
    assert other_skill.bind == {}
    assert other_skill.outputs == {}

    other_frame = WorkflowRuntimeContext.from_checkpoint(
        checkpoint,
        skill_id="screen_ops",
        task_frame_id="task-2",
    )
    assert other_frame.bind == {}
    assert other_frame.outputs == {}

    # 旧 checkpoint（无 skill_id/task_frame_id 键）按空状态加载，不报错。
    legacy = WorkflowRuntimeContext.from_checkpoint(
        {
            "workflow_state": {
                "bind": {"targets": ["kpi_3"]},
                "outputs": {},
                "llm_calls": 0,
                "tool_calls": 3,
            }
        },
        skill_id="screen_ops",
        task_frame_id=TASK_FRAME_ID,
    )
    assert legacy.bind == {}
    assert legacy.tool_calls == 0


def test_runtime_checkpoint_roundtrip_keeps_scope_keys() -> None:
    runtime = _runtime(_workflow_skill())
    result = runtime.run_step(
        node_id="resolve",
        node=_node(_workflow_skill(), "resolve"),
    )

    state = result.loop_checkpoint["workflow_state"]
    assert state["skill_id"] == SKILL_ID
    assert state["task_frame_id"] == TASK_FRAME_ID

    resumed = WorkflowRuntimeContext.from_checkpoint(
        result.loop_checkpoint,
        skill_id=SKILL_ID,
        task_frame_id=TASK_FRAME_ID,
    )
    assert "resolve" in resumed.outputs


# ---------------------------------------------------------------- expressions


def test_expressions_resolve_all_four_scopes() -> None:
    scopes = ExpressionScopes(
        runtime={"screen_id": "scr_1"},
        slot={"topic": "销售"},
        bind={"targets": ["kpi_3"]},
        out={"apply": {"data": {"ok": True}}},
    )

    assert resolve_arguments(
        {
            "screen_id": "$runtime.screen_id",
            "topic": "$slot.topic",
            "targets": "$bind.targets",
            "ok": "$out.apply.data.ok",
            "note": "改 $slot.topic 的 $runtime.screen_id",
            "count": 3,
        },
        scopes,
    ) == {
        "screen_id": "scr_1",
        "topic": "销售",
        "targets": ["kpi_3"],
        "ok": True,
        "note": "改 销售 的 scr_1",
        "count": 3,
    }
    assert render_template("$runtime.screen_id/$slot.topic", scopes) == "scr_1/销售"


def test_expressions_raise_on_missing_paths() -> None:
    scopes = ExpressionScopes(runtime={"screen_id": "scr_1"})

    with pytest.raises(UnresolvedExpression):
        resolve_arguments({"screen_id": "$runtime.screen_name"}, scopes)
    with pytest.raises(UnresolvedExpression):
        render_template("读取 $out.resolve.data", scopes)
    # 未知作用域前缀不解析、也不当作表达式静默吞掉。
    with pytest.raises(UnresolvedExpression):
        ExpressionScopes().resolve_token("$warp.screen_id")


# ---------------------------------------------------------------- pack layer


def test_pack_without_workflow_metadata_returns_none() -> None:
    skill = _workflow_skill(with_workflow=False)

    assert WorkflowPack.from_skill(skill) is None


def test_pack_with_unknown_runtime_raises() -> None:
    skill = _workflow_skill(runtime="workflow_v2")

    with pytest.raises(WorkflowPackError):
        WorkflowPack.from_skill(skill)


def test_pack_exposes_policy_fields() -> None:
    pack = WorkflowPack.from_skill(_workflow_skill())

    assert pack.runtime == "workflow_v1"
    assert pack.binder_ref == "app.workflow_packs.screen.binder:bind"
    assert pack.tool_policy.conversation_allow == ["get_screen"]
    assert pack.stop_policy.max_llm_calls == 2
    assert pack.stop_policy.max_tool_calls == 6


# ------------------------------------------------------- engine integration


def _frame(kind: str = "sop", target_step_id: str = "resolve"):
    return SimpleNamespace(kind=kind, target_step_id=target_step_id)


def _request() -> ChatTurnRequest:
    return ChatTurnRequest(
        tenant_id="tenant-demo",
        message="读取当前大屏",
        screen_context=_turn_context(),
    )


def _session() -> ChatSession:
    return ChatSession(id="session-1", tenant_id="tenant-demo")


def _enable_workflow_runtime(monkeypatch, enabled: bool = True) -> None:
    monkeypatch.setattr(
        engine_module,
        "get_settings",
        lambda: SimpleNamespace(workflow_runtime_enabled=enabled),
    )


def _engine_result(monkeypatch, skill: Skill, *, enabled: bool = True):
    _enable_workflow_runtime(monkeypatch, enabled)
    invoker = FakeInvoker()
    trace = TraceRecorder()
    result = _workflow_frame_result(
        frame=_frame(),
        active_skill=skill,
        session=_session(),
        request=_request(),
        requirement=SimpleNamespace(task_frame_id=TASK_FRAME_ID),
        manifest=_manifest(),
        invoker=invoker,
        trace=trace,
        checkpoint={},
        slots={},
    )
    return result, invoker, trace


def test_engine_branch_runs_workflow_tool_node(monkeypatch) -> None:
    result, invoker, trace = _engine_result(monkeypatch, _workflow_skill())

    assert result is not None
    assert result.status == "completed"
    assert result.capability_results[0]["tool_name"] == "get_screen"
    assert invoker.calls == [("get_screen", {"screen_id": "scr_456"})]
    assert len(trace.by_type("workflow_step_started")) == 1


def test_engine_branch_blocks_undeclared_tools(monkeypatch) -> None:
    # 节点没有 capability_refs：引擎算出空 tool_ids → 运行时 fail closed。
    skill = _workflow_skill()
    del skill.content_json["nodes"][0]["capability_refs"]

    result, invoker, _ = _engine_result(monkeypatch, skill)

    assert result is not None
    assert result.status == "failed"
    assert result.error["code"] == "TOOL_NOT_ALLOWED"
    assert invoker.calls == []


def test_engine_branch_accepts_legacy_call_tool_refs(monkeypatch) -> None:
    skill = _workflow_skill()
    del skill.content_json["nodes"][0]["capability_refs"]
    skill.content_json["nodes"][0]["allowed_actions"] = ["call_tool:get_screen"]

    result, invoker, _ = _engine_result(monkeypatch, skill)

    assert result is not None
    assert result.status == "completed"
    assert invoker.calls == [("get_screen", {"screen_id": "scr_456"})]


def test_engine_branch_returns_none_for_skill_without_workflow_metadata(
    monkeypatch,
) -> None:
    result, _, _ = _engine_result(monkeypatch, _workflow_skill(with_workflow=False))

    assert result is None


def test_engine_branch_disabled_by_kill_switch(monkeypatch) -> None:
    result, _, _ = _engine_result(
        monkeypatch, _workflow_skill(), enabled=False
    )

    assert result is None


def test_engine_branch_ignores_conversation_frames(monkeypatch) -> None:
    _enable_workflow_runtime(monkeypatch)

    result = _workflow_frame_result(
        frame=_frame(kind="conversation"),
        active_skill=_workflow_skill(),
        session=_session(),
        request=_request(),
        requirement=SimpleNamespace(task_frame_id=TASK_FRAME_ID),
        manifest=_manifest(),
        invoker=FakeInvoker(),
        trace=TraceRecorder(),
        checkpoint={},
        slots={},
    )

    assert result is None


def test_engine_branch_fails_closed_on_invalid_pack(monkeypatch) -> None:
    result, _, trace = _engine_result(
        monkeypatch, _workflow_skill(runtime="workflow_v3")
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error["code"] == "WORKFLOW_PACK_INVALID"
    assert trace.by_type("workflow_pack_invalid")


def test_engine_branch_skips_nodes_without_runtime_action(monkeypatch) -> None:
    skill = _workflow_skill(with_workflow=True)
    skill.content_json["nodes"][0]["metadata"] = {}

    result, _, _ = _engine_result(monkeypatch, skill)

    assert result is None


def test_structured_step_awaiting_carries_slot_updates() -> None:
    from app.workflows.registry import STRUCTURED_STEPS, StructuredStepError

    def handler(**_kwargs):
        raise StructuredStepError(
            "FIELD_MAPPING_MISSING",
            "用哪个字段做分类、哪个做数值？",
            ask_fields=["field_mapping"],
            slot_updates={
                "pending_edit": {"target_component_id": "cmp_1", "component_type": "pie"}
            },
        )

    STRUCTURED_STEPS["g4_test_asker"] = handler
    try:
        skill = _workflow_skill(
            node_metadata={"runtime_action": "structured_llm", "handler": "g4_test_asker"}
        )
        runtime = WorkflowRuntime(
            pack=WorkflowPack.from_skill(skill) or pytest.fail("workflow pack missing"),
            invoker=FakeInvoker(),
            manifest=_manifest(),
            trace=TraceRecorder(),
            task_frame_id=TASK_FRAME_ID,
            skill_id=skill.skill_id,
            allowed_tool_refs=set(),
            turn_context=_turn_context(),
            checkpoint={},
            slots={},
            llm_generate=lambda _system, _payload: {},
        )

        result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))
    finally:
        STRUCTURED_STEPS.pop("g4_test_asker", None)

    assert result.status == "awaiting_user"
    # 随追问携带的结构化载荷必须能持久化到下一轮（agent_loop 按 slot_updates 合并）。
    assert result.slot_updates == {
        "pending_edit": {"target_component_id": "cmp_1", "component_type": "pie"}
    }


def test_reply_node_persists_declared_slot_updates() -> None:
    skill = _workflow_skill(
        node_metadata={
            "runtime_action": "reply",
            "template": "已完成。",
            "slot_updates": {"pending_edit": None},
        }
    )
    runtime = _runtime(skill)

    result = runtime.run_step(node_id="resolve", node=_node(skill, "resolve"))

    assert result.status == "completed"
    assert result.reply_fragment == "已完成。"
    # 执行成功后清空 pending_edit（设计 §6.3）。
    assert result.slot_updates == {"pending_edit": None}

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.core import turn_planner as turn_planner_module
from app.core.turn_planner import TurnPlanner
from app.db.models import ChatSession, ModelConfig, Skill
from app.session.session_schema import ChatTurnRequest, TurnPlan
from app.session.turn_context import ScreenTurnContext, SelectedComponent


def _request(**overrides: object) -> ChatTurnRequest:
    values: dict[str, object] = {
        "tenant_id": "tenant-demo",
        "message": "把这个指标改成合同总额",
    }
    values.update(overrides)
    return ChatTurnRequest(**values)


def _screen_context(**overrides: object) -> ScreenTurnContext:
    values: dict[str, object] = {
        "project_id": "proj_123",
        "screen_id": "scr_456",
        "screen_name": "销售大屏",
        "revision": 12,
        "component_count": 5,
        "selected": [
            {
                "component_id": "kpi_3",
                "name": "合同总额",
                "component_type": "metric",
            }
        ],
        "editor_dirty": False,
    }
    values.update(overrides)
    return ScreenTurnContext(**values)


def test_legacy_request_without_screen_context_still_parses() -> None:
    request = _request()

    assert request.screen_context is None
    # exclude=True: server-only fields must not leak into serialized payloads
    # that older clients or background jobs may replay.
    dumped = request.model_dump()
    assert "screen_context" not in dumped


def test_valid_screen_context_parses() -> None:
    request = _request(screen_context=_screen_context())
    context = request.screen_context

    assert context is not None
    assert context.schema_id == "screen/v1"
    assert context.screen_id == "scr_456"
    assert context.revision == 12
    assert context.component_count == 5
    assert context.selected == [
        SelectedComponent(
            component_id="kpi_3",
            name="合同总额",
            component_type="metric",
        )
    ]
    assert "screen_context" not in request.model_dump()


def test_screen_context_accepts_an_optional_clarification_answer() -> None:
    context = _screen_context(
        clarification_answer={
            "planId": "plan_1",
            "questionId": "q_focus",
            "optionIds": ["focus.avg_progress"],
            "text": "",
            "labels": ["平均进度"],
            "datasetVersionIds": ["ver_1"],
            "draftPatch": {
                "sources": ["ver_1"],
                "slots": [{"title": "平均进度", "role": "metric"}],
            },
            "suggestedOptionIds": [],
        }
    )

    assert context.clarification_answer is not None
    assert context.clarification_answer.planId == "plan_1"
    assert context.clarification_answer.draftPatch["sources"] == ["ver_1"]
    assert _screen_context().clarification_answer is None


def test_unknown_schema_id_is_rejected() -> None:
    # The API boundary maps this ValidationError to HTTP 422; an unknown
    # schema must fail closed instead of being silently ignored.
    with pytest.raises(ValidationError):
        ScreenTurnContext.model_validate(
            {
                "schema_id": "screen/v2",
                "screen_id": "scr_456",
            }
        )
    with pytest.raises(ValidationError):
        _request(
            screen_context={"schema_id": "screen/v9", "screen_id": "scr_456"}
        )


def _chat_session() -> ChatSession:
    return ChatSession(id="session-1", tenant_id="tenant-demo")


def _workflow_skill() -> Skill:
    return Skill(
        id="skill-screen",
        tenant_id="tenant-demo",
        skill_id="screen_ops",
        name="大屏岗位 SOP",
        status="published",
        content_json={
            "start_node_id": "bind",
            "user_utterance_examples": ["帮我做一个销售大屏"],
            "goal": ["按选中组件完成大屏修改"],
            "required_info": ["screen_id", "target"],
            "metadata": {
                "workflow": {
                    "runtime": "workflow_v1",
                    "binder": "app.workflow_packs.screen.binder:bind",
                    "tool_policy": {
                        "conversation_allow": ["get_screen", "describe_dataset"]
                    },
                    "stop": {"max_llm_calls": 2, "max_tool_calls": 6},
                }
            },
            "nodes": [
                {
                    "node_id": "bind",
                    "name": "绑定目标",
                    "metadata": {"runtime_action": "bind"},
                },
                {
                    "node_id": "reply",
                    "name": "回复",
                    "metadata": {"runtime_action": "reply"},
                },
            ],
            "terminal_node_ids": ["reply"],
        },
    )


def _model_config() -> ModelConfig:
    return ModelConfig(
        id="model-test",
        tenant_id="tenant-demo",
        name="测试模型",
        api_key_encrypted="test",
        model="test-model",
    )


def _plan_with_fake_llm(monkeypatch, available_skills, turn_context):
    payloads: list[dict[str, object]] = []

    class FakeLLMClient:
        def __init__(self, _model_config: ModelConfig):
            pass

        def generate_json(
            self, _system_prompt: str, payload: dict[str, object]
        ) -> dict[str, object]:
            payloads.append(deepcopy(payload))
            return {
                "decision": "answer_only",
                "user_intent": "修改指标",
                "task_frames": [
                    {
                        "kind": "conversation",
                        "decision": "answer_only",
                        "requirements": ["回复用户"],
                    }
                ],
            }

    monkeypatch.setattr(turn_planner_module, "LLMClient", FakeLLMClient)
    plan = TurnPlanner().plan(
        "把这个指标改成合同总额",
        _chat_session(),
        available_skills=available_skills,
        model_config=_model_config(),
        turn_context=turn_context,
    )
    return payloads, plan


def test_planner_payload_contains_turn_context_summary_without_props(
    monkeypatch,
) -> None:
    payloads, plan = _plan_with_fake_llm(
        monkeypatch,
        available_skills=[],
        turn_context=_screen_context(),
    )

    assert plan.decision == "answer_only"
    summary = payloads[0]["turn_context"]
    assert summary == {
        "screen_name": "销售大屏",
        "revision": 12,
        "component_count": 5,
        "selected": [
            {
                "component_id": "kpi_3",
                "name": "合同总额",
                "component_type": "metric",
            }
        ],
    }
    # 组件 props/frame/style 等快照负载绝不进入路由提示词。
    assert "props" not in str(summary)
    assert "frame" not in str(summary)
    assert "style" not in str(summary)
    assert "screen_id" not in summary


def test_planner_payload_has_no_turn_context_key_when_absent(monkeypatch) -> None:
    payloads, _ = _plan_with_fake_llm(
        monkeypatch,
        available_skills=[],
        turn_context=None,
    )

    assert "turn_context" not in payloads[0]


def test_sop_payload_exposes_examples_goal_required_info_and_workflow(
    monkeypatch,
) -> None:
    payloads, _ = _plan_with_fake_llm(
        monkeypatch,
        available_skills=[_workflow_skill()],
        turn_context=None,
    )

    sop = payloads[0]["available_sops"][0]
    assert sop["skill_id"] == "screen_ops"
    assert sop["user_utterance_examples"] == ["帮我做一个销售大屏"]
    assert sop["goal"] == ["按选中组件完成大屏修改"]
    assert sop["required_info"] == ["screen_id", "target"]
    # 运行时只暴露一个标记字符串；binder 模块路径、tool_policy、stop
    # 预算绝不进入路由提示词。
    assert sop["workflow"] == "workflow_v1"
    assert "metadata" not in sop
    serialized = str(sop)
    assert "binder" not in serialized
    assert "tool_policy" not in serialized
    assert "max_tool_calls" not in serialized


def test_sop_payload_without_metadata_stays_backward_compatible(monkeypatch) -> None:
    skill = _workflow_skill()
    skill.content_json = {
        "start_node_id": "bind",
        "nodes": [{"node_id": "bind", "name": "绑定目标"}],
        "terminal_node_ids": ["bind"],
    }
    payloads, _ = _plan_with_fake_llm(
        monkeypatch,
        available_skills=[skill],
        turn_context=None,
    )

    sop = payloads[0]["available_sops"][0]
    assert "metadata" not in sop
    assert "workflow" not in sop
    assert "user_utterance_examples" not in sop


def test_planner_prompt_documents_structured_turn_context() -> None:
    from app.core.turn_planner import PROMPT_PATH

    text = PROMPT_PATH.read_text(encoding="utf-8")
    # turn_context 语义必须写进提示词，planner 才能正确对待编辑器状态。
    assert "结构化回合上下文" in text
    assert "turn_context" in text
    assert "component_count" in text
    assert "selected" in text


def test_turn_plan_accepts_non_writing_screen_intent() -> None:
    plan = TurnPlan.model_validate(
        {
            "decision": "answer_only",
            "screen_intent": {
                "kind": "query",
                "confidence": 0.92,
                "target": "current",
                "dataset": "erp_projects_active",
                "metrics": ["合同总额"],
                "missing": [],
                "ambiguities": [],
            },
        }
    )
    assert plan.screen_intent is not None
    assert plan.screen_intent.kind == "query"
    assert plan.screen_intent.confidence == 0.92


def test_planner_summary_includes_plan_state(monkeypatch) -> None:
    payloads, _ = _plan_with_fake_llm(
        monkeypatch,
        available_skills=[],
        turn_context=_screen_context(
            blank=False,
            plan_id="plan_1",
            plan_status="needs_confirmation",
        ),
    )

    summary = payloads[0]["turn_context"]
    assert summary["plan_id"] == "plan_1"
    assert summary["plan_status"] == "needs_confirmation"
    assert summary["blank"] is False

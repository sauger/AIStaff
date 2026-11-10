"""原生工具调用接入 Harness 动作循环的回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import harness_agent as harness_agent_module
from app.core.harness_agent import HarnessTaskAgent
from app.core.task_request_compiler import (
    CapabilityDescriptor,
    CapabilityManifest,
    TaskRequirement,
)
from app.db.models import ModelConfig
from app.llm.client import ToolCallRecord, ToolCallingUnsupported


def _model_config() -> ModelConfig:
    return ModelConfig(
        id="model-test",
        tenant_id="tenant-demo",
        name="测试模型",
        api_key_encrypted="test",
        model="test-model",
    )


def _requirement(*descriptors: CapabilityDescriptor) -> TaskRequirement:
    return TaskRequirement(
        task_frame_id="task-tools",
        kind="conversation",
        goal="查询平台账单",
        requirements=["列出平台并汇总账单"],
        capability_manifest=CapabilityManifest(available=list(descriptors)),
    )


def _descriptor(name: str, *, read_only: bool = True) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=name.replace(".", "_"),
        name=name,
        kind="tool",
        read_only=read_only,
        metadata={"method": "GET"} if read_only else {},
    )


def _call(name: str, arguments: dict[str, object] | None = None) -> ToolCallRecord:
    return ToolCallRecord(
        id="call-1",
        name=name,
        arguments="{}",
        parsed_arguments=dict(arguments or {}),
    )


def _enable_native_protocol(monkeypatch) -> None:
    settings = SimpleNamespace(
        agent_action_protocol="tool_calls",
        agent_tools_max_description_chars=300,
        agent_tools_schema_budget_chars=2000,
    )
    monkeypatch.setattr(harness_agent_module, "get_settings", lambda: settings)


def test_harness_agent_executes_native_tool_calls_and_finish(monkeypatch) -> None:
    _enable_native_protocol(monkeypatch)
    system_prompts: list[str] = []
    responses = iter(
        [
            [_call("tokenop__list_platform_organizations")],
            [_call("finish", {"reply": "共 2 个平台。", "status": "completed"})],
        ]
    )

    class FakeLLMClient:
        def __init__(self, _model_config: ModelConfig):
            pass

        def generate_tool_calls(self, system_prompt, _payload, *, tools, tool_choice):
            system_prompts.append(system_prompt)
            assert tool_choice == "auto"
            assert any(
                item["function"]["name"] == "finish" for item in tools
            ), "finish must be exposed as a native tool"
            return next(responses)

        def generate_json(self, _system_prompt, _payload):  # pragma: no cover - must not run
            raise AssertionError("JSON action loop must not be used when tools succeed")

    monkeypatch.setattr(harness_agent_module, "LLMClient", FakeLLMClient)
    invoked: list[tuple[str, dict[str, object]]] = []
    trace_events: list[str] = []

    def invoke_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        invoked.append((name, arguments))
        return {"success": True, "data": {"platforms": ["a", "b"]}}

    result = HarnessTaskAgent().run(
        _requirement(_descriptor("tokenop.list_platform_organizations")),
        _model_config(),
        invoke_tool,
        max_actions=4,
        trace_sink=lambda event_type, _payload: trace_events.append(event_type),
    )

    assert result.status == "completed"
    assert result.reply_fragment == "共 2 个平台。"
    assert invoked == [("tokenop.list_platform_organizations", {})]
    assert "原生工具调用协议" in system_prompts[0]
    assert "harness_tool_protocol_enabled" in trace_events


def test_harness_agent_runs_a_read_only_batch_then_finishes(monkeypatch) -> None:
    _enable_native_protocol(monkeypatch)
    responses = iter(
        [
            [
                _call("tokenop__list_platform_organizations"),
                _call("tokenop__get_organization_account", {"organization_id": "org-1"}),
            ],
            [_call("finish", {"reply": "批量完成。"})],
        ]
    )

    class FakeLLMClient:
        def __init__(self, _model_config: ModelConfig):
            pass

        def generate_tool_calls(self, _system_prompt, _payload, *, tools, tool_choice):
            return next(responses)

        def generate_json(self, _system_prompt, _payload):  # pragma: no cover
            raise AssertionError("batch must be executed without a JSON repair call")

    monkeypatch.setattr(harness_agent_module, "LLMClient", FakeLLMClient)
    invoked: list[str] = []

    def invoke_tool(name: str, _arguments: dict[str, object]) -> dict[str, object]:
        invoked.append(name)
        return {"success": True, "data": {"ok": True}}

    result = HarnessTaskAgent().run(
        _requirement(
            _descriptor("tokenop.list_platform_organizations"),
            _descriptor("tokenop.get_organization_account"),
        ),
        _model_config(),
        invoke_tool,
        max_actions=4,
    )

    assert result.status == "completed"
    assert invoked == [
        "tokenop.list_platform_organizations",
        "tokenop.get_organization_account",
    ]
    assert result.action_count == 3


def test_harness_agent_falls_back_to_json_when_tools_unsupported(monkeypatch) -> None:
    _enable_native_protocol(monkeypatch)
    class FakeLLMClient:
        def __init__(self, _model_config: ModelConfig):
            pass

        def generate_tool_calls(self, _system_prompt, _payload, *, tools, tool_choice):
            raise ToolCallingUnsupported("provider does not support tools")

        def generate_json(self, _system_prompt, _payload):
            return {
                "action": "finish",
                "status": "completed",
                "reply_fragment": "回落到 JSON 协议。",
            }

    monkeypatch.setattr(harness_agent_module, "LLMClient", FakeLLMClient)
    trace_events: list[str] = []

    result = HarnessTaskAgent().run(
        _requirement(_descriptor("tokenop.list_platform_organizations")),
        _model_config(),
        lambda _name, _arguments: {"success": True, "data": {}},
        max_actions=3,
        trace_sink=lambda event_type, _payload: trace_events.append(event_type),
    )

    assert result.status == "completed"
    assert result.reply_fragment == "回落到 JSON 协议。"
    assert "harness_tool_protocol_fallback" in trace_events



def test_harness_tool_specs_full_surface_covers_catalog_capabilities(monkeypatch) -> None:
    _enable_native_protocol(monkeypatch)
    frame_tool = _descriptor("tokenop.list_platform_organizations")
    catalog_tool = _descriptor("tokenop.get_platform_consumption_bill")
    discovery = _descriptor("capability_search")
    requirement = _requirement(frame_tool, discovery)
    full_manifest = CapabilityManifest(available=[frame_tool, catalog_tool, discovery])

    tools, names = harness_agent_module._harness_tool_specs(requirement, full_manifest)
    tool_names = [item["function"]["name"] for item in tools]

    assert "tokenop__get_platform_consumption_bill" in tool_names
    assert "capability_search" not in tool_names
    assert names["tokenop__get_platform_consumption_bill"] == (
        "tokenop.get_platform_consumption_bill"
    )
    assert "finish" in tool_names

    frame_tools, _ = harness_agent_module._harness_tool_specs(requirement)
    frame_names = [item["function"]["name"] for item in frame_tools]
    assert "tokenop__get_platform_consumption_bill" not in frame_names
    assert "capability_search" in frame_names


def test_harness_agent_skips_tools_when_protocol_is_json(monkeypatch) -> None:
    settings = SimpleNamespace(
        agent_action_protocol="json",
        agent_tools_max_description_chars=300,
        agent_tools_schema_budget_chars=2000,
    )
    monkeypatch.setattr(harness_agent_module, "get_settings", lambda: settings)

    class FakeLLMClient:
        def __init__(self, _model_config: ModelConfig):
            pass

        def generate_tool_calls(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("tools must stay disabled when protocol=json")

        def generate_json(self, _system_prompt, _payload):
            return {"action": "finish", "status": "completed", "reply_fragment": "JSON 协议。"}

    monkeypatch.setattr(harness_agent_module, "LLMClient", FakeLLMClient)

    result = HarnessTaskAgent().run(
        _requirement(_descriptor("tokenop.list_platform_organizations")),
        _model_config(),
        lambda _name, _arguments: {"success": True, "data": {}},
        max_actions=2,
    )

    assert result.status == "completed"
    assert result.reply_fragment == "JSON 协议。"


@pytest.mark.parametrize(
    ("calls", "names", "expected"),
    [
        (
            [_call("finish", {"reply": "好"})],
            {},
            {"actions": [{"action": "finish", "status": "completed", "reply_fragment": "好"}]},
        ),
        (
            [_call("tokenop__list", {"page": 1})],
            {"tokenop__list": "tokenop.list"},
            {"actions": [{"action": "tool", "tool_name": "tokenop.list", "arguments": {"page": 1}}]},
        ),
    ],
)
def test_actions_from_tool_calls_shape(calls, names, expected) -> None:
    assert harness_agent_module._actions_from_tool_calls(calls, names) == expected

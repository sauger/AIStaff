from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from app.core import plan_compiler as plan_compiler_module
from app.core.plan_compiler import (
    ExecutionPlan,
    PlanAnswer,
    PlanCompiler,
    PlanNode,
    build_compiled_tools,
    validate_plan,
)
from app.core.plan_executor import (
    NodeOutcome,
    PlanExecutor,
    ReferenceError,
    resolve_refs,
    synthesize_reply,
)
from app.core.plan_synthesizer import PlanSynthesizer
from app.core.task_request_compiler import (
    CapabilityDescriptor,
    CapabilityManifest,
    TaskRequirement,
)

ALLOWED = {"tokenop.get_platform_consumption_bill", "tokenop.list_platform_organizations"}


def _requirement(**overrides) -> TaskRequirement:
    payload = {
        "task_frame_id": "tf_plan",
        "kind": "conversation",
        "goal": "查上个月各平台消费账单",
        "current_time": "2026-11-11T04:20:00+08:00",
        "source_user_message": "查上个月各平台消费账单",
        "known_slots": {"month": "2026-10"},
    }
    payload.update(overrides)
    return TaskRequirement(**payload)


def _plan(**overrides) -> ExecutionPlan:
    payload = {
        "nodes": [
            PlanNode(id="n1", tool="tokenop.list_platform_organizations", args={}),
            PlanNode(
                id="n2",
                tool="tokenop.get_platform_consumption_bill",
                args={"org_id": "${n1.data.items[0].id}", "month": "${goal.month}"},
                deps=["n1"],
            ),
        ],
        "answer": {"mode": "text"},
        "reply": "上月账单：${n2.data.summary}",
    }
    payload.update(overrides)
    return ExecutionPlan(**payload)


@pytest.fixture(autouse=True)
def _no_spans(monkeypatch):
    monkeypatch.setattr(plan_compiler_module, "llm_operation", lambda *a, **k: contextlib.nullcontext())


def test_validate_plan_accepts_a_small_dag():
    assert validate_plan(_plan(), allowed_tools=ALLOWED, max_nodes=12) == []


def test_validate_plan_rejects_unknown_tool():
    plan = _plan(nodes=[PlanNode(id="n1", tool="tokenop.nope")], reply="x")
    errors = validate_plan(plan, allowed_tools=ALLOWED, max_nodes=12)
    assert any("工具不可用" in item for item in errors)


def test_validate_plan_rejects_duplicate_ids_and_bad_deps():
    plan = _plan(
        nodes=[
            PlanNode(id="n1", tool="tokenop.list_platform_organizations"),
            PlanNode(id="n1", tool="tokenop.list_platform_organizations"),
        ],
        reply="x",
    )
    errors = validate_plan(plan, allowed_tools=ALLOWED, max_nodes=12)
    assert any("重复" in item for item in errors)

    plan = _plan(
        nodes=[
            PlanNode(id="n1", tool="tokenop.list_platform_organizations", deps=["n2"]),
            PlanNode(id="n2", tool="tokenop.list_platform_organizations"),
        ],
        reply="x",
    )
    errors = validate_plan(plan, allowed_tools=ALLOWED, max_nodes=12)
    assert any("未定义或后置" in item for item in errors)


def test_validate_plan_rejects_too_many_nodes_and_missing_reply():
    nodes = [
        PlanNode(id=f"n{index}", tool="tokenop.list_platform_organizations")
        for index in range(3)
    ]
    plan = _plan(nodes=nodes, reply="")
    errors = validate_plan(plan, allowed_tools=ALLOWED, max_nodes=2)
    assert any("超过上限" in item for item in errors)
    assert any("reply" in item for item in errors)


def test_validate_plan_rejects_unknown_reference_roots():
    plan = _plan(
        nodes=[
            PlanNode(
                id="n1",
                tool="tokenop.list_platform_organizations",
                args={"org": "${n9.data.id}"},
            )
        ],
        reply="${n9.data.id}",
    )
    errors = validate_plan(plan, allowed_tools=ALLOWED, max_nodes=12)
    assert sum("未知节点" in item for item in errors) == 2


def test_build_compiled_tools_skips_write_and_discovery_capabilities():
    manifest = CapabilityManifest(
        available=[
            CapabilityDescriptor(
                capability_id="c1",
                name="tokenop.list_platform_organizations",
                kind="tool",
                available=True,
                read_only=True,
                metadata={"side_effect": "read"},
                description="列出平台",
                input_schema={"type": "object", "properties": {"page": {"type": "integer"}}},
            ),
            CapabilityDescriptor(
                capability_id="c2",
                name="write_file",
                kind="file",
                available=True,
                read_only=False,
                metadata={"side_effect": "write"},
            ),
            CapabilityDescriptor(
                capability_id="c3",
                name="capability_describe",
                kind="internal",
                available=True,
                read_only=True,
            ),
        ]
    )
    tools = build_compiled_tools(manifest)
    assert [item["name"] for item in tools] == ["tokenop.list_platform_organizations"]
    assert tools[0]["parameters"]["properties"]["page"]["type"] == "integer"


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_json(self, system_prompt, user_payload, cancellation=None):
        self.calls.append(user_payload)
        return self._responses.pop(0)


def test_plan_compiler_repairs_once_then_returns_the_plan():
    client = _FakeClient(
        [
            {"nodes": [{"id": "n1", "tool": "tokenop.nope"}], "reply": "x"},
            {
                "nodes": [
                    {
                        "id": "n1",
                        "tool": "tokenop.list_platform_organizations",
                        "args": {},
                    }
                ],
                "reply": "共 ${n1.data.total} 个平台",
            },
        ]
    )
    compiler = PlanCompiler(client_factory=lambda model_config: client, max_nodes=4)
    decision = compiler.compile(
        requirement=_requirement(),
        model_config=object(),
        tools=[{"name": "tokenop.list_platform_organizations"}],
    )
    assert decision.plan is not None
    assert decision.repair_used is True
    assert "repair" in client.calls[1]


def test_plan_compiler_gives_up_after_two_invalid_plans():
    client = _FakeClient([{"nodes": [], "reply": ""}, {"nodes": [], "reply": ""}])
    compiler = PlanCompiler(client_factory=lambda model_config: client, max_nodes=4)
    decision = compiler.compile(
        requirement=_requirement(),
        model_config=object(),
        tools=[{"name": "tokenop.list_platform_organizations"}],
    )
    assert decision.plan is None
    assert decision.error["code"] == "PLAN_INVALID"


def test_resolve_refs_handles_goal_nodes_and_lists():
    scope = {"goal": {"month": "2026-10"}, "n1": {"data": {"items": [{"id": "7"}]}}}
    assert resolve_refs("${goal.month}", scope) == "2026-10"
    assert resolve_refs({"org": "${n1.data.items[0].id}"}, scope) == {"org": "7"}
    assert resolve_refs("本月 ${goal.month} 账单", scope) == "本月 2026-10 账单"
    with pytest.raises(ReferenceError):
        resolve_refs("${n9.data.id}", scope)


def test_executor_runs_nodes_in_order_and_substitutes_arguments():
    invocations = []

    def invoke(name, arguments):
        invocations.append((name, arguments))
        if name == "tokenop.list_platform_organizations":
            return {"success": True, "data": {"items": [{"id": "7"}]}}
        return {"success": True, "data": {"summary": "6 分"}}

    executor = PlanExecutor(invoke, node_timeout=60)
    run = executor.run(_plan(), {"goal": {"month": "2026-10"}})
    assert [outcome.status for outcome in run.outcomes] == ["ok", "ok"]
    assert invocations[1][1] == {"org_id": "7", "month": "2026-10"}
    assert synthesize_reply(_plan(), run.outcomes) == "上月账单：6 分"


def test_executor_skips_downstream_nodes_after_a_failure():
    invocations = []

    def invoke(name, arguments):
        invocations.append(name)
        return {"success": False, "error": {"code": "BOOM", "message": "失败"}}

    executor = PlanExecutor(invoke, node_timeout=60)
    run = executor.run(_plan(), {"goal": {}})
    assert [outcome.status for outcome in run.outcomes] == ["failed", "skipped"]
    assert invocations == ["tokenop.list_platform_organizations"]


def test_executor_stops_when_budget_is_gone():
    def invoke(name, arguments):
        raise AssertionError("should not be called")

    executor = PlanExecutor(invoke, budget_deadline=0.0)
    run = executor.run(_plan(), {"goal": {}})
    assert {outcome.status for outcome in run.outcomes} == {"skipped"}


def test_validate_plan_rejects_javascript_expressions_in_reply():
    plan = _plan(
        reply="${n1.data.items.map(item => item.id).join(',')}",
    )
    errors = validate_plan(plan, allowed_tools=ALLOWED, max_nodes=12)
    assert any("表达式" in item for item in errors)


def test_synthesize_reply_renders_a_list_of_dicts_as_a_table():
    plan = _plan(nodes=[PlanNode(id="n1", tool="tokenop.list_platform_organizations")], reply="${n1.data.items}")
    outcomes = [
        NodeOutcome(
            node_id="n1",
            tool="tokenop.list_platform_organizations",
            status="ok",
            result={"success": True, "data": {"items": [{"id": "7", "name": "甲"}, {"id": "8", "name": "乙"}]}},
        )
    ]
    reply = synthesize_reply(plan, outcomes)
    assert "| id | name |" in reply
    assert "| 7 | 甲 |" in reply


def test_executor_materializes_sandbox_json_file_references():
    reference = {
        "kind": "sandbox_json_file",
        "sandbox_path": "/workspace/.harness/tool-results/hcall_x.json",
        "size": 12,
        "sha256": "0" * 64,
    }
    calls: list[tuple[str, dict]] = []

    def invoke(name, args):
        calls.append((name, args))
        if name == "tokenop.list_platform_organizations":
            return {"success": True, "data": reference}
        return {"success": True, "data": {"ok": True}}

    def read_reference(ref):
        assert ref["kind"] == "sandbox_json_file"
        return {"items": [{"id": "7", "name": "甲"}]}

    plan = _plan(
        nodes=[
            PlanNode(id="n1", tool="tokenop.list_platform_organizations"),
            PlanNode(
                id="n2",
                tool="tokenop.get_platform_consumption_bill",
                deps=["n1"],
                args={"org_id": "${n1.data.items[0].id}"},
            ),
        ],
        reply="ok ${n2.data.ok}",
    )
    run = PlanExecutor(invoke, read_reference=read_reference).run(plan, {"goal": {}})
    assert [item.status for item in run.outcomes] == ["ok", "ok"]
    assert calls[1][1] == {"org_id": "7"}
    assert run.outcomes[0].result["data"]["items"][0]["name"] == "甲"


def test_synthesizer_builds_reply_from_outcomes():
    class _Client:
        def __init__(self, model_config):
            self.payload = None

        def generate_json(self, system_prompt, payload, cancellation=None):
            self.payload = payload
            return {"reply": "合计 301.81 元。"}

    synthesizer = PlanSynthesizer(client_factory=_Client)
    requirement = SimpleNamespace(
        task_frame_id="tf1",
        source_user_message="查上个月账单",
        known_slots={"user_request": "查上个月账单"},
        requirements=["报表"],
    )
    outcomes = [
        NodeOutcome(
            node_id="n1",
            tool="tokenop.get_platform_consumption_bill",
            status="ok",
            result={"success": True, "data": {"summary": {"amount_fen": 30181}}},
        )
    ]
    reply = synthesizer.synthesize(
        requirement=requirement,
        model_config=object(),
        outcomes=outcomes,
    )
    assert reply == "合计 301.81 元。"


def test_validate_plan_allows_empty_reply_in_synthesize_mode():
    plan = _plan(reply="", answer=PlanAnswer(mode="synthesize"))
    errors = validate_plan(plan, allowed_tools=ALLOWED, max_nodes=12)
    assert errors == []

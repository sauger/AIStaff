"""Constructor for the screen position SOP (workflow_v1).

The graph keeps the edit spine and adds a plan spine:

    bind ─┬─ describe_catalog ─> draft_plan ─> submit_plan ─> reply_plan
          └─ resolve ─> plan ─> apply ─> get_doc_updated ─> verify_updated ─> reply_updated

Whole-screen generation uses the plan spine; the model cannot create a
template screen or confirm a plan. A selected component still uses resolve.
"""

from __future__ import annotations

from typing import Any

SCREEN_SKILL_ID = "screen.change"

_WORKFLOW_METADATA: dict[str, Any] = {
    "workflow": {
        "runtime": "workflow_v1",
        "binder": "screen",
        "tool_policy": {
            "conversation_allow": [
                "get_screen",
                "describe_dataset",
                "list_component_types",
                "knowledge_search",
            ]
        },
        "stop": {"max_llm_calls": 2, "max_tool_calls": 6},
    }
}


def _node(
    node_id: str,
    name: str,
    *,
    runtime_action: str | None = None,
    instruction: str = "",
    tool_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_id": node_id,
        "type": "action",
        "name": name,
        "instruction": instruction,
    }
    if tool_refs:
        node["capability_refs"] = {"tool_ids": list(tool_refs)}
    if metadata:
        node["metadata"] = metadata
    if runtime_action:
        node.setdefault("metadata", {})["runtime_action"] = runtime_action
    return node


def screen_sop_content(
    *,
    skill_id: str = SCREEN_SKILL_ID,
    template: str = "sales",
) -> dict[str, Any]:
    _ = skill_id
    return {
        "start_node_id": "bind",
        "trigger_intents": [
            "新建大屏",
            "生成经营大屏",
            "在大屏上补一个组件",
            "改组件文案",
            "改组件样式",
            "换图表",
            "换指标",
            "调整组件布局",
            "删除组件",
        ],
        "user_utterance_examples": [
            "帮我做一个销售经营大屏",
            "在这个大屏上生成一个销售看板",
            "把这个指标改成合同总额",
            "把这个改一下",
        ],
        "goal": ["按结构化回合上下文完成大屏的创建、补齐与定向修改"],
        "required_info": ["screen_id", "target_component"],
        "metadata": _WORKFLOW_METADATA,
        "nodes": [
            _node(
                "bind",
                "绑定目标",
                runtime_action="bind",
                metadata={"binder": "screen", "template": template, "exclusive_next": True},
            ),
            _node(
                "resolve",
                "读取大屏",
                runtime_action="tool",
                tool_refs=["get_screen"],
                metadata={
                    "tool": "get_screen",
                    "arguments": {"screenId": "$bind.screen_id"},
                    "next_step_id": "plan",
                },
            ),
            _node(
                "plan",
                "生成变更计划",
                runtime_action="structured_llm",
                instruction="按用户原话与选中组件生成最小变更计划。",
                metadata={"handler": "plan_change", "next_step_id": "apply"},
            ),
            _node(
                "apply",
                "提交操作批次",
                runtime_action="tool",
                tool_refs=["apply_screen_operations"],
                metadata={
                    "tool": "apply_screen_operations",
                    "arguments": {
                        "requestId": "$bind.request_id",
                        "screenId": "$bind.screen_id",
                        "expectedRevision": "$out.resolve.data.revision",
                        "operations": "$out.plan.data.operations",
                    },
                    "next_step_id": "get_doc_updated",
                },
            ),
            _node(
                "get_doc_updated",
                "读取修改后文档",
                runtime_action="tool",
                tool_refs=["get_screen"],
                metadata={
                    "tool": "get_screen",
                    "arguments": {
                        "screenId": "$bind.screen_id",
                        "includeDocument": True,
                    },
                    "next_step_id": "verify_updated",
                },
            ),
            _node(
                "verify_updated",
                "校验修改结果",
                runtime_action="tool",
                tool_refs=["validate_screen"],
                metadata={
                    "tool": "validate_screen",
                    "arguments": {"document": "$out.get_doc_updated.data.document"},
                    "next_step_id": "reply_updated",
                },
            ),
            _node(
                "reply_updated",
                "回复修改结果",
                runtime_action="reply",
                metadata={
                    "template": (
                        "已完成修改：变更组件 $out.apply.data.changedComponentIds，"
                        "当前 revision=$out.get_doc_updated.data.revision，"
                        "校验通过=$out.verify_updated.data.valid。"
                    )
                },
            ),
            _node(
                "describe_catalog",
                "读取数据目录",
                runtime_action="tool",
                tool_refs=["describe_dataset"],
                metadata={
                    "tool": "describe_dataset",
                    "arguments": {},
                    "next_step_id": "draft_plan",
                },
            ),
            _node(
                "draft_plan",
                "起草大屏方案",
                runtime_action="structured_llm",
                instruction="只填 ScreenPlanDraft。禁止输出 frame、x、y、document、operations。",
                metadata={"handler": "draft_screen_plan", "next_step_id": "submit_plan"},
            ),
            _node(
                "submit_plan",
                "提交大屏方案",
                runtime_action="tool",
                tool_refs=["plan_screen"],
                metadata={
                    "tool": "plan_screen",
                    "arguments": {
                        "requestId": "$bind.request_id",
                        "planId": "$bind.plan_id",
                        "sessionId": "$bind.session_id",
                        "screenId": "$bind.screen_id",
                        "projectId": "$bind.project_id",
                        "expectedRevision": "$bind.revision",
                        "placementHint": "$bind.placement_hint",
                        "draft": "$out.draft_plan.data.draft",
                    },
                    "next_step_id": "reply_plan",
                },
            ),
            _node(
                "reply_plan",
                "回复方案",
                runtime_action="reply",
                metadata={
                    "template": (
                        "方案「$out.submit_plan.data.goal」"
                        "当前是$out.submit_plan.data.status。"
                        "$out.submit_plan.data.summary"
                    )
                },
            ),
        ],
        "edges": [
            {"source_node_id": "bind", "next_node_id": "resolve"},
            {"source_node_id": "resolve", "next_node_id": "plan"},
            {"source_node_id": "plan", "next_node_id": "apply"},
            {"source_node_id": "apply", "next_node_id": "get_doc_updated"},
            {"source_node_id": "get_doc_updated", "next_node_id": "verify_updated"},
            {"source_node_id": "verify_updated", "next_node_id": "reply_updated"},
            {"source_node_id": "bind", "next_node_id": "describe_catalog"},
            {"source_node_id": "describe_catalog", "next_node_id": "draft_plan"},
            {"source_node_id": "draft_plan", "next_node_id": "submit_plan"},
            {"source_node_id": "submit_plan", "next_node_id": "reply_plan"},
        ],
        "terminal_node_ids": ["reply_updated", "reply_plan"],
    }


def build_screen_sop(*, template: str = "sales") -> dict[str, Any]:
    """Return a validated SkillCard dict for the screen SOP (not published)."""

    from app.skills.skill_schema import SkillCard

    content = screen_sop_content(template=template)
    card = SkillCard.model_validate(
        {
            **content,
            "skill_id": SCREEN_SKILL_ID,
            "name": "大屏变更",
            "version": "1",
            "business_domain": "大屏",
            "description": "按岗位 SOP 完成大屏创建、空白屏补齐与单组件修改",
            "capability_scope": "general",
            "step_timeout_seconds": 45,
        }
    )
    return card.model_dump(mode="json")

"""Constructor for the screen position SOP (workflow_v1).

The graph keeps the edit spine and adds two plan spines:

    bind ─┬─ describe_catalog ─> draft_plan ─> submit_plan ─> compose ─> reply_plan
          ├─ submit_plan_resume ─> compose_resume ─> reply_plan_resume
          └─ resolve ─> plan ─> apply ─> get_doc_updated ─> verify_updated ─> reply_updated

Whole-screen generation plans first and only the user confirms on the
PlanCard. A structured clarification answer resumes the same planId with a
merged draft. The model cannot create a template screen or confirm a plan.
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
                "get_screen_plan",
                "revise_screen_plan",
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
    expected_user_info: list[str] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_id": node_id,
        "type": "action",
        "name": name,
        "instruction": instruction,
    }
    if tool_refs:
        node["capability_refs"] = {"tool_ids": list(tool_refs)}
    if expected_user_info:
        node["expected_user_info"] = list(expected_user_info)
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
                # Bound-target edit spine: same read, then the field catalog of
                # the target's dataset feeds plan_change (design §6.2). The
                # binder only routes here when the target's datasetVersionId is
                # known (selection binding summary or a pending_edit).
                "resolve_bound",
                "读取大屏（带字段目录）",
                runtime_action="tool",
                tool_refs=["get_screen"],
                metadata={
                    "tool": "get_screen",
                    "arguments": {"screenId": "$bind.screen_id"},
                    "next_step_id": "describe_fields",
                },
            ),
            _node(
                "describe_fields",
                "读取目标数据集字段目录",
                runtime_action="tool",
                tool_refs=["describe_dataset"],
                metadata={
                    "tool": "describe_dataset",
                    "arguments": {
                        "datasetVersionId": "$bind.target_dataset_version_id"
                    },
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
                        "expectedRevision": "$bind.revision",
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
                    ),
                    # 执行成功后清空 pending_edit（设计 §6.3）。
                    "slot_updates": {"pending_edit": None},
                },
            ),
            _node(
                "get_current_plan",
                "读取当前方案",
                runtime_action="tool",
                tool_refs=["get_screen_plan"],
                metadata={
                    "tool": "get_screen_plan",
                    "arguments": {
                        "planId": "$bind.plan_id",
                        "taskId": "$bind.task_id",
                    },
                    "next_step_id": "describe_catalog_for_revise",
                },
            ),
            _node(
                "describe_catalog_for_revise",
                "读取修订用目录",
                runtime_action="tool",
                tool_refs=["describe_dataset"],
                metadata={
                    "tool": "describe_dataset",
                    "arguments": {},
                    "next_step_id": "draft_intent",
                },
            ),
            _node(
                "draft_intent",
                "理解有限修改意图",
                runtime_action="structured_llm",
                instruction=(
                    "只输出受限 ScreenDraftIntent，不输出完整 draft、fieldId、document、frame 或屏幕操作。"
                    "操作只有 add_slots/remove_slots/replace_slots/set_title/set_sources；"
                    "字段必须来自当前目录，无法匹配时写 unresolved，不要发明字段。"
                ),
                metadata={"handler": "screen_draft_intent", "next_step_id": "revise_plan"},
            ),
            _node(
                "revise_plan",
                "提交方案修订",
                runtime_action="tool",
                tool_refs=["revise_screen_plan"],
                metadata={
                    "tool": "revise_screen_plan",
                    "arguments": {
                        "planId": "$bind.plan_id",
                        "taskId": "$bind.task_id",
                        "requestId": "$bind.request_id",
                        "baseDraftHash": "$out.get_current_plan.data.draftHash",
                        "intent": "$out.draft_intent.data.intent",
                        "retryOnStale": True,
                    },
                    "next_step_id": "compose_revise_reply",
                },
            ),
            _node(
                "compose_revise_reply",
                "组织修订回复",
                runtime_action="structured_llm",
                instruction=(
                    "只根据 revise_screen_plan 结果组织回复：ready/needs_confirmation 说方案已更新、"
                    "请确认后再创建并填充；needs_input 只问第一个问题或请在卡片选择；"
                    "不得声称已写屏或已确认。"
                ),
                metadata={
                    "handler": "plan_reply",
                    "submit_step": "revise_plan",
                    "reply_kind": "revise",
                    "next_step_id": "reply_revise",
                },
            ),
            _node(
                "reply_revise",
                "回复修订结果",
                runtime_action="reply",
                metadata={
                    "template": "$out.compose_revise_reply.data.reply",
                    "slot_updates": {
                        "plan_draft": None,
                        "plan_clarification": "$out.compose_revise_reply.data.clarification",
                        "clarification_plan_id": "$out.compose_revise_reply.data.plan_id",
                        "clarification_task_id": "$bind.task_id",
                        "clarification_followup": None,
                        "clarification_stale": False,
                    },
                },
            ),
            _node(
                "query_dataset",
                "回答只读数据问题",
                runtime_action="tool",
                tool_refs=["describe_dataset"],
                metadata={
                    "tool": "describe_dataset",
                    "arguments": {
                        "includeSummary": True,
                        "planId": "$bind.plan_id",
                        "taskId": "$bind.task_id",
                    },
                    "next_step_id": "compose_query_reply",
                },
            ),
            _node(
                "compose_query_reply",
                "组织只读回答",
                runtime_action="structured_llm",
                instruction="只根据 describe_dataset 的 summary/rowCount 回答，不返回原始行，不改方案或任务。",
                metadata={"handler": "screen_query_reply", "next_step_id": "reply_query"},
            ),
            _node(
                "reply_query",
                "回复只读问题",
                runtime_action="reply",
                metadata={"template": "$out.compose_query_reply.data.reply"},
            ),
            _node(
                "present_plan",
                "读取当前方案",
                runtime_action="tool",
                tool_refs=["get_screen_plan"],
                metadata={
                    "tool": "get_screen_plan",
                    "arguments": {
                        "planId": "$bind.plan_id",
                        "taskId": "$bind.task_id",
                    },
                    "next_step_id": "present_catalog",
                },
            ),
            _node(
                "present_catalog",
                "读取数据目录",
                runtime_action="tool",
                tool_refs=["describe_dataset"],
                metadata={
                    "tool": "describe_dataset",
                    "arguments": {},
                    "next_step_id": "compose_plan_reply_present",
                },
            ),
            _node(
                "compose_plan_reply_present",
                "组织默认方案回复",
                runtime_action="structured_llm",
                instruction=(
                    "只根据 get_screen_plan 的当前方案和 describe_dataset 的目录组织回复："
                    "列出可用字段和建议组件，说明可以回复「直接做」确认这版方案，"
                    "也可以直接说改哪个组件/图。不得声称已经写屏或已经确认。"
                ),
                metadata={
                    "handler": "plan_reply",
                    "reply_kind": "present",
                    "source_step": "present_plan",
                    "next_step_id": "reply_present",
                },
            ),
            _node(
                "reply_present",
                "回复默认方案",
                runtime_action="reply",
                metadata={
                    "template": "$out.compose_plan_reply_present.data.reply",
                    "slot_updates": {
                        "plan_draft": "$out.present_plan.data.draft",
                        "plan_clarification": "$out.compose_plan_reply_present.data.clarification",
                        "clarification_plan_id": "$out.present_plan.data.planId",
                        "clarification_task_id": "$bind.task_id",
                        "clarification_followup": "$out.present_plan.data.followUp",
                        "clarification_stale": False,
                    },
                },
            ),
            _node(
                "create_blank",
                "创建空白画布",
                runtime_action="tool",
                tool_refs=["create_screen"],
                metadata={
                    "tool": "create_screen",
                    "arguments": {
                        "requestId": "$bind.request_id",
                        "mode": "new",
                        "template": "blank",
                        "name": "$bind.name",
                        "taskId": "$bind.task_id",
                    },
                    "next_step_id": "ask_catalog",
                },
            ),
            _node(
                "ask_catalog",
                "读取数据目录",
                runtime_action="tool",
                tool_refs=["describe_dataset"],
                metadata={
                    "tool": "describe_dataset",
                    "arguments": {},
                    "next_step_id": "ask_data",
                },
            ),
            _node(
                "ask_data",
                "问数据",
                runtime_action="reply",
                instruction=(
                    "只问数据，不猜字段；用户跳过时按真实目录的默认数据集出方案。"
                ),
                metadata={
                    "template": (
                        "我帮你先建了一张空白大屏（$out.create_blank.data.name）。"
                        "空白只是画布，数据和组件我来放。"
                        "这个大屏用哪份数据？直接说数据集名称，我从目录里接。"
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
                instruction=(
                    "只填 ScreenPlanDraft（goal/screenTitle/sources/slots/assumptions/unmatched）。"
                    "禁止输出 frame、x、y、document、operations、clarification 等额外字段。"
                    "宽泛请求只写 goal；服务端会从真实目录补默认模板，不猜指标、数据集或选项。"
                    "只有用户明说「标题是/名称为/叫……」时才写 screenTitle；"
                    "不要把整句 goal 当标题。"
                    "指标写 slots（role/measures/dimensions/chartType）；"
                    "只有用户说平均/最大/最小/合计时才写 measureAggregations，未说不要指定。"
                    "「使用已有数据/现有数据」不要写进 sources，由服务端目录匹配；"
                    "只有用户明确指定数据集时才写 sources。"
                    "当前 ERP 目录没有合同实体，禁止发明合同数量，也不要把合同额当数量。"
                    "目录为空时不编数据。"
                ),
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
                        "taskId": "$bind.task_id",
                        "planId": "$bind.plan_id",
                        "sessionId": "$bind.session_id",
                        "screenId": "$bind.screen_id",
                        "projectId": "$bind.project_id",
                        "expectedRevision": "$bind.revision",
                        "placementHint": "$bind.placement_hint",
                        "draft": "$out.draft_plan.data.draft",
                    },
                    "next_step_id": "compose_plan_reply",
                },
            ),
            _node(
                "compose_plan_reply",
                "组织方案回复",
                runtime_action="structured_llm",
                instruction=(
                    "只根据 plan_screen 结果组织回复：ready/needs_confirmation 说"
                    "方案已生成、请确认后再创建并填充；needs_input 带 clarification 时"
                    "只说请在方案卡片中选择；纯文本 questions 只问第一个问题。"
                    "不得声称大屏已经生成。"
                ),
                metadata={
                    "handler": "plan_reply",
                    "submit_step": "submit_plan",
                    "next_step_id": "reply_plan",
                },
            ),
            _node(
                "reply_plan",
                "回复方案",
                runtime_action="reply",
                metadata={
                    "template": "$out.compose_plan_reply.data.reply",
                    # 方案结果留在会话里，供下一轮澄清续用同一 planId。
                    "slot_updates": {
                        "plan_draft": "$out.draft_plan.data.draft",
                        "plan_clarification": "$out.compose_plan_reply.data.clarification",
                        "clarification_plan_id": "$out.compose_plan_reply.data.plan_id",
                        "clarification_task_id": "$bind.task_id",
                        "clarification_followup": None,
                        "clarification_stale": False,
                    },
                },
            ),
            _node(
                "submit_plan_resume",
                "按澄清续提交方案",
                runtime_action="tool",
                tool_refs=["plan_screen"],
                metadata={
                    "tool": "plan_screen",
                    "arguments": {
                        "requestId": "$bind.request_id",
                        "taskId": "$bind.task_id",
                        "planId": "$bind.plan_id",
                        "sessionId": "$bind.session_id",
                        "screenId": "$bind.screen_id",
                        "projectId": "$bind.project_id",
                        "expectedRevision": "$bind.revision",
                        "placementHint": "$bind.placement_hint",
                        "draft": "$bind.draft",
                        "baseDraftHash": "$bind.clarification_base_hash",
                    },
                    "next_step_id": "compose_plan_reply_resume",
                },
            ),
            _node(
                "compose_plan_reply_resume",
                "组织澄清后续回复",
                runtime_action="structured_llm",
                instruction=(
                    "只根据 plan_screen 结果组织回复：ready/needs_confirmation 说"
                    "方案已生成、请确认后再创建并填充；needs_input 带新的 clarification 时"
                    "只说请在方案卡片中选择；纯文本 questions 只问第一个问题。"
                    "不得声称大屏已经生成，也不要重试旧选项。"
                ),
                metadata={
                    "handler": "plan_reply",
                    "submit_step": "submit_plan_resume",
                    "next_step_id": "reply_plan_resume",
                },
            ),
            _node(
                "reply_plan_resume",
                "回复续提交结果",
                runtime_action="reply",
                metadata={
                    "template": "$out.compose_plan_reply_resume.data.reply",
                    "slot_updates": {
                        "plan_draft": "$bind.draft",
                        "plan_clarification": "$out.compose_plan_reply_resume.data.clarification",
                        "clarification_plan_id": "$out.compose_plan_reply_resume.data.plan_id",
                        "clarification_task_id": "$bind.task_id",
                        "clarification_followup": None,
                        "clarification_stale": False,
                    },
                },
            ),
        ],
        "edges": [
            {"source_node_id": "bind", "next_node_id": "resolve"},
            {"source_node_id": "bind", "next_node_id": "resolve_bound"},
            {"source_node_id": "resolve_bound", "next_node_id": "describe_fields"},
            {"source_node_id": "describe_fields", "next_node_id": "plan"},
            {"source_node_id": "resolve", "next_node_id": "plan"},
            {"source_node_id": "plan", "next_node_id": "apply"},
            {"source_node_id": "apply", "next_node_id": "get_doc_updated"},
            {"source_node_id": "get_doc_updated", "next_node_id": "verify_updated"},
            {"source_node_id": "verify_updated", "next_node_id": "reply_updated"},
            {"source_node_id": "bind", "next_node_id": "query_dataset"},
            {"source_node_id": "query_dataset", "next_node_id": "compose_query_reply"},
            {"source_node_id": "compose_query_reply", "next_node_id": "reply_query"},
            {"source_node_id": "bind", "next_node_id": "present_plan"},
            {"source_node_id": "present_plan", "next_node_id": "present_catalog"},
            {"source_node_id": "present_catalog", "next_node_id": "compose_plan_reply_present"},
            {"source_node_id": "compose_plan_reply_present", "next_node_id": "reply_present"},
            {"source_node_id": "bind", "next_node_id": "get_current_plan"},
            {"source_node_id": "get_current_plan", "next_node_id": "describe_catalog_for_revise"},
            {"source_node_id": "describe_catalog_for_revise", "next_node_id": "draft_intent"},
            {"source_node_id": "draft_intent", "next_node_id": "revise_plan"},
            {"source_node_id": "revise_plan", "next_node_id": "compose_revise_reply"},
            {"source_node_id": "compose_revise_reply", "next_node_id": "reply_revise"},
            {"source_node_id": "bind", "next_node_id": "describe_catalog"},
            {"source_node_id": "bind", "next_node_id": "create_blank"},
            {"source_node_id": "create_blank", "next_node_id": "ask_catalog"},
            {"source_node_id": "ask_catalog", "next_node_id": "ask_data"},
            {"source_node_id": "describe_catalog", "next_node_id": "draft_plan"},
            {"source_node_id": "draft_plan", "next_node_id": "submit_plan"},
            {"source_node_id": "submit_plan", "next_node_id": "compose_plan_reply"},
            {"source_node_id": "compose_plan_reply", "next_node_id": "reply_plan"},
            {"source_node_id": "bind", "next_node_id": "submit_plan_resume"},
            {"source_node_id": "submit_plan_resume", "next_node_id": "compose_plan_reply_resume"},
            {"source_node_id": "compose_plan_reply_resume", "next_node_id": "reply_plan_resume"},
        ],
        "terminal_node_ids": [
            "reply_updated",
            "reply_plan",
            "reply_plan_resume",
            "reply_revise",
            "reply_query",
            "reply_present",
            "ask_data",
        ],
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
            "version": "2.2.0",
            "business_domain": "大屏",
            "description": "按岗位 SOP 完成大屏方案、澄清续答、空白屏补齐与单组件修改",
            "capability_scope": "general",
            "step_timeout_seconds": 45,
        }
    )
    return card.model_dump(mode="json")

from __future__ import annotations

import copy
import json
from typing import Any

from app import paths
from app.text import single_line

UNIFIED_PROMPT_PATH = (
    paths.resource_dir() / "app" / "llm" / "prompts" / "unified_agent_prompt.md"
)
STAGE_PROTOCOL_KEY = "_agent_stage"
TURN_STAGE_MESSAGES_KEY = "_agent_turn_messages"


ROUTER_OUTPUT_SCHEMA: dict[str, Any] = {
    "decision": "continue_active | switch_to_pending | create_pending | update_pending | complete_task | start_new_task | answer_only | handoff_human | clarify",
    "selected_task_id": "string?",
    "target_skill_id": "string?",
    "target_step_id": "string?",
    "confidence": "number",
    "user_intent": "string?",
    "general_intent": "string?",
    "reason": "string?",
    "clarification_question": "string?",
    "slot_hints": "object?",
    "task_frames": [
        {
            "task_id": "string?",
            "status": "pending?",
            "decision": "start_new_task | continue_active?",
            "target_skill_id": "string",
            "target_step_id": "string?",
            "user_intent": "string?",
            "slot_hints": "object?",
        }
    ],
    "pending_tasks": [
        {
            "task_id": "string?",
            "status": "pending?",
            "decision": "start_new_task | continue_active?",
            "target_skill_id": "string?",
            "target_step_id": "string?",
            "confidence": "number?",
            "user_intent": "string?",
            "reason": "string?",
            "slot_hints": "object?",
        }
    ],
    "task_updates": [
        {
            "task_id": "string",
            "status": "string?",
            "target_skill_id": "string?",
            "target_step_id": "string?",
            "user_intent": "string?",
            "reason": "string?",
            "slot_hints": "object?",
            "remove": "boolean?",
        }
    ],
}

TURN_PLANNER_OUTPUT_SCHEMA: dict[str, Any] = {
    "decision": "continue_active | switch_to_pending | complete_task | start_new_task | answer_only | handoff_human | clarify",
    "selected_task_id": "string?",
    "confidence": "number",
    "user_intent": "string?",
    "reason": "string?",
    "clarification_question": "string?",
    "task_frames": [
        {
            "task_id": "string?",
            "kind": "sop | conversation",
            "decision": "continue_active | switch_to_pending | start_new_task | answer_only | clarify",
            "target_skill_id": "string?",
            "target_step_id": "string?",
            "user_intent": "string?",
            "requirements": ["string"],
            "slot_hints": "object (use {} when empty; never null)",
            "depends_on_task_ids": ["string"],
            "execution_target": "self | team_member",
            "assignee_agent_id": "exact agent_id from stage_data.team_context.members?",
            "activation_condition": "object (use {} when empty; never null)",
        }
    ],
    "task_updates": [
        {
            "task_id": "string",
            "status": "string?",
            "target_skill_id": "string?",
            "target_step_id": "string?",
            "user_intent": "string?",
            "slot_hints": "object (use {} when empty; never null)",
            "remove": "boolean?",
        }
    ],
}

STEP_AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "action": "ask_user | clarify | reply | advance | call_tool | query_knowledge | handoff",
    "reply": "string?",
    "slot_updates": "object",
    "tool_call": {"name": "string", "arguments": "object"},
    "knowledge_query": {
        "query": "string",
        "reason": "string?",
        "scope": "object?",
        "max_chunks": "integer?",
        "query_type": "answer | policy_check | tool_discovery | skill_discovery?",
    },
    "next_step_id": "string?",
    "is_step_completed": "boolean",
    "handoff": "boolean?",
}

REFLECTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "action": "pass | retry_tool | try_other_tool | ask_user | revise_step | stop",
    "needs_retry": "boolean",
    "reason": "string?",
    "target_skill_id": "string?",
    "target_step_id": "string?",
    "target_tool_name": "string?",
}


def unified_system_prompt() -> str:
    return UNIFIED_PROMPT_PATH.read_text(encoding="utf-8").strip()


def speaker_prompt_text(speaker: object) -> str | None:
    """模型可见的当前说话人。姓名和日常称呼都只来自已挂上的字段，这里不查库。"""

    if not isinstance(speaker, dict) or "status" not in speaker:
        return None
    if speaker.get("status") != "resolved":
        return "未确定"
    user_id = str(speaker.get("user_id") or "").strip()
    if not user_id:
        return "未确定"
    channel = str(speaker.get("channel") or "").strip()
    lines = [f"user_id={user_id}"]
    if channel:
        lines.append(f"channel={channel}")
    roster_status = str(speaker.get("roster_status") or "").strip()
    name = single_line(speaker.get("name"))
    department = single_line(speaker.get("department"))
    preferred_name = single_line(speaker.get("preferred_name"))
    if roster_status == "matched" and name:
        lines.append("roster_status=matched")
        lines.append(f"name={name}")
        if department:
            lines.append(f"department={department}")
        lines.append(_formal_name_instruction(has_preferred_name=bool(preferred_name)))
        lines.extend(_preferred_name_lines(preferred_name, has_formal_name=True))
    elif roster_status in {"matched", "unmatched"}:
        lines.append("roster_status=unmatched")
        lines.append("企业花名册没有这条账号的记录。不要编造姓名或部门。")
        lines.extend(_preferred_name_lines(preferred_name, has_formal_name=False))
    else:
        # 已解析但本轮没查花名册。只有 user_id，不能让模型把别的字段当成姓名。
        lines.append("不要编造姓名或部门。")
        lines.extend(_preferred_name_lines(preferred_name, has_formal_name=False))
    return "\n".join(lines)


def _formal_name_instruction(*, has_preferred_name: bool) -> str:
    if has_preferred_name:
        return (
            "name 是当前联系人的正式展示名；组织内联系人与企业花名册一致。"
            "用户问「我是谁」或「我叫什么名字」时直接用 name 回答，"
            "不要说没有身份信息，也不要念出 user_id。"
            "没有 department 时不要编造部门。"
        )
    return (
        "name 是当前联系人的展示名；组织内联系人与企业花名册一致。"
        "用户询问自己是谁时直接用 name 回答，"
        "不要说没有身份信息，也不要念出 user_id。"
        "没有 department 时不要编造部门。"
    )


def _preferred_name_lines(preferred_name: str, *, has_formal_name: bool) -> list[str]:
    if not preferred_name:
        return []
    if has_formal_name:
        usage = (
            "preferred_name 是本地记忆里的日常称呼，也会写入槽位 user_name。"
            "主动称呼用户，以及用户问「你叫我什么」或「怎么称呼我」时用 preferred_name，"
            "不要改用 name。"
        )
    else:
        usage = (
            "preferred_name 是本地记忆里的日常称呼，也会写入槽位 user_name。"
            "主动称呼用户，以及用户问「你叫我什么」或「怎么称呼我」时用 preferred_name。"
            "不要编造正式姓名。"
        )
    return [f"preferred_name={preferred_name}", usage]


def stage_payload(
    *,
    phase: str,
    user_message: str,
    conversation_context: dict[str, object] | None,
    memory_context: list[dict[str, object]] | str | None,
    instructions: str,
    stage_data: dict[str, Any],
    output_contract: dict[str, Any] | str,
) -> dict[str, Any]:
    metadata = (
        conversation_context.get("metadata", {})
        if isinstance(conversation_context, dict)
        else {}
    )
    turn_time = metadata.get("current_turn_time") if isinstance(metadata, dict) else None
    speaker_text = (
        speaker_prompt_text(metadata.get("speaker")) if isinstance(metadata, dict) else None
    )
    memory_text = ""
    if phase in {"Router", "TurnPlanner", "Response Generator"}:
        memory_text = (
            _memory_text(memory_context)
            if isinstance(memory_context, list)
            else str(memory_context or "").strip()
        )
    stage: dict[str, Any] = {
        "phase": phase,
        "instructions": instructions.strip(),
        "output_contract": output_contract,
        "memory": memory_text,
        "turn_time": str(turn_time or "未提供"),
    }
    if speaker_text is not None:
        stage["speaker"] = speaker_text
    return {
        STAGE_PROTOCOL_KEY: stage,
        "user_message": user_message,
        "conversation_context": (
            conversation_context if isinstance(conversation_context, dict) else {}
        ),
        **stage_data,
    }


def render_stage_user_message(
    user_payload: dict[str, Any], *, include_turn_header: bool = True
) -> str:
    """Render the exact stage input sent as the current user message."""
    payload = copy.deepcopy(
        {
            key: value
            for key, value in user_payload.items()
            if key != "conversation_context"
        }
    )
    stage = payload.pop(STAGE_PROTOCOL_KEY, {})
    user_message = str(payload.pop("user_message", "") or "").strip()
    projected = _drop_empty_values(payload)
    output_contract = stage.get("output_contract") if isinstance(stage, dict) else None
    if not isinstance(output_contract, str):
        output_contract = json.dumps(
            output_contract or {}, ensure_ascii=False, separators=(",", ":")
        )
    sections = []
    if include_turn_header:
        sections.extend(
            [
                f"用户记忆：\n{stage.get('memory') or '无'}",
                f"本轮时间：\n{stage.get('turn_time') or '未提供'}",
            ]
        )
        if "speaker" in stage:
            sections.append(f"当前对话用户：\n{stage.get('speaker') or '未确定'}")
        sections.append(f"本轮用户输入：\n{user_message or '（空）'}")
    sections.extend(
        [
            f"当前阶段：\n{stage.get('phase') or '未指定'}",
            (
                "思考要求：\n保留完成当前阶段所需的简短思考；不要复述上下文、逐字段展开检查、"
                "罗列无关备选方案或反复验证已明确的信息。得到可靠结论后立即按输出约束作答。"
            ),
            f"阶段规则：\n{str(stage.get('instructions') or '').strip()}",
            "当前阶段独有内容：\n"
            + json.dumps(projected, ensure_ascii=False, separators=(",", ":")),
            f"输出约束：\n{output_contract}",
        ]
    )
    return "\n\n".join(sections)


def _memory_text(items: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or "").split())
        if not content or content in lines:
            continue
        lines.append(content)
    return "\n".join(f"- {line}" for line in lines)


def _drop_empty_values(value: Any) -> Any:
    if isinstance(value, dict):
        projected = {key: _drop_empty_values(item) for key, item in value.items()}
        return {
            key: item
            for key, item in projected.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            projected
            for item in value
            if (projected := _drop_empty_values(item)) not in (None, "", [], {})
        ]
    return value

"""plan_change: one structured LLM call that produces a validated ChangePlan.

Contract (task plan A4.3/A4.4): input is a projection of the bound screen and
the user's message, output is structured JSON; targets outside the bound set
fail with a follow-up question and are never retried as writes.

Shape errors get exactly one repair attempt. Out-of-scope targets do not:
they are answerable by the user, so the runtime asks once instead of letting
the model retry a write.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.session.turn_context import ScreenTurnContext
from app.workflow_packs.screen.operations import compile_operations, validate_change_plan
from app.workflows.registry import StructuredStepError, register_structured_step

LlmGenerate = Callable[[str, dict[str, Any]], dict[str, Any]]

PLAN_SYSTEM_PROMPT = (
    "你是大屏变更计划生成器。只输出一个 JSON object，符合给定的 output_schema，"
    "不要输出解释。target_component_ids 只能从 bind.targets（编辑模式）中选取；"
    "空白屏生成（fill 模式）则产出新增组件，使用新的 componentId 并给出完整组件对象。"
    "props 只允许各组件类型白名单内的字段；props.title 是显示标题字符串，"
    "不是字段绑定对象，禁止输出 {\"field\": ...}。"
    "不要输出 screenId、revision、requestId 或 operations；这些由运行时填写。"
)

_ALLOWED_PLAN_KEYS = {
    "change_kind",
    "target_component_ids",
    "patches",
    "componentType",
    "components",
    "summary",
}

CHANGE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "change_kind": {
            "type": "string",
            "enum": ["update_component", "change_component_type", "add_components"],
        },
        "target_component_ids": {"type": "array", "items": {"type": "string"}},
        "patches": {
            "type": "object",
            "description": "update/change 时按 componentId 给出 name/frame/props/style 补丁",
        },
        "componentType": {"type": "string", "description": "change_component_type 必填"},
        "components": {
            "type": "object",
            "description": "add_components 时按 componentId 给出完整组件对象",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["text", "metric", "line", "bar", "pie", "table", "shape"],
                    }
                },
                "required": ["type"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["change_kind", "target_component_ids"],
}


def _normalize_prop_aliases(
    plan: dict[str, Any], components: list[dict[str, Any]]
) -> None:
    """Map model-friendly prop names onto the domain whitelist.

    The planner sometimes writes a metric's display text as ``props.metric``
    because the component type is "metric"; the domain calls that ``title``.
    """

    types = {
        str(item.get("componentId")): str(item.get("type") or "")
        for item in components
        if isinstance(item, dict)
    }
    patches = plan.get("patches")
    if not isinstance(patches, dict):
        return
    for component_id, patch in patches.items():
        if not isinstance(patch, dict):
            continue
        props = patch.get("props")
        if not isinstance(props, dict):
            continue
        if types.get(str(component_id)) == "metric" and "metric" in props:
            title = props.pop("metric")
            props.setdefault("title", title)
        title = props.get("title")
        if isinstance(title, dict):
            label = title.get("label") or patch.get("name")
            if isinstance(label, str) and label.strip():
                props["title"] = label.strip()
            else:
                props.pop("title", None)



def _projection(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    resolve_data: dict[str, Any],
) -> dict[str, Any]:
    components = []
    for item in resolve_data.get("components") or []:
        if not isinstance(item, dict):
            continue
        projected = {
            "componentId": item.get("componentId"),
            "name": item.get("name"),
            "type": item.get("type"),
        }
        frame = item.get("frame")
        if isinstance(frame, dict) and {"x", "y", "width", "height"} <= set(frame):
            projected["frame"] = {key: frame.get(key) for key in ("x", "y", "width", "height")}
        components.append(projected)
    return {
        "user_message": user_message,
        "bind": {
            "mode": bind.get("mode"),
            "screen_id": bind.get("screen_id"),
            "revision": bind.get("revision"),
            "component_count": bind.get("component_count"),
            "targets": list(bind.get("targets") or []),
        },
        "screen": {
            "name": resolve_data.get("name"),
            "revision": resolve_data.get("revision"),
            "components": components,
        },
        "selected": (
            [
                {
                    "component_id": item.component_id,
                    "name": item.name,
                    "component_type": item.component_type,
                }
                for item in turn_context.selected
            ]
            if turn_context is not None
            else []
        ),
    }


def _shape_errors(plan: dict[str, Any]) -> str:
    extra = sorted(set(plan) - _ALLOWED_PLAN_KEYS)
    if extra:
        return "输出含不允许的顶层字段：" + "、".join(extra) + "。"
    return ""


@register_structured_step("plan_change")
def plan_change(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    slots: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
    llm: LlmGenerate,
) -> dict[str, Any]:
    _ = slots
    screen_step = str(metadata.get("screen_step") or "resolve").strip() or "resolve"
    resolve_data = dict((outputs.get(screen_step) or {}).get("data") or {})
    projection = _projection(
        user_message=user_message,
        turn_context=turn_context,
        bind=bind,
        resolve_data=resolve_data,
    )
    instruction = str(metadata.get("instruction") or "")
    last_code = "CHANGE_PLAN_INVALID"
    last_message = "计划未通过校验。"
    for attempt in range(2):
        raw = llm(
            PLAN_SYSTEM_PROMPT,
            {
                "phase": "WorkflowStructuredStep",
                "handler": "plan_change",
                "projection": projection,
                "instruction": instruction,
                "output_schema": CHANGE_PLAN_SCHEMA,
                "repair": last_message if attempt else "",
            },
        )
        if not isinstance(raw, dict):
            last_message = "plan_change 输出不是 JSON object。"
            continue
        plan: dict[str, Any] = dict(raw)
        _normalize_prop_aliases(plan, list(resolve_data.get("components") or []))
        if plan.get("change_kind") == "add_components":
            components = plan.get("components")
            if isinstance(components, dict) and components and not plan.get(
                "target_component_ids"
            ):
                plan["target_component_ids"] = list(components)
        shape_error = _shape_errors(plan)
        if shape_error:
            last_message = shape_error
            continue
        code, message, ask_fields = validate_change_plan(
            plan,
            bind=bind,
            components=list(resolve_data.get("components") or []),
        )
        if code:
            if ask_fields:
                # 目标越界：直接追问，不做第二次 LLM 调用，更不重试写。
                raise StructuredStepError(code, message, ask_fields=ask_fields)
            last_code, last_message = code, message
            continue
        operations = compile_operations(
            plan,
            components=list(resolve_data.get("components") or []),
        )
        if not operations:
            last_code = "CHANGE_PLAN_INVALID"
            last_message = "计划没有编译出任何可执行操作。"
            continue
        plan["operations"] = operations
        return plan
    raise StructuredStepError(
        last_code,
        last_message + "（已自动重试一次，仍未通过校验。）",
    )


_FORBIDDEN_DRAFT_KEYS = {"frame", "x", "y", "document", "operations"}
_PLAN_ROLES = {"metric", "trend", "compare", "share", "detail"}
_PLAN_CHARTS = {"metric", "line", "bar", "pie", "table"}

DRAFT_SYSTEM_PROMPT = (
    "你是大屏方案起草器。只输出一个 JSON object，符合给定的 output_schema。"
    "draft 只能包含 goal、sources、slots、assumptions、unmatched、placement、joinRequested。"
    "slot 只能包含 title、role、datasetVersionId、datasetSlug、dimensions、measures、chartType、timeRange。"
    "禁止输出 frame、x、y、document、operations，也不要编造坐标。"
    "目录里没有的字段放进 unmatched，不要换成别的字段。"
    "用户明确接受方案时 confirm=true，否则 confirm=false。"
)

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "confirm": {"type": "boolean"},
        "draft": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "goal": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "slots": {"type": "array"},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "unmatched": {"type": "array", "items": {"type": "string"}},
                "placement": {"type": "string", "enum": ["current", "new"]},
                "joinRequested": {"type": "boolean"},
            },
            "required": ["goal"],
        },
    },
    "required": ["confirm", "draft"],
}


def _strip_forbidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_forbidden(item)
            for key, item in value.items()
            if str(key) not in _FORBIDDEN_DRAFT_KEYS
        }
    if isinstance(value, list):
        return [_strip_forbidden(item) for item in value]
    return value


def _draft_error(draft: dict[str, Any]) -> str:
    if not str(draft.get("goal") or "").strip():
        return "goal 不能为空。"
    slots = draft.get("slots") or []
    if not isinstance(slots, list):
        return "slots 必须是数组。"
    for slot in slots:
        if not isinstance(slot, dict):
            return "slot 必须是对象。"
        if not str(slot.get("title") or "").strip():
            return "slot.title 不能为空。"
        if slot.get("role") not in _PLAN_ROLES:
            return "slot.role 不在允许的角色里。"
        chart = slot.get("chartType")
        if chart is not None and chart not in _PLAN_CHARTS:
            return "slot.chartType 不在允许的图型里。"
    placement = draft.get("placement")
    if placement is not None and placement not in {"current", "new"}:
        return "placement 只能是 current 或 new。"
    return ""


@register_structured_step("draft_screen_plan")
def draft_screen_plan(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    slots: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
    llm: LlmGenerate,
) -> dict[str, Any]:
    _ = turn_context, slots
    catalog = dict((outputs.get("describe_catalog") or {}).get("data") or {})
    instruction = str(metadata.get("instruction") or "")
    last_message = "方案草稿无法读取。"
    for attempt in range(2):
        raw = llm(
            DRAFT_SYSTEM_PROMPT,
            {
                "phase": "WorkflowStructuredStep",
                "handler": "draft_screen_plan",
                "user_message": user_message,
                "catalog": catalog,
                "placement_hint": bind.get("placement_hint") or "",
                "instruction": instruction,
                "output_schema": DRAFT_SCHEMA,
                "repair": last_message if attempt else "",
            },
        )
        if not isinstance(raw, dict):
            last_message = "draft_screen_plan 输出不是 JSON object。"
            continue
        draft = _strip_forbidden(raw.get("draft"))
        if not isinstance(draft, dict):
            last_message = "draft 必须是对象。"
            continue
        if bind.get("placement_hint") == "new":
            draft["placement"] = "new"
        error = _draft_error(draft)
        if error:
            last_message = error
            continue
        return {"confirm": bool(raw.get("confirm")), "draft": draft}
    raise StructuredStepError(
        "INVALID_PLAN",
        last_message + "（已自动重试一次，仍未通过校验。）",
    )

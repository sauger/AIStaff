"""Screen binder: route a turn from structured context, zero LLM.

Whole-screen generation goes to the plan branch. A single selected component
still goes to the edit branch. Confirmation is a user REST action in ai-screen;
the model never confirms or writes a plan from chat. The only phrase match is
the existing "explicit new screen" signal, and negation must not win over a
selected component.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from app.session.turn_context import ScreenTurnContext
from app.workflows.registry import BindError, BindOutcome, register_binder

_NEW_SCREEN_RE = re.compile(r"新建|另建|新开|重新做一张|另做一个")
_NEGATED_NEW_RE = re.compile(r"(不要|别|不用|无需)(新建|另建|新开|重新做|另做)")
_OPEN_PLAN = {"draft", "needs_input", "needs_confirmation", "ready", "blocked"}


def _request_id() -> str:
    return f"wfr_{uuid4().hex}"


def _selected_snapshot(turn_context: ScreenTurnContext) -> list[dict[str, Any]]:
    return [
        {
            "component_id": item.component_id,
            "name": item.name,
            "component_type": item.component_type,
        }
        for item in turn_context.selected
    ]


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _plan_outcome(
    *,
    turn_context: ScreenTurnContext | None,
    request_id: str,
    placement_hint: str,
    next_step_id: str,
) -> BindOutcome:
    revision = getattr(turn_context, "revision", None) if turn_context is not None else None
    return BindOutcome(
        bind={
            "request_id": request_id,
            "mode": "plan",
            "plan_id": _text(getattr(turn_context, "plan_id", "")),
            "plan_status": _text(getattr(turn_context, "plan_status", "")),
            "screen_id": _text(getattr(turn_context, "screen_id", "")),
            "revision": revision if isinstance(revision, int) and revision >= 1 else 0,
            "project_id": _text(getattr(turn_context, "project_id", "")),
            "placement_hint": placement_hint if placement_hint in {"current", "new"} else "",
            "session_id": _text(getattr(turn_context, "session_id", "")),
            "targets": [],
            "selected": _selected_snapshot(turn_context) if turn_context is not None else [],
            "template": None,
            "name": None,
            "create_mode": None,
        },
        next_step_id=next_step_id,
    )


@register_binder("screen")
def bind_screen(
    *,
    turn_context: ScreenTurnContext | None,
    slots: dict[str, Any],
    metadata: dict[str, Any],
    user_message: str = "",
) -> BindOutcome:
    request_id = _request_id()
    plan_step = str(metadata.get("plan_step") or "describe_catalog").strip() or "describe_catalog"
    edit_step = str(metadata.get("edit_step") or "resolve").strip() or "resolve"
    message = str(user_message or "")
    intent = getattr(turn_context, "intent", None) if turn_context is not None else None
    selected = list(getattr(turn_context, "selected", None) or [])
    screen_id = _text(getattr(turn_context, "screen_id", ""))
    count = getattr(turn_context, "component_count", None)
    blank = getattr(turn_context, "blank", None)
    plan_status = _text(getattr(turn_context, "plan_status", ""))
    base: dict[str, Any] = {
        "request_id": request_id,
        "template": None,
        "name": None,
        "create_mode": None,
        "screen_id": screen_id,
        "revision": getattr(turn_context, "revision", None),
        "project_id": getattr(turn_context, "project_id", None),
        "component_count": count,
        "selected": _selected_snapshot(turn_context) if turn_context is not None else [],
        "plan_id": _text(getattr(turn_context, "plan_id", "")),
        "plan_status": plan_status,
        "placement_hint": "",
        "session_id": _text(getattr(turn_context, "session_id", "")),
    }

    def _plan(placement: str) -> BindOutcome:
        return _plan_outcome(
            turn_context=turn_context,
            request_id=request_id,
            placement_hint=placement,
            next_step_id=plan_step,
        )

    def _ask(field: str, question: str) -> BindOutcome:
        return BindOutcome(
            bind={**base, "mode": "edit", "targets": []},
            missing_fields=[field],
            question=question,
            next_step_id=None,
        )

    # Planner intent is only a routing hint: it never writes and never carries
    # a confirm flag. Low confidence falls back to the deterministic rules.
    if intent is not None and float(getattr(intent, "confidence", 0.0) or 0.0) >= 0.75:
        kind = str(getattr(intent, "kind", "") or "")
        target = str(getattr(intent, "target", "unspecified") or "unspecified")
        if kind == "create":
            if target == "new":
                if not _NEGATED_NEW_RE.search(message):
                    return _plan("new")
                # "不要新建，把标题改大" is an edit; never turn a negated
                # new-screen phrase into a placement question.
                if len(selected) == 1:
                    return BindOutcome(
                        bind={
                            **base,
                            "mode": "edit",
                            "targets": [str(selected[0].component_id)],
                        },
                        next_step_id=edit_step,
                    )
                return _ask(
                    "target_component",
                    "没有选中组件，也没有新建大屏。请先在画布上选中要修改的组件。",
                )
            if target == "current" and screen_id:
                return _plan("current")
            if screen_id and isinstance(count, int) and count > 0 and blank is not True:
                return _ask(
                    "target_placement",
                    "当前屏已有内容。要覆盖这张屏，还是新建一张？"
                    "默认覆盖当前屏；回复「新建」则另建一张。",
                )
            return _plan("current" if screen_id and blank is True else "")
        if kind == "modify_selected":
            if len(selected) == 1:
                return BindOutcome(
                    bind={
                        **base,
                        "mode": "edit",
                        "targets": [str(selected[0].component_id)],
                    },
                    next_step_id=edit_step,
                )
            if not selected:
                return _ask(
                    "target_component",
                    "还没有选中组件。请先在画布上选中要修改的组件，再发一次。",
                )
            return _ask(
                "target_component",
                f"你选中了 {len(selected)} 个组件。请只保留一个选中，或直接告诉我要改哪一个。",
            )
        if kind == "modify_screen":
            if screen_id:
                return _plan("current")
            return _ask(
                "target_screen",
                "还没有打开大屏。请先进入一张大屏，或明确说「新建一张销售大屏」。",
            )
        if kind == "confirm":
            return _ask(
                "confirm_plan",
                "确认要在方案卡片里点「按此生成」，或直接回复「按这个生成」。",
            )
        if kind == "cancel":
            return _ask("cancel_plan", "本轮已取消，没有写入大屏。")
        if kind == "query":
            return _ask(
                "query_dataset",
                "这是数据问题。请告诉我要看哪个数据集或指标，例如「ERP 合同总额」。",
            )
        if kind == "help":
            return _ask(
                "help",
                "我可以做大屏方案、修改选中组件、回答数据问题；"
                "请告诉我你要做哪一张屏，或要改哪个组件。",
            )

    if _NEW_SCREEN_RE.search(message) and not _NEGATED_NEW_RE.search(message):
        return _plan("new")

    if len(selected) > 1:
        return _ask(
            "target_component",
            f"你选中了 {len(selected)} 个组件。请只保留一个选中，"
            "或直接告诉我要改哪一个。",
        )
    if len(selected) == 1:
        return BindOutcome(
            bind={
                **base,
                "mode": "edit",
                "targets": [str(selected[0].component_id)],
            },
            next_step_id=edit_step,
        )

    if not screen_id or count == 0 or blank is True or plan_status in _OPEN_PLAN:
        return _plan(
            "current" if screen_id and blank is True else "",
        )
    if count is None:
        raise BindError(
            "SCREEN_COMPONENT_COUNT_MISSING",
            "编辑器没有上报当前大屏的组件数量，无法判断是空白屏还是需要指定组件；"
            "请刷新页面后重试。",
        )
    return _ask(
        "target_component",
        f"当前大屏有 {int(count)} 个组件。你想改哪一个？"
        "请先在画布上选中它，再发一次；也可以直接告诉我组件名称。",
    )

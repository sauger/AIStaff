"""Screen binder: route a turn from structured context, zero LLM.

Whole-screen generation goes to the plan branch. A single selected component
still goes to the edit branch. The only phrase match is the existing
"explicit new screen" signal, and that signal sets placement_hint=new on the
plan branch.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from app.session.turn_context import ScreenTurnContext
from app.workflows.registry import BindError, BindOutcome, register_binder

_NEW_SCREEN_RE = re.compile(
    r"新建|另建|新开|重新做一张|另做一个|不要动当前屏|不要修改当前屏"
)
_OPEN_PLAN = {"draft", "needs_input", "needs_confirmation", "ready", "blocked"}
_CONFIRM_PLAN = {"needs_confirmation", "ready"}


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
    confirm_step = str(metadata.get("confirm_step") or "confirm_plan").strip() or "confirm_plan"
    message = str(user_message or "")
    if _NEW_SCREEN_RE.search(message):
        return _plan_outcome(
            turn_context=turn_context,
            request_id=request_id,
            placement_hint="new",
            next_step_id=plan_step,
        )

    edit_step = str(metadata.get("edit_step") or "resolve").strip() or "resolve"
    selected = list(getattr(turn_context, "selected", None) or [])
    screen_id = _text(getattr(turn_context, "screen_id", ""))
    base: dict[str, Any] = {
        "request_id": request_id,
        "template": None,
        "name": None,
        "create_mode": None,
        "screen_id": screen_id,
        "revision": getattr(turn_context, "revision", None),
        "project_id": getattr(turn_context, "project_id", None),
        "component_count": getattr(turn_context, "component_count", None),
        "selected": _selected_snapshot(turn_context) if turn_context is not None else [],
        "plan_id": _text(getattr(turn_context, "plan_id", "")),
        "plan_status": _text(getattr(turn_context, "plan_status", "")),
        "placement_hint": "",
        "session_id": _text(getattr(turn_context, "session_id", "")),
    }

    if len(selected) > 1:
        return BindOutcome(
            bind={**base, "mode": "edit", "targets": []},
            missing_fields=["target_component"],
            question=(
                f"你选中了 {len(selected)} 个组件。请只保留一个选中，"
                "或直接告诉我要改哪一个。"
            ),
            next_step_id=None,
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

    plan_status = _text(getattr(turn_context, "plan_status", ""))
    plan_id = _text(getattr(turn_context, "plan_id", ""))
    if (
        bool(getattr(turn_context, "confirm_plan", False))
        and plan_id
        and plan_status in _CONFIRM_PLAN
    ):
        return _plan_outcome(
            turn_context=turn_context,
            request_id=request_id,
            placement_hint="",
            next_step_id=confirm_step,
        )

    count = getattr(turn_context, "component_count", None)
    if not screen_id or count == 0 or plan_status in _OPEN_PLAN:
        return _plan_outcome(
            turn_context=turn_context,
            request_id=request_id,
            placement_hint="",
            next_step_id=plan_step,
        )
    if count is None:
        raise BindError(
            "SCREEN_COMPONENT_COUNT_MISSING",
            "编辑器没有上报当前大屏的组件数量，无法判断是空白屏还是需要指定组件；"
            "请刷新页面后重试。",
        )
    return BindOutcome(
        bind={**base, "mode": "edit", "targets": []},
        missing_fields=["target_component"],
        question=(
            f"当前大屏有 {int(count)} 个组件。你想改哪一个？"
            "请先在画布上选中它，再发一次；也可以直接告诉我组件名称。"
        ),
        next_step_id=None,
    )

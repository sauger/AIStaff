"""Screen binder: route a turn from structured context, zero LLM.

Whole-screen generation goes to the plan branch. A single selected component
still goes to the edit branch. Confirmation is a user REST action in ai-screen;
the model never confirms or writes a plan from chat.

Routing order is fixed (design §5.9); no keyword regexes anywhere:

1. ``task_brief.transitioned`` — the proxy already switched tasks and re-sent
   the same message. Execute by placement; never ask placement again, never
   emit another transition.
2. ``turn_context.placement`` — the task itself carries a placement decision
   made when it was created. Route by it without re-asking.
3. Placement unknown + (non-empty workspace or current screen has content)
   → ask one placement question; no writes, no transition.
4. Explicit placement choice (``placement_confirmed``) + new generation with
   no usable task placement → emit ``task_transition``; this turn writes
   nothing. The proxy archives the old task, creates the new one, and re-sends
   the same user message.
5. Otherwise route by intent kind; selected components still edit; low
   confidence asks one specific question instead of guessing.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.session.turn_context import ScreenTurnContext
from app.workflows.registry import BindError, BindOutcome, register_binder

_OPEN_PLAN = {"draft", "needs_input", "needs_confirmation", "ready", "blocked"}
_TITLE_MAX = 60
_BRIEF_MAX = 500


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


def _workspace_screens(turn_context: ScreenTurnContext | None) -> list[dict[str, Any]]:
    workspace = getattr(turn_context, "workspace", None) or {}
    screens = workspace.get("screens")
    return [item for item in screens if isinstance(item, dict)] if isinstance(screens, list) else []


def _workspace_nonempty(turn_context: ScreenTurnContext | None) -> bool:
    workspace = getattr(turn_context, "workspace", None) or {}
    count = workspace.get("screen_count")
    if isinstance(count, int):
        return count > 0
    # No workspace facts sent: fall back to the screen facts we do have.
    return bool(_workspace_screens(turn_context))


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
            "task_id": _text(getattr(turn_context, "task_id", "")),
            "targets": [],
            "selected": _selected_snapshot(turn_context) if turn_context is not None else [],
            "template": None,
            "name": None,
            "create_mode": None,
        },
        next_step_id=next_step_id,
    )


def _transition_outcome(
    *,
    base: dict[str, Any],
    message: str,
    placement: str,
    reason: str,
    turn_context: ScreenTurnContext | None,
) -> BindOutcome:
    screen_id = _text(getattr(turn_context, "screen_id", "")) if turn_context is not None else ""
    goal = _text(message)[:_TITLE_MAX] or "新任务"
    brief = (
        f"用户原话：「{_text(message)}」。归属已确认："
        f"{'新建一张' if placement == 'new' else '在当前屏改'}。数据未定。"
    )[:_BRIEF_MAX]
    return BindOutcome(
        bind={
            **base,
            "mode": "task_transition",
            "targets": [],
            "task_transition": {
                "reason": reason,
                "goal": goal,
                "placement": placement,
                "screenId": None if placement == "new" or not screen_id else screen_id,
                "title": goal,
                "brief": brief,
            },
        },
        missing_fields=["task_transition"],
        question="正在切换任务…",
        next_step_id=None,
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
    task_brief = getattr(turn_context, "task_brief", None) or {}
    task_placement = _text(getattr(turn_context, "placement", ""))
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
        "task_id": _text(getattr(turn_context, "task_id", "")),
    }

    def _plan(placement: str, *, next_step_id: str | None = None) -> BindOutcome:
        # A new placement enters through the blank-canvas node: create the
        # empty screen, then ask exactly one data question (design §6).
        step = next_step_id or ("create_blank" if placement == "new" else plan_step)
        outcome = _plan_outcome(
            turn_context=turn_context,
            request_id=request_id,
            placement_hint=placement,
            next_step_id=step,
        )
        if placement == "new":
            outcome.bind["name"] = _text(message)[:40] or "AI 大屏"
        return outcome

    def _ask(field: str, question: str) -> BindOutcome:
        return BindOutcome(
            bind={**base, "mode": "edit", "targets": []},
            missing_fields=[field],
            question=question,
            next_step_id=None,
        )

    def _edit(target: str) -> BindOutcome:
        return BindOutcome(
            bind={**base, "mode": "edit", "targets": [target]},
            next_step_id=edit_step,
        )

    intent_kind = str(getattr(intent, "kind", "") or "") if intent is not None else ""
    confidence = float(getattr(intent, "confidence", 0.0) or 0.0) if intent is not None else 0.0
    target = str(getattr(intent, "target", "unspecified") or "unspecified") if intent is not None else "unspecified"
    placement_confirmed = bool(getattr(intent, "placement_confirmed", False)) if intent is not None else False
    new_generation = bool(getattr(intent, "new_generation", False)) if intent is not None else False

    # 1. The proxy already switched tasks and re-sent this very message.
    if bool(task_brief.get("transitioned")):
        placement = str(task_brief.get("placement") or task_placement or "")
        if placement == "new":
            return _plan("new", next_step_id="create_blank")
        if placement == "current":
            return _plan("current" if screen_id else "")
        return _plan("current" if screen_id and blank is True else ("new" if not screen_id else ""))

    # 2. The task was created with an explicit placement; never re-ask.
    if task_placement in {"new", "current"}:
        if task_placement == "new":
            return _plan("new", next_step_id="create_blank")
        return _plan("current" if screen_id else "")

    screen_has_content = bool(screen_id) and (blank is False or (isinstance(count, int) and count > 0))
    needs_placement = _workspace_nonempty(turn_context) or screen_has_content
    placement_known = target in {"new", "current"}

    # 3. Placement unknown + somewhere to collide → ask exactly one question
    #    with real screen names; no writes, no transition.
    if intent_kind == "create" and not placement_known and needs_placement:
        screens = _workspace_screens(turn_context)
        names = "、".join(_text(item.get("name")) for item in screens if item.get("name"))
        if screen_has_content:
            return _ask(
                "target_placement",
                f"当前这张「{_text(getattr(turn_context, 'screen_name', '')) or '大屏'}」已经有内容。"
                f"{_text(message)}是新建一张，还是在这张上改？① 新建一张 ② 在当前屏改（覆盖现有内容）",
            )
        known = f"（{names}）" if names else ""
        total = (getattr(turn_context, "workspace", None) or {}).get("screen_count") or len(screens)
        options = "① 新建一张" + ("".join(
            f" {'②③④⑤⑥⑦⑧⑨'[index + 1]} {name}"
            for index, name in enumerate(names.split("、")[:7])
        ))
        return _ask(
            "target_placement",
            f"这个工作区已经有 {total} 张大屏{known}。{_text(message)}是新建一张，还是在某张上改？{options}",
        )

    # 4. Explicit placement choice + a new generation and no task placement →
    #    hand the switch to the proxy. This turn writes nothing.
    if intent_kind == "create" and placement_confirmed and new_generation and placement_known:
        placement = "new" if target == "new" else "current"
        if placement == "current" and not screen_id:
            return _ask("target_screen", "还没有打开大屏；请先进入一张大屏，或明确说「新建一张」。")
        return _transition_outcome(
            base=base,
            message=message,
            placement=placement,
            reason="new_screen" if placement == "new" else "new_generation",
            turn_context=turn_context,
        )

    # 5. High-confidence intent routing (same as before, minus regexes).
    if intent is not None and confidence >= 0.75:
        if intent_kind == "create":
            if target == "new":
                return _plan("new")
            if target == "current" and screen_id:
                return _plan("current")
            if screen_id and blank is True:
                return _plan("current")
            if not screen_id:
                # Placement unresolved: the plan target resolver asks next.
                return _plan("")
            if count == 0 or plan_status in _OPEN_PLAN:
                return _plan("current")
            return _ask(
                "target_component",
                f"当前大屏有 {int(count)} 个组件。你想改哪一个？请先选中它再说，或直接说组件名。",
            )
        if intent_kind == "modify_selected":
            if len(selected) == 1:
                return _edit(str(selected[0].component_id))
            if not selected:
                return _ask("target_component", "还没有选中组件。请先在画布上选中要修改的组件，再发一次。")
            return _ask("target_component", f"你选中了 {len(selected)} 个组件。请只保留一个选中，或直接告诉我要改哪一个。")
        if intent_kind == "modify_screen":
            if screen_id:
                return _plan("current")
            return _ask("target_screen", "还没有打开大屏。请先进入一张大屏，或明确说「新建一张销售大屏」。")
        if intent_kind == "confirm":
            return _ask("confirm_plan", "确认要在方案卡片里点「按此生成」，或直接回复「按这个生成」。")
        if intent_kind == "cancel":
            return _ask("cancel_plan", "本轮已取消，没有写入大屏。")
        if intent_kind == "query":
            return _ask("query_dataset", "这是数据问题。请告诉我要看哪个数据集或指标，例如「ERP 合同总额」。")
        if intent_kind == "help":
            return _ask("help", "我可以做大屏方案、修改选中组件、回答数据问题；请告诉我你要做哪一张屏，或要改哪个组件。")

    # 6. No usable intent: deterministic structure rules only. Same-screen
    #    second generation with an explicit new_generation flag transitions.
    if new_generation and placement_confirmed and screen_has_content:
        return _transition_outcome(
            base=base,
            message=message,
            placement="current",
            reason="new_generation",
            turn_context=turn_context,
        )

    if len(selected) > 1:
        return _ask("target_component", f"你选中了 {len(selected)} 个组件。请只保留一个选中，或直接告诉我要改哪一个。")
    if len(selected) == 1:
        return _edit(str(selected[0].component_id))

    if not screen_id:
        return _plan("new")
    if count == 0 or blank is True or plan_status in _OPEN_PLAN:
        # The canvas already exists (blank or with an open plan): fill it,
        # don't create another screen.
        return _plan("current")
    if count is None:
        raise BindError(
            "SCREEN_COMPONENT_COUNT_MISSING",
            "编辑器没有上报当前大屏的组件数量，无法判断是空白屏还是需要指定组件；请刷新页面后重试。",
        )
    if confidence < 0.75 and intent is not None:
        # Low confidence asks one specific question; it never guesses.
        return _ask(
            "target_intent",
            "我不确定这一句是要新建大屏、修改当前屏，还是查数据。请直接说：新建一张、在当前屏改，或问一个数据问题。",
        )
    return _ask(
        "target_component",
        f"当前大屏有 {int(count)} 个组件。你想改哪一个？请先在画布上选中它，再发一次；也可以直接告诉我组件名称。",
    )

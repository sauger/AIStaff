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
4. Explicit placement choice (``placement_confirmed``) with
   no usable task placement → emit ``task_transition``; this turn writes
   nothing. The proxy archives the old task, creates the new one, and re-sends
   the same user message.
5. Otherwise route by intent kind; selected components still edit; low
   confidence asks one specific question instead of guessing.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from app.session.turn_context import ScreenTurnContext
from app.workflow_packs.screen.plan import merge_clarification_draft
from app.workflows.registry import BindError, BindOutcome, register_binder

_OPEN_PLAN = {"draft", "needs_input", "needs_confirmation", "ready", "blocked"}
_TITLE_MAX = 60
_BRIEF_MAX = 500


def _request_id() -> str:
    return f"wfr_{uuid4().hex}"


def _wants_blank_canvas(message: str) -> bool:
    """Explicit blank-canvas request only (never a normal generation)."""

    text = str(message or "")
    for match in re.finditer("空白", text):
        start = match.start()
        before = text[max(0, start - 4) : start]
        if any(word in before for word in ("不要", "不用", "别", "取消", "不")):
            continue
        window = text[max(0, start - 2) : start + 8]
        if any(word in window for word in ("画布", "大屏", "看板", "屏")):
            return True
    return False


def _wants_default_plan(message: str) -> bool:
    """Explicit acceptance of the assistant's default plan."""

    text = "".join(str(message or "").split()).strip("，。！!；;")
    return text in {
        "直接做",
        "直接生成",
        "按建议做",
        "按你的建议",
        "按这个来",
        "按默认",
        "按默认做",
        "你来定",
    }


def _wants_layout_swap(message: str) -> bool:
    text = "".join(str(message or "").split())
    return any(word in text for word in ("互换", "交换", "对调")) and any(
        word in text for word in ("位置", "布局", "模块", "组件")
    )


def _wants_regenerate(message: str) -> bool:
    text = "".join(str(message or "").split())
    return text in {"重新生成", "重新生成方案", "重新做一版", "再来一版", "换一版"}


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


def _label(item: object) -> str:
    name = _text(getattr(item, "name", ""))
    return name or _text(getattr(item, "component_id", ""))


def _component_names(turn_context: ScreenTurnContext | None) -> list[str]:
    if turn_context is None:
        return []
    return [_label(item) for item in turn_context.components if _label(item)]


def _match_component_by_name(turn_context: ScreenTurnContext | None, name: str) -> list[Any]:
    """Point-name resolution against the real component list; never invents."""
    target = _text(name)
    if not target or turn_context is None:
        return []
    lowered = target.casefold()
    exact = [
        item
        for item in turn_context.components
        if _text(item.name) == target or item.component_id == target
    ]
    if exact:
        return exact
    return [
        item
        for item in turn_context.components
        if _label(item).casefold() == lowered
    ]


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
    plan_id: str = "",
    draft: dict[str, Any] | None = None,
) -> BindOutcome:
    revision = getattr(turn_context, "revision", None) if turn_context is not None else None
    return BindOutcome(
        bind={
            "request_id": request_id,
            "mode": "plan",
            "plan_id": plan_id or _text(getattr(turn_context, "plan_id", "")),
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
            # Whole-screen turns clear any leftover component-edit pending
            # state (topic change, design §6.3).
            "resolve_step": "resolve",
            "target_dataset_version_id": "",
            "pending_edit": None,
            # A new plan starts from a clean clarification state. The resume
            # path writes these keys again after the merge.
            "draft": draft,
            "plan_draft": None,
            "plan_clarification": None,
            "clarification_plan_id": "",
            "clarification_task_id": "",
            "clarification_followup": None,
            "clarification_stale": False,
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
        question="",
        next_step_id=None,
    )


def _wants_template_choice(message: str) -> bool:
    text = _text(message)
    if not any(word in text for word in ("模板", "方向", "方案", "个")):
        return False
    return bool(
        re.search(r"(?:第[一二三1-3]个|选[一二三1-3]|模板[一二三1-3]|方案[一二三1-3])", text)
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
    current_plan_id = _text(getattr(turn_context, "plan_id", ""))
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
        # Edit-spine routing + pending_edit lifecycle. These keys ride in every
        # bind outcome's slot_updates, so a stale value from the previous turn
        # is always overwritten (pending_edit: None clears it).
        "resolve_step": "resolve",
        "target_dataset_version_id": "",
        "pending_edit": None,
        "plan_draft": None,
        "plan_clarification": None,
        "clarification_plan_id": "",
        "clarification_task_id": "",
        "clarification_followup": None,
        "clarification_stale": False,
    }
    pending = slots.get("pending_edit")
    pending = dict(pending) if isinstance(pending, dict) else None

    def _plan(
        placement: str,
        *,
        next_step_id: str | None = None,
        new_plan: bool = False,
    ) -> BindOutcome:
        # Normal whole-screen generation plans first and never creates the
        # screen. Only an explicit blank-canvas request enters create_blank.
        step = next_step_id or plan_step
        if _wants_blank_canvas(message):
            step = "create_blank"
        elif (
            step == plan_step
            and current_plan_id
            and plan_status == "needs_input"
            and not new_plan
        ):
            return _ask(
                "follow_up_card",
                "这个方案还在等你补充信息，请在方案卡片里提交，或重新生成一张方案。",
            )
        elif (
            step == plan_step
            and current_plan_id
            and plan_status in {"ready", "needs_confirmation"}
            and not new_plan
        ):
            # The active plan is the source of truth: read it, propose a
            # limited intent, and let ai-screen merge/CAS. Never rebuild the
            # whole draft from this turn's message.
            step = "get_current_plan"
        outcome = _plan_outcome(
            turn_context=turn_context,
            request_id=request_id,
            placement_hint=placement,
            next_step_id=step,
        )
        if new_plan:
            outcome.bind["plan_id"] = ""
            outcome.bind["plan_status"] = ""
        if step == "create_blank":
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
        # Route the bound edit spine when the target's dataset version is
        # known (pending_edit, or the selection's binding summary); the
        # summary only picks the spine — the binding used in operations always
        # comes from the resolve/get_screen result in plan_change.
        dataset_version_id = ""
        if pending is not None and str(pending.get("target_component_id")) == target:
            dataset_version_id = _text(pending.get("dataset_version_id"))
            base["pending_edit"] = pending
        if not dataset_version_id:
            for item in selected:
                if item.component_id == target and getattr(item, "binding", None) is not None:
                    dataset_version_id = _text(item.binding.datasetVersionId)
                    break
        if dataset_version_id:
            base["target_dataset_version_id"] = dataset_version_id
            base["resolve_step"] = "resolve_bound"
            return BindOutcome(
                bind={**base, "mode": "edit", "targets": [target]},
                next_step_id="resolve_bound",
            )
        return BindOutcome(
            bind={**base, "mode": "edit", "targets": [target]},
            next_step_id=edit_step,
        )

    intent_kind = str(getattr(intent, "kind", "") or "") if intent is not None else ""
    confidence = float(getattr(intent, "confidence", 0.0) or 0.0) if intent is not None else 0.0
    target = str(getattr(intent, "target", "unspecified") or "unspecified") if intent is not None else "unspecified"
    placement_confirmed = bool(getattr(intent, "placement_confirmed", False)) if intent is not None else False
    new_generation = bool(getattr(intent, "new_generation", False)) if intent is not None else False
    named_component = _text(getattr(intent, "component", "") if intent is not None else "")
    primary_id = _text(getattr(turn_context, "primary_component_id", "") if turn_context is not None else "")

    def _primary_selection() -> Any | None:
        """The primary selection: primary_component_id, else the only/first item."""
        if not selected:
            return None
        if len(selected) == 1:
            return selected[0]
        for item in selected:
            if item.component_id == primary_id:
                return item
        return None

    def _locked_ask(item: Any) -> BindOutcome:
        return _ask(
            "locked_component",
            f"「{_label(item)}」已锁定，不能直接改；我没有解锁它。"
            "要改其他组件请选中或点名，要调整整张屏请明确说。",
        )

    def _resolve_edit_target() -> BindOutcome:
        """Component-edit target resolution (design §6.1): primary → point-name
        → one real-options question. Never widens to whole-screen generation."""
        chosen = _primary_selection()
        if selected:
            if chosen is None:
                # Multi-select without a primary: ask which one is the target,
                # never batch-edit (old clients without primary_component_id
                # land here too — we do not guess the primary).
                names = "、".join(_label(item) for item in selected)
                return _ask(
                    "target_component",
                    f"你选中了 {len(selected)} 个组件（{names}）。以哪个为主？我只改它，其余不动。",
                )
            if bool(getattr(chosen, "locked", False)):
                return _locked_ask(chosen)
            return _edit(str(chosen.component_id))
        if named_component:
            matches = _match_component_by_name(turn_context, named_component)
            if len(matches) == 1:
                if matches[0].locked:
                    return _locked_ask(matches[0])
                return _edit(str(matches[0].component_id))
            if len(matches) > 1:
                names = "、".join(_label(item) for item in matches)
                return _ask(
                    "target_component",
                    f"「{named_component}」有 {len(matches)} 个同名组件（{names}）。是哪一个？",
                )
            names = _component_names(turn_context)
            if names:
                options = "、".join(names[:8])
                return _ask(
                    "target_component",
                    f"当前屏没有叫「{named_component}」的组件。你是指哪一个？{options}",
                )
            return _ask(
                "target_component",
                f"当前屏没有叫「{named_component}」的组件。请先在画布上选中要改的组件，再发一次。",
            )
        names = _component_names(turn_context)
        if names:
            options = "、".join(names[:8])
            return _ask("target_component", f"改哪一个组件？{options}")
        return _ask(
            "target_component",
            "还没有选中组件。请先在画布上选中要修改的组件，或直接告诉我组件名。",
        )

    def _clarification_stale(question: str, *, plan_id: str = "") -> BindOutcome:
        return BindOutcome(
            bind={
                **base,
                "mode": "plan",
                "plan_id": plan_id or base["plan_id"],
                "targets": [],
                "clarification_stale": True,
                "plan_draft": None,
                "plan_clarification": None,
                "clarification_plan_id": "",
                "clarification_task_id": "",
                "clarification_followup": None,
            },
            missing_fields=["clarification_stale"],
            question=question,
            next_step_id=None,
        )

    # 1b. A structured clarification answer always continues the same plan:
    # same task, same planId, a fresh requestId, and the server's draftPatch
    # merged into the draft we resubmit. It never re-runs intent routing and
    # never asks a generic "what do you want to see" question.
    answer = getattr(turn_context, "clarification_answer", None) if turn_context is not None else None
    if answer is not None and not bool(task_brief.get("transitioned")):
        answer_plan_id = _text(getattr(answer, "planId", ""))
        plan_id = answer_plan_id or _text(getattr(turn_context, "plan_id", ""))
        if not plan_id:
            return _clarification_stale("方案已经失效，请重新生成方案后再确认。")
        current_plan_id = _text(getattr(turn_context, "plan_id", ""))
        if current_plan_id and answer_plan_id and current_plan_id != answer_plan_id:
            return _clarification_stale("方案已经更新，请以当前方案卡片为准。", plan_id=current_plan_id)
        stored_task_id = _text(slots.get("clarification_task_id"))
        current_task_id = _text(getattr(turn_context, "task_id", ""))
        if stored_task_id and current_task_id and stored_task_id != current_task_id:
            return _clarification_stale("这个选项属于之前的任务，已经失效，请以当前任务的方案卡片为准。")
        stored_plan_id = _text(slots.get("clarification_plan_id"))
        if stored_plan_id and stored_plan_id != plan_id:
            return _clarification_stale("这个问题已经失效，请以当前方案卡片为准。", plan_id=plan_id)
        stored = slots.get("plan_clarification")
        stored = dict(stored) if isinstance(stored, dict) else None
        option_ids = [str(item) for item in (getattr(answer, "optionIds", None) or []) if str(item)]
        answer_text = _text(getattr(answer, "text", ""))
        question_id = _text(getattr(answer, "questionId", ""))
        if stored is not None:
            stored_question_id = _text(stored.get("id"))
            if stored_question_id and question_id and stored_question_id != question_id:
                return _clarification_stale("方案卡片已经更新，请以最新卡片为准，或重新生成方案。")
            options = [item for item in (stored.get("options") or []) if isinstance(item, dict)]
            known_ids = {_text(item.get("id")) for item in options}
            if any(item not in known_ids for item in option_ids):
                return _clarification_stale("这个选项不在最新方案卡片里，请以最新卡片为准。")
            if not bool(stored.get("multiple", True)) and len(option_ids) > 1:
                return _clarification_stale("这道题只能选择一项，请以最新卡片为准。")
            if answer_text and stored.get("allowFreeText") is False:
                return _clarification_stale("这道题需要从卡片选项中选择，请以最新卡片为准。")
        if not option_ids and not answer_text:
            return _clarification_stale("请先在方案卡片中选择，或补充说明。")
        if "focus.other" in option_ids and not answer_text:
            return _clarification_stale("选择「其他」时请补充具体内容。")
        answer_draft = getattr(answer, "planDraft", None)
        base_draft = answer_draft if isinstance(answer_draft, dict) else slots.get("plan_draft")
        draft, problem = merge_clarification_draft(
            base_draft,
            draft_patch=getattr(answer, "draftPatch", None),
            text=answer_text,
        )
        plan_draft_hash = _text(getattr(answer, "planDraftHash", None))
        clarification_base_hash = plan_draft_hash or ""
        if problem:
            problem_bind = {
                **base,
                "mode": "plan",
                "plan_id": plan_id,
                "targets": [],
                "clarification_stale": False,
                "plan_draft": base_draft if isinstance(base_draft, dict) else None,
                "plan_clarification": stored,
                "clarification_plan_id": plan_id,
                "clarification_task_id": current_task_id,
                "clarification_base_hash": clarification_base_hash,
            }
            if plan_draft_hash:
                problem_bind["plan_draft_hash"] = plan_draft_hash
            return BindOutcome(
                bind=problem_bind,
                missing_fields=["clarification_text"],
                question=problem,
                next_step_id=None,
            )
        placement_hint = _text(draft.get("placement")) or task_placement
        if placement_hint not in {"current", "new"}:
            placement_hint = ""
        outcome = _plan_outcome(
            turn_context=turn_context,
            request_id=request_id,
            placement_hint=placement_hint,
            next_step_id="submit_plan_resume",
            plan_id=plan_id,
            draft=draft,
        )
        outcome.bind.update(
            {
                "mode": "plan",
                "clarification_resume": True,
                "clarification_stale": False,
                "plan_draft": draft,
                "plan_clarification": stored,
                "clarification_plan_id": plan_id,
                "clarification_task_id": current_task_id,
                "clarification_base_hash": clarification_base_hash,
            }
        )
        if plan_draft_hash:
            outcome.bind["plan_draft_hash"] = plan_draft_hash
        return outcome

    # 1. The proxy already switched tasks and re-sent this very message.
    if bool(task_brief.get("transitioned")):
        placement = str(task_brief.get("placement") or task_placement or "")
        if placement == "new":
            return _plan("new")
        if placement == "current":
            return _plan("current" if screen_id else "")
        return _plan("current" if screen_id and blank is True else ("new" if not screen_id else ""))

    # 2. The task was created with an explicit placement; never re-ask.
    #    Once a "new" task has produced its screen, later ordinary turns work
    #    on that screen. An explicit new-generation request still falls
    #    through to the placement question/transition below.
    if (
        task_placement in {"new", "current"}
        and intent_kind not in {"modify_selected", "modify_screen", "query", "help", "cancel"}
        and not (
            intent_kind == "create"
            and (new_generation or (placement_confirmed and target == "new"))
        )
    ):
        if task_placement == "new":
            if screen_id:
                return _plan("current")
            return _plan("new")
        return _plan("current" if screen_id else "")

    screen_has_content = bool(screen_id) and (blank is False or (isinstance(count, int) and count > 0))
    needs_placement = _workspace_nonempty(turn_context) or screen_has_content
    placement_known = target in {"new", "current"}

    # 2b. Selected component + a generation-flavored request whose placement
    #     is not explicit and not a clear redo → this is ambiguous between
    #     "edit the selection" and "redo the screen". Ask exactly one
    #     either/or question (design §7A); no writes, no plan.
    if (
        intent_kind == "create"
        and selected
        and target not in {"new", "current"}
        and not placement_confirmed
        and not new_generation
    ):
        primary = _primary_selection()
        label = _label(primary) if primary is not None else "选中的组件"
        return _ask(
            "edit_or_screen",
            f"你是要改选中的组件「{label}」，还是调整整张屏？① 改选中的组件 ② 调整整张屏",
        )

    # 3. Placement unresolved + somewhere to collide → ask exactly one
    #    question with real screen names; no writes, no transition. An
    #    explicit target=new/current already names the placement (the planner
    #    only sets target=new for 新建/另建 wording), so it never lands here.
    if (
        intent_kind == "create"
        and needs_placement
        and not placement_confirmed
        and not placement_known
    ):
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
        markers = "②③④⑤⑥⑦⑧"
        options = "① 新建一张" + "".join(
            f" {markers[index]} {name}"
            for index, name in enumerate(
                [item.get("name") for item in screens if item.get("name")][:7]
            )
        )
        return _ask(
            "target_placement",
            f"这个工作区已经有 {total} 张大屏{known}。{_text(message)}是新建一张，还是在某张上改？{options}",
        )


    # 3c. Accepting the default plan is a plan-continuation, not a new screen.
    #     This guard must run before the transition branch below: the planner
    #     may classify a bare "直接做" as create and confirm a placement.
    if _wants_default_plan(message) and current_plan_id and plan_status in _OPEN_PLAN:
        if plan_status in {"ready", "needs_confirmation"}:
            return _plan("current", next_step_id="present_plan")
        return _ask(
            "follow_up_card",
            "这个方案还在等你补充信息，请在方案卡片里提交；补充后仍使用当前方案。",
        )
    if _wants_default_plan(message):
        return _plan("current" if screen_id else "")

    # Choosing one of the three offered templates is a plan-continuation with
    # a new plan version in the same task, not a new task or a whole-screen rebuild.
    if _wants_template_choice(message) and current_plan_id and plan_status in _OPEN_PLAN:
        return _plan("current", new_plan=True)

    # 3b. A natural "confirm" never writes from chat. The plan card remains
    #     the only confirm surface, even when the intent classifier is absent.
    if bool(getattr(turn_context, "confirm_plan", False)) and plan_status in _OPEN_PLAN:
        return _ask("confirm_plan", "确认要在方案卡片里点「按此生成」，或直接回复「按这个生成」。")
    # 3d. "重新生成方案" = a fresh plan inside the same task. Clear plan_id so
    #     the server creates a new ScreenPlan; do not switch task.
    if _wants_regenerate(message) and current_plan_id:
        return _plan("current", next_step_id=plan_step, new_plan=True)

    # 3e. Position swaps are plan-layout edits, not selected-component edits.
    #     Route them through get_current_plan -> draft_intent -> revise_screen_plan.
    if _wants_layout_swap(message) and current_plan_id:
        if plan_status == "needs_input":
            return _plan("current")
        if plan_status in _OPEN_PLAN:
            return _plan("current", next_step_id="get_current_plan")
        return _ask(
            "layout_plan_required",
            "请先在方案卡片确认或重新生成一版方案，再告诉我哪两个模块互换位置。",
        )

    # 4. Explicit placement choice + a new generation and no task placement →
    #    hand the switch to the proxy. This turn writes nothing.
    if intent_kind == "create" and placement_confirmed and placement_known:
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
            return _resolve_edit_target()
        if intent_kind == "modify_screen":
            if current_plan_id and plan_status in _OPEN_PLAN:
                return _plan("current")
            if screen_id:
                return _plan("current")
            return _ask("target_screen", "还没有打开大屏。请先进入一张大屏，或明确说「新建一张销售大屏」。")
        if intent_kind == "confirm":
            return _ask("confirm_plan", "确认要在方案卡片里点「按此生成」，或直接回复「按这个生成」。")
        if intent_kind == "cancel":
            return _ask("cancel_plan", "本轮已取消，没有写入大屏。")
        if intent_kind == "query":
            return _plan("current", next_step_id="query_dataset")
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
        chosen = _primary_selection()
        if chosen is None:
            names = "、".join(_label(item) for item in selected)
            return _ask(
                "target_component",
                f"你选中了 {len(selected)} 个组件（{names}）。以哪个为主？我只改它，其余不动。",
            )
        if bool(getattr(chosen, "locked", False)):
            return _locked_ask(chosen)
        return _edit(str(chosen.component_id))
    if len(selected) == 1:
        chosen = selected[0]
        if bool(getattr(chosen, "locked", False)):
            return _locked_ask(chosen)
        return _edit(str(chosen.component_id))

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
        if intent_kind == "modify_screen":
            return _ask(
                "edit_scope",
                "你是想改选中的组件，还是调整整张屏的布局？我只动你确认的那一处。",
            )
        # Low confidence asks one specific question; it never guesses.
        return _ask(
            "target_intent",
            "我不确定这一句是要改选中的组件、调整整张屏，还是新建/查数据。请直接说：改选中的组件、调整整张屏，或新建一张。",
        )
    return _ask(
        "target_component",
        f"当前大屏有 {int(count)} 个组件。你想改哪一个？请先在画布上选中它，再发一次；也可以直接告诉我组件名称。",
    )

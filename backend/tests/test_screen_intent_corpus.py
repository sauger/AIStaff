from __future__ import annotations

import pytest

from app.session.turn_context import (
    ClarificationAnswer,
    ComponentSummary,
    ScreenIntent,
    ScreenTurnContext,
    SelectedComponent,
)
from app.workflow_packs.screen.binder import bind_screen


def _ctx(
    *,
    screen_id: str | None = "scr_1",
    count: int | None = 5,
    blank: bool | None = False,
    selected: list[str] | None = None,
    primary_component_id: str | None = None,
    components: list[str] | None = None,
    plan_status: str | None = None,
    workspace: dict | None = None,
    task_placement: str | None = None,
    task_brief: dict | None = None,
    plan_id: str | None = None,
    task_id: str = "task_1",
    clarification_answer: ClarificationAnswer | None = None,
) -> ScreenTurnContext:
    return ScreenTurnContext(
        project_id="proj_1" if screen_id else None,
        screen_id=screen_id,
        screen_name="销售大屏" if screen_id else None,
        revision=4 if screen_id else None,
        component_count=count,
        blank=blank,
        primary_component_id=primary_component_id,
        components=[
            ComponentSummary(component_id=item, name=item, component_type="text")
            for item in (components or [])
        ],
        selected=[
            SelectedComponent(component_id=item, name=item, component_type="text")
            for item in (selected or [])
        ],
        plan_status=plan_status,
        workspace=workspace,
        placement=task_placement,
        task_brief=task_brief,
        task_id=task_id,
        task_status="active",
        plan_id=plan_id,
        clarification_answer=clarification_answer,
    )


def _bind(context: ScreenTurnContext, message: str, intent: ScreenIntent):
    return bind_screen(
        turn_context=context.model_copy(update={"intent": intent}),
        slots={},
        metadata={},
        user_message=message,
    )


CASES = [
    ("new-sales", "做一个销售大屏", _ctx(screen_id=None, count=None), ScreenIntent(kind="create", target="new", confidence=0.95), "plan", "new", []),
    ("new-erp", "新建 ERP 项目大屏", _ctx(screen_id=None, count=None), ScreenIntent(kind="create", target="new", confidence=0.9), "plan", "new", []),
    ("new-explicit", "另做一个经营看板", _ctx(), ScreenIntent(kind="create", target="new", confidence=0.9), "plan", "new", []),
    ("blank-current", "补全这个空白屏", _ctx(count=0, blank=True), ScreenIntent(kind="create", target="current", confidence=0.95), "plan", "current", []),
    ("blank-current-2", "在这个屏上加内容", _ctx(count=2, blank=True), ScreenIntent(kind="create", target="current", confidence=0.9), "plan", "current", []),
    ("nonblank-current", "在这个屏上做销售看板", _ctx(count=5), ScreenIntent(kind="create", target="current", confidence=0.95), "plan", "current", []),
    ("nonblank-unspecified", "做一个销售大屏", _ctx(count=5), ScreenIntent(kind="create", target="unspecified", confidence=0.9), "edit", "", ["target_placement"]),
    ("nonblank-unspecified-2", "再加一个图", _ctx(count=7), ScreenIntent(kind="create", target="unspecified", confidence=0.9), "edit", "", ["target_placement"]),
    ("modify-selected-title", "标题改大", _ctx(selected=["cmp_title"]), ScreenIntent(kind="modify_selected", target="unspecified", confidence=0.95), "edit", "", []),
    ("modify-selected-metric", "这个指标换个字段", _ctx(selected=["cmp_metric"]), ScreenIntent(kind="modify_selected", target="current", confidence=0.95), "edit", "", []),
    ("modify-selected-none", "把标题改大", _ctx(), ScreenIntent(kind="modify_selected", target="unspecified", confidence=0.95), "edit", "", ["target_component"]),
    ("modify-selected-multi", "改一下", _ctx(selected=["a", "b"]), ScreenIntent(kind="modify_selected", target="unspecified", confidence=0.9), "edit", "", ["target_component"]),
    ("modify-screen", "整屏改成深色", _ctx(), ScreenIntent(kind="modify_screen", target="current", confidence=0.9), "plan", "current", []),
    ("modify-screen-2", "换一个布局", _ctx(count=3), ScreenIntent(kind="modify_screen", target="current", confidence=0.9), "plan", "current", []),
    ("modify-screen-no-target", "改一下整张屏", _ctx(screen_id=None, count=None), ScreenIntent(kind="modify_screen", target="current", confidence=0.9), "edit", "", ["target_screen"]),
    ("query-erp", "ERP 合同额多少", _ctx(), ScreenIntent(kind="query", target="unspecified", confidence=0.95), "plan", "current", []),
    ("query-sales", "本月销售额是多少", _ctx(), ScreenIntent(kind="query", target="unspecified", confidence=0.95), "plan", "current", []),
    ("query-trend", "最近趋势怎么样", _ctx(), ScreenIntent(kind="query", target="unspecified", confidence=0.9), "plan", "current", []),
    ("confirm", "可以", _ctx(plan_status="needs_confirmation"), ScreenIntent(kind="confirm", target="unspecified", confidence=0.95), "edit", "", ["confirm_plan"]),
    ("confirm-2", "按这个生成", _ctx(plan_status="ready"), ScreenIntent(kind="confirm", target="unspecified", confidence=0.95), "edit", "", ["confirm_plan"]),
    ("confirm-3", "就这样", _ctx(plan_status="needs_confirmation"), ScreenIntent(kind="confirm", target="unspecified", confidence=0.95), "edit", "", ["confirm_plan"]),
    ("cancel", "算了不做了", _ctx(), ScreenIntent(kind="cancel", target="unspecified", confidence=0.9), "edit", "", ["cancel_plan"]),
    ("cancel-2", "取消这个方案", _ctx(plan_status="needs_confirmation"), ScreenIntent(kind="cancel", target="unspecified", confidence=0.9), "edit", "", ["cancel_plan"]),
    ("help", "你能做什么", _ctx(), ScreenIntent(kind="help", target="unspecified", confidence=0.9), "edit", "", ["help"]),
    ("help-2", "怎么用", _ctx(), ScreenIntent(kind="help", target="unspecified", confidence=0.9), "edit", "", ["help"]),
    ("low-confidence", "做一个大屏", _ctx(count=0, blank=True), ScreenIntent(kind="help", target="unspecified", confidence=0.3), "plan", "current", []),
    ("negated-new-selected", "不要新建，把标题改大", _ctx(selected=["cmp_title"]), ScreenIntent(kind="modify_selected", target="unspecified", confidence=0.9), "edit", "", []),
    ("explicit-new-selected", "新建一张销售大屏", _ctx(selected=["cmp_title"]), ScreenIntent(kind="create", target="new", confidence=0.95), "plan", "new", []),
    ("blank-unspecified", "做一个经营大屏", _ctx(screen_id=None, count=None), ScreenIntent(kind="create", target="unspecified", confidence=0.9), "plan", "", []),
    ("nonblank-plan-open", "再补一个图", _ctx(count=5, plan_status="needs_confirmation"), ScreenIntent(kind="create", target="unspecified", confidence=0.9), "edit", "", ["target_placement"]),
    # --- acceptance dialogues (design §8) ---
    ("acc-empty-workspace-create", "帮我生成一个 ERP 大屏",
     _ctx(screen_id=None, count=None, workspace={"screen_count": 0, "screens": [], "empty": True}),
     ScreenIntent(kind="create", target="new", confidence=0.95), "plan", "new", []),
    ("acc-nonempty-workspace-asks", "帮我生成一个 ERP 大屏",
     _ctx(screen_id=None, count=None, workspace={
         "screen_count": 3, "empty": False,
         "screens": [
             {"id": "s1", "name": "销售大屏", "blank": False},
             {"id": "s2", "name": "库存大屏", "blank": False},
             {"id": "s3", "name": "财务大屏", "blank": False},
         ]}),
     ScreenIntent(kind="create", target="unspecified", confidence=0.95), "edit", "", ["target_placement"]),
    ("acc-current-screen-asks", "生成 ERP",
     _ctx(count=12, blank=False),
     ScreenIntent(kind="create", target="unspecified", confidence=0.95), "edit", "", ["target_placement"]),
    ("acc-second-generation-current", "重新做一张",
     _ctx(count=12, blank=False),
     ScreenIntent(kind="create", target="current", confidence=0.95, placement_confirmed=True, new_generation=True),
     "task_transition", "", ["task_transition"]),
    ("acc-task-brief-transitioned-new", "帮我生成一个 ERP 大屏",
     _ctx(screen_id=None, count=None, task_brief={"transitioned": True, "placement": "new", "brief": "用户要新建 ERP；数据未定。"}),
     ScreenIntent(kind="create", target="new", confidence=0.95, placement_confirmed=True, new_generation=True),
     "plan", "new", []),
    ("acc-task-placement-new-no-reask", "帮我生成一个 ERP 大屏",
     _ctx(screen_id=None, count=None, workspace={"screen_count": 3, "screens": [], "empty": False}, task_placement="new"),
     ScreenIntent(kind="create", target="unspecified", confidence=0.9),
     "plan", "new", []),
    # --- component-edit utterances must never route to create/plan ---
    ("cmp-change-type-selected", "把选中的组件改为饼图",
     _ctx(selected=["cmp_pie"], primary_component_id="cmp_pie"),
     ScreenIntent(kind="modify_selected", target="current", confidence=0.95),
     "edit", "", []),
    ("cmp-change-type-named", "部门合同额换成折线图",
     _ctx(components=["合同总额", "部门合同额"]),
     ScreenIntent(kind="modify_selected", target="current", confidence=0.95, component="部门合同额"),
     "edit", "", []),
    ("cmp-change-title", "标题改成合同总额",
     _ctx(selected=["cmp_title"], primary_component_id="cmp_title"),
     ScreenIntent(kind="modify_selected", target="current", confidence=0.95),
     "edit", "", []),
    ("cmp-change-data", "用这份数据替换",
     _ctx(selected=["cmp_chart"], primary_component_id="cmp_chart"),
     ScreenIntent(kind="modify_selected", target="current", confidence=0.9),
     "edit", "", []),
    ("cmp-multi-primary", "改成饼图",
     _ctx(selected=["cmp_a", "cmp_b"], primary_component_id="cmp_b"),
     ScreenIntent(kind="modify_selected", target="current", confidence=0.9),
     "edit", "", []),
    ("cmp-no-selection-asks", "改成饼图",
     _ctx(),
     ScreenIntent(kind="modify_selected", target="current", confidence=0.9),
     "edit", "", ["target_component"]),
    ("cmp-indecipherable-asks-either-or", "这个不太对",
     _ctx(selected=["cmp_a"], primary_component_id="cmp_a"),
     ScreenIntent(kind="create", target="unspecified", confidence=0.5),
     "edit", "", ["edit_or_screen"]),
]


@pytest.mark.parametrize("name,message,context,intent,mode,placement,missing", CASES)
def test_screen_intent_routing_corpus(name, message, context, intent, mode, placement, missing):
    outcome = _bind(context, message, intent)
    assert outcome.bind["mode"] == mode, name
    assert outcome.bind["placement_hint"] == placement, name
    assert outcome.missing_fields == missing, name


def test_clarification_ordinal_does_not_cross_tasks() -> None:
    answer = ClarificationAnswer(
        planId="plan_old",
        questionId="q_old",
        optionIds=["focus.old"],
        draftPatch={"slots": [{"title": "旧指标", "role": "metric", "measures": ["旧指标"]}]},
    )
    context = _ctx(
        screen_id=None,
        count=None,
        plan_id="plan_old",
        plan_status="needs_input",
        task_id="task_2",
        clarification_answer=answer,
    )
    outcome = bind_screen(
        turn_context=context,
        slots={
            "plan_draft": {"goal": "旧任务"},
            "plan_clarification": {
                "id": "q_old",
                "options": [{"id": "focus.old"}],
                "multiple": True,
                "allowFreeText": True,
            },
            "clarification_plan_id": "plan_old",
            "clarification_task_id": "task_1",
        },
        metadata={},
        user_message="1",
    )

    assert outcome.missing_fields == ["clarification_stale"]
    assert outcome.bind["clarification_stale"] is True
    assert outcome.bind["plan_draft"] is None
    assert outcome.bind["mode"] == "plan"
    assert "task_transition" not in outcome.bind

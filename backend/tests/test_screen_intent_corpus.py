from __future__ import annotations

import pytest

from app.session.turn_context import ScreenIntent, ScreenTurnContext, SelectedComponent
from app.workflow_packs.screen.binder import bind_screen


def _ctx(
    *,
    screen_id: str | None = "scr_1",
    count: int | None = 5,
    blank: bool | None = False,
    selected: list[str] | None = None,
    plan_status: str | None = None,
) -> ScreenTurnContext:
    return ScreenTurnContext(
        project_id="proj_1" if screen_id else None,
        screen_id=screen_id,
        revision=4 if screen_id else None,
        component_count=count,
        blank=blank,
        selected=[
            SelectedComponent(component_id=item, name=item, component_type="text")
            for item in (selected or [])
        ],
        plan_status=plan_status,
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
    ("query-erp", "ERP 合同额多少", _ctx(), ScreenIntent(kind="query", target="unspecified", confidence=0.95), "edit", "", ["query_dataset"]),
    ("query-sales", "本月销售额是多少", _ctx(), ScreenIntent(kind="query", target="unspecified", confidence=0.95), "edit", "", ["query_dataset"]),
    ("query-trend", "最近趋势怎么样", _ctx(), ScreenIntent(kind="query", target="unspecified", confidence=0.9), "edit", "", ["query_dataset"]),
    ("confirm", "可以", _ctx(plan_status="needs_confirmation"), ScreenIntent(kind="confirm", target="unspecified", confidence=0.95), "edit", "", ["confirm_plan"]),
    ("confirm-2", "按这个生成", _ctx(plan_status="ready"), ScreenIntent(kind="confirm", target="unspecified", confidence=0.95), "edit", "", ["confirm_plan"]),
    ("confirm-3", "就这样", _ctx(plan_status="needs_confirmation"), ScreenIntent(kind="confirm", target="unspecified", confidence=0.95), "edit", "", ["confirm_plan"]),
    ("cancel", "算了不做了", _ctx(), ScreenIntent(kind="cancel", target="unspecified", confidence=0.9), "edit", "", ["cancel_plan"]),
    ("cancel-2", "取消这个方案", _ctx(plan_status="needs_confirmation"), ScreenIntent(kind="cancel", target="unspecified", confidence=0.9), "edit", "", ["cancel_plan"]),
    ("help", "你能做什么", _ctx(), ScreenIntent(kind="help", target="unspecified", confidence=0.9), "edit", "", ["help"]),
    ("help-2", "怎么用", _ctx(), ScreenIntent(kind="help", target="unspecified", confidence=0.9), "edit", "", ["help"]),
    ("low-confidence", "做一个大屏", _ctx(count=0, blank=True), ScreenIntent(kind="help", target="unspecified", confidence=0.3), "plan", "current", []),
    ("negated-new-selected", "不要新建，把标题改大", _ctx(selected=["cmp_title"]), ScreenIntent(kind="create", target="new", confidence=0.9), "edit", "", []),
    ("explicit-new-selected", "新建一张销售大屏", _ctx(selected=["cmp_title"]), ScreenIntent(kind="create", target="new", confidence=0.95), "plan", "new", []),
    ("blank-unspecified", "做一个经营大屏", _ctx(screen_id=None, count=None), ScreenIntent(kind="create", target="unspecified", confidence=0.9), "plan", "", []),
    ("nonblank-plan-open", "再补一个图", _ctx(count=5, plan_status="needs_confirmation"), ScreenIntent(kind="create", target="unspecified", confidence=0.9), "edit", "", ["target_placement"]),
]


@pytest.mark.parametrize("name,message,context,intent,mode,placement,missing", CASES)
def test_screen_intent_routing_corpus(name, message, context, intent, mode, placement, missing):
    outcome = _bind(context, message, intent)
    assert outcome.bind["mode"] == mode, name
    assert outcome.bind["placement_hint"] == placement, name
    assert outcome.missing_fields == missing, name

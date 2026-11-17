from __future__ import annotations

import pytest

from app.core.task_request_compiler import CapabilityDescriptor, CapabilityManifest
from app.db.models import Skill
from app.session.turn_context import (
    BindingSummary,
    ClarificationAnswer,
    ComponentSummary,
    ScreenIntent,
    ScreenTurnContext,
    SelectedComponent,
)
from app.workflow_packs.screen.binder import bind_screen
from app.workflow_packs.screen.operations import compile_operations, validate_change_plan
from app.workflow_packs.screen.plan import (
    build_screen_plan_draft,
    merge_clarification_draft,
    plan_reply,
)
from app.workflow_packs.screen.sop import build_screen_sop, screen_sop_content
from app.workflows import WorkflowPack, WorkflowRuntime
from app.workflows.registry import BindError


def _context(
    *,
    screen_id: str | None = "scr_1",
    count: int | None = 5,
    blank: bool | None = None,
    selected: list[SelectedComponent] | None = None,
    primary_component_id: str | None = None,
    components: list[ComponentSummary] | None = None,
    revision: int | None = 11,
    plan_id: str | None = None,
    plan_status: str | None = None,
    confirm_plan: bool = False,
    intent: ScreenIntent | None = None,
    task_id: str | None = None,
    placement: str | None = None,
    workspace: dict[str, object] | None = None,
    task_brief: dict[str, object] | None = None,
    clarification_answer: ClarificationAnswer | None = None,
) -> ScreenTurnContext:
    return ScreenTurnContext(
        project_id="proj_1",
        screen_id=screen_id,
        screen_name="销售大屏",
        revision=revision,
        component_count=count,
        blank=blank,
        selected=selected or [],
        primary_component_id=primary_component_id,
        components=components or [],
        plan_id=plan_id,
        plan_status=plan_status,
        confirm_plan=confirm_plan,
        intent=intent,
        task_id=task_id,
        placement=placement,
        workspace=workspace,
        task_brief=task_brief,
        clarification_answer=clarification_answer,
    )


def _bind(context: ScreenTurnContext, message: str = "", slots: dict | None = None, **metadata: object):
    return bind_screen(
        turn_context=context,
        slots=slots or {},
        metadata=dict(metadata),
        user_message=message,
    )


_CATALOG = {
    "datasets": [
        {
            "slug": "erp_projects",
            "name": "ERP 项目",
            "datasetVersionId": "ver_1",
            "fields": [
                {
                    "fieldId": "f_name",
                    "displayName": "项目名称",
                    "role": "dimension",
                    "type": "string",
                },
                {
                    "fieldId": "f_progress",
                    "displayName": "进度",
                    "role": "measure",
                    "type": "number",
                },
                {
                    "fieldId": "f_amount",
                    "displayName": "合同额",
                    "role": "measure",
                    "type": "number",
                },
                {
                    "fieldId": "f_month",
                    "displayName": "月份",
                    "role": "dimension",
                    "type": "string",
                },
            ],
        }
    ]
}


def test_binder_routes_a_new_screen_to_the_plan_branch() -> None:
    outcome = _bind(_context(screen_id=None, count=None), "帮我做一个大屏")

    assert outcome.missing_fields == []
    # Normal generation plans first; it never creates a screen before the
    # user confirms on the PlanCard.
    assert outcome.next_step_id == "describe_catalog"
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["placement_hint"] == "new"
    assert outcome.bind["screen_id"] == ""


def test_binder_keeps_the_message_on_the_plan_branch() -> None:
    outcome = _bind(_context(screen_id=None, count=None), "做一张 ERP 项目经营大屏")

    assert outcome.bind["mode"] == "plan"
    assert outcome.next_step_id == "describe_catalog"


def test_binder_plans_the_current_empty_screen() -> None:
    outcome = _bind(_context(count=0), "在这个大屏上生成一个销售看板")

    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["screen_id"] == "scr_1"
    assert outcome.next_step_id == "describe_catalog"


def test_binder_plans_a_blank_current_screen_without_a_create_phrase() -> None:
    outcome = _bind(_context(count=1, blank=True), "做一个销售经营大屏")

    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["placement_hint"] == "current"
    assert outcome.next_step_id == "describe_catalog"


def test_binder_edits_the_single_selected_component() -> None:
    outcome = _bind(
        _context(
            selected=[
                SelectedComponent(
                    component_id="kpi_3",
                    name="合同总额",
                    component_type="metric",
                )
            ]
        ),
        "把这个指标改成合同总额",
    )

    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == ["kpi_3"]
    assert outcome.next_step_id == "resolve"


def test_binder_edits_selected_component_when_task_placement_is_current() -> None:
    # A task created with placement=current must not force every later turn
    # onto the plan spine: an explicit component edit still edits the selection.
    outcome = _bind(
        _context(
            selected=[
                SelectedComponent(
                    component_id="kpi_3",
                    name="合同总额",
                    component_type="metric",
                )
            ],
            intent=ScreenIntent(kind="modify_selected", confidence=0.95),
            placement="current",
        ),
        "把选中的组件改成柱状图",
    )

    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == ["kpi_3"]
    assert outcome.next_step_id == "resolve"


def test_binder_asks_when_multiple_components_are_selected() -> None:
    # No primary: ask which one is the target (old clients land here too).
    outcome = _bind(
        _context(
            selected=[
                SelectedComponent(component_id="kpi_1", name="甲", component_type="metric"),
                SelectedComponent(component_id="kpi_2", name="乙", component_type="metric"),
            ]
        ),
        "把这个改一下",
    )

    assert outcome.missing_fields == ["target_component"]
    assert "以哪个为主" in outcome.question
    assert "甲" in outcome.question and "乙" in outcome.question
    assert outcome.next_step_id is None


def test_binder_edits_only_the_primary_selection() -> None:
    outcome = _bind(
        _context(
            selected=[
                SelectedComponent(component_id="kpi_1", name="甲", component_type="metric"),
                SelectedComponent(component_id="kpi_2", name="乙", component_type="metric"),
                SelectedComponent(component_id="chart_3", name="丙", component_type="pie"),
            ],
            primary_component_id="chart_3",
        ),
        "改成饼图",
    )

    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == ["chart_3"]
    assert outcome.missing_fields == []


def test_binder_primary_falls_back_to_selection_membership() -> None:
    # A stale primary not in selected must not be trusted: pick nothing, ask.
    outcome = _bind(
        _context(
            selected=[SelectedComponent(component_id="kpi_1", name="甲", component_type="metric")],
            primary_component_id="gone_9",
        ),
        "改一下",
    )

    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == ["kpi_1"]


def test_binder_refuses_to_edit_a_locked_component() -> None:
    outcome = _bind(
        _context(
            selected=[SelectedComponent(component_id="kpi_1", name="甲", component_type="metric", locked=True)]
        ),
        "把甲改成饼图",
    )

    assert outcome.missing_fields == ["locked_component"]
    assert "已锁定" in outcome.question
    assert outcome.bind["targets"] == []
    assert outcome.next_step_id is None


def test_binder_resolves_a_point_named_component() -> None:
    outcome = _bind(
        _context(
            intent=ScreenIntent(kind="modify_selected", confidence=0.9, component="部门合同额"),
            components=[
                ComponentSummary(component_id="kpi_1", name="合同总额", component_type="metric"),
                ComponentSummary(component_id="chart_2", name="部门合同额", component_type="bar"),
            ],
        ),
        "把部门合同额改成饼图",
    )

    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == ["chart_2"]


def test_binder_asks_when_point_name_is_duplicated() -> None:
    outcome = _bind(
        _context(
            intent=ScreenIntent(kind="modify_selected", confidence=0.9, component="合同额"),
            components=[
                ComponentSummary(component_id="chart_1", name="合同额", component_type="bar"),
                ComponentSummary(component_id="chart_2", name="合同额", component_type="pie"),
            ],
        ),
        "把合同额改成饼图",
    )

    assert outcome.missing_fields == ["target_component"]
    assert "同名" in outcome.question
    assert outcome.bind["targets"] == []


def test_binder_asks_with_real_options_when_point_name_is_unknown() -> None:
    outcome = _bind(
        _context(
            intent=ScreenIntent(kind="modify_selected", confidence=0.9, component="不存在的东西"),
            components=[
                ComponentSummary(component_id="kpi_1", name="合同总额", component_type="metric"),
                ComponentSummary(component_id="chart_2", name="部门合同额", component_type="bar"),
            ],
        ),
        "把不存在的东西改成饼图",
    )

    assert outcome.missing_fields == ["target_component"]
    assert "没有叫「不存在的东西」的组件" in outcome.question
    assert "合同总额" in outcome.question and "部门合同额" in outcome.question
    assert outcome.bind["targets"] == []


def test_binder_asks_which_component_with_real_names_when_nothing_selected() -> None:
    outcome = _bind(
        _context(
            intent=ScreenIntent(kind="modify_selected", confidence=0.9),
            components=[
                ComponentSummary(component_id="kpi_1", name="合同总额", component_type="metric"),
                ComponentSummary(component_id="chart_2", name="部门合同额", component_type="bar"),
            ],
        ),
        "改成饼图",
    )

    assert outcome.missing_fields == ["target_component"]
    assert "改哪一个组件" in outcome.question
    assert "合同总额" in outcome.question and "部门合同额" in outcome.question
    assert outcome.bind["targets"] == []


def test_binder_selected_plus_ambiguous_create_asks_either_or() -> None:
    outcome = _bind(
        _context(
            selected=[SelectedComponent(component_id="chart_1", name="部门合同额", component_type="bar")],
            intent=ScreenIntent(kind="create", confidence=0.8),
        ),
        "这个不太对，弄一下",
    )

    assert outcome.missing_fields == ["edit_or_screen"]
    assert "改选中的组件" in outcome.question
    assert "调整整张屏" in outcome.question
    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == []
    assert outcome.next_step_id is None


def test_binder_selected_plus_explicit_new_still_plans() -> None:
    outcome = _bind(
        _context(
            selected=[SelectedComponent(component_id="chart_1", name="部门合同额", component_type="bar")],
            intent=ScreenIntent(kind="create", confidence=0.95, target="new"),
        ),
        "新建一张销售大屏",
    )

    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["placement_hint"] == "new"


def test_binder_asks_when_screen_has_components_and_nothing_selected() -> None:
    outcome = _bind(_context(count=5), "把这个改一下")

    assert outcome.missing_fields == ["target_component"]
    assert "5 个组件" in outcome.question
    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == []


def test_binder_fails_closed_without_component_count() -> None:
    with pytest.raises(BindError) as excinfo:
        _bind(_context(count=None), "把这个改一下")

    assert excinfo.value.code == "SCREEN_COMPONENT_COUNT_MISSING"


def test_binder_negated_new_with_selection_stays_on_edit() -> None:
    outcome = _bind(
        _context(
            selected=[
                SelectedComponent(
                    component_id="cmp_title",
                    name="标题",
                    component_type="text",
                )
            ]
        ),
        "不要动当前屏，把标题改大",
    )

    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == ["cmp_title"]


def test_binder_explicit_new_screen_transitions_instead_of_writing() -> None:
    # "重新做一张" is an explicit placement choice; the structured intent
    # carries it (no regex). The turn emits task_transition and writes nothing.
    outcome = _bind(
        _context(
            count=5,
            intent=ScreenIntent(
                kind="create",
                target="new",
                confidence=0.95,
                placement_confirmed=True,
                new_generation=True,
            ),
        ),
        "不要动当前屏，重新做一张销售大屏",
    )

    assert outcome.bind["mode"] == "task_transition"
    transition = outcome.bind["task_transition"]
    assert transition["placement"] == "new"
    assert transition["screenId"] is None
    assert transition["reason"] == "new_screen"
    assert "重新做一张销售大屏" in transition["brief"]


def test_binder_does_not_confirm_or_write_an_open_plan_from_chat() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="needs_confirmation", confirm_plan=True),
        "确认",
    )

    assert outcome.bind["mode"] == "edit"
    assert outcome.missing_fields == ["confirm_plan"]
    assert outcome.next_step_id is None
    assert outcome.bind["plan_id"] == "plan_1"


def test_binder_continues_an_open_plan_on_a_populated_screen() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="needs_input"),
        "改成收入",
    )

    assert outcome.next_step_id is None
    assert outcome.missing_fields == ["follow_up_card"]
    assert outcome.bind["mode"] == "edit"


_COMPONENTS = [
    {"componentId": "kpi_3", "name": "合同总额", "type": "metric"},
    {"componentId": "txt_1", "name": "标题", "type": "text"},
]
_EDIT_BIND = {"mode": "edit", "targets": ["kpi_3"]}
_FILL_BIND = {"mode": "fill", "targets": []}


def _validate(plan: dict, bind: dict = _EDIT_BIND, components=None):
    return validate_change_plan(
        plan,
        bind=bind,
        components=components if components is not None else _COMPONENTS,
    )


def test_plan_allows_one_patch_for_the_bound_target() -> None:
    plan = {
        "change_kind": "update_component",
        "target_component_ids": ["kpi_3"],
        "patches": {"kpi_3": {"props": {"title": "合同总额"}}},
    }

    assert _validate(plan) == ("", "", [])
    operations = compile_operations(plan, components=_COMPONENTS)
    assert operations == [
        {
            "op": "update_component",
            "componentId": "kpi_3",
            "changes": {"props": {"title": "合同总额"}},
        }
    ]


def test_plan_rejects_a_target_outside_the_bound_set() -> None:
    plan = {
        "change_kind": "update_component",
        "target_component_ids": ["txt_1"],
        "patches": {"txt_1": {"props": {"text": "新标题"}}},
    }

    code, message, ask_fields = _validate(plan)

    assert code == "CHANGE_PLAN_OUT_OF_SCOPE"
    assert ask_fields == ["target_component"]
    assert "txt_1" in message


def test_plan_rejects_a_stale_bound_target() -> None:
    plan = {
        "change_kind": "update_component",
        "target_component_ids": ["kpi_3"],
        "patches": {"kpi_3": {"props": {"title": "合同总额"}}},
    }

    code, _message, ask_fields = _validate(plan, components=[])

    assert code == "CHANGE_PLAN_OUT_OF_SCOPE"
    assert ask_fields == ["target_component"]


def test_plan_requires_a_patch_for_every_target() -> None:
    plan = {
        "change_kind": "update_component",
        "target_component_ids": ["kpi_3"],
        "patches": {},
    }

    code, message, ask_fields = _validate(plan)

    assert code == "CHANGE_PLAN_INVALID"
    assert ask_fields == []
    assert "patches" in message


def test_plan_rejects_duplicate_targets() -> None:
    plan = {
        "change_kind": "update_component",
        "target_component_ids": ["kpi_3", "kpi_3"],
        "patches": {"kpi_3": {"props": {"title": "合同总额"}}},
    }

    assert _validate(plan)[0] == "CHANGE_PLAN_INVALID"


def test_plan_rejects_props_outside_the_component_whitelist() -> None:
    plan = {
        "change_kind": "update_component",
        "target_component_ids": ["txt_1"],
        "patches": {"txt_1": {"props": {"title": "不允许的字段"}}},
    }

    code, message, _ = _validate(
        plan,
        bind={"mode": "edit", "targets": ["txt_1"]},
    )

    assert code == "CHANGE_PLAN_INVALID"
    assert "白名单" in message


def test_change_component_type_accepts_exactly_one_target() -> None:
    plan = {
        "change_kind": "change_component_type",
        "target_component_ids": ["kpi_3"],
        "componentType": "line",
        "patches": {"kpi_3": {"props": {"title": "趋势"}}},
    }

    assert _validate(plan) == ("", "", [])
    assert compile_operations(plan, components=_COMPONENTS)[0]["componentType"] == "line"

    plan["target_component_ids"] = ["kpi_3", "txt_1"]
    assert (
        _validate(plan, bind={"mode": "edit", "targets": ["kpi_3", "txt_1"]})[0]
        == "CHANGE_PLAN_INVALID"
    )


def test_fill_mode_only_allows_add_components() -> None:
    update = {
        "change_kind": "update_component",
        "target_component_ids": ["kpi_3"],
        "patches": {"kpi_3": {"props": {"title": "x"}}},
    }
    assert _validate(update, bind=_FILL_BIND, components=[])[0] == "CHANGE_PLAN_INVALID"

    add = {
        "change_kind": "add_components",
        "target_component_ids": ["cmp_line_1"],
        "components": {
            "cmp_line_1": {
                "type": "line",
                "frame": {"x": 0, "y": 0, "width": 6, "height": 4},
                "props": {"title": "销售趋势"},
            }
        },
    }
    assert _validate(add, bind=_FILL_BIND, components=[])[0] == ""

    operations = compile_operations(add, components=[])
    assert operations[0]["op"] == "add_component"
    assert operations[0]["componentId"] == "cmp_line_1"
    assert operations[0]["component"]["type"] == "line"


def test_fill_mode_normalizes_component_defaults_for_the_mcp_contract() -> None:
    add = {
        "change_kind": "add_components",
        "target_component_ids": ["cmp_text_1", "cmp_table_1"],
        "components": {
            "cmp_text_1": {
                "type": "text",
                "name": "标题",
                "frame": {"x": 0, "y": 0, "width": 10, "height": 10},
                "props": {"text": "看板", "fontWeight": "bold", "fontSize": 10},
                "style": {"backgroundColor": "#111111", "padding": 8},
            },
            "cmp_table_1": {
                "type": "table",
                "frame": {"x": 0, "y": 0, "width": 320, "height": 160},
                "props": {"title": "明细"},
            },
        },
    }

    operations = compile_operations(add, components=[])

    assert [op["componentId"] for op in operations] == ["cmp_text_1"]
    component = operations[0]["component"]
    assert component["locked"] is False and component["visible"] is True
    assert component["props"]["fontWeight"] == 700
    assert component["props"]["fontSize"] == 12
    assert component["style"] == {"background": "#111111"}
    assert (component["frame"]["width"], component["frame"]["height"]) == (40, 24)


def test_fill_mode_drops_a_table_without_columns() -> None:
    add = {
        "change_kind": "add_components",
        "target_component_ids": ["cmp_table"],
        "components": {
            "cmp_table": {
                "type": "table",
                "frame": {"x": 0, "y": 0, "width": 320, "height": 160},
                "props": {"title": "明细"},
            }
        },
    }

    assert compile_operations(add, components=[]) == []


def test_fill_mode_rejects_an_existing_component_id() -> None:
    add = {
        "change_kind": "add_components",
        "target_component_ids": ["kpi_3"],
        "components": {
            "kpi_3": {
                "type": "metric",
                "frame": {"x": 0, "y": 0, "width": 3, "height": 2},
            }
        },
    }

    assert _validate(add, bind=_FILL_BIND)[0] == "CHANGE_PLAN_OUT_OF_SCOPE"


def test_fill_mode_requires_type_and_frame() -> None:
    add = {
        "change_kind": "add_components",
        "target_component_ids": ["cmp_new"],
        "components": {"cmp_new": {"type": "line"}},
    }

    assert _validate(add, bind=_FILL_BIND, components=[])[0] == "CHANGE_PLAN_INVALID"


def test_sop_contract_is_valid_and_declares_the_runtime_actions() -> None:
    sop = build_screen_sop()

    assert sop["skill_id"] == "screen.change"
    assert sop["start_node_id"] == "bind"
    assert sop["metadata"]["workflow"]["runtime"] == "workflow_v1"
    assert sop["metadata"]["workflow"]["binder"] == "screen"

    nodes = {node["node_id"]: node for node in sop["nodes"]}
    actions = {
        node_id: node.get("metadata", {}).get("runtime_action")
        for node_id, node in nodes.items()
    }
    assert actions["bind"] == "bind"
    assert actions["plan"] == "structured_llm"
    assert actions["apply"] == "tool"
    assert actions["reply_plan"] == "reply"
    assert set(actions.values()) <= {"bind", "structured_llm", "tool", "ask", "reply"}

    apply_args = nodes["apply"]["metadata"]["arguments"]
    assert apply_args["expectedRevision"] == "$bind.revision"
    assert "screenId" not in apply_args or apply_args["screenId"] == "$bind.screen_id"
    assert nodes["plan"]["metadata"]["handler"] == "plan_change"


def test_sop_content_is_stable_for_tests() -> None:
    assert screen_sop_content()["start_node_id"] == "bind"


def test_sop_blank_canvas_spine_asks_exactly_one_data_question() -> None:
    content = screen_sop_content()
    nodes = {node["node_id"]: node for node in content["nodes"]}

    # create_blank uses MCP create_screen with a blank template and the task id
    args = nodes["create_blank"]["metadata"]["arguments"]
    assert args["mode"] == "new"
    assert args["template"] == "blank"
    assert args["taskId"] == "$bind.task_id"

    # ask_data is terminal: the instruction is the rule, the user-facing
    # first sentence lives in metadata.template (the uncommitted same-turn
    # rework moved the copy out of the instruction).
    assert "ask_data" in content["terminal_node_ids"]
    instruction = nodes["ask_data"]["instruction"]
    assert "只问数据" in instruction
    assert "不猜字段" in instruction
    template = nodes["ask_data"]["metadata"]["template"]
    assert "我帮你先建了一张空白大屏" in template
    assert "空白只是画布，数据和组件我来放" in template
    assert "用哪份数据" in template

    # draft_plan keeps the plan draft deterministic and free of invented data.
    draft = nodes["draft_plan"]["instruction"]
    assert "只填 ScreenPlanDraft" in draft
    assert "不猜指标" in draft
    assert "使用已有数据" in draft
    assert "禁止发明合同数量" in draft

    # spine: bind -> create_blank -> ask_catalog -> ask_data
    edges = {(e["source_node_id"], e["next_node_id"]) for e in content["edges"]}
    assert ("bind", "create_blank") in edges
    assert ("create_blank", "ask_catalog") in edges
    assert ("ask_catalog", "ask_data") in edges


def test_plan_change_maps_metric_prop_to_title():
    from app.workflow_packs.screen.plan import plan_change

    def llm(_system: str, _payload: dict) -> dict:
        return {
            "change_kind": "update_component",
            "target_component_ids": ["cmp_avg_rev"],
            "patches": {"cmp_avg_rev": {"props": {"metric": "合同总额"}}},
            "summary": "改指标",
        }

    result = plan_change(
        user_message="把平均客单改成合同总额",
        turn_context=None,
        bind={
            "mode": "edit",
            "targets": ["cmp_avg_rev"],
            "screen_id": "scr_1",
            "revision": 2,
            "component_count": 9,
        },
        slots={},
        outputs={
            "resolve": {
                "data": {
                    "name": "销售大屏",
                    "revision": 2,
                    "components": [
                        {"componentId": "cmp_avg_rev", "name": "平均客单", "type": "metric"}
                    ],
                }
            }
        },
        metadata={},
        llm=llm,
    )
    assert result["patches"]["cmp_avg_rev"]["props"] == {"title": "合同总额"}
    assert result["operations"] == [
        {
            "op": "update_component",
            "componentId": "cmp_avg_rev",
            "changes": {"props": {"title": "合同总额"}},
        }
    ]


def test_plan_change_coerces_dict_title_to_string():
    from app.workflow_packs.screen.plan import plan_change

    def llm(_system: str, _payload: dict) -> dict:
        return {
            "change_kind": "update_component",
            "target_component_ids": ["cmp_avg_rev"],
            "patches": {
                "cmp_avg_rev": {
                    "name": "合同总额",
                    "props": {"title": {"field": "contract_total"}},
                }
            },
            "summary": "改指标",
        }

    result = plan_change(
        user_message="把平均客单改成合同总额",
        turn_context=None,
        bind={
            "mode": "edit",
            "targets": ["cmp_avg_rev"],
            "screen_id": "scr_1",
            "revision": 2,
            "component_count": 9,
        },
        slots={},
        outputs={
            "resolve": {
                "data": {
                    "name": "销售大屏",
                    "revision": 2,
                    "components": [
                        {"componentId": "cmp_avg_rev", "name": "平均客单", "type": "metric"}
                    ],
                }
            }
        },
        metadata={},
        llm=llm,
    )
    assert result["patches"]["cmp_avg_rev"]["props"] == {"title": "合同总额"}


def test_intent_create_current_fills_the_blank_screen() -> None:
    outcome = _bind(
        _context(count=0, intent=ScreenIntent(kind="create", confidence=0.9, target="current")),
        "补全这个屏",
    )
    assert outcome.missing_fields == []
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["placement_hint"] == "current"


def test_intent_create_nonblank_asks_current_or_new() -> None:
    outcome = _bind(
        _context(count=5, blank=False, intent=ScreenIntent(kind="create", confidence=0.9)),
        "做一个销售大屏",
    )
    assert outcome.bind["mode"] == "edit"
    assert outcome.missing_fields == ["target_placement"]
    assert "覆盖" in (outcome.question or "")


def test_intent_modify_selected_routes_to_edit() -> None:
    outcome = _bind(
        _context(
            selected=[SelectedComponent(component_id="cmp_title", name="标题", component_type="text")],
            intent=ScreenIntent(kind="modify_selected", confidence=0.95),
        ),
        "改大一点",
    )
    assert outcome.bind["mode"] == "edit"
    assert outcome.bind["targets"] == ["cmp_title"]
    assert outcome.next_step_id == "resolve"


def test_intent_modify_selected_without_selection_asks_one_question() -> None:
    outcome = _bind(
        _context(intent=ScreenIntent(kind="modify_selected", confidence=0.95)),
        "把标题改大",
    )
    assert outcome.missing_fields == ["target_component"]
    assert outcome.next_step_id is None


def test_intent_confirm_never_writes_a_plan() -> None:
    outcome = _bind(
        _context(plan_status="needs_confirmation", intent=ScreenIntent(kind="confirm", confidence=0.95)),
        "按这个生成",
    )
    assert outcome.bind["mode"] == "edit"
    assert outcome.missing_fields == ["confirm_plan"]
    assert outcome.next_step_id is None


def test_intent_query_routes_to_read_only_data_answer() -> None:
    outcome = _bind(
        _context(intent=ScreenIntent(kind="query", confidence=0.95)),
        "ERP 合同额多少",
    )
    assert outcome.missing_fields == []
    assert outcome.bind["mode"] == "plan"
    assert outcome.next_step_id == "query_dataset"


def test_low_confidence_intent_falls_back_to_rules() -> None:
    outcome = _bind(
        _context(count=0, intent=ScreenIntent(kind="modify_screen", confidence=0.4)),
        "做一个大屏",
    )
    assert outcome.bind["mode"] == "plan"


def test_binder_continues_on_the_screen_of_a_new_task_after_binding() -> None:
    # A new task creates its blank screen on turn 1; turn 2 must fill that
    # screen, not create a second blank screen (review P0-2).
    outcome = _bind(
        _context(
            screen_id="scr_new",
            count=0,
            blank=True,
            placement="new",
            task_id="task_1",
            workspace={"screen_count": 1, "empty": False, "screens": [{"name": "ERP 大屏"}]},
        ),
        "用项目合同数据做一个销售看板",
    )

    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["placement_hint"] == "current"
    assert outcome.bind["screen_id"] == "scr_new"
    assert outcome.next_step_id == "describe_catalog"


def test_binder_emits_transition_for_explicit_new_generation_after_binding() -> None:
    intent = ScreenIntent(
        kind="create",
        target="new",
        confidence=0.9,
        placement_confirmed=True,
        new_generation=True,
    )
    outcome = _bind(
        _context(
            screen_id="scr_new",
            count=0,
            blank=True,
            placement="new",
            task_id="task_1",
            intent=intent,
            workspace={"screen_count": 1, "empty": False, "screens": [{"name": "ERP 大屏"}]},
        ),
        "再帮我生成一个销售大屏",
    )

    assert outcome.bind["mode"] == "task_transition"
    assert outcome.bind["task_transition"]["placement"] == "new"


# --------------------------------------------------------------------------
# G4: 缺映射只问一次 + 改数据 binding（设计 §6.3/§6.4）

_FIELDS = [
    {"fieldId": "fld_region", "name": "地区", "type": "text"},
    {"fieldId": "fld_contract", "name": "合同额", "type": "number"},
]
_PENDING = {
    "target_component_id": "cmp_chart",
    "component_type": "pie",
    "dataset_version_id": "dsv_1",
    "goal": "改为饼图",
}


def _bound_outputs(*, measures: list | None = None) -> dict:
    binding = {
        "source": "dataset",
        "datasetVersionId": "dsv_1",
        "dimensions": ["fld_region"],
        "measures": (
            measures
            if measures is not None
            else []  # 缺数值字段 → 触发映射追问
        ),
    }
    return {
        "resolve_bound": {
            "data": {
                "name": "销售大屏",
                "revision": 7,
                "components": [
                    {
                        "componentId": "cmp_chart",
                        "name": "部门合同额",
                        "type": "bar",
                        "binding": binding,
                    }
                ],
            }
        },
        "describe_fields": {"data": {"datasetVersionId": "dsv_1", "fields": _FIELDS}},
    }


def _bound_bind() -> dict:
    return {
        "mode": "edit",
        "targets": ["cmp_chart"],
        "screen_id": "scr_1",
        "revision": 7,
        "component_count": 5,
        "resolve_step": "resolve_bound",
        "target_dataset_version_id": "dsv_1",
    }


def test_plan_change_missing_mapping_asks_once_with_pending_edit() -> None:
    from app.workflow_packs.screen.plan import plan_change
    from app.workflows.registry import StructuredStepError

    def llm(_system: str, _payload: dict) -> dict:
        return {
            "change_kind": "change_component_type",
            "target_component_ids": ["cmp_chart"],
            "componentType": "pie",
            "summary": "改为饼图",
        }

    with pytest.raises(StructuredStepError) as excinfo:
        plan_change(
            user_message="把选中的组件改为饼图",
            turn_context=None,
            bind=_bound_bind(),
            slots={},
            outputs=_bound_outputs(),
            metadata={},
            llm=llm,
        )

    assert excinfo.value.code == "FIELD_MAPPING_MISSING"
    assert excinfo.value.ask_fields == ["field_mapping"]
    # 问题里是真实字段名，不是自由文本。
    assert "地区" in excinfo.value.message and "合同额" in excinfo.value.message
    assert excinfo.value.slot_updates == {"pending_edit": _PENDING}


def test_plan_change_pending_answer_executes_without_asking_again() -> None:
    from app.workflow_packs.screen.plan import plan_change

    def llm(_system: str, _payload: dict) -> dict:
        # 用户回答“用地区做分类、合同额做数值”后映射出的 binding。
        return {
            "change_kind": "change_component_type",
            "target_component_ids": ["cmp_chart"],
            "componentType": "pie",
            "binding": {
                "source": "dataset",
                "datasetVersionId": "dsv_1",
                "dimensions": ["fld_region"],
                "measures": [
                    {"fieldId": "fld_contract", "aggregate": "sum", "alias": "contract_sum"}
                ],
            },
            "summary": "改为饼图",
        }

    result = plan_change(
        user_message="用地区做分类，合同额做数值",
        turn_context=None,
        bind={**_bound_bind(), "pending_edit": _PENDING},
        slots={},
        outputs=_bound_outputs(),
        metadata={},
        llm=llm,
    )

    assert result["operations"] == [
        {
            "op": "change_component_type",
            "componentId": "cmp_chart",
            "componentType": "pie",
            "binding": {
                "source": "dataset",
                "datasetVersionId": "dsv_1",
                "dimensions": ["fld_region"],
                "measures": [
                    {"fieldId": "fld_contract", "aggregate": "sum", "alias": "contract_sum"}
                ],
            },
        }
    ]


def test_plan_change_unrecognized_answer_neither_writes_nor_reasks() -> None:
    from app.workflow_packs.screen.plan import plan_change
    from app.workflows.registry import StructuredStepError

    def llm(_system: str, _payload: dict) -> dict:
        # 回答没有映射成 binding：计划仍缺数值字段。
        return {
            "change_kind": "change_component_type",
            "target_component_ids": ["cmp_chart"],
            "componentType": "pie",
            "summary": "改为饼图",
        }

    with pytest.raises(StructuredStepError) as excinfo:
        plan_change(
            user_message="就随便弄弄",
            turn_context=None,
            bind={**_bound_bind(), "pending_edit": _PENDING},
            slots={},
            outputs=_bound_outputs(),
            metadata={},
            llm=llm,
        )

    assert excinfo.value.code == "FIELD_MAPPING_UNRECOGNIZED"
    # 不写（无 operations）、不重复追问（无 ask_fields）、不重写 pending。
    assert excinfo.value.ask_fields == []
    assert excinfo.value.slot_updates is None
    assert "未改动" in excinfo.value.message or "没有改动" in excinfo.value.message


def test_plan_change_rebind_compiles_bind_component_for_target_only() -> None:
    from app.workflow_packs.screen.plan import plan_change

    def llm(_system: str, _payload: dict) -> dict:
        return {
            "change_kind": "bind_component",
            "target_component_ids": ["cmp_chart"],
            "binding": {
                "source": "dataset",
                "datasetVersionId": "dsv_1",
                "dimensions": ["fld_region"],
                "measures": [
                    {"fieldId": "fld_contract", "aggregate": "sum", "alias": "contract_sum"}
                ],
            },
            "summary": "换数据",
        }

    result = plan_change(
        user_message="这个图改用 ERP 数据",
        turn_context=None,
        bind=_bound_bind(),
        slots={},
        outputs=_bound_outputs(),
        metadata={},
        llm=llm,
    )

    # 改数据只产生一个 bind_component op，只作用于目标组件的一个数据集。
    assert result["operations"] == [
        {
            "op": "bind_component",
            "componentId": "cmp_chart",
            "binding": {
                "source": "dataset",
                "datasetVersionId": "dsv_1",
                "dimensions": ["fld_region"],
                "measures": [
                    {"fieldId": "fld_contract", "aggregate": "sum", "alias": "contract_sum"}
                ],
            },
        }
    ]


def test_plan_change_rejects_fieldids_outside_the_catalog() -> None:
    from app.workflow_packs.screen.plan import plan_change
    from app.workflows.registry import StructuredStepError

    def llm(_system: str, _payload: dict) -> dict:
        return {
            "change_kind": "bind_component",
            "target_component_ids": ["cmp_chart"],
            "binding": {
                "datasetVersionId": "dsv_1",
                "dimensions": ["fld_made_up"],
                "measures": [
                    {"fieldId": "fld_contract", "aggregate": "sum", "alias": "contract_sum"}
                ],
            },
            "summary": "换数据",
        }

    with pytest.raises(StructuredStepError) as excinfo:
        plan_change(
            user_message="改用编的字段",
            turn_context=None,
            bind=_bound_bind(),
            slots={},
            outputs=_bound_outputs(),
            metadata={},
            llm=llm,
        )

    assert excinfo.value.code == "FIELD_MAPPING_UNRECOGNIZED"
    assert "fld_made_up" in excinfo.value.message


def test_binder_forwards_pending_edit_on_the_bound_spine() -> None:
    outcome = _bind(
        _context(
            selected=[
                SelectedComponent(
                    component_id="cmp_chart",
                    name="部门合同额",
                    component_type="bar",
                    binding=BindingSummary(
                        datasetVersionId="dsv_1", dimensions=["fld_region"], measures=[]
                    ),
                )
            ],
            primary_component_id="cmp_chart",
            intent=ScreenIntent(kind="modify_selected", confidence=0.95, target="current"),
        ),
        "用地区做分类，合同额做数值",
        slots={"pending_edit": _PENDING},
    )

    assert outcome.bind["targets"] == ["cmp_chart"]
    assert outcome.bind["resolve_step"] == "resolve_bound"
    assert outcome.bind["target_dataset_version_id"] == "dsv_1"
    assert outcome.bind["pending_edit"] == _PENDING
    assert outcome.next_step_id == "resolve_bound"


def test_binder_clears_pending_edit_when_the_user_changes_topic() -> None:
    outcome = _bind(
        _context(
            screen_id=None,
            count=None,
            intent=ScreenIntent(kind="create", confidence=0.95, target="new"),
        ),
        "重新做一张销售大屏",
        slots={"pending_edit": _PENDING},
    )

    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["pending_edit"] is None


def test_binder_clears_pending_edit_when_the_target_differs() -> None:
    outcome = _bind(
        _context(
            selected=[SelectedComponent(component_id="cmp_other", name="别的图", component_type="line")],
            primary_component_id="cmp_other",
            intent=ScreenIntent(kind="modify_selected", confidence=0.95, target="current"),
        ),
        "把这个也改一下",
        slots={"pending_edit": _PENDING},
    )

    assert outcome.bind["targets"] == ["cmp_other"]
    assert outcome.bind["pending_edit"] is None


# --- 2026-11-12 clarification + confirmation contract -----------------------


def test_plan_draft_for_a_broad_request_uses_the_default_template() -> None:
    message = "帮我生成一个新的 ERP"
    draft = build_screen_plan_draft(
        user_message=message,
        turn_context=_context(
            intent=ScreenIntent(kind="create", confidence=0.9, target="new")
        ),
        bind={},
        catalog=_CATALOG,
    )

    assert draft["goal"] == message
    assert [slot["role"] for slot in draft["slots"]] == ["metric", "trend", "compare", "detail"]
    assert draft["sources"] == ["erp_projects"]
    assert "screenTitle" not in draft


def test_add_request_on_a_nonempty_screen_is_marked_append() -> None:
    draft = build_screen_plan_draft(
        user_message="增加一个进度指标",
        turn_context=_context(
            screen_id="scr_1",
            count=5,
            intent=ScreenIntent(kind="create", confidence=0.9, target="current"),
        ),
        bind={},
        catalog=_CATALOG,
    )

    assert draft["append"] is True
    assert draft["slots"]


def test_default_plan_slot_titles_are_human_readable() -> None:
    draft = build_screen_plan_draft(
        user_message="帮我生成一个 ERP 大屏",
        turn_context=_context(
            intent=ScreenIntent(kind="create", confidence=0.9, target="new")
        ),
        bind={},
        catalog=_CATALOG,
    )

    assert [slot["title"] for slot in draft["slots"]] == [
        "合同额",
        "合同额分布",
        "各项目合同额",
        "项目经营明细",
    ]


def test_plan_draft_keeps_an_explicit_title_separate_from_the_goal() -> None:
    message = "标题是ERP项目经营大屏，帮我生成一个新的 ERP"
    draft = build_screen_plan_draft(
        user_message=message,
        turn_context=_context(),
        bind={},
        catalog=_CATALOG,
    )

    assert draft["goal"] == message
    assert draft["screenTitle"] == "ERP项目经营大屏"


def test_plan_draft_marks_an_explicit_average_aggregation() -> None:
    draft = build_screen_plan_draft(
        user_message="帮我生成一个 ERP，看平均进度",
        turn_context=_context(
            intent=ScreenIntent(
                kind="create",
                confidence=0.9,
                target="new",
                metrics=["进度"],
            )
        ),
        bind={},
        catalog=_CATALOG,
    )

    slot = draft["slots"][0]
    assert slot["measures"] == ["进度"]
    assert slot["measureAggregations"] == ["avg"]
    assert slot["role"] == "metric"
    assert slot["chartType"] == "metric"

    plain = build_screen_plan_draft(
        user_message="帮我生成一个 ERP，看进度",
        turn_context=_context(),
        bind={},
        catalog=_CATALOG,
    )
    assert "measureAggregations" not in plain["slots"][0]

    for message, aggregate in (
        ("帮我生成一个 ERP，看最高进度", "max"),
        ("帮我生成一个 ERP，看最低进度", "min"),
        ("帮我生成一个 ERP，看合计合同额", "sum"),
    ):
        aggregate_draft = build_screen_plan_draft(
            user_message=message,
            turn_context=_context(),
            bind={},
            catalog=_CATALOG,
        )
        assert aggregate_draft["slots"][0]["measureAggregations"] == [aggregate]


def test_plan_draft_does_not_turn_a_policy_phrase_into_a_source() -> None:
    draft = build_screen_plan_draft(
        user_message="帮我生成一个 ERP，使用已有数据",
        turn_context=_context(),
        bind={},
        catalog=_CATALOG,
    )

    assert "sources" not in draft
    assert "unmatched" not in draft

    explicit = build_screen_plan_draft(
        user_message="用 ERP 项目数据生成一个大屏",
        turn_context=_context(),
        bind={},
        catalog=_CATALOG,
    )
    assert explicit["sources"] == ["erp_projects"]


def test_plan_draft_does_not_invent_contract_count() -> None:
    draft = build_screen_plan_draft(
        user_message="帮我生成一个 ERP，看合同数量",
        turn_context=_context(
            intent=ScreenIntent(
                kind="create",
                confidence=0.9,
                target="new",
                metrics=["合同数量"],
            )
        ),
        bind={},
        catalog=_CATALOG,
    )

    # The catalog has 合同额, not 合同数量; never bind one as the other.
    assert "slots" not in draft
    assert "合同额" not in str(draft)
    assert "合同额" not in str(draft)


def test_binder_resumes_the_same_plan_from_a_clarification_answer() -> None:
    patch = {
        "sources": ["ver_1"],
        "slots": [
            {
                "title": "进度",
                "role": "metric",
                "measures": ["进度"],
                "measureAggregations": ["avg"],
                "chartType": "metric",
            }
        ],
    }
    answer = ClarificationAnswer(
        planId="plan_1",
        questionId="q1",
        optionIds=["focus.progress"],
        labels=["平均进度"],
        datasetVersionIds=["ver_1"],
        draftPatch=patch,
    )
    context = _context(
        screen_id=None,
        count=None,
        plan_id="plan_1",
        plan_status="needs_input",
        task_id="task_1",
        clarification_answer=answer,
    )
    slots = {
        "plan_draft": {
            "goal": "帮我生成一个新的 ERP",
            "screenTitle": "ERP 项目经营大屏",
            "sources": ["base_ds"],
        },
        "plan_clarification": {
            "id": "q1",
            "kind": "focus",
            "options": [{"id": "focus.progress", "label": "平均进度"}],
            "multiple": True,
            "allowFreeText": True,
        },
        "clarification_plan_id": "plan_1",
        "clarification_task_id": "task_1",
    }

    outcome = _bind(context, "", slots=slots)

    assert outcome.missing_fields == []
    assert outcome.question == ""
    assert outcome.next_step_id == "submit_plan_resume"
    assert outcome.bind["mode"] == "plan"
    assert "task_transition" not in outcome.bind
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.bind["task_id"] == "task_1"
    assert outcome.bind["request_id"].startswith("wfr_")
    assert outcome.bind["clarification_resume"] is True
    draft = outcome.bind["draft"]
    assert draft["goal"] == "帮我生成一个新的 ERP"
    assert draft["screenTitle"] == "ERP 项目经营大屏"
    assert draft["sources"] == ["base_ds", "ver_1"]
    assert draft["slots"][0]["measureAggregations"] == ["avg"]
    assert "clarification" not in draft
    assert "clarification_answer" not in draft


def test_binder_rebuilds_clarification_draft_when_session_slots_were_lost() -> None:
    patch = {
        "goal": "帮我生成一个新的 ERP",
        "screenTitle": "ERP 项目经营大屏",
        "sources": ["ver_1"],
        "slots": [
            {
                "title": "项目进度",
                "role": "metric",
                "measures": ["进度"],
                "measureAggregations": ["avg"],
            }
        ],
    }
    answer = ClarificationAnswer(
        planId="plan_1",
        questionId="q1",
        optionIds=["focus.progress"],
        draftPatch=patch,
    )
    context = _context(
        screen_id=None,
        count=None,
        plan_id="plan_1",
        plan_status="needs_input",
        task_id="task_1",
        clarification_answer=answer,
    )
    slots = {
        "clarification_plan_id": "plan_1",
        "clarification_task_id": "task_1",
    }

    outcome = _bind(context, "", slots=slots)

    assert outcome.missing_fields == []
    assert outcome.next_step_id == "submit_plan_resume"
    assert outcome.bind["draft"]["goal"] == "帮我生成一个新的 ERP"
    assert outcome.bind["draft"]["screenTitle"] == "ERP 项目经营大屏"
    assert outcome.bind["draft"]["sources"] == ["ver_1"]
    assert outcome.bind["draft"]["slots"][0]["title"] == "项目进度"


def test_binder_does_not_reuse_a_clarification_answer_after_a_task_switch() -> None:
    answer = ClarificationAnswer(
        planId="plan_old",
        questionId="q_old",
        optionIds=["focus.old"],
        draftPatch={"slots": [{"title": "旧指标", "role": "metric", "measures": ["旧指标"]}]},
    )
    context = _context(
        screen_id=None,
        count=None,
        plan_id="plan_old",
        plan_status="needs_input",
        task_id="task_2",
        clarification_answer=answer,
    )
    slots = {
        "plan_draft": {"goal": "旧任务"},
        "plan_clarification": {
            "id": "q_old",
            "options": [{"id": "focus.old"}],
            "multiple": True,
            "allowFreeText": True,
        },
        "clarification_plan_id": "plan_old",
        "clarification_task_id": "task_1",
    }

    outcome = _bind(context, "1", slots=slots)

    assert outcome.next_step_id is None
    assert outcome.missing_fields == ["clarification_stale"]
    assert outcome.bind["clarification_stale"] is True
    assert outcome.bind["plan_draft"] is None
    assert "task_transition" not in outcome.bind


def test_binder_rejects_an_option_that_is_not_on_the_current_card() -> None:
    answer = ClarificationAnswer(
        planId="plan_1",
        questionId="q1",
        optionIds=["focus.unknown"],
        draftPatch={"slots": [{"title": "进度", "role": "metric", "measures": ["进度"]}]},
    )
    context = _context(
        screen_id=None,
        count=None,
        plan_id="plan_1",
        plan_status="needs_input",
        task_id="task_1",
        clarification_answer=answer,
    )
    slots = {
        "plan_draft": {"goal": "帮我生成一个新的 ERP"},
        "plan_clarification": {
            "id": "q1",
            "options": [{"id": "focus.progress"}],
            "multiple": True,
            "allowFreeText": True,
        },
        "clarification_plan_id": "plan_1",
        "clarification_task_id": "task_1",
    }

    outcome = _bind(context, "", slots=slots)

    assert outcome.next_step_id is None
    assert outcome.missing_fields == ["clarification_stale"]
    assert outcome.bind["clarification_stale"] is True


def test_plan_reply_waits_for_confirmation_without_claiming_a_screen() -> None:
    for status in ("ready", "needs_confirmation"):
        data = {
            "status": status,
            "summary": "方案没有假设，确认后落屏",
            "clarification": None,
            "questions": [],
        }
        result = plan_reply(
            user_message="",
            turn_context=None,
            bind={},
            slots={},
            outputs={"submit_plan": {"data": data}},
            metadata={},
            llm=lambda *args, **kwargs: {},
        )
        assert "确认" in result["reply"]
        assert "大屏已生成" not in result["reply"]
        assert "已创建" not in result["reply"]


def test_plan_reply_uses_only_the_first_plain_question() -> None:
    data = {
        "status": "needs_input",
        "summary": "还需要补充一项",
        "clarification": None,
        "questions": ["先问这个", "不要问第二个"],
    }
    result = plan_reply(
        user_message="",
        turn_context=None,
        bind={},
        slots={},
        outputs={"submit_plan": {"data": data}},
        metadata={},
        llm=lambda *args, **kwargs: {},
    )

    assert result["reply"] == "先问这个"


def test_plan_reply_points_to_the_plan_card_for_a_structured_clarification() -> None:
    clarification = {
        "id": "q_focus",
        "kind": "focus",
        "prompt": "你最想关注哪些内容？",
        "options": [
            {"id": "focus.progress", "label": "平均进度", "description": "按平均值"},
        ],
        "multiple": True,
        "allowFreeText": True,
    }
    result = plan_reply(
        user_message="",
        turn_context=None,
        bind={},
        slots={},
        outputs={
            "submit_plan": {
                "data": {
                    "planId": "plan_1",
                    "status": "needs_input",
                    "summary": "你最想关注哪些内容？",
                    "clarification": clarification,
                    "questions": ["你最想关注哪些内容？"],
                }
            }
        },
        metadata={},
        llm=lambda *args, **kwargs: {},
    )

    assert result["reply"] == "我先确认一个关键点，请在方案卡片中选择。"
    assert "focus.progress" not in result["reply"]
    assert result["clarification"] == clarification
    assert result["plan_id"] == "plan_1"


def test_plan_spine_has_no_write_before_confirmation() -> None:
    content = screen_sop_content()
    nodes = {node["node_id"]: node for node in content["nodes"]}

    for node_id in ("submit_plan", "compose_plan_reply", "reply_plan"):
        refs = nodes[node_id].get("capability_refs", {}).get("tool_ids", [])
        assert "create_screen" not in refs
    plan_nodes = {
        node_id
        for node_id, node in nodes.items()
        if "plan_screen" in node.get("capability_refs", {}).get("tool_ids", [])
    }
    assert plan_nodes == {"submit_plan", "submit_plan_resume"}
    args = nodes["submit_plan"]["metadata"]["arguments"]
    assert "confirm" not in args
    assert nodes["submit_plan"]["metadata"]["next_step_id"] == "compose_plan_reply"
    assert nodes["reply_plan"]["metadata"]["template"] == "$out.compose_plan_reply.data.reply"

    resume_args = nodes["submit_plan_resume"]["metadata"]["arguments"]
    assert resume_args["planId"] == "$bind.plan_id"
    assert resume_args["taskId"] == "$bind.task_id"
    assert resume_args["draft"] == "$bind.draft"
    edges = {(edge["source_node_id"], edge["next_node_id"]) for edge in content["edges"]}
    assert ("bind", "submit_plan_resume") in edges
    assert ("submit_plan_resume", "compose_plan_reply_resume") in edges
    assert ("compose_plan_reply_resume", "reply_plan_resume") in edges
    assert "reply_plan_resume" in content["terminal_node_ids"]


def test_binder_creates_a_blank_canvas_only_for_an_explicit_blank_request() -> None:
    explicit = _bind(_context(screen_id=None, count=None), "帮我建一个空白画布")
    assert explicit.next_step_id == "create_blank"
    assert explicit.bind["name"] == "帮我建一个空白画布"

    normal = _bind(_context(screen_id=None, count=None), "帮我生成一个新的 ERP")
    assert normal.next_step_id == "describe_catalog"
    assert normal.bind["name"] is None


def test_clarification_resume_submits_the_same_plan_through_the_sop() -> None:
    calls: list[tuple[str, dict]] = []

    class _Invoker:
        def invoke(self, name: str, arguments: dict) -> dict:
            calls.append((name, dict(arguments)))
            return {
                "success": True,
                "data": {
                    "planId": "plan_1",
                    "status": "needs_confirmation",
                    "summary": "方案可以生成",
                    "clarification": None,
                    "questions": [],
                },
            }

        def read_tool_result_reference(self, reference: dict) -> dict:
            return {}

    manifest = CapabilityManifest(
        available=[
            CapabilityDescriptor(
                capability_id="cap-plan",
                name="plan_screen",
                kind="tool",
                input_schema={
                    "type": "object",
                    "required": ["requestId", "draft"],
                    "properties": {
                        "requestId": {"type": "string"},
                        "taskId": {"type": "string"},
                        "planId": {"type": "string"},
                        "draft": {"type": "object"},
                        "placementHint": {"type": "string"},
                    },
                },
            )
        ]
    )
    content = screen_sop_content()
    skill = Skill(
        id="skill-screen",
        tenant_id="tenant-demo",
        skill_id="screen_ops",
        name="大屏岗位 SOP",
        status="published",
        content_json=content,
    )
    pack = WorkflowPack.from_skill(skill)
    assert pack is not None
    answer = ClarificationAnswer(
        planId="plan_1",
        questionId="q_focus",
        optionIds=["focus.avg_progress"],
        labels=["平均进度"],
        datasetVersionIds=["ver_1"],
        draftPatch={
            "sources": ["ver_1"],
            "slots": [
                {
                    "title": "平均进度",
                    "role": "metric",
                    "datasetVersionId": "ver_1",
                    "measures": ["进度"],
                    "measureAggregations": ["avg"],
                }
            ],
        },
    )
    runtime = WorkflowRuntime(
        pack=pack,
        invoker=_Invoker(),
        manifest=manifest,
        trace=lambda *_: None,
        task_frame_id="task_1",
        skill_id="screen_ops",
        allowed_tool_refs={"plan_screen"},
        turn_context=_context(
            screen_id="scr_1",
            count=0,
            plan_id="plan_1",
            plan_status="needs_input",
            task_id="task_1",
            clarification_answer=answer,
        ),
        slots={
            "plan_draft": {
                "goal": "帮我生成一个新的 ERP",
                "screenTitle": "ERP 项目经营大屏",
            },
            "plan_clarification": {
                "id": "q_focus",
                "options": [
                    {"id": "focus.avg_progress", "label": "平均进度"},
                    {"id": "focus.contract", "label": "合同额"},
                ],
                "multiple": True,
                "allowFreeText": True,
            },
            "clarification_plan_id": "plan_1",
            "clarification_task_id": "task_1",
        },
    )
    nodes = {node["node_id"]: node for node in content["nodes"]}

    bound = runtime.run_step(node_id="bind", node=nodes["bind"])
    assert bound.status == "completed"
    assert bound.next_step_id == "submit_plan_resume"

    submitted = runtime.run_step(
        node_id="submit_plan_resume", node=nodes["submit_plan_resume"]
    )

    assert submitted.status == "completed"
    assert submitted.next_step_id == "compose_plan_reply_resume"
    assert len(calls) == 1
    name, args = calls[0]
    assert name == "plan_screen"
    assert args["planId"] == "plan_1"
    assert args["taskId"] == "task_1"
    assert args["requestId"] == runtime.context.bind["request_id"]
    assert args["draft"]["goal"] == "帮我生成一个新的 ERP"
    assert args["draft"]["screenTitle"] == "ERP 项目经营大屏"
    assert args["draft"]["sources"] == ["ver_1"]
    assert args["draft"]["slots"][0]["measureAggregations"] == ["avg"]
    assert "clarification_answer" not in args["draft"]
    assert "clarification" not in args["draft"]
    assert runtime.context.bind["clarification_base_hash"] == ""
    assert args["baseDraftHash"] == ""
    nodes_by_id = {node["node_id"]: node for node in content["nodes"]}
    assert "baseDraftHash" not in nodes_by_id["submit_plan"]["metadata"]["arguments"]
    assert (
        nodes_by_id["submit_plan_resume"]["metadata"]["arguments"]["baseDraftHash"]
        == "$bind.clarification_base_hash"
    )


def test_merge_clarification_keeps_existing_slot_when_patch_adds_another() -> None:
    draft, problem = merge_clarification_draft(
        {
            "goal": "ERP 经营",
            "placement": "current",
            "slots": [{"title": "项目进度", "role": "metric", "measures": ["进度"]}],
            "sources": ["base"],
        },
        draft_patch={
            "sources": ["erp"],
            "slots": [{"title": "阶段合同额", "role": "compare", "measures": ["合同额"], "chartType": "bar"}],
        },
        text="",
    )
    assert problem == ""
    assert [slot["title"] for slot in draft["slots"]] == ["项目进度", "阶段合同额"]
    assert draft["sources"] == ["base", "erp"]
    assert draft["placement"] == "current"


def test_merge_clarification_updates_same_key_and_keeps_a_duplicate_occurrence() -> None:
    updated, problem = merge_clarification_draft(
        {
            "goal": "ERP 经营",
            "slots": [
                {
                    "title": "项目进度",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["进度"],
                }
            ],
        },
        draft_patch={
            "slots": [
                {
                    "title": "项目进度",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["进度"],
                    "measureAggregations": ["avg"],
                }
            ]
        },
        text="项目进度",
    )
    assert problem == ""
    assert len(updated["slots"]) == 1
    assert updated["slots"][0]["measures"] == ["进度"]
    assert updated["slots"][0]["measureAggregations"] == ["avg"]

    kept, problem = merge_clarification_draft(
        {
            "goal": "ERP 经营",
            "slots": [
                {
                    "title": "项目进度",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["进度"],
                },
                {
                    "title": "项目进度",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["进度"],
                },
            ],
        },
        draft_patch={
            "slots": [
                {
                    "title": "项目进度",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["进度"],
                    "measureAggregations": ["avg"],
                }
            ]
        },
        text="",
    )
    assert problem == ""
    assert len(kept["slots"]) == 2
    assert kept["slots"][0]["measureAggregations"] == ["avg"]
    assert "measureAggregations" not in kept["slots"][1]
    assert kept["slots"][1]["measures"] == ["进度"]


def test_merge_clarification_appends_same_title_with_different_measures() -> None:
    draft, problem = merge_clarification_draft(
        {
            "goal": "ERP 经营",
            "slots": [
                {
                    "title": "金额",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["合同额"],
                    "chartType": "metric",
                }
            ],
        },
        draft_patch={
            "slots": [
                {
                    "title": "金额",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["实际成本"],
                    "chartType": "metric",
                }
            ]
        },
        text="",
    )
    assert problem == ""
    assert [slot["measures"] for slot in draft["slots"]] == [["合同额"], ["实际成本"]]


def test_merge_clarification_ignores_free_text_when_patch_has_no_slots() -> None:
    draft, problem = merge_clarification_draft(
        {"goal": "ERP 经营", "slots": [{"title": "项目进度", "role": "metric", "measures": ["进度"]}]},
        draft_patch={"goal": "ERP 经营"},
        text="阶段合同额",
    )
    assert problem == ""
    assert [slot["title"] for slot in draft["slots"]] == ["项目进度"]


def test_merge_clarification_drops_slots_past_twelve() -> None:
    base_slots = [
        {"title": f"槽{index:02d}", "role": "metric", "measures": ["进度"]}
        for index in range(1, 13)
    ]
    draft, problem = merge_clarification_draft(
        {"goal": "ERP 经营", "slots": base_slots},
        draft_patch={"slots": [{"title": "槽13", "role": "metric", "measures": ["合同额"]}]},
        text="",
    )
    assert problem == ""
    assert len(draft["slots"]) == 12
    assert [slot["title"] for slot in draft["slots"]] == [f"槽{index:02d}" for index in range(1, 13)]
    assert all(slot["title"] != "槽13" for slot in draft["slots"])


def _clarification_slots() -> dict:
    return {
        "plan_clarification": {
            "id": "q1",
            "kind": "focus",
            "options": [{"id": "erp.project_count", "label": "项目数量"}],
            "multiple": True,
            "allowFreeText": True,
        },
        "clarification_plan_id": "plan_1",
        "clarification_task_id": "task_1",
    }


def test_binder_merges_clarification_onto_answer_plan_draft() -> None:
    stale = {
        "goal": "ERP 经营",
        "screenTitle": "ERP 经营",
        "sources": ["erp"],
        "slots": [
            {
                "title": "项目进度",
                "role": "metric",
                "datasetVersionId": "dsv",
                "measures": ["进度"],
                "chartType": "metric",
            }
        ],
    }
    fresh = {
        "goal": "ERP 经营",
        "screenTitle": "ERP 经营",
        "sources": ["erp"],
        "slots": [
            stale["slots"][0],
            {
                "title": "阶段合同额",
                "role": "compare",
                "datasetVersionId": "dsv",
                "measures": ["合同额"],
                "dimensions": ["阶段"],
                "chartType": "bar",
            },
        ],
    }
    answer = ClarificationAnswer(
        planId="plan_1",
        questionId="q1",
        optionIds=["erp.project_count"],
        labels=["项目数量"],
        planDraft=fresh,
        planDraftHash="draft-hash-1",
        draftPatch={
            "slots": [
                {
                    "title": "项目数量",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["项目ID"],
                    "chartType": "metric",
                }
            ]
        },
    )
    outcome = _bind(
        _context(
            screen_id=None,
            count=None,
            plan_id="plan_1",
            plan_status="needs_input",
            task_id="task_1",
            clarification_answer=answer,
        ),
        slots={"plan_draft": stale, **_clarification_slots()},
    )
    titles = [slot["title"] for slot in outcome.bind["draft"]["slots"]]
    assert titles == ["项目进度", "阶段合同额", "项目数量"]
    assert outcome.bind["plan_draft_hash"] == "draft-hash-1"
    assert outcome.bind["clarification_base_hash"] == "draft-hash-1"
    assert "baseDraftHash" not in outcome.bind["draft"]
    assert "planDraftHash" not in outcome.bind["draft"]


def test_binder_falls_back_to_session_draft_without_plan_draft() -> None:
    answer = ClarificationAnswer(
        planId="plan_1",
        questionId="q1",
        optionIds=["erp.project_count"],
        labels=["项目数量"],
        draftPatch={
            "slots": [
                {
                    "title": "项目数量",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["项目ID"],
                    "chartType": "metric",
                }
            ]
        },
    )
    outcome = _bind(
        _context(
            screen_id=None,
            count=None,
            plan_id="plan_1",
            plan_status="needs_input",
            task_id="task_1",
            clarification_answer=answer,
        ),
        slots={
            "plan_draft": {
                "goal": "ERP 经营",
                "screenTitle": "ERP 经营",
                "slots": [
                    {
                        "title": "项目进度",
                        "role": "metric",
                        "datasetVersionId": "dsv",
                        "measures": ["进度"],
                        "chartType": "metric",
                    }
                ],
            },
            **_clarification_slots(),
        },
    )
    assert [slot["title"] for slot in outcome.bind["draft"]["slots"]] == ["项目进度", "项目数量"]
    assert "plan_draft_hash" not in outcome.bind
    assert outcome.bind["clarification_base_hash"] == ""


def test_resume_submit_stops_when_plan_screen_reports_stale_hash() -> None:
    calls: list[tuple[str, dict]] = []
    base_hash = "ab" * 32

    class _Invoker:
        def invoke(self, name: str, arguments: dict) -> dict:
            calls.append((name, dict(arguments)))
            return {
                "success": False,
                "error": {
                    "code": "PLAN_DRAFT_STALE",
                    "message": "screen plan draft changed",
                },
            }

        def read_tool_result_reference(self, reference: dict) -> dict:
            return {}

    content = screen_sop_content()
    skill = Skill(
        id="skill-screen-stale",
        tenant_id="tenant-demo",
        skill_id="screen_ops",
        name="大屏岗位 SOP",
        status="published",
        content_json=content,
    )
    pack = WorkflowPack.from_skill(skill)
    assert pack is not None
    original_slots = {
        "plan_draft": {
            "goal": "ERP 经营",
            "screenTitle": "ERP 经营",
            "slots": [
                {
                    "title": "项目进度",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["进度"],
                    "chartType": "metric",
                }
            ],
        },
        **_clarification_slots(),
    }
    answer = ClarificationAnswer(
        planId="plan_1",
        questionId="q1",
        optionIds=["erp.project_count"],
        labels=["项目数量"],
        planDraft={
            "goal": "ERP 经营",
            "screenTitle": "ERP 经营",
            "slots": [
                original_slots["plan_draft"]["slots"][0],
                {
                    "title": "阶段合同额",
                    "role": "compare",
                    "datasetVersionId": "dsv",
                    "measures": ["合同额"],
                    "dimensions": ["阶段"],
                    "chartType": "bar",
                },
            ],
        },
        planDraftHash=base_hash,
        draftPatch={
            "slots": [
                {
                    "title": "项目数量",
                    "role": "metric",
                    "datasetVersionId": "dsv",
                    "measures": ["项目ID"],
                    "chartType": "metric",
                }
            ]
        },
    )
    manifest = CapabilityManifest(
        available=[
            CapabilityDescriptor(
                capability_id="cap-plan",
                name="plan_screen",
                kind="tool",
                input_schema={
                    "type": "object",
                    "required": ["requestId", "draft"],
                    "properties": {
                        "requestId": {"type": "string"},
                        "planId": {"type": "string"},
                        "draft": {"type": "object"},
                        "baseDraftHash": {"type": "string"},
                    },
                },
            )
        ]
    )
    runtime = WorkflowRuntime(
        pack=pack,
        invoker=_Invoker(),
        manifest=manifest,
        trace=lambda *_: None,
        task_frame_id="task_1",
        skill_id="screen_ops",
        allowed_tool_refs={"plan_screen"},
        turn_context=_context(
            screen_id=None,
            count=None,
            plan_id="plan_1",
            plan_status="needs_input",
            task_id="task_1",
            clarification_answer=answer,
        ),
        slots=original_slots,
    )
    nodes = {node["node_id"]: node for node in content["nodes"]}
    bound = runtime.run_step(node_id="bind", node=nodes["bind"])
    assert bound.status == "completed"
    assert runtime.context.bind["clarification_base_hash"] == base_hash
    failed = runtime.run_step(node_id="submit_plan_resume", node=nodes["submit_plan_resume"])
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error["code"] == "PLAN_DRAFT_STALE"
    assert failed.slot_updates == {}
    assert failed.next_step_id is None
    assert "submit_plan_resume" not in runtime.context.outputs
    assert len(calls) == 1
    assert calls[0][1]["baseDraftHash"] == base_hash
    assert [slot["title"] for slot in runtime.context.slots["plan_draft"]["slots"]] == ["项目进度"]


def test_binder_routes_ready_plan_to_authoritative_revise() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="ready"),
        "把标题改成合同额看板",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.next_step_id == "get_current_plan"


def test_binder_routes_position_swap_to_plan_revise() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="ready"),
        "将布局中 5 和 3 位置互换",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.next_step_id == "get_current_plan"


def test_binder_revises_active_plan_even_when_target_screen_is_not_chosen_yet() -> None:
    outcome = _bind(
        _context(screen_id=None, count=None, plan_id="plan_1", plan_status="ready"),
        "合同金额按部门做柱状图，进度按阶段做饼图",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.next_step_id == "get_current_plan"


def test_binder_revises_active_plan_with_modify_screen_intent_without_screen() -> None:
    outcome = _bind(
        _context(
            screen_id=None,
            count=None,
            plan_id="plan_1",
            plan_status="ready",
            intent=ScreenIntent(kind="modify_screen", confidence=0.9, target="current"),
        ),
        "经营总览 合同金额按部门做柱状图，进度按阶段做饼图",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.next_step_id == "get_current_plan"


def test_binder_template_choice_keeps_task_and_starts_a_new_plan() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="ready", task_id="task_1"),
        "第二个",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == ""
    assert outcome.bind["task_id"] == "task_1"
    assert outcome.next_step_id == "describe_catalog"


def test_screen_sop_has_revise_and_query_spines() -> None:
    from app.workflow_packs.screen.sop import build_screen_sop

    content = build_screen_sop()
    nodes = {node["node_id"]: node for node in content["nodes"]}
    edges = {(edge["source_node_id"], edge["next_node_id"]) for edge in content["edges"]}
    assert {"get_current_plan", "draft_intent", "revise_plan", "reply_revise"} <= set(nodes)
    assert {"query_dataset", "compose_query_reply", "reply_query"} <= set(nodes)
    assert ("draft_intent", "revise_plan") in edges
    assert ("revise_plan", "compose_revise_reply") in edges
    assert ("query_dataset", "compose_query_reply") in edges
    assert "get_screen_plan" in nodes["get_current_plan"]["capability_refs"]["tool_ids"]
    assert "revise_screen_plan" in nodes["revise_plan"]["capability_refs"]["tool_ids"]
    assert nodes["revise_plan"]["metadata"]["arguments"]["retryOnStale"] is True


def test_screen_query_reply_asks_when_multiple_datasets() -> None:
    from app.workflow_packs.screen.plan import screen_query_reply

    result = screen_query_reply(
        user_message="合同额是多少",
        turn_context=None,
        bind={},
        slots={},
        outputs={
            "query_dataset": {
                "data": {
                    "datasets": [
                        {"name": "销售", "summary": {"amount": 10}, "fields": []},
                        {"name": "项目", "summary": {"amount": 20}, "fields": []},
                    ]
                }
            }
        },
        metadata={},
        llm=lambda *args, **kwargs: {},
    )
    assert "多个数据集" in result["reply"]
    assert "10" not in result["reply"]
    assert "当前方案和任务没有变化" in result["reply"]


def test_screen_query_reply_answers_a_unique_dataset() -> None:
    from app.workflow_packs.screen.plan import screen_query_reply

    result = screen_query_reply(
        user_message="合同额是多少",
        turn_context=None,
        bind={},
        slots={},
        outputs={
            "query_dataset": {
                "data": {
                    "datasets": [
                        {
                            "name": "销售",
                            "summary": {"amount": 10},
                            "fields": [{"fieldId": "amount", "displayName": "合同额"}],
                        }
                    ]
                }
            }
        },
        metadata={},
        llm=lambda *args, **kwargs: {},
    )
    assert "合同额=10" in result["reply"]
    assert "当前方案和任务没有变化" in result["reply"]


def test_binder_accepts_default_plan_in_the_same_task() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="ready"),
        "直接做",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.next_step_id == "present_plan"


def test_binder_regenerates_a_new_plan_in_the_same_task() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="ready"),
        "重新生成方案",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == ""
    assert outcome.next_step_id == "describe_catalog"


def test_default_plan_builds_four_modules_from_the_catalog() -> None:
    draft = build_screen_plan_draft(
        user_message="做一个项目经营大屏",
        turn_context=_context(count=5),
        bind={},
        catalog=_CATALOG,
    )
    slots = draft["slots"]
    assert [slot["role"] for slot in slots] == ["metric", "trend", "compare", "detail"]
    assert draft["sources"] == ["erp_projects"]
    names = {name for slot in slots for name in (slot.get("measures") or []) + (slot.get("dimensions") or [])}
    assert names <= {"项目名称", "进度", "合同额", "月份"}
    assert any("没有日期字段" in item for item in draft["assumptions"])


def test_default_plan_prioritizes_progress_when_asked() -> None:
    draft = build_screen_plan_draft(
        user_message="做一个项目进度大屏",
        turn_context=_context(count=5),
        bind={},
        catalog=_CATALOG,
    )
    assert draft["slots"][0]["role"] == "metric"
    assert draft["slots"][0]["measures"] == ["进度"]


def test_draft_intent_parses_multiple_component_answers() -> None:
    from app.workflow_packs.screen.draft_intent import build_intent

    catalog = {
        "datasets": [
            {
                **_CATALOG["datasets"][0],
                "fields": [
                    *_CATALOG["datasets"][0]["fields"],
                    {"fieldId": "f_dept", "displayName": "部门", "role": "dimension", "type": "string"},
                ],
            }
        ]
    }
    intent = build_intent(
        "项目名称用文字，合同额用柱状图按部门",
        {"slots": []},
        catalog,
    )
    assert intent.confidence >= 0.75
    assert intent.unresolved == []
    assert len(intent.operations) == 2
    assert intent.operations[0].slots[0].chartType == "table"
    assert intent.operations[1].slots[0].chartType == "bar"
    assert intent.operations[1].slots[0].dimensions


def test_plan_reply_shows_fields_and_direct_do_entry() -> None:
    result = plan_reply(
        user_message="做一个项目经营大屏",
        turn_context=None,
        bind={},
        slots={},
        outputs={
            "describe_catalog": {"data": _CATALOG},
            "submit_plan": {
                "data": {
                    "status": "needs_confirmation",
                    "slots": [
                        {"title": "合同额", "role": "metric", "measures": ["合同额"], "chartType": "metric"}
                    ],
                }
            },
        },
        metadata={"submit_step": "submit_plan"},
        llm=lambda *args, **kwargs: {},
    )
    assert "先按" in result["reply"]
    assert "数据用" in result["reply"]
    assert "1 个模块" in result["reply"]
    assert "确认后生成" in result["reply"]
    assert "目录里可用字段" not in result["reply"]
    assert "直接做" not in result["reply"]


def test_direct_do_beats_a_planner_new_screen_transition() -> None:
    outcome = _bind(
        _context(
            count=5,
            plan_id="plan_1",
            plan_status="ready",
            intent=ScreenIntent(
                kind="create",
                confidence=0.9,
                target="new",
                placement_confirmed=True,
            ),
        ),
        "直接做",
    )
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.next_step_id == "present_plan"


def test_screen_sop_has_present_plan_spine() -> None:
    content = screen_sop_content()
    nodes = {node["node_id"] for node in content["nodes"]}
    edges = {(edge["source_node_id"], edge["next_node_id"]) for edge in content["edges"]}
    assert {"present_plan", "present_catalog", "compose_plan_reply_present", "reply_present"} <= nodes
    assert ("bind", "present_plan") in edges
    assert ("present_plan", "present_catalog") in edges
    assert "reply_present" in content["terminal_node_ids"]


def test_direct_do_beats_planner_confirm_flag() -> None:
    context = _context(count=5, plan_id="plan_1", plan_status="ready")
    context.confirm_plan = True
    outcome = _bind(context, "直接做")
    assert outcome.bind["plan_id"] == "plan_1"
    assert outcome.next_step_id == "present_plan"


def test_plan_reply_present_reads_current_plan_and_fields() -> None:
    result = plan_reply(
        user_message="直接做",
        turn_context=None,
        bind={},
        slots={},
        outputs={
            "present_catalog": {"data": _CATALOG},
            "present_plan": {
                "data": {
                    "planId": "plan_1",
                    "status": "ready",
                    "draft": {
                        "slots": [
                            {"title": "合同额", "role": "metric", "measures": ["合同额"], "chartType": "metric"}
                        ]
                    },
                }
            },
        },
        metadata={"reply_kind": "present", "source_step": "present_plan"},
        llm=lambda *args, **kwargs: {},
    )
    assert "plan_1" == result["plan_id"]
    assert "当前方案" in result["reply"]
    assert "数据用" in result["reply"]
    assert "确认" in result["reply"]
    assert "可用字段" not in result["reply"]


def test_plan_reply_revise_says_updated_not_first_turn() -> None:
    result = plan_reply(
        user_message="经营总览 合同金额按部门做柱状图，进度按阶段做饼图",
        turn_context=None,
        bind={},
        slots={},
        outputs={
            "revise_plan": {
                "data": {
                    "planId": "plan_1",
                    "status": "ready",
                    "draft": {
                        "slots": [
                            {"title": "合同额", "role": "compare", "measures": ["合同额"], "dimensions": ["部门"], "chartType": "bar"},
                            {"title": "进度", "role": "share", "measures": ["进度"], "dimensions": ["阶段"], "chartType": "pie"},
                        ]
                    },
                }
            }
        },
        metadata={"submit_step": "revise_plan", "reply_kind": "revise"},
        llm=lambda *args, **kwargs: {},
    )
    assert "方案已更新" in result["reply"]
    assert "可以先选一个方向" not in result["reply"]

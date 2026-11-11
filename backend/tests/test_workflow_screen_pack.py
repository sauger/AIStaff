from __future__ import annotations

import pytest

from app.session.turn_context import ScreenTurnContext, SelectedComponent
from app.workflow_packs.screen.binder import bind_screen
from app.workflow_packs.screen.operations import compile_operations, validate_change_plan
from app.workflow_packs.screen.sop import build_screen_sop, screen_sop_content
from app.workflows.registry import BindError


def _context(
    *,
    screen_id: str | None = "scr_1",
    count: int | None = 5,
    selected: list[SelectedComponent] | None = None,
    revision: int | None = 11,
    plan_id: str | None = None,
    plan_status: str | None = None,
    confirm_plan: bool = False,
) -> ScreenTurnContext:
    return ScreenTurnContext(
        project_id="proj_1",
        screen_id=screen_id,
        screen_name="销售大屏",
        revision=revision,
        component_count=count,
        selected=selected or [],
        plan_id=plan_id,
        plan_status=plan_status,
        confirm_plan=confirm_plan,
    )


def _bind(context: ScreenTurnContext, message: str = "", **metadata: object):
    return bind_screen(
        turn_context=context,
        slots={},
        metadata=dict(metadata),
        user_message=message,
    )


def test_binder_routes_a_new_screen_to_the_plan_branch() -> None:
    outcome = _bind(_context(screen_id=None, count=None), "帮我做一个大屏")

    assert outcome.missing_fields == []
    assert outcome.next_step_id == "describe_catalog"
    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["placement_hint"] == ""
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


def test_binder_asks_when_multiple_components_are_selected() -> None:
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
    assert "只保留一个" in outcome.question
    assert outcome.next_step_id is None


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


def test_binder_explicit_new_screen_wins_over_existing_screen() -> None:
    outcome = _bind(_context(count=5), "不要动当前屏，重新做一张销售大屏")

    assert outcome.bind["mode"] == "plan"
    assert outcome.bind["placement_hint"] == "new"
    assert outcome.next_step_id == "describe_catalog"


def test_binder_does_not_confirm_or_write_an_open_plan_from_chat() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="needs_confirmation", confirm_plan=True),
        "确认",
    )

    assert outcome.bind["mode"] == "plan"
    assert outcome.next_step_id == "describe_catalog"
    assert outcome.bind["plan_id"] == "plan_1"


def test_binder_continues_an_open_plan_on_a_populated_screen() -> None:
    outcome = _bind(
        _context(count=5, plan_id="plan_1", plan_status="needs_input"),
        "改成收入",
    )

    assert outcome.next_step_id == "describe_catalog"
    assert outcome.bind["mode"] == "plan"


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
    assert actions["reply_created"] == "reply"
    assert set(actions.values()) <= {"bind", "structured_llm", "tool", "ask", "reply"}

    apply_args = nodes["apply"]["metadata"]["arguments"]
    assert apply_args["expectedRevision"] == "$out.resolve.data.revision"
    assert "screenId" not in apply_args or apply_args["screenId"] == "$bind.screen_id"
    assert nodes["plan"]["metadata"]["handler"] == "plan_change"


def test_sop_content_is_stable_for_tests() -> None:
    assert screen_sop_content()["start_node_id"] == "bind"


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

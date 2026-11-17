"""Limited ScreenDraftIntent tests for the demo screen dialogue path."""

from __future__ import annotations

from app.workflow_packs.screen.draft_intent import build_intent, projected_catalog
from app.workflow_packs.screen.plan import screen_draft_intent

CATALOG = {
    "datasets": [
        {
            "slug": "erp",
            "name": "ERP",
            "datasetVersionId": "ver-erp",
            "fields": [
                {"fieldId": "fld_progress", "displayName": "进度", "dataType": "number", "role": "measure"},
                {"fieldId": "fld_contract", "displayName": "合同额", "dataType": "number", "role": "measure"},
                {"fieldId": "fld_project_count", "displayName": "项目数量", "dataType": "number", "role": "measure"},
                {"fieldId": "fld_project_name", "displayName": "项目名称", "dataType": "text", "role": "dimension"},
                {"fieldId": "fld_department", "displayName": "部门", "dataType": "text", "role": "dimension"},
                {"fieldId": "fld_stage", "displayName": "阶段", "dataType": "text", "role": "dimension"},
            ],
        }
    ]
}
DRAFT = {
    "goal": "ERP 经营",
    "screenTitle": "ERP",
    "sources": ["erp"],
    "slots": [
        {
            "title": "项目进度",
            "role": "metric",
            "datasetSlug": "erp",
            "measures": ["进度"],
            "chartType": "metric",
        }
    ],
}


def _outputs() -> dict:
    return {
        "get_current_plan": {"data": {"planId": "p1", "draft": DRAFT, "draftHash": "a" * 64}},
        "describe_catalog_for_revise": {"data": CATALOG},
    }


def test_projected_catalog_has_no_resource_ids() -> None:
    projected = projected_catalog(CATALOG)
    assert "datasetVersionId" not in projected["datasets"][0]
    assert "fieldId" not in projected["datasets"][0]["fields"][0]


def test_screen_draft_intent_uses_model_output() -> None:
    def fake_llm(system_prompt: str, payload: dict) -> dict:
        assert "fieldId" not in str(payload)
        return {
            "version": 1,
            "confidence": 0.82,
            "operations": [{"op": "set_title", "title": "合同额看板"}],
            "unresolved": [],
        }

    result = screen_draft_intent(
        user_message="标题改成合同额看板",
        turn_context=None,
        bind={},
        slots={},
        outputs=_outputs(),
        metadata={},
        llm=fake_llm,
    )
    intent = result["intent"]
    assert intent["version"] == 1
    assert intent["confidence"] == 0.82
    assert intent["operations"][0]["op"] == "set_title"
    assert intent["operations"][0]["title"] == "合同额看板"


def test_screen_draft_intent_falls_back_only_when_model_unavailable() -> None:
    def broken_llm(system_prompt: str, payload: dict) -> dict:
        raise RuntimeError("model down")

    result = screen_draft_intent(
        user_message="删掉项目进度，补一个合同额",
        turn_context=None,
        bind={},
        slots={},
        outputs=_outputs(),
        metadata={},
        llm=broken_llm,
    )
    intent = result["intent"]
    assert intent["confidence"] == 0.9
    assert [item["op"] for item in intent["operations"]] == ["remove_slots", "add_slots"]


def test_screen_draft_intent_uses_planner_context_for_elliptical_reply() -> None:
    """A reply such as "按阶段" needs the planner's prior metric/chart context."""

    class _Intent:
        kind = "modify_screen"
        metrics = ["进度"]
        dimensions = ["阶段"]
        charts = ["柱状图"]

    class _Context:
        intent = _Intent()

    def fake_llm(system_prompt: str, payload: dict) -> dict:
        assert payload["turn_intent"]["metrics"] == ["进度"]
        return {
            "version": 1,
            "confidence": 0.3,
            "operations": [],
            "unresolved": ["这句话缺少明确的字段说明"],
        }

    result = screen_draft_intent(
        user_message="按阶段",
        turn_context=_Context(),
        bind={},
        slots={},
        outputs=_outputs(),
        metadata={},
        llm=fake_llm,
    )
    intent = result["intent"]
    assert intent["confidence"] >= 0.75
    assert intent["unresolved"] == []
    op = intent["operations"][0]
    assert op["op"] == "add_slots"
    assert op["slots"][0]["measures"] == ["进度"]
    assert op["slots"][0]["dimensions"] == ["阶段"]
    assert op["slots"][0]["chartType"] == "bar"


def test_screen_draft_intent_does_not_rewrite_low_confidence_model_output() -> None:
    def low_confidence_llm(system_prompt: str, payload: dict) -> dict:
        return {
            "version": 1,
            "confidence": 0.4,
            "operations": [],
            "unresolved": ["不确定要改哪个字段"],
        }

    result = screen_draft_intent(
        user_message="改一下那个数",
        turn_context=None,
        bind={},
        slots={},
        outputs=_outputs(),
        metadata={},
        llm=low_confidence_llm,
    )
    intent = result["intent"]
    assert intent["confidence"] == 0.4
    assert intent["operations"] == []
    assert intent["unresolved"] == ["不确定要改哪个字段"]


def test_component_slots_splits_two_chart_clauses() -> None:
    from app.workflow_packs.screen.understanding import component_slots

    slots, unresolved = component_slots(
        "合同金额按部门做柱状图，项目数量按阶段做饼图", CATALOG
    )
    assert unresolved == []
    assert [slot["chartType"] for slot in slots] == ["bar", "pie"]
    assert slots[0]["measures"] == ["合同额"]
    assert slots[0]["dimensions"] == ["部门"]
    assert slots[1]["measures"] == ["项目数量"]
    assert slots[1]["dimensions"] == ["阶段"]


def test_custom_field_list_builds_one_detail_table_with_aliases() -> None:
    from app.workflow_packs.screen.plan import build_screen_plan_draft

    draft = build_screen_plan_draft(
        user_message="我想要合同金额 项目名称 部门名称",
        turn_context=None,
        bind={},
        catalog=CATALOG,
    )
    assert len(draft["slots"]) == 1
    slot = draft["slots"][0]
    assert slot["chartType"] == "table"
    assert slot["measures"] == ["合同额"]
    assert slot["dimensions"] == ["项目名称", "部门"]


def test_text_component_and_bar_component_are_both_kept() -> None:
    from app.workflow_packs.screen.plan import build_screen_plan_draft

    draft = build_screen_plan_draft(
        user_message="项目名称用文字，合同额用柱状图按部门",
        turn_context=None,
        bind={},
        catalog=CATALOG,
    )
    assert [slot["chartType"] for slot in draft["slots"]] == ["table", "bar"]
    assert draft["slots"][0].get("measures", []) == []
    assert draft["slots"][0]["dimensions"] == ["项目名称"]


def test_custom_list_revision_becomes_atomic_set_slots() -> None:
    from app.workflow_packs.screen.draft_intent import build_intent

    intent = build_intent("我想要合同金额 项目名称 部门名称", DRAFT, CATALOG)
    assert intent.confidence == 0.9
    assert len(intent.operations) == 1
    assert intent.operations[0].op == "set_slots"
    assert intent.operations[0].slots[0].chartType == "table"
    assert intent.operations[0].slots[0].measures == ["合同额"]


def test_first_turn_model_set_slots_is_used_without_default_template() -> None:
    from app.workflow_packs.screen.plan import draft_screen_plan

    catalog = {
        "datasets": [
            {
                "slug": "erp",
                "name": "ERP",
                "fields": [
                    {"fieldId": "f_amount", "displayName": "合同额", "dataType": "number"},
                    {"fieldId": "f_dept", "displayName": "部门", "dataType": "text"},
                ],
            }
        ]
    }

    def fake_llm(system_prompt: str, payload: dict) -> dict:
        assert "datasetVersionId" not in str(payload)
        return {
            "version": 1,
            "confidence": 0.88,
            "operations": [
                {
                    "op": "set_slots",
                    "slots": [
                        {
                            "title": "合同额",
                            "role": "metric",
                            "datasetSlug": "erp",
                            "measures": ["合同额"],
                            "chartType": "metric",
                        }
                    ],
                }
            ],
            "unresolved": [],
        }

    result = draft_screen_plan(
        user_message="我要按部门看合同额",
        turn_context=None,
        bind={},
        slots={},
        outputs={"describe_catalog": {"data": catalog}},
        metadata={},
        llm=fake_llm,
    )
    draft = result["draft"]
    assert [slot["title"] for slot in draft["slots"]] == ["合同额"]
    assert draft["sources"] == ["erp"]
    assert "assumptions" not in draft


def test_join_verb_adds_component_phrase() -> None:
    intent = build_intent("我想加入 进度指标 柱状图", {"slots": []}, CATALOG)
    assert intent.confidence >= 0.75
    assert not intent.unresolved
    ops = [op for op in intent.operations if op.op == "add_slots"]
    slots = [slot for op in ops for slot in op.slots]
    assert {slot.chartType for slot in slots} == {"bar", "metric"}
    bar = next(slot for slot in slots if slot.chartType == "bar")
    assert bar.dimensions == ["阶段"]


def test_modify_existing_component_chart_uses_replace() -> None:
    intent = build_intent("把项目进度改成柱状图", DRAFT, CATALOG)
    assert not intent.unresolved
    assert intent.operations[0].op == "replace_slots"
    assert intent.operations[0].selector.title == "项目进度"
    assert intent.operations[0].slot.chartType == "bar"
    assert intent.operations[0].slot.dimensions == ["阶段"]


def test_remove_component_phrase() -> None:
    intent = build_intent("删除项目进度", DRAFT, CATALOG)
    assert not intent.unresolved
    assert intent.operations[0].op == "remove_slots"
    assert intent.operations[0].selectors[0].title == "项目进度"


DRAFT_FIVE = {
    "goal": "ERP 经营",
    "sources": ["erp"],
    "slots": [
        {"title": "合同额", "role": "metric", "datasetSlug": "erp", "measures": ["合同额"], "chartType": "metric"},
        {"title": "合同额分布", "role": "compare", "datasetSlug": "erp", "measures": ["合同额"], "dimensions": ["阶段"], "chartType": "bar"},
        {"title": "各部门合同额", "role": "compare", "datasetSlug": "erp", "measures": ["合同额"], "dimensions": ["部门"], "chartType": "bar"},
        {"title": "项目经营明细", "role": "detail", "datasetSlug": "erp", "measures": ["合同额"], "dimensions": ["项目名称"], "chartType": "table"},
        {"title": "项目进度", "role": "metric", "datasetSlug": "erp", "measures": ["进度"], "chartType": "metric"},
    ],
}


def test_swap_slots_by_layout_position() -> None:
    intent = build_intent("将布局中 5 和 3 位置互换", DRAFT_FIVE, CATALOG)
    assert intent.confidence == 0.9
    assert [op.op for op in intent.operations] == ["swap_slots"]
    assert intent.operations[0].positions == [5, 3]


def test_swap_slots_by_component_names() -> None:
    intent = build_intent("将项目进度 和 各部门合同额位置互换", DRAFT_FIVE, CATALOG)
    assert [op.op for op in intent.operations] == ["swap_slots"]
    assert intent.operations[0].positions == [5, 3]


def test_screen_draft_intent_uses_deterministic_swap_without_model() -> None:
    def broken_llm(system_prompt: str, payload: dict) -> dict:
        raise RuntimeError("model should not be called for a structural swap")

    result = screen_draft_intent(
        user_message="将项目进度 和 各部门合同额位置互换",
        turn_context=None,
        bind={},
        slots={},
        outputs={"get_current_plan": {"data": {"draft": DRAFT_FIVE}}, "describe_catalog_for_revise": {"data": CATALOG}},
        metadata={},
        llm=broken_llm,
    )
    assert result["intent"]["operations"][0]["op"] == "swap_slots"
    assert result["intent"]["operations"][0]["positions"] == [5, 3]

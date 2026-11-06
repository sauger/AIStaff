import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.dashboard.chart_policy import normalize_spec
from app.dashboard.compose import ComposeError, compose_dashboard
from app.dashboard.service import spec_digest
from app.dashboard.spec import MAX_INLINE_CELLS, SANITIZED_PLACEHOLDER, RenderInput

FIXTURES = Path(__file__).parent / "fixtures" / "dashboard"


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _compose(data, **arguments):
    return compose_dashboard({"title": "经营看板", "data": data, **arguments})


def _charts(result):
    return {chart.id: chart for chart in result.spec.charts}


def _slots(result):
    return {slot.id: slot for slot in result.spec.layout.slots}


def test_demo_contract_composes_in_one_call() -> None:
    result = _compose(_fixture("demo_blocks"), theme="tech", source="erp.sales")
    spec = result.spec
    assert spec.spec_version == 2
    assert spec.as_of == "2026-09-30"
    assert spec.meta.generated_by == "compose_dashboard"
    assert spec.meta.source == "erp.sales"
    assert [(kpi.id, kpi.label, kpi.value, kpi.unit, kpi.delta) for kpi in spec.kpis] == [
        ("kpi-blocks-0", "销售额", 12840000, "元", 0.12),
        ("kpi-blocks-1", "订单数", 3821, "单", -0.03),
    ]
    charts = _charts(result)
    assert (charts["blocks-2"].type, charts["blocks-2"].role) == ("line", "trend")
    assert (charts["blocks-3"].type, charts["blocks-3"].role) == ("donut", "composition")
    ranking = charts["blocks-4"]
    assert (ranking.type, ranking.orientation, ranking.role) == ("bar", "horizontal", "ranking")
    assert all(chart.source_path == f"blocks[{chart.id[-1]}]" for chart in spec.charts)
    assert all(0.3 <= chart.confidence <= 0.98 for chart in spec.charts)
    assert result.template == spec.layout.template == "pulse"
    slots = _slots(result)
    assert (slots["blocks-4"].region, slots["blocks-2"].region) == ("left", "right")
    # span stays meaningful for renderers that only understand v1
    assert charts["blocks-2"].span == 2 and charts["blocks-4"].span == 1
    assert spec.warnings == []


def test_user_requested_layout_is_honoured() -> None:
    result = _compose(
        _fixture("demo_blocks"),
        plan={
            "blocks": [
                {"path": "blocks[4]", "region": "left", "title": "区域销售排名"},
                {"label": "月度趋势", "region": "right"},
            ],
            "layout": {"template": "pulse", "hero": "blocks[2]"},
        },
    )
    slots = _slots(result)
    assert result.template == "pulse"
    assert (slots["blocks-4"].region, slots["blocks-4"].x) == ("left", 0)
    assert (slots["blocks-2"].region, slots["blocks-2"].x) == ("right", 4)
    assert _charts(result)["blocks-4"].title == "区域销售排名"
    assert [warning.code for warning in result.spec.warnings] == ["PLAN_LABEL_MATCHED"]


def test_empty_result_is_a_coded_non_retryable_error() -> None:
    with pytest.raises(ComposeError) as excinfo:
        _compose({"success": True, "data": []})
    error = excinfo.value
    assert (error.code, error.retryable) == ("EMPTY_DATA", False)
    assert "blocks" in error.details["expected"]
    assert error.details["empty_blocks"] == ["data"]


def test_capacity_is_eight_charts_and_six_kpis() -> None:
    result = _compose(_fixture("oversized"))
    assert len(result.spec.kpis) == 6
    assert len(result.spec.charts) == 8
    codes = [warning.code for warning in result.spec.warnings]
    assert codes.count("TRUNCATED") >= 2
    assert any("指标卡最多 6 个" in warning.message for warning in result.spec.warnings)
    assert any("图表最多 8 个" in warning.message for warning in result.spec.warnings)


def test_twelve_blocks_keep_trend_before_ranking_and_detail() -> None:
    blocks = [{"name": f"指标{index}", "value": index} for index in range(3)]
    blocks += [
        {"name": f"排名{index}", "columns": ["r", "v"], "rows": [["a", 30 + index], ["b", 20], ["c", 10]]}
        for index in range(5)
    ]
    blocks += [
        {"name": f"趋势{index}", "columns": ["m", "v"], "rows": [["2026-01", 1], ["2026-02", 2], ["2026-03", index]]}
        for index in range(4)
    ]
    result = _compose({"blocks": blocks})
    titles = [chart.title for chart in result.spec.charts]
    assert len(titles) == 8
    assert {f"趋势{index}" for index in range(4)} <= set(titles)
    assert "TRUNCATED" in {warning.code for warning in result.spec.warnings}


def test_negative_shares_become_a_horizontal_bar_with_the_phase_one_note() -> None:
    result = _compose(
        {"blocks": [{"name": "渠道分布", "columns": ["c", "v"], "rows": [["直营", 310], ["退货", -40]]}]}
    )
    chart = result.spec.charts[0]
    assert (chart.type, chart.orientation) == ("bar", "horizontal")
    assert chart.note.startswith("数据含负值，饼图无法表达，已改为条形图")


def test_long_detail_tables_are_trimmed_to_fifty_rows() -> None:
    rows = [{"store": f"门店{index}", "city": "上海", "amount": index} for index in range(200)]
    result = _compose({"门店明细": rows})
    chart = result.spec.charts[0]
    assert chart.type == "table"
    assert len(chart.data.rows) == 50
    assert "仅展示前 50 行" in chart.note
    assert any("仅展示前 50 行" in warning.message for warning in result.spec.warnings)


def test_board_cells_stay_within_the_contract() -> None:
    tables = {
        f"明细{index}": [{f"c{column}": f"v{column}" for column in range(12)} for _ in range(50)]
        for index in range(4)
    }
    result = _compose(tables)
    cells = sum(len(chart.data.rows) * len(chart.data.columns) for chart in result.spec.charts)
    assert cells <= MAX_INLINE_CELLS


def test_long_time_series_keep_the_latest_points() -> None:
    rows = [{"day": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}", "v": index} for index in range(80)]
    result = _compose({"每日销量": rows})
    chart = result.spec.charts[0]
    assert chart.type == "line"
    assert len(chart.data.rows) == 50
    assert chart.data.rows[-1][1] == 79
    assert "仅展示最近 50 个点" in chart.note


def test_dirty_cells_are_sanitised_but_render_dashboard_stays_strict() -> None:
    result = _compose(_fixture("dirty"))
    orders = _charts(result)["data-orders"]
    names = [column.name for column in orders.data.columns]
    link = names.index("link")
    amount = names.index("amount")
    assert orders.data.rows[0][link] == SANITIZED_PLACEHOLDER
    assert orders.data.rows[1][amount] is None
    remark = orders.data.rows[2][names.index("remark")]
    assert len(remark) <= 200
    codes = {warning.code for warning in result.spec.warnings}
    assert {"SANITIZED_CELL", "NESTED_CELL"} <= codes
    assert "https://" not in json.dumps(result.spec.model_dump(mode="json"), ensure_ascii=False)

    with pytest.raises(ValidationError):
        RenderInput.model_validate(
            {
                "title": "x",
                "charts": [
                    {
                        "id": "a",
                        "data": {"columns": [{"name": "u"}], "rows": [["https://example.com"]]},
                    }
                ],
            }
        )


def test_string_json_is_accepted_and_bad_payloads_are_coded() -> None:
    result = _compose(json.dumps(_fixture("demo_blocks"), ensure_ascii=False))
    assert len(result.spec.charts) == 3
    with pytest.raises(ComposeError) as excinfo:
        _compose("{oops")
    assert excinfo.value.code == "DATA_NOT_JSON"
    with pytest.raises(ComposeError) as excinfo:
        _compose("x" * (4 * 1024 * 1024 + 1))
    assert excinfo.value.code == "DATA_TOO_LARGE"


def test_invalid_arguments_carry_field_paths() -> None:
    with pytest.raises(ComposeError) as excinfo:
        compose_dashboard({"title": "x", "data": [], "charts": []})
    assert excinfo.value.code == "INVALID_ARGUMENTS"
    assert "render_dashboard" in excinfo.value.message
    with pytest.raises(ComposeError) as excinfo:
        compose_dashboard({"title": "x"})
    assert excinfo.value.details["errors"][0]["path"] == "data"
    with pytest.raises(ComposeError) as excinfo:
        compose_dashboard({"title": "x", "data": {"a": [1]}, "theme": "neon"})
    assert excinfo.value.details["errors"][0]["path"] == "theme"


def test_text_fields_are_cleaned_not_rejected() -> None:
    result = compose_dashboard(
        {"title": "看板 https://evil.example/x " + "长" * 100, "data": {"销售额": 1}, "source": "erp"}
    )
    assert "https://" not in result.spec.title
    assert len(result.spec.title) <= 80


def test_outline_lists_blocks_without_drawing() -> None:
    result = _compose(_fixture("demo_blocks"), mode="outline")
    assert result.spec is None
    outline = result.outline
    assert outline["mode"] == "outline"
    assert [entry["path"] for entry in outline["blocks"]] == [f"blocks[{index}]" for index in range(5)]
    assert outline["blocks"][2]["suggest"] == "line/trend"
    assert outline["template"] == "pulse"
    assert all(len(json.dumps(entry, ensure_ascii=False)) <= 100 for entry in outline["blocks"])
    assert "12840000" not in json.dumps(outline, ensure_ascii=False)


def test_outline_caps_the_block_list() -> None:
    outline = _compose(_fixture("oversized"), mode="outline").outline
    assert len(outline["blocks"]) == 12
    assert outline["omitted"] == outline["block_count"] - 12


def test_composition_is_deterministic() -> None:
    first = _compose(_fixture("nested")).spec
    second = _compose(_fixture("nested")).spec
    assert spec_digest(first) == spec_digest(second)


def test_v1_render_path_is_unchanged() -> None:
    spec, _ = normalize_spec(
        RenderInput.model_validate(
            {
                "title": "旧",
                "charts": [
                    {
                        "id": "a",
                        "intent": "compare",
                        "type": "bar",
                        "data": {"columns": [{"name": "k"}, {"name": "v", "type": "number"}], "rows": [["x", 1]]},
                    }
                ],
            }
        )
    )
    dumped = spec.model_dump(mode="json")
    assert dumped["spec_version"] == 1
    assert "orientation" not in dumped["charts"][0]
    assert "layout" not in dumped

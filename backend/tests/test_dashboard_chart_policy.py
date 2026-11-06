
from app.dashboard.chart_policy import (
    Adjustment,
    decide,
    infer_intent,
    normalize_spec,
    profile,
    resolve_encoding,
)
from app.dashboard.spec import Chart, RenderInput


def _chart(chart_type=None, intent=None, months=None, values=None, **extra):
    rows = list(zip(months or ["2026-07"], values or [1]))
    payload = {
        "id": "c1",
        "title": "图表",
        "intent": intent,
        "type": chart_type,
        "encoding": {"x": "bucket", "y": "value"},
        "data": {
            "columns": [{"name": "bucket", "type": "string"}, {"name": "value", "type": "number"}],
            "rows": [list(row) for row in rows],
        },
    }
    payload.update(extra)
    return Chart.model_validate(payload)


def _types(adjustments: list[Adjustment]) -> list[tuple[str, str | None, str]]:
    return [(item.chart_id, item.from_type, item.to_type) for item in adjustments]


def test_three_point_trend_stays_line() -> None:
    chart = _chart(intent="trend", months=["2026-01", "2026-02", "2026-03"], values=[1, 2, 3])
    decision = decide("trend", profile(chart))
    assert decision.chart_type == "line"
    assert decision.reason is None


def test_two_point_trend_is_downgraded_to_column_with_reason() -> None:
    chart = _chart(intent="trend", months=["2026-01", "2026-02"], values=[1, 2])
    decision = decide("trend", profile(chart))
    assert decision.chart_type == "column"
    assert decision.reason and "连续趋势" in decision.reason


def test_single_point_line_request_becomes_column() -> None:
    chart = _chart(chart_type="line", months=["2026-01"], values=[7])
    decision = decide("trend", profile(chart), requested="line")
    assert decision.chart_type == "column"


def test_pie_with_seven_categories_is_downgraded_to_bar() -> None:
    months = [f"cat{index}" for index in range(7)]
    values = [10, 9, 8, 7, 6, 5, 4]
    chart = _chart(chart_type="pie", intent="composition", months=months, values=values)
    decision = decide("composition", profile(chart), requested="pie")
    assert decision.chart_type == "bar"
    assert "超过饼图可读上限" in (decision.reason or "")


def test_pie_with_dominant_category_is_downgraded() -> None:
    chart = _chart(chart_type="pie", intent="composition", months=["a", "b", "c"], values=[95, 3, 2])
    decision = decide("composition", profile(chart), requested="pie")
    assert decision.chart_type == "bar"
    assert "退化" in (decision.reason or "")


def test_pie_with_negative_values_is_never_drawn() -> None:
    chart = _chart(chart_type="pie", intent="composition", months=["a", "b"], values=[5, -2])
    decision = decide("composition", profile(chart), requested="pie")
    assert decision.chart_type in {"bar", "table"}
    assert "负值" in (decision.reason or "")


def test_many_categories_fall_back_to_table() -> None:
    months = [f"c{index}" for index in range(40)]
    values = list(range(40))
    chart = _chart(intent="compare", months=months, values=values)
    decision = decide("compare", profile(chart))
    assert decision.chart_type == "table"


def test_long_labels_use_horizontal_bar() -> None:
    months = ["华东区域销售中心", "华南区域销售中心", "华北区域销售中心"]
    values = [3, 2, 1]
    chart = _chart(intent="compare", months=months, values=values)
    decision = decide("compare", profile(chart))
    assert decision.chart_type == "bar"


def test_line_request_on_categorical_axis_becomes_bar() -> None:
    chart = _chart(chart_type="line", months=["北京", "上海"], values=[3, 4])
    decision = decide("compare", profile(chart), requested="line")
    assert decision.chart_type == "bar"
    assert "不是时间维度" in (decision.reason or "")


def test_single_scalar_becomes_kpi_card() -> None:
    chart = _chart(intent="kpi", months=["本月"], values=[128])
    spec, adjustments = normalize_spec(RenderInput.model_validate({"title": "单值", "charts": [chart.model_dump()]}))
    assert spec.charts == []
    assert len(spec.kpis) == 1
    assert spec.kpis[0].value == 128
    assert _types(adjustments) == [("c1", None, "kpi")]


def test_missing_encoding_is_filled_from_data() -> None:
    chart = Chart.model_validate(
        {
            "id": "auto",
            "intent": "trend",
            "data": {
                "columns": [
                    {"name": "month", "type": "string"},
                    {"name": "sales", "type": "number"},
                ],
                "rows": [["2026-01", 1], ["2026-02", 2], ["2026-03", 3]],
            },
        }
    )
    assert resolve_encoding(chart) == ("month", "sales", None)
    spec, _ = normalize_spec(RenderInput.model_validate({"title": "自动", "charts": [chart.model_dump()]}))
    assert spec.charts[0].encoding == {"x": "month", "y": "sales"}
    assert spec.charts[0].type == "line"


def test_note_records_the_downgrade() -> None:
    chart = _chart(chart_type="pie", intent="composition", months=["a", "b", "c", "d", "e", "f", "g"], values=[7, 6, 5, 4, 3, 2, 1])
    spec, adjustments = normalize_spec(RenderInput.model_validate({"title": "占比", "charts": [chart.model_dump()]}))
    assert spec.charts[0].type == "bar"
    assert spec.charts[0].note and "饼图" in spec.charts[0].note
    assert adjustments[0].from_type == "pie"
    assert adjustments[0].to_type == "bar"


def test_empty_data_renders_as_table() -> None:
    chart = Chart.model_validate(
        {
            "id": "empty",
            "intent": "compare",
            "data": {"columns": [{"name": "bucket"}, {"name": "value"}], "rows": []},
        }
    )
    spec, _adjustments = normalize_spec(RenderInput.model_validate({"title": "空", "charts": [chart.model_dump()]}))
    assert spec.charts[0].type == "table"
    assert "没有可绘制的数据" in (spec.charts[0].note or "")


def test_all_equal_values_are_still_rendered() -> None:
    chart = _chart(intent="compare", months=["a", "b", "c"], values=[5, 5, 5])
    decision = decide("compare", profile(chart))
    assert decision.chart_type in {"column", "bar", "table"}


def test_infer_intent_prefers_time_for_date_columns() -> None:
    chart = _chart(months=["2026-01-01", "2026-02-01", "2026-03-01"], values=[1, 2, 3])
    assert infer_intent(chart, profile(chart)) == "trend"


def test_infer_intent_falls_back_to_compare() -> None:
    chart = _chart(months=["a", "b", "c"], values=[1, 2, 3])
    assert infer_intent(chart, profile(chart)) == "compare"


def test_kpis_only_board_stays_valid() -> None:
    spec, adjustments = normalize_spec(
        RenderInput.model_validate({"title": "只有 kpi", "kpis": [{"label": "总数", "value": 1}]})
    )
    assert spec.charts == []
    assert len(spec.kpis) == 1
    assert adjustments == []


def test_chart_without_numeric_measure_falls_back_to_table() -> None:
    chart = Chart.model_validate(
        {
            "id": "text",
            "intent": "compare",
            "data": {
                "columns": [{"name": "bucket"}, {"name": "value"}],
                "rows": [["a", "not-a-number"], ["b", "also-text"]],
            },
        }
    )
    spec, _ = normalize_spec(RenderInput.model_validate({"title": "文本", "charts": [chart.model_dump()]}))
    assert spec.charts[0].type == "table"

def test_small_composition_uses_donut() -> None:
    chart = _chart(intent="composition", months=["a", "b", "c"], values=[5, 3, 2])
    decision = decide("composition", profile(chart))
    assert decision.chart_type == "donut"


def test_multi_series_comparison_uses_stacked_column() -> None:
    chart = Chart.model_validate(
        {
            "id": "stack",
            "intent": "compare",
            "encoding": {"x": "month", "y": "sales", "series": "region"},
            "data": {
                "columns": [
                    {"name": "month", "type": "string"},
                    {"name": "sales", "type": "number"},
                    {"name": "region", "type": "string"},
                ],
                "rows": [
                    ["2026-07", 10, "华东"],
                    ["2026-07", 5, "华南"],
                    ["2026-08", 12, "华东"],
                    ["2026-08", 7, "华南"],
                ],
            },
        }
    )
    decision = decide("compare", profile(chart))
    assert decision.chart_type == "stacked_column"


def test_gauge_allowed_for_single_value() -> None:
    chart = _chart(chart_type="gauge", months=["完成率"], values=[72])
    decision = decide("kpi", profile(chart), requested="gauge")
    assert decision.chart_type == "gauge"


def test_gauge_request_with_many_points_falls_back_to_table() -> None:
    chart = _chart(chart_type="gauge", months=["a", "b", "c"], values=[1, 2, 3])
    decision = decide("kpi", profile(chart), requested="gauge")
    assert decision.chart_type == "table"


def test_funnel_requires_non_negative_values() -> None:
    chart = _chart(chart_type="funnel", months=["a", "b"], values=[5, -2])
    decision = decide("composition", profile(chart), requested="funnel")
    assert decision.chart_type == "bar"

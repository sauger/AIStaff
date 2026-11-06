import json

import pytest
from pydantic import ValidationError

from app.dashboard.spec import (
    MAX_INLINE_CELLS,
    RenderInput,
    RenderSpec,
    compose_input_json_schema,
    render_input_json_schema,
)


def _chart(**overrides):
    chart = {
        "id": "trend",
        "title": "月度趋势",
        "intent": "trend",
        "encoding": {"x": "month", "y": "sales"},
        "data": {
            "columns": [{"name": "month", "type": "string"}, {"name": "sales", "type": "number"}],
            "rows": [["2026-07", 1], ["2026-08", 2]],
        },
    }
    chart.update(overrides)
    return chart


def _spec(**overrides):
    payload = {"title": "销售大屏", "charts": [_chart()]}
    payload.update(overrides)
    return payload


def test_minimal_spec_is_accepted() -> None:
    spec = RenderInput.model_validate(_spec())
    assert spec.title == "销售大屏"
    assert spec.charts[0].id == "trend"
    assert spec.meta.generated_by == "model"


def test_board_without_blocks_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate({"title": "空大屏"})
    assert "至少需要 1 个指标卡或 1 个图表" in str(excinfo.value)


def test_chart_without_intent_or_type_is_allowed_for_inference() -> None:
    spec = RenderInput.model_validate(_spec(charts=[_chart(intent=None, type=None)]))
    assert spec.charts[0].intent is None
    assert spec.charts[0].type is None


def test_unknown_encoding_key_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate(_spec(charts=[_chart(encoding={"x": "month", "color": "sales"})]))
    assert "encoding 只支持" in str(excinfo.value)


def test_encoding_must_reference_existing_column() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate(_spec(charts=[_chart(encoding={"x": "month", "y": "missing"})]))
    assert "指向不存在的列" in str(excinfo.value)


def test_row_width_must_match_columns() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate(
            _spec(
                charts=[
                    _chart(
                        data={
                            "columns": [{"name": "month"}, {"name": "sales"}],
                            "rows": [["2026-07"]],
                        }
                    )
                ]
            )
        )
    assert "与 2 列不一致" in str(excinfo.value)


def test_url_in_data_cell_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate(
            _spec(
                charts=[
                    _chart(
                        data={
                            "columns": [{"name": "month"}, {"name": "sales"}],
                            "rows": [["https://evil.example/x", 1]],
                        }
                    )
                ]
            )
        )
    assert "不允许包含 URL" in str(excinfo.value)


def test_nan_value_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate(
            _spec(
                charts=[
                    _chart(
                        data={
                            "columns": [{"name": "month"}, {"name": "sales"}],
                            "rows": [["2026-07", float("nan")]],
                        }
                    )
                ]
            )
        )
    assert "NaN" in str(excinfo.value)


def test_duplicate_chart_ids_are_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate(_spec(charts=[_chart(), _chart()]))
    assert "图表 id 重复" in str(excinfo.value)


def test_too_many_charts_are_rejected() -> None:
    charts = [_chart(id=f"c{index}") for index in range(9)]
    with pytest.raises(ValidationError):
        RenderInput.model_validate(_spec(charts=charts))


def test_inline_cells_limit_is_enforced() -> None:
    columns = [
        {"name": "bucket"},
        {"name": "a", "type": "number"},
        {"name": "b", "type": "number"},
        {"name": "c", "type": "number"},
    ]
    rows = [[f"row{index}", index, index + 1, index + 2] for index in range(50)]
    charts = [
        _chart(
            id=f"c{index}",
            encoding={"x": "bucket", "y": "a"},
            data={"columns": columns, "rows": rows},
        )
        for index in range(8)
    ]
    with pytest.raises(ValidationError) as excinfo:
        RenderInput.model_validate(_spec(charts=charts))
    assert str(MAX_INLINE_CELLS) in str(excinfo.value)


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RenderInput.model_validate(_spec(html="<script>alert(1)</script>"))


def test_model_schema_is_flat_without_refs() -> None:
    schema = render_input_json_schema()
    dumped = json.dumps(schema)
    assert "$ref" not in dumped
    assert "$defs" not in dumped
    assert schema["required"] == ["title"]
    assert schema["properties"]["charts"]["items"]["properties"]["intent"]["enum"] == [
        "trend",
        "compare",
        "composition",
        "detail",
        "kpi",
    ]


# --- RenderSpec v2 -----------------------------------------------------------

V2_EXAMPLE = {
    "spec_version": 2,
    "title": "华东区 Q3 经营看板",
    "theme": "tech",
    "kpis": [
        {"id": "kpi_sales", "label": "销售额", "value": 12840000, "unit": "元", "delta": 0.12}
    ],
    "charts": [
        {
            "id": "monthly",
            "title": "月度销售趋势",
            "intent": "trend",
            "type": "line",
            "role": "trend",
            "confidence": 0.92,
            "orientation": "auto",
            "source_path": "result.monthly",
            "span": 2,
            "encoding": {"x": "month", "y": "sales"},
            "data": {
                "columns": [{"name": "month", "type": "date"}, {"name": "sales", "type": "number"}],
                "rows": [["2026-07", 431], ["2026-08", 452], ["2026-09", 433]],
            },
            "note": "时间轴 3 个点，按折线展示",
        }
    ],
    "layout": {
        "template": "overview",
        "grid": {"columns": 12, "rows": 12, "width": 1920, "height": 1080},
        "slots": [
            {"id": "kpi_sales", "kind": "kpi", "region": "kpi_row", "x": 0, "y": 1, "w": 3, "h": 1},
            {"id": "monthly", "kind": "chart", "region": "hero", "x": 0, "y": 2, "w": 12, "h": 4},
        ],
        "overflow": "fit",
    },
    "warnings": [],
}


def _v2(**overrides):
    payload = json.loads(json.dumps(V2_EXAMPLE))
    payload.update(overrides)
    return payload


def _v1_stored_json() -> dict:
    from app.dashboard.chart_policy import normalize_spec

    spec, _ = normalize_spec(RenderInput.model_validate(_spec(kpis=[{"label": "销售额", "value": 1}])))
    return spec.model_dump(mode="json")


def test_v2_plan_example_validates() -> None:
    spec = RenderSpec.model_validate(V2_EXAMPLE)
    assert spec.spec_version == 2
    assert spec.layout is not None and spec.layout.template == "overview"
    assert spec.layout.grid.width == 1920
    assert spec.kpis[0].id == "kpi_sales"
    assert spec.charts[0].role == "trend"
    assert spec.charts[0].source_path == "result.monthly"
    dumped = spec.model_dump(mode="json")
    assert dumped["layout"]["slots"][1]["w"] == 12
    assert dumped["meta"]["generator_version"] == "2.0"


def test_v1_stored_json_round_trips_without_v2_keys() -> None:
    stored = _v1_stored_json()
    assert stored["spec_version"] == 1
    assert "layout" not in stored and "warnings" not in stored
    assert not {"role", "confidence", "source_path", "orientation"} & set(stored["charts"][0])
    assert "id" not in stored["kpis"][0]
    assert "generator_version" not in stored["meta"]

    reread = RenderSpec.model_validate(stored)
    assert reread.spec_version == 1
    assert reread.model_dump(mode="json") == stored


def test_new_specs_default_to_version_two() -> None:
    spec = RenderSpec.model_validate({"title": "x", "kpis": [{"label": "a", "value": 1}]})
    assert spec.spec_version == 2
    assert spec.layout is None
    assert spec.model_dump(mode="json")["warnings"] == []


def test_span_follows_slot_width() -> None:
    narrow = _v2()
    narrow["charts"][0]["span"] = 2
    narrow["layout"]["slots"][1].update({"x": 0, "w": 6})
    assert RenderSpec.model_validate(narrow).charts[0].span == 1

    wide = _v2()
    wide["charts"][0]["span"] = 1
    wide["layout"]["slots"][1].update({"x": 0, "w": 8})
    assert RenderSpec.model_validate(wide).charts[0].span == 2


def test_layout_rejects_overlapping_slots() -> None:
    payload = _v2()
    payload["layout"]["slots"][1].update({"y": 1})
    with pytest.raises(ValidationError) as excinfo:
        RenderSpec.model_validate(payload)
    assert "重叠" in str(excinfo.value)


def test_layout_rejects_out_of_grid_slots() -> None:
    wide = _v2()
    wide["layout"]["slots"][1].update({"x": 6, "w": 12})
    with pytest.raises(ValidationError) as excinfo:
        RenderSpec.model_validate(wide)
    assert "超出 12 列" in str(excinfo.value)

    tall = _v2()
    tall["layout"]["slots"][1].update({"y": 9, "h": 4})
    with pytest.raises(ValidationError) as excinfo:
        RenderSpec.model_validate(tall)
    assert "超出 12 行" in str(excinfo.value)


def test_scroll_layout_may_run_past_the_fold() -> None:
    payload = _v2()
    payload["layout"]["overflow"] = "scroll"
    payload["layout"]["slots"][1].update({"y": 9, "h": 4})
    assert RenderSpec.model_validate(payload).layout.overflow == "scroll"


def test_layout_slots_must_match_blocks_exactly_once() -> None:
    missing = _v2()
    missing["layout"]["slots"] = missing["layout"]["slots"][:1]
    with pytest.raises(ValidationError) as excinfo:
        RenderSpec.model_validate(missing)
    assert "缺少槽位" in str(excinfo.value)

    stray = _v2()
    stray["layout"]["slots"].append(
        {"id": "ghost", "kind": "chart", "region": "grid", "x": 0, "y": 6, "w": 6, "h": 3}
    )
    with pytest.raises(ValidationError) as excinfo:
        RenderSpec.model_validate(stray)
    assert "没有对应" in str(excinfo.value)

    duplicate = _v2()
    duplicate["layout"]["slots"].append(dict(duplicate["layout"]["slots"][1], y=7))
    with pytest.raises(ValidationError) as excinfo:
        RenderSpec.model_validate(duplicate)
    assert "槽位 id 重复" in str(excinfo.value)


def test_layout_requires_kpi_ids() -> None:
    payload = _v2()
    del payload["kpis"][0]["id"]
    with pytest.raises(ValidationError) as excinfo:
        RenderSpec.model_validate(payload)
    assert "必须有 id" in str(excinfo.value)


def test_layout_is_rejected_on_v1_specs() -> None:
    with pytest.raises(ValidationError):
        RenderSpec.model_validate(_v2(spec_version=1))


def test_warning_list_is_capped() -> None:
    warnings = [{"code": "TRUNCATED", "message": f"m{index}"} for index in range(21)]
    with pytest.raises(ValidationError):
        RenderSpec.model_validate(_v2(warnings=warnings))
    with pytest.raises(ValidationError):
        RenderSpec.model_validate(_v2(warnings=[{"code": "lower", "message": "x"}]))


def test_orientation_and_confidence_are_bounded() -> None:
    bad_orientation = _v2()
    bad_orientation["charts"][0]["orientation"] = "diagonal"
    with pytest.raises(ValidationError):
        RenderSpec.model_validate(bad_orientation)
    bad_confidence = _v2()
    bad_confidence["charts"][0]["confidence"] = 1.2
    with pytest.raises(ValidationError):
        RenderSpec.model_validate(bad_confidence)


def test_compose_schema_is_flat_and_compact() -> None:
    schema = compose_input_json_schema()
    dumped = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    assert "$ref" not in dumped
    assert "$defs" not in dumped
    assert len(dumped.encode("utf-8")) <= 2048
    assert schema["required"] == ["title", "data"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["data"]["type"] == ["object", "array", "string"]
    assert "charts" not in schema["properties"]
    plan = schema["properties"]["plan"]["properties"]
    assert plan["layout"]["properties"]["template"]["enum"] == [
        "overview",
        "compare",
        "pulse",
        "detail",
        "stack",
    ]
    assert plan["blocks"]["items"]["properties"]["role"]["enum"][-1] == "auto"

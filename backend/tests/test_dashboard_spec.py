import json

import pytest
from pydantic import ValidationError

from app.dashboard.spec import (
    MAX_INLINE_CELLS,
    RenderInput,
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

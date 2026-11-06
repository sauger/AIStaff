import json
from pathlib import Path

import pytest

from app.dashboard.normalize import (
    MAX_FRAMES,
    DashboardDataError,
    normalize,
    normalize_source,
    parse_data,
    parse_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dashboard"


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _shape(frames):
    return [(frame.path, frame.shape) for frame in frames]


def _columns(frame):
    return [(column.name, column.type) for column in frame.columns]


def test_demo_contract_is_the_primary_path() -> None:
    frames, warnings = normalize_source(_fixture("demo_blocks"))
    assert warnings == []
    assert [frame.path for frame in frames] == [f"blocks[{index}]" for index in range(5)]
    assert [frame.label for frame in frames] == ["销售额", "订单数", "月度趋势", "渠道分布", "区域排名"]
    sales = frames[0]
    assert sales.shape == "scalar"
    assert sales.rows == [[12840000]]
    assert (sales.unit, sales.delta) == ("元", 0.12)
    assert frames[1].delta == -0.03
    assert _columns(frames[2]) == [("month", "date"), ("sales", "number")]
    assert frames[4].rows[0] == ["上海", 320]


def test_columns_with_list_rows_keep_declared_types() -> None:
    frames, _ = normalize_source(
        {
            "columns": [{"name": "d", "type": "date"}, {"name": "v", "type": "number"}],
            "rows": [["2026-01", 1], ["2026-02", None]],
        }
    )
    assert _shape(frames) == [("$", "table")]
    assert _columns(frames[0]) == [("d", "date"), ("v", "number")]


def test_columns_with_object_rows_follow_column_order() -> None:
    frames, _ = normalize_source(
        {"columns": ["region", "sales"], "rows": [{"sales": 3, "region": "上海"}, {"region": "江苏"}]}
    )
    assert frames[0].rows == [["上海", 3], ["江苏", None]]


def test_record_array_uses_first_seen_column_order() -> None:
    frames, _ = normalize_source(
        [{"month": "2026-07", "sales": 1}, {"sales": 2, "month": "2026-08", "note": "峰值"}]
    )
    assert _shape(frames) == [("$", "records")]
    assert _columns(frames[0]) == [("month", "date"), ("sales", "number"), ("note", "string")]
    assert frames[0].rows == [["2026-07", 1, None], ["2026-08", 2, "峰值"]]


def test_equal_length_columns_become_one_frame() -> None:
    frames, _ = normalize_source(
        {"week": ["W1", "W2", "W3"], "visits": [10, 12, 9], "unit": "次"}
    )
    assert _shape(frames) == [("$", "columns")]
    assert frames[0].rows == [["W1", 10], ["W2", 12], ["W3", 9]]


def test_matrix_header_detection() -> None:
    with_header, _ = normalize_source([["region", "sales"], ["上海", 320], ["江苏", 280]])
    assert _columns(with_header[0]) == [("region", "string"), ("sales", "number")]
    assert with_header[0].synthetic_headers is False

    without_header, _ = normalize_source([[1, 2], [3, 4]])
    assert [column.name for column in without_header[0].columns] == ["c1", "c2"]
    assert without_header[0].synthetic_headers is True
    assert without_header[0].rows == [[1, 2], [3, 4]]


def test_named_wrappers_carry_their_label() -> None:
    frames, _ = normalize_source(
        {"name": "门店明细", "columns": ["store", "amount"], "rows": [["静安店", 1]]}
    )
    assert frames[0].label == "门店明细"
    titled, _ = normalize_source({"title": "渠道", "data": [{"c": "直营", "v": 1}]})
    assert titled[0].label == "渠道"
    assert titled[0].path == "data"


def test_nested_objects_produce_one_frame_per_child() -> None:
    frames, _ = normalize_source(
        {
            "result": {
                "monthly": [{"m": "2026-07", "v": 1}, {"m": "2026-08", "v": 2}],
                "items": [{"columns": ["a"], "rows": [[1]]}, {"columns": ["b"], "rows": [[2]]}],
            },
            "meta": {"region": "华东", "sales": 1},
        }
    )
    assert [frame.path for frame in frames] == [
        "result.monthly",
        "result.items[0]",
        "result.items[1]",
        "meta",
    ]


def test_scalar_groups_temporal_maps_and_large_mappings() -> None:
    kpis, _ = normalize_source({"销售额": 100, "订单数": 20, "状态": "正常"})
    assert _shape(kpis) == [("$", "scalar_group")]
    assert kpis[0].rows == [[100, 20, "正常"]]

    temporal, _ = normalize_source({"2026-01": 12, "2026-02": 15, "2026-03": None})
    assert _shape(temporal) == [("$", "temporal_map")]
    assert _columns(temporal[0]) == [("时间", "date"), ("数值", "number")]

    wide, _ = normalize_source({f"城市{index}": index for index in range(13)})
    assert _shape(wide) == [("$", "mapping")]
    assert len(wide[0].rows) == 13


def test_envelopes_are_unwrapped_without_their_status_fields() -> None:
    frames, _ = normalize_source({"success": True, "data": [{"region": "上海", "sales": 1}]})
    assert _shape(frames) == [("data", "records")]
    nested, _ = normalize_source(
        {"code": 0, "msg": "ok", "request_id": "r1", "data": {"result": [{"a": 1}]}}
    )
    assert [frame.path for frame in nested] == ["data.result"]


def test_column_type_inference() -> None:
    frames, _ = normalize_source(
        [
            {"flag": True, "n": 1, "d": "2026-07-01", "t": "2026-07-01T08:00:00", "s": "x"},
            {"flag": False, "n": 2.5, "d": "2026-07-02", "t": "2026-07-02T09:00:00", "s": 3},
        ]
    )
    assert _columns(frames[0]) == [
        ("flag", "boolean"),
        ("n", "number"),
        ("d", "date"),
        ("t", "datetime"),
        ("s", "string"),
    ]


def test_duplicate_content_is_kept_once() -> None:
    rows = [{"a": "x", "b": 1}, {"a": "y", "b": 2}]
    frames, _ = normalize_source({"data": rows, "result": rows})
    assert [frame.path for frame in frames] == ["data"]


def test_limits_are_reported_not_fatal() -> None:
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": [{"x": 1}]}}}}}}}
    frames, warnings = normalize_source({"top": [{"x": 1}], "deep": deep})
    assert [frame.path for frame in frames] == ["top"]
    assert [item.code for item in warnings] == ["DEPTH_LIMIT"]

    many = {f"k{index}": [{"x": index}] for index in range(30)}
    frames, warnings = normalize_source(many)
    assert len(frames) == MAX_FRAMES
    assert warnings[0].code == "TRUNCATED"

    sprawling = {f"k{index}": {"x": {}} for index in range(300)}
    _frames, warnings = normalize_source(sprawling)
    assert [item.code for item in warnings] == ["TRUNCATED"]
    assert "节点" in warnings[0].message


def test_nested_cells_are_flattened_to_short_text() -> None:
    frames, warnings = normalize_source(
        [{"id": 1, "meta": {"tags": ["vip"] * 40}}, {"id": 2, "meta": None}]
    )
    cell = frames[0].rows[0][1]
    assert isinstance(cell, str) and len(cell) <= 120
    assert frames[0].columns[1].type == "string"
    assert [item.code for item in warnings] == ["NESTED_CELL"]


def test_empty_inputs_produce_no_frames() -> None:
    for value in ({}, [], {"success": True, "data": []}, "", None, {"data": {"rows": []}}):
        result = normalize(value)
        assert result.frames == []
    assert normalize({"a": [], "b": {}}).empty_paths == ["a", "b"]


def test_string_json_is_parsed_and_invalid_json_is_rejected() -> None:
    frames, _ = normalize_source(json.dumps({"blocks": [{"name": "销售额", "value": 1}]}))
    assert frames[0].label == "销售额"
    with pytest.raises(DashboardDataError) as excinfo:
        parse_data("{not json")
    assert excinfo.value.code == "DATA_NOT_JSON"
    with pytest.raises(DashboardDataError) as excinfo:
        parse_data("[" + "1," * 2_200_000 + "1]")
    assert excinfo.value.code == "DATA_TOO_LARGE"


def test_unresolved_references_are_explained() -> None:
    with pytest.raises(DashboardDataError) as excinfo:
        parse_data({"kind": "sandbox_text_file", "sandbox_path": "/workspace/exports/a.csv"})
    assert excinfo.value.code == "UNSUPPORTED_DATA_REFERENCE"
    with pytest.raises(DashboardDataError) as excinfo:
        parse_data({"kind": "sandbox_json_file", "sandbox_path": "/workspace/.harness/x.json"})
    assert excinfo.value.retryable is True


def test_path_syntax_accepts_dots_indexes_and_json_pointer() -> None:
    assert parse_path("result.monthly") == ("result", "monthly")
    assert parse_path("$.items[0].data") == ("items", 0, "data")
    assert parse_path("/result/monthly") == ("result", "monthly")
    assert parse_path("/items/0") == ("items", 0)
    assert parse_path('$["月度 销售"]') == ("月度 销售",)
    assert parse_path("$") == ()
    assert parse_path("a..b") is None


def test_fixture_nested_shapes() -> None:
    frames, warnings = normalize_source(_fixture("nested"))
    assert warnings == []
    assert _shape(frames) == [
        ("data.result.summary", "scalar_group"),
        ("data.result.monthly", "records"),
        ("data.result.by_channel", "table"),
        ("data.result.region_top", "matrix"),
        ("data.result.daily", "temporal_map"),
        ("data.result.weekly", "columns"),
        ("data.result.items[0].data", "records"),
    ]
    assert frames[-1].label == "门店明细"


def test_fixture_dirty_data() -> None:
    result = normalize(_fixture("dirty"))
    assert [frame.path for frame in result.frames] == ["data.orders", "data.matrix", "data.flags"]
    orders = result.frames[0]
    assert [column.name for column in orders.columns] == [
        "order_id",
        "store",
        "amount",
        "link",
        "meta",
        "remark",
    ]
    assert orders.columns[2].type == "number"
    assert result.empty_paths == ["data.empty_list", "data.empty_obj"]
    assert [item.code for item in result.warnings] == ["NESTED_CELL"]


def test_fixture_oversized_is_truncated_with_warnings() -> None:
    result = normalize(_fixture("oversized"))
    assert len(result.frames) == MAX_FRAMES
    assert result.frames[0].path == "report.summary"
    assert result.frames[1].path == "report.detail"
    assert result.frames[1].total_rows == 200
    assert [item.code for item in result.warnings] == ["DEPTH_LIMIT", "TRUNCATED"]


def test_blocks_with_a_bare_data_field_stay_a_record_table() -> None:
    frames = normalize(
        {"blocks": [{"name": "a", "data": 1}, {"name": "b", "data": 2}]}
    ).frames
    assert len(frames) == 1
    assert frames[0].shape == "records"
    assert [column.name for column in frames[0].columns] == ["name", "data"]
    assert frames[0].rows == [["a", 1], ["b", 2]]

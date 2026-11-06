import pytest

from app.dashboard.blocks import Block, split_blocks
from app.dashboard.normalize import Frame, normalize_source
from app.dashboard.select import choose_encoding, kpis_from_block, select_component
from app.dashboard.spec import Column


def _block(data, *, title=None, hint=None):
    frames, _ = normalize_source(data)
    block = split_blocks(frames)[0]
    if title is not None:
        block.title = title
    block.role_hint = hint
    return block


def _records(rows, *, x="name", y="value"):
    return [{x: label, y: value} for label, value in rows]


def test_rule_1_empty_block_is_dropped() -> None:
    frame = Frame(path="$", label="空", columns=[Column(name="a")], rows=[], shape="records")
    block = Block(id="b", path="$", label="空", title="空", frame=frame, role_hint=None, score=0, order=0)
    assert select_component(block).component == "drop"


def test_rule_2_scalar_blocks_become_kpis() -> None:
    selection = select_component(_block({"blocks": [{"name": "销售额", "value": 1, "unit": "元"}]}))
    assert (selection.component, selection.rule, selection.confidence) == ("kpi", 2, 0.95)

    labelled = _block([{"region": "上海", "sales": 320}], title="上海销售")
    assert select_component(labelled).component == "kpi"
    cards = kpis_from_block(labelled)
    assert [(card.label, card.value, card.hint) for card in cards] == [("上海销售", 320, "上海")]


def test_rule_3_single_row_with_several_measures_splits_into_kpis() -> None:
    block = _block([{"region": "华东", "sales": 10, "orders": 2, "customers": 3}])
    selection = select_component(block)
    assert (selection.component, selection.rule, selection.confidence) == ("kpi", 3, 0.9)
    assert [card.label for card in kpis_from_block(block)] == ["Sales", "Orders", "Customers"]


def test_kpi_cards_respect_the_limit_and_carry_units() -> None:
    group = _block({f"m{index}": index for index in range(8)})
    assert len(kpis_from_block(group)) == 6
    assert len(kpis_from_block(group, limit=2)) == 2
    scalar = _block({"blocks": [{"name": "订单数", "value": 3821, "unit": "单", "delta": -0.03}]})
    card = kpis_from_block(scalar)[0]
    assert (card.label, card.value, card.unit, card.delta) == ("订单数", 3821, "单", -0.03)


def test_rule_4_no_measure_means_table() -> None:
    selection = select_component(_block([{"store": "静安", "city": "上海"}, {"store": "西湖", "city": "杭州"}]))
    assert (selection.component, selection.rule) == ("table", 4)
    assert "未发现数值度量" in selection.reason


def test_detail_role_keeps_numbers_in_a_table() -> None:
    block = _block(_records([("a", 3), ("b", 2), ("c", 1)]), title="门店明细", hint="detail")
    assert (select_component(block).component, select_component(block).rule) == ("table", 4)
    plain = _block(_records([("a", 1), ("b", 3), ("c", 2)]))
    assert select_component(plain, role="detail").component == "table"


def test_rule_5_time_axis_with_three_points_is_a_line() -> None:
    block = _block(_records([("2026-07", 1), ("2026-08", 2), ("2026-09", 3)], x="month"))
    selection = select_component(block)
    assert (selection.component, selection.rule, selection.confidence) == ("line", 5, 0.9)
    assert selection.reason == "时间轴 3 个点，按折线展示"
    assert dict(selection.encoding) == {"x": "month", "y": "value"}


def test_rule_6_two_time_points_fall_back_to_columns() -> None:
    block = _block(_records([("2026-08", 1), ("2026-09", 2)], x="month"))
    selection = select_component(block)
    assert (selection.component, selection.rule, selection.confidence) == ("column", 6, 0.85)
    assert "折线会误示连续趋势" in selection.reason


def test_rule_7_composition_uses_donut_or_pie() -> None:
    small = _block(_records([("直营", 310), ("经销", 280), ("线上", 190)]), hint="composition")
    selection = select_component(small)
    assert (selection.component, selection.rule) == ("donut", 7)
    five = _block(_records([(f"c{i}", 10 + i) for i in range(5)]), hint="composition")
    assert select_component(five).component == "pie"


def test_rule_7_negative_shares_fall_back_to_horizontal_bars() -> None:
    block = _block(_records([("直营", 310), ("退货", -40), ("线上", 190)]), hint="composition")
    selection = select_component(block)
    assert (selection.component, selection.orientation, selection.rule) == ("bar", "horizontal", 7)
    assert selection.reason == "数据含负值，饼图无法表达，已改为条形图"


@pytest.mark.parametrize(
    ("rows", "hint", "expected_reason"),
    [
        ([("上海", 1), ("江苏", 3), ("浙江", 2)], "ranking", "排名数据"),
        ([("上海", 3), ("江苏", 2), ("浙江", 1)], None, "降序"),
        ([("上海市浦东新区张江高科", 1), ("江苏", 3), ("浙江", 2)], None, "名称较长"),
        ([(f"c{i}", (i * 7) % 5) for i in range(12)], None, "12 个类目"),
    ],
)
def test_rule_8_rankings_are_horizontal_bars(rows, hint, expected_reason) -> None:
    selection = select_component(_block(_records(rows), hint=hint))
    assert (selection.component, selection.orientation, selection.rule) == ("bar", "horizontal", 8)
    assert expected_reason in selection.reason


def test_rule_9_multi_series_is_stacked() -> None:
    rows = [
        {"month": "M1", "region": "华东", "sales": 1},
        {"month": "M1", "region": "华北", "sales": 2},
        {"month": "M2", "region": "华东", "sales": 3},
        {"month": "M2", "region": "华北", "sales": 1},
    ]
    block = _block(rows)
    assert choose_encoding(block.frame.columns, block.frame.rows)["series"] == "region"
    selection = select_component(block)
    assert (selection.component, selection.rule, selection.confidence) == ("stacked_column", 9, 0.8)


def test_rule_10_few_short_categories_are_columns() -> None:
    selection = select_component(_block(_records([("A", 1), ("B", 3), ("C", 2)])))
    assert (selection.component, selection.rule, selection.confidence) == ("column", 10, 0.8)


def test_rule_11_more_than_thirty_categories_is_a_table() -> None:
    selection = select_component(_block(_records([(f"c{i}", (i * 7) % 11) for i in range(35)])))
    assert (selection.component, selection.rule) == ("table", 11)
    assert "35 个类目" in selection.reason


def test_rule_12_between_twenty_and_thirty_categories_is_a_bar() -> None:
    selection = select_component(_block(_records([(f"c{i}", (i * 7) % 11) for i in range(25)])))
    assert (selection.component, selection.rule, selection.confidence) == ("bar", 12, 0.75)
    assert selection.orientation == "horizontal"


def test_confidence_adjustments_and_clamp() -> None:
    synthetic = _block([[1, 5], [2, 3], [3, 4]])
    assert select_component(synthetic).confidence == pytest.approx(0.65)

    sparse = _block(_records([("A", None), ("B", 3), ("C", None)]))
    assert select_component(sparse).confidence == pytest.approx(0.7)

    conflict = _block(_records([("A", 1), ("B", 3), ("C", 2)]))
    assert select_component(conflict, role="trend").confidence == pytest.approx(0.7)

    hinted = _block(_records([("2026-07", 1), ("2026-08", 2), ("2026-09", 3)], x="m"), hint="trend")
    assert select_component(hinted).confidence == pytest.approx(0.95)

    worst = _block([[None, 1], [None, None], [None, 2]])
    assert select_component(worst).confidence >= 0.3


def test_requested_type_is_validated_through_chart_policy() -> None:
    block = _block(_records([(f"c{i}", 10 + i) for i in range(9)]))
    selection = select_component(block, requested="pie")
    assert selection.component == "bar"
    assert "饼图" in selection.reason

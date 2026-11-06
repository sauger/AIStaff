import itertools

import pytest

from app.dashboard.layout import LayoutItem, plan_layout, score_templates
from app.dashboard.spec import GRID_COLUMNS, GRID_ROWS, LayoutPlan
from app.dashboard.templates import TEMPLATES

ROLES = ("trend", "composition", "ranking", "compare", "detail")


def _items(count: int, *, roles=ROLES, planned: bool = False) -> list[LayoutItem]:
    items = []
    for index in range(count):
        role = roles[index % len(roles)]
        component = {"trend": "line", "composition": "donut", "ranking": "bar", "detail": "table"}.get(
            role, "column"
        )
        items.append(
            LayoutItem(
                id=f"c{index}", title=f"图{index}", role=role, component=component,
                planned=planned, priority=10 - index,
            )
        )
    return items


def _assert_invariants(result, kpi_ids, items) -> None:
    layout = result.layout
    # the spec model re-checks bounds and overlaps; it must accept every plan we build
    LayoutPlan.model_validate(layout.model_dump())
    ids = [slot.id for slot in layout.slots]
    assert len(ids) == len(set(ids))
    assert sorted(ids) == sorted(kpi_ids + result.kept)
    assert set(result.kept) | set(result.dropped) == {item.id for item in items}
    for slot in layout.slots:
        assert slot.x + slot.w <= GRID_COLUMNS
        if layout.overflow == "fit":
            assert slot.y + slot.h <= GRID_ROWS
    for first, second in itertools.combinations(layout.slots, 2):
        assert not first.overlaps(second), (first, second)
    if layout.overflow == "fit":
        assert max((slot.y + slot.h for slot in layout.slots), default=0) * 90 <= 1080


@pytest.mark.parametrize("count", range(1, 9))
@pytest.mark.parametrize("kpis", [0, 2, 6])
def test_auto_layout_invariants_for_one_to_eight_charts(count, kpis) -> None:
    kpi_ids = [f"k{index}" for index in range(kpis)]
    items = _items(count)
    result = plan_layout(kpi_ids, items)
    _assert_invariants(result, kpi_ids, items)
    assert result.layout.overflow == "fit"
    assert result.dropped == []


@pytest.mark.parametrize("template", sorted(TEMPLATES))
@pytest.mark.parametrize("count", range(1, 9))
def test_forced_templates_keep_invariants(template, count) -> None:
    kpi_ids = ["k0", "k1", "k2"]
    items = _items(count)
    result = plan_layout(kpi_ids, items, template=template)
    _assert_invariants(result, kpi_ids, items)
    assert result.layout.template == template
    assert len(result.kept) <= TEMPLATES[template].capacity


def test_template_scores_follow_the_plan_table() -> None:
    assert score_templates(_items(2, roles=("trend", "ranking")))["pulse"] == 5
    assert score_templates(_items(1, roles=("detail",)))["detail"] == 5
    assert score_templates(_items(3, roles=("trend", "compare")))["overview"] == 4
    assert score_templates(_items(6, roles=("compare",)))["compare"] == 3
    assert score_templates(_items(2, roles=("compare",)))["stack"] == 1
    # a template that cannot hold every chart is never preferred
    assert score_templates(_items(6, roles=("trend", "ranking")))["pulse"] < 0


def test_auto_template_selection() -> None:
    assert plan_layout([], _items(1, roles=("trend",))).layout.template == "overview"
    assert plan_layout([], _items(3, roles=("ranking", "trend", "compare"))).layout.template == "pulse"
    assert plan_layout([], _items(2, roles=("compare", "detail"))).layout.template == "detail"
    assert plan_layout([], _items(6, roles=("compare",))).layout.template == "compare"
    assert plan_layout([], _items(2, roles=("compare",))).layout.template == "stack"
    # detail would have to drop charts here, so the next template that fits wins
    crowded = _items(5, roles=("compare",)) + [LayoutItem(id="t", role="detail", component="table")]
    assert plan_layout(["k0", "k1"], crowded).layout.template == "compare"


def test_pulse_puts_ranking_left_and_trend_right() -> None:
    items = [
        LayoutItem(id="share", role="composition", component="donut"),
        LayoutItem(id="trend", role="trend", component="line"),
        LayoutItem(id="rank", role="ranking", component="bar"),
        LayoutItem(id="extra", role="compare", component="column"),
    ]
    result = plan_layout(["k0"], items, template="pulse")
    slots = {slot.id: slot for slot in result.layout.slots}
    assert (slots["rank"].region, slots["rank"].x, slots["rank"].w, slots["rank"].h) == ("left", 0, 4, 5)
    assert (slots["trend"].region, slots["trend"].x, slots["trend"].w) == ("right", 4, 8)
    assert {slots["share"].y, slots["extra"].y} == {7}
    assert (slots["share"].w, slots["extra"].w) == (6, 6)


def test_pulse_honours_explicit_regions_and_folds_missing_ones() -> None:
    items = [
        LayoutItem(id="a", role="trend", component="line", region="left"),
        LayoutItem(id="b", role="ranking", component="bar", region="right"),
    ]
    slots = {slot.id: slot for slot in plan_layout([], items, template="pulse").layout.slots}
    assert (slots["a"].region, slots["b"].region) == ("left", "right")

    single = plan_layout([], [LayoutItem(id="only", role="trend", component="line")], template="pulse")
    assert single.layout.slots[0].w == GRID_COLUMNS
    assert [item.code for item in single.warnings] == ["TEMPLATE_UNDERFILLED"]


def test_detail_puts_the_table_on_the_last_row() -> None:
    items = [
        LayoutItem(id="table", role="detail", component="table"),
        LayoutItem(id="a", role="compare", component="column"),
        LayoutItem(id="b", role="trend", component="line"),
    ]
    result = plan_layout(["k0"], items, template="detail")
    slots = {slot.id: slot for slot in result.layout.slots}
    assert (slots["table"].x, slots["table"].w, slots["table"].h) == (0, 12, 6)
    assert slots["table"].y == max(slot.y for slot in result.layout.slots)
    assert (slots["a"].w, slots["a"].h, slots["b"].w) == (6, 4, 6)


def test_overview_hero_and_plan_hero() -> None:
    items = _items(3, roles=("compare", "trend", "ranking"))
    slots = {slot.id: slot for slot in plan_layout([], items, template="overview").layout.slots}
    assert (slots["c1"].region, slots["c1"].w, slots["c1"].h) == ("hero", 12, 4)
    chosen = [LayoutItem(id="x", role="compare", component="column"), LayoutItem(id="y", hero=True)]
    slots = {slot.id: slot for slot in plan_layout([], chosen, template="compare").layout.slots}
    assert (slots["y"].region, slots["y"].w, slots["y"].y) == ("hero", 12, 1)


def test_kpi_row_widths() -> None:
    four = plan_layout(["a", "b", "c", "d"], []).layout.slots
    assert [(slot.x, slot.w, slot.y) for slot in four] == [(0, 3, 1), (3, 3, 1), (6, 3, 1), (9, 3, 1)]
    six = plan_layout([f"k{index}" for index in range(6)], []).layout.slots
    assert [slot.w for slot in six] == [2] * 6


def test_wall_degrades_by_shrinking_first() -> None:
    result = plan_layout(["k0"], _items(6, roles=("compare",)), template="compare")
    heights = {slot.h for slot in result.layout.slots if slot.kind == "chart"}
    assert heights == {3}
    assert result.dropped == []


def test_wall_degrades_by_dropping_the_hero_before_blocks() -> None:
    items = [
        LayoutItem(id="hero", role="trend", component="line", hero=True, planned=True),
        LayoutItem(id="a", role="compare", component="column", planned=True),
        LayoutItem(id="t", role="detail", component="table", planned=True),
    ]
    result = plan_layout(["k0"], items, template="detail")
    assert result.dropped == []
    assert all(slot.region != "hero" for slot in result.layout.slots)
    assert result.layout.overflow == "fit"


def test_wall_drops_lowest_priority_unplanned_blocks() -> None:
    items = _items(4, roles=("compare",)) + [LayoutItem(id="t", role="detail", component="table")]
    result = plan_layout(["k0"], items, template="detail")
    assert result.layout.overflow == "fit"
    assert result.dropped == ["c3", "c2"]
    assert [item.code for item in result.warnings] == ["TRUNCATED", "TRUNCATED"]


def test_planned_overflow_falls_back_to_scroll() -> None:
    items = _items(4, roles=("compare",), planned=True) + [
        LayoutItem(id="t", role="detail", component="table", planned=True)
    ]
    result = plan_layout(["k0"], items, template="detail")
    _assert_invariants(result, ["k0"], items)
    assert result.layout.overflow == "scroll"
    assert "WALL_SCROLL" in {item.code for item in result.warnings}


def test_unknown_template_and_region_are_reported() -> None:
    result = plan_layout([], _items(2), template="mosaic")
    assert result.warnings[0].code == "TEMPLATE_UNKNOWN"
    misplaced = [LayoutItem(id="a", role="trend", component="line", region="left")]
    result = plan_layout([], misplaced, template="compare")
    assert [item.code for item in result.warnings] == ["PLAN_REGION_UNKNOWN"]
    assert result.layout.slots[0].region == "grid"


def test_region_requests_steer_auto_template() -> None:
    items = [
        LayoutItem(id="a", role="compare", component="column", region="left"),
        LayoutItem(id="b", role="compare", component="column"),
    ]
    assert plan_layout([], items).layout.template == "pulse"

import json
from pathlib import Path

from app.dashboard.blocks import (
    ROLE_LEXICON,
    _pretty_label,
    merge_plan,
    parse_plan,
    resolve_block,
    role_hint,
    split_blocks,
)
from app.dashboard.normalize import normalize_source

FIXTURES = Path(__file__).parent / "fixtures" / "dashboard"


def _blocks(data):
    frames, _ = normalize_source(data)
    return split_blocks(frames)


def _fixture_blocks(name: str):
    return _blocks(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def test_demo_blocks_have_paths_titles_ids_and_hints() -> None:
    blocks = _fixture_blocks("demo_blocks")
    assert [(block.path, block.title) for block in blocks] == [
        ("blocks[0]", "销售额"),
        ("blocks[1]", "订单数"),
        ("blocks[2]", "月度趋势"),
        ("blocks[3]", "渠道分布"),
        ("blocks[4]", "区域排名"),
    ]
    assert [block.id for block in blocks] == [f"blocks-{index}" for index in range(5)]
    assert [block.role_hint for block in blocks] == [
        "kpi",
        None,
        "trend",
        "composition",
        "ranking",
    ]


def test_fixture_nested_labels_come_from_the_last_meaningful_key() -> None:
    blocks = _fixture_blocks("nested")
    assert [(block.id, block.title) for block in blocks] == [
        ("data-result-summary", "Summary"),
        ("data-result-monthly", "Monthly"),
        ("data-result-by_channel", "By channel"),
        ("data-result-region_top", "Region top"),
        ("data-result-daily", "Daily"),
        ("data-result-weekly", "Weekly"),
        ("data-result-items-0-data", "门店明细"),
    ]


def test_fixture_dirty_and_oversized_block_counts() -> None:
    dirty = _fixture_blocks("dirty")
    assert [(block.path, block.title) for block in dirty] == [
        ("data.orders", "Orders"),
        ("data.matrix", "Matrix"),
        ("data.flags", "Flags"),
    ]
    oversized = _fixture_blocks("oversized")
    assert len(oversized) == 24
    assert oversized[0].title == "Summary"
    assert oversized[1].title == "Detail"


def test_pretty_label_skips_generic_words() -> None:
    assert _pretty_label(("result", "monthly_sales")) == "Monthly sales"
    assert _pretty_label(("report", "data", "items", 0)) == "Report"
    assert _pretty_label(("区域-排名",)) == "区域 排名"
    assert _pretty_label(("data", "list")) == ""


def test_titles_fall_back_and_duplicates_are_numbered() -> None:
    blocks = _blocks({"data": [[1, 2], [3, 4]], "list": {"columns": ["a"], "rows": [[5]]}})
    assert [block.title for block in blocks] == ["数据块 1", "数据块 2"]
    twins = _blocks(
        {"x": {"name": "趋势", "rows": [[1]], "columns": ["a"]}, "y": {"name": "趋势", "columns": ["b"], "rows": [[2]]}}
    )
    assert [block.title for block in twins] == ["趋势", "趋势（2）"]


def test_ids_are_slot_safe_unique_and_short() -> None:
    blocks = _blocks(
        {
            "月度": [{"a": 1}],
            "季度": [{"a": 2}],
            "a_very_long_key_name_that_keeps_going_and_going": [{"a": 3}],
        }
    )
    ids = [block.id for block in blocks]
    assert ids[0] == "block-1" and ids[1] == "block-2"
    assert all(len(item) <= 32 for item in ids)
    assert len(set(ids)) == len(ids)


def test_limit_keeps_the_highest_scores_in_document_order() -> None:
    frames, _ = normalize_source(
        {
            "sparse": [{"label": "a"}],
            "trend": [{"m": "2026-01", "v": 1}, {"m": "2026-02", "v": 2}],
            "wide": [{"a": 1, "b": 2, "c": 3}],
        }
    )
    kept = split_blocks(frames, limit=2)
    assert [block.path for block in kept] == ["trend", "wide"]


def test_role_hints_use_the_lexicon() -> None:
    assert role_hint("区域排名") == "ranking"
    assert role_hint("渠道占比") == "composition"
    assert role_hint("Monthly sales") == "trend"
    assert role_hint("订单明细") == "detail"
    assert role_hint("销售额") == "kpi"
    assert role_hint("stop sign") is None
    assert role_hint("region top10") == "ranking"
    assert set(ROLE_LEXICON) == {"ranking", "composition", "trend", "detail", "kpi"}


def test_plan_path_match_applies_title_role_and_region() -> None:
    blocks = _fixture_blocks("demo_blocks")
    plan = parse_plan(
        {"blocks": [{"path": "blocks[4]", "title": "TOP 区域", "role": "ranking", "region": "left"}]}
    )
    merged, warnings = merge_plan(blocks, plan)
    assert warnings == []
    ranking = next(block for block in merged if block.path == "blocks[4]")
    assert (ranking.title, ranking.plan_role, ranking.region, ranking.planned) == (
        "TOP 区域",
        "ranking",
        "left",
        True,
    )


def test_plan_paths_tolerate_wrappers_and_json_pointer() -> None:
    blocks = _fixture_blocks("nested")
    by_suffix, how = resolve_block(blocks, path="result.monthly")
    assert by_suffix is not None and by_suffix.path == "data.result.monthly" and how == "path"
    by_pointer, _ = resolve_block(blocks, path="/data/result/daily")
    assert by_pointer is not None and by_pointer.path == "data.result.daily"
    by_prefix, _ = resolve_block(blocks, path="data.result.items[0]")
    assert by_prefix is not None and by_prefix.title == "门店明细"


def test_plan_label_match_must_be_unique() -> None:
    blocks = _fixture_blocks("demo_blocks")
    merged, warnings = merge_plan(blocks, parse_plan({"blocks": [{"label": "渠道", "role": "composition"}]}))
    assert [item.code for item in warnings] == ["PLAN_LABEL_MATCHED"]
    assert next(block for block in merged if block.title == "渠道分布").planned is True

    twins = _blocks(
        {"a": {"name": "华东销售", "columns": ["x"], "rows": [[1]]}, "b": {"name": "华北销售", "columns": ["y"], "rows": [[2]]}}
    )
    _merged, warnings = merge_plan(twins, parse_plan({"blocks": [{"label": "销售"}]}))
    assert [item.code for item in warnings] == ["PLAN_BLOCK_NOT_FOUND"]


def test_unknown_plan_entries_warn_and_fall_back_to_automatic() -> None:
    blocks = _fixture_blocks("demo_blocks")
    merged, warnings = merge_plan(
        blocks,
        parse_plan({"blocks": [{"path": "nope.monthly"}, {"path": "blocks[9]", "role": "bogus"}]}),
    )
    assert [item.code for item in warnings] == ["PLAN_BLOCK_NOT_FOUND", "PLAN_BLOCK_NOT_FOUND"]
    assert "blocks[0]（销售额）" in warnings[0].message
    assert [block.path for block in merged] == [block.path for block in blocks]
    assert not any(block.planned for block in merged)

    garbage, garbage_warnings = merge_plan(blocks, parse_plan(["not", "an", "object"]))
    assert [item.code for item in garbage_warnings] == ["PLAN_INVALID"]
    assert len(garbage) == len(blocks)


def test_order_hero_and_first_entry_wins() -> None:
    blocks = _fixture_blocks("demo_blocks")
    plan = parse_plan(
        {
            "blocks": [
                {"path": "blocks[2]", "title": "第一次"},
                {"path": "blocks[2]", "title": "第二次"},
            ],
            "layout": {"order": ["blocks[4]", "月度趋势"], "hero": "blocks[2]", "template": "PULSE"},
        }
    )
    assert plan.template == "pulse"
    merged, warnings = merge_plan(blocks, plan)
    assert warnings == []
    assert [block.path for block in merged][:2] == ["blocks[4]", "blocks[2]"]
    trend = next(block for block in merged if block.path == "blocks[2]")
    assert trend.title == "第一次"
    assert trend.hero is True

"""Component selection for ``compose_dashboard`` (pipeline step 3).

The decision table (plan §4.5) short-circuits top to bottom.  Only the KPI
rows (2/3), the explicit detail role and the horizontal ranking row (8) are
new; every chart-type rule below them is delegated to
``chart_policy.decide`` so phase 1 and phase 2 can never disagree on what a
readable chart is.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from app.dashboard.blocks import Block, _pretty_label
from app.dashboard.chart_policy import (
    LONG_LABEL_WIDTH,
    MAX_ANY_CATEGORIES,
    MAX_BAR_CATEGORIES,
    ChartProfile,
    decide,
    profile,
)
from app.dashboard.spec import MAX_INLINE_ROWS, MAX_KPIS, Chart, Column, Kpi, TableData

MIN_CONFIDENCE = 0.30
MAX_CONFIDENCE = 0.98
LOW_CONFIDENCE = 0.60
_SERIES_MAX = 8


@dataclass(frozen=True)
class Selection:
    component: str  # "kpi" | "drop" | a chart type
    intent: str
    confidence: float
    reason: str
    role: str | None = None
    orientation: str = "auto"
    encoding: tuple[tuple[str, str], ...] = ()
    rule: int = 0


def _clamp(value: float) -> float:
    return round(min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, value)), 2)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric_indexes(columns: list[Column]) -> list[int]:
    return [index for index, column in enumerate(columns) if column.type == "number"]


def choose_encoding(columns: list[Column], rows: list[list[Any]]) -> dict[str, str]:
    """x = time column, else first label column; y = first measure; series for long data."""

    numeric = set(numeric_indexes(columns))
    labels = [index for index in range(len(columns)) if index not in numeric]
    temporal = [index for index in labels if columns[index].type in {"date", "datetime"}]
    if temporal:
        x_index = temporal[0]
    elif labels:
        x_index = labels[0]
    else:
        x_index = 0
    encoding = {"x": columns[x_index].name}
    measure = next((index for index in sorted(numeric) if index != x_index), None)
    if measure is not None:
        encoding["y"] = columns[measure].name
    x_values = [row[x_index] for row in rows if x_index < len(row)]
    if len(set(map(str, x_values))) < len(x_values):
        for index in labels:
            if index == x_index:
                continue
            distinct = {str(row[index]) for row in rows if index < len(row) and row[index] is not None}
            if 2 <= len(distinct) <= _SERIES_MAX:
                encoding["series"] = columns[index].name
                break
    return encoding


def _probe(block: Block, encoding: dict[str, str]) -> ChartProfile:
    frame = block.frame
    data = TableData.model_construct(columns=frame.columns, rows=frame.rows[:MAX_INLINE_ROWS])
    chart = Chart.model_construct(id=block.id, encoding=encoding, data=data, type=None, intent=None)
    return profile(chart)


def _sorted_desc(block: Block, encoding: dict[str, str]) -> bool:
    if "series" in encoding or "y" not in encoding:
        return False
    names = [column.name for column in block.frame.columns]
    index = names.index(encoding["y"])
    values = [row[index] for row in block.frame.rows if _is_number(row[index])]
    return len(values) >= 3 and all(a >= b for a, b in pairwise(values))


def _null_ratio(block: Block) -> float:
    cells = sum(len(row) for row in block.frame.rows) or 1
    return sum(1 for row in block.frame.rows for cell in row if cell is None) / cells


def select_component(
    block: Block, *, role: str | None = None, requested: str | None = None
) -> Selection:
    """Pick KPI / chart type / table for one block, with confidence and reason.

    ``role`` is the plan role (``plan.blocks[].role``); the lexicon hint on the
    block only steers rows 7/8 and never overrides a shape rule.
    ``requested`` is reserved for a future ``plan.blocks[].component``: it is
    routed through ``decide`` so an unreadable request is downgraded with a note.
    """

    frame = block.frame
    rows = frame.rows
    if not rows:
        return Selection("drop", "detail", 0.0, "没有数据行", rule=1)

    effective = role or block.role_hint
    adjust = 0.0
    if frame.synthetic_headers:
        adjust -= 0.15
    if _null_ratio(block) > 0.2:
        adjust -= 0.10
    if block.role_hint:
        adjust += 0.05

    numeric = numeric_indexes(frame.columns)
    labels = [index for index in range(len(frame.columns)) if index not in numeric]

    if frame.shape in {"scalar", "scalar_group"} or (
        len(rows) == 1 and len(numeric) == 1 and len(labels) <= 1
    ):
        return Selection("kpi", "kpi", _clamp(0.95 + adjust), "单值数据，按指标卡展示", "kpi", rule=2)
    if len(rows) == 1 and len(numeric) >= 2:
        reason = f"单行 {len(numeric)} 个数值，拆为指标卡"
        return Selection("kpi", "kpi", _clamp(0.90 + adjust), reason, "kpi", rule=3)
    if role == "kpi":
        adjust -= 0.10  # a multi-row block cannot be a single card

    if not numeric:
        return Selection(
            "table", "detail", _clamp(0.90 + adjust), "未发现数值度量，按表格展示", "detail", rule=4
        )
    if effective == "detail":
        reason = "按明细表展示" if role == "detail" else "块名提示为明细，按表格展示"
        return Selection("table", "detail", _clamp(0.90 + adjust), reason, "detail", rule=4)

    encoding = choose_encoding(frame.columns, rows)
    chart_profile = _probe(block, encoding)
    pairs = tuple(sorted(encoding.items()))

    if requested:
        decision = decide("compare", chart_profile, requested)
        reason = decision.reason or f"按要求使用 {decision.chart_type}"
        orientation = "horizontal" if decision.chart_type == "bar" else "auto"
        return Selection(
            decision.chart_type, "compare", _clamp(0.80 + adjust), reason, effective,
            orientation, pairs, rule=0,
        )

    temporal = chart_profile.x_kind == "temporal"
    if role == "trend" and not temporal:
        adjust -= 0.10
    if temporal and chart_profile.n_points >= 3:
        decision = decide("trend", chart_profile)
        reason = decision.reason or f"时间轴 {chart_profile.n_points} 个点，按折线展示"
        return Selection(
            decision.chart_type, "trend", _clamp(0.90 + adjust), reason, "trend", "auto", pairs, rule=5
        )
    if temporal:
        decision = decide("trend", chart_profile)
        reason = decision.reason or f"仅 {chart_profile.n_points} 个时间点，按柱状展示"
        return Selection(
            decision.chart_type, "trend", _clamp(0.85 + adjust), reason, "trend", "auto", pairs, rule=6
        )

    if effective == "composition":
        decision = decide("composition", chart_profile)
        if decision.chart_type in {"pie", "donut"}:
            shape = "环形图" if decision.chart_type == "donut" else "饼图"
            reason = f"{chart_profile.n_points} 个类目的占比，按{shape}展示"
            return Selection(
                decision.chart_type, "composition", _clamp(0.85 + adjust), reason,
                "composition", "auto", pairs, rule=7,
            )
        # phase-1 wording explains why the pie was refused (negative values, too many slices…)
        return Selection(
            "bar", "composition", _clamp(0.75 + adjust), decision.reason or "饼图不可读，已改为条形图",
            "composition", "horizontal", pairs, rule=7,
        )

    sorted_desc = _sorted_desc(block, encoding)
    within_chart_range = chart_profile.n_points <= MAX_ANY_CATEGORIES
    if (
        effective == "ranking"
        or (within_chart_range and (sorted_desc or chart_profile.label_width > LONG_LABEL_WIDTH))
        or 9 <= chart_profile.n_points <= MAX_BAR_CATEGORIES
    ):
        if effective == "ranking":
            reason = "排名数据，按横向条形图降序展示"
        elif sorted_desc:
            reason = "数值已降序排列，按横向条形图展示"
        elif chart_profile.label_width > LONG_LABEL_WIDTH:
            reason = "类目名称较长，按横向条形图展示"
        else:
            reason = f"{chart_profile.n_points} 个类目，按横向条形图展示"
        return Selection(
            "bar", "compare", _clamp(0.85 + adjust), reason, "ranking", "horizontal", pairs, rule=8
        )

    decision = decide("compare", chart_profile)
    if decision.chart_type == "stacked_column":
        reason = f"{chart_profile.n_series} 个序列 × {chart_profile.n_points} 个类目，按堆叠柱状展示"
        return Selection(
            "stacked_column", "compare", _clamp(0.80 + adjust), reason, effective, "auto", pairs, rule=9
        )
    if decision.chart_type == "column":
        reason = f"{chart_profile.n_points} 个类目，按柱状图对比"
        return Selection("column", "compare", _clamp(0.80 + adjust), reason, effective, "auto", pairs, rule=10)
    if decision.chart_type == "table":
        return Selection(
            "table", "detail", _clamp(0.90 + adjust), decision.reason or "类目过多，按表格展示",
            "detail", "auto", pairs, rule=11,
        )
    reason = decision.reason or f"{chart_profile.n_points} 个类目，按条形图展示"
    orientation = "horizontal" if decision.chart_type == "bar" else "auto"
    return Selection(
        decision.chart_type, "compare", _clamp(0.75 + adjust), reason, effective, orientation, pairs, rule=12
    )


def _kpi_label(text: str) -> str:
    return (_pretty_label((text,)) or text or "指标")[:40]


def kpis_from_block(block: Block, *, limit: int = MAX_KPIS) -> list[Kpi]:
    """Cards for a KPI selection: one per measure, labelled by block or column."""

    frame = block.frame
    row = frame.rows[0] if frame.rows else []
    if frame.shape == "scalar":
        return [Kpi(label=block.title[:40], value=row[0], unit=frame.unit, delta=frame.delta)][:limit]
    if frame.shape == "scalar_group":
        return [
            Kpi(label=_kpi_label(column.name), value=row[index])
            for index, column in enumerate(frame.columns)
            if index < len(row) and row[index] is not None
        ][:limit]
    numeric = numeric_indexes(frame.columns)
    labels = [index for index in range(len(frame.columns)) if index not in numeric]
    hint = None
    if labels and isinstance(row[labels[0]], str):
        hint = row[labels[0]][:40] or None
    if len(numeric) == 1:
        return [Kpi(label=block.title[:40], value=row[numeric[0]], hint=hint)][:limit]
    return [
        Kpi(label=_kpi_label(frame.columns[index].name), value=row[index], hint=hint)
        for index in numeric
    ][:limit]

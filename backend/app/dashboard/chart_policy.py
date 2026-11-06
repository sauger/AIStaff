"""Deterministic chart selection for the dashboard render capability.

Models are good at reading intent and bad at counting categories, so the
contract asks for an ``intent`` and this module turns (intent, data profile)
into a concrete chart type.  Every downgrade is recorded as a human-readable
note so the board can explain itself.

Rules are intentionally tiny and table-driven; DB-GPT's ``chart_selection.py``
does the same thing for a single case ("a line with <= 3 points becomes a bar"),
which is a good signal that post-hoc repair is necessary but not sufficient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.dashboard.spec import (
    Chart,
    ChartIntent,
    DashboardSpecError,
    FinalChart,
    Kpi,
    RenderInput,
    RenderSpec,
    TableData,
)

MAX_COLUMN_CATEGORIES = 8
MAX_BAR_CATEGORIES = 20
MAX_ANY_CATEGORIES = 30
MAX_PIE_CATEGORIES = 6
PIE_MAX_TOP_SHARE = 0.8
SMALL_SERIES_POINTS = 2
LONG_LABEL_WIDTH = 12
MIN_LINE_POINTS = 3

_TEMPORAL_TEXT = re.compile(
    r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}[-/]\d{1,2}|\d{4}年\d{1,2}月?|\d{4}Q[1-4]|"
    r"\d{4}-\d{2}-\d{2}T[\d:]+)$"
)


class Adjustment(BaseModel):
    """One chart-type decision that differs from what the producer asked for."""

    chart_id: str
    from_type: str | None = None
    to_type: str
    reason: str


@dataclass(frozen=True)
class ChartProfile:
    x_kind: str  # temporal | ordinal | categorical | none
    n_points: int
    n_series: int
    n_measures: int
    has_negative: bool
    all_equal: bool
    null_ratio: float
    label_width: int
    top_share: float
    value_sum: float
    has_numeric: bool = False


@dataclass(frozen=True)
class Decision:
    chart_type: str
    reason: str | None = None


def display_width(value: str) -> int:
    """Count CJK characters as two columns so label thresholds match reality."""

    width = 0
    for char in value:
        width += 2 if "\u2e80" <= char <= "\u9fff" or "\uff00" <= char <= "\uffef" else 1
    return width


def _is_temporal_text(value: Any) -> bool:
    return isinstance(value, str) and bool(_TEMPORAL_TEXT.match(value.strip()))


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def resolve_encoding(chart: Chart) -> tuple[str | None, str | None, str | None]:
    """Fill a missing x/y from the data so producers can stay minimal."""

    if chart.data is None:
        return chart.encoding.get("x"), chart.encoding.get("y"), chart.encoding.get("series")
    x = chart.encoding.get("x")
    y = chart.encoding.get("y")
    series = chart.encoding.get("series")
    names = chart.data.column_names
    if x is None and names:
        if len(names) > 1:
            numeric_names = _numeric_columns(chart.data)
            x = next((name for name in names if name not in numeric_names), names[0])
        else:
            x = names[0]
    if y is None:
        numeric_names = _numeric_columns(chart.data)
        y = next((name for name in numeric_names if name != x), None)
    return x, y, series


def _numeric_columns(data: TableData) -> list[str]:
    numeric: list[str] = []
    for index, column in enumerate(data.columns):
        if column.type in {"number"}:
            numeric.append(column.name)
            continue
        sample = [row[index] for row in data.rows[:20] if index < len(row)]
        if sample and all(_numeric(value) is not None for value in sample):
            numeric.append(column.name)
    return numeric


def profile(chart: Chart) -> ChartProfile:
    data = chart.data
    if data is None or not data.rows:
        return ChartProfile(
            x_kind="none",
            n_points=0,
            n_series=0,
            n_measures=0,
            has_negative=False,
            all_equal=False,
            null_ratio=0.0,
            label_width=0,
            top_share=0.0,
            value_sum=0.0,
            has_numeric=False,
        )
    x_name, y_name, series_name = resolve_encoding(chart)
    names = data.column_names
    x_index = names.index(x_name) if x_name in names else 0
    y_index = names.index(y_name) if y_name in names else None
    series_index = names.index(series_name) if series_name in names else None

    x_values = [row[x_index] for row in data.rows if x_index < len(row)]
    distinct_x = {str(value) for value in x_values if value is not None}
    n_points = len(distinct_x) or len(data.rows)
    distinct_series = (
        {str(row[series_index]) for row in data.rows if series_index is not None and series_index < len(row)}
        if series_index is not None
        else set()
    )
    n_series = len(distinct_series) or 1

    values = [row[y_index] for row in data.rows if y_index is not None and y_index < len(row)]
    numeric_values = [value for value in (_numeric(raw) for raw in values) if value is not None]
    nulls = sum(1 for value in values if value is None)
    total = len(values) or 1
    value_sum = sum(numeric_values) if numeric_values else 0.0
    top_share = (max(numeric_values) / value_sum) if numeric_values and value_sum > 0 else 0.0
    has_negative = any(value < 0 for value in numeric_values)
    all_equal = len(set(numeric_values)) <= 1 and len(numeric_values) > 1

    x_kind = "categorical"
    column = data.columns[x_index] if x_index < len(data.columns) else None
    if column is not None and column.type in {"date", "datetime"} or x_values and all(_is_temporal_text(value) or value is None for value in x_values):
        x_kind = "temporal"
    label_width = max((display_width(str(value)) for value in x_values if value is not None), default=0)
    numeric_columns = _numeric_columns(data)
    n_measures = len([name for name in numeric_columns if name != x_name]) or (1 if y_name else 0)

    return ChartProfile(
        x_kind=x_kind,
        n_points=n_points,
        n_series=n_series,
        n_measures=n_measures,
        has_negative=has_negative,
        all_equal=all_equal,
        null_ratio=nulls / total,
        label_width=label_width,
        top_share=top_share,
        value_sum=value_sum,
        has_numeric=bool(numeric_values),
    )


def infer_intent(chart: Chart, chart_profile: ChartProfile) -> ChartIntent:
    if chart.intent is not None:
        return chart.intent
    if chart_profile.n_points <= 1:
        return "kpi"
    if chart_profile.x_kind == "temporal":
        return "trend"
    if chart_profile.n_measures > 2:
        return "detail"
    return "compare"


def _pie_allowed(chart_profile: ChartProfile) -> bool:
    return (
        chart_profile.n_points <= MAX_PIE_CATEGORIES
        and not chart_profile.has_negative
        and chart_profile.value_sum > 0
        and chart_profile.top_share < PIE_MAX_TOP_SHARE
    )


def _type_allowed(chart_type: str, chart_profile: ChartProfile) -> bool:
    if chart_type == "table":
        return True
    if chart_profile.n_points == 0:
        return chart_type == "table"
    if chart_type in {"pie", "donut"}:
        return _pie_allowed(chart_profile)
    if chart_type in {"line", "area"}:
        return (
            chart_profile.x_kind in {"temporal", "ordinal"}
            and chart_profile.n_points >= MIN_LINE_POINTS
        )
    if chart_type == "gauge":
        return chart_profile.n_points == 1 and chart_profile.has_numeric
    if chart_type == "funnel":
        return (
            chart_profile.n_points <= 8
            and not chart_profile.has_negative
            and chart_profile.value_sum > 0
        )
    if chart_type == "scatter":
        return chart_profile.has_numeric and chart_profile.n_points >= 2
    return chart_profile.n_points <= MAX_ANY_CATEGORIES


def _pie_reason(chart_profile: ChartProfile) -> str:
    if chart_profile.has_negative:
        return "数据含负值，饼图无法表达，已改为条形图"
    if chart_profile.n_points > MAX_PIE_CATEGORIES:
        return f"{chart_profile.n_points} 个类目超过饼图可读上限 {MAX_PIE_CATEGORIES}，已改为条形图"
    if chart_profile.value_sum <= 0:
        return "合计不为正，饼图无意义，已改为条形图"
    return f"最大一类占比 {chart_profile.top_share:.0%}，饼图退化，已改为条形图"


def decide(intent: ChartIntent, chart_profile: ChartProfile, requested: str | None = None) -> Decision:
    """Return the chart type for one chart, with an optional downgrade reason."""

    if chart_profile.n_points == 0:
        return Decision("table", "没有可绘制的数据，按表格展示")
    if not chart_profile.has_numeric:
        return Decision("table", "没有可聚合的数值度量，图表无法表达，按表格展示")

    if requested and _type_allowed(requested, chart_profile):
        return Decision(requested)

    if requested == "pie" and not _pie_allowed(chart_profile):
        return Decision("bar", _pie_reason(chart_profile))
    if requested in {"line", "area"} and not _type_allowed(requested, chart_profile):
        if chart_profile.x_kind == "temporal":
            return Decision("column", f"仅 {chart_profile.n_points} 个数据点，折线会误示连续趋势，已改为柱状")
        return Decision("bar", "横轴不是时间维度，折线无意义，已改为条形图")
    if requested == "gauge" and not _type_allowed("gauge", chart_profile):
        return Decision("table", "仪表盘只适合单个数值，已改为表格")

    if intent == "kpi":
        return Decision("table", "单值指标建议放入 KPI 卡")
    if intent == "detail":
        return Decision("table")
    if intent == "trend":
        if chart_profile.x_kind not in {"temporal", "ordinal"}:
            return decide("compare", chart_profile)
        if chart_profile.n_points <= SMALL_SERIES_POINTS:
            return Decision("column", f"仅 {chart_profile.n_points} 个数据点，折线会误示连续趋势，已改为柱状")
        return Decision("line")
    if intent == "composition":
        if _pie_allowed(chart_profile):
            return Decision("donut" if chart_profile.n_points <= 4 else "pie")
        return Decision("bar", _pie_reason(chart_profile))

    # compare and everything else
    if chart_profile.n_series > 1 and chart_profile.n_points <= MAX_COLUMN_CATEGORIES:
        return Decision("stacked_column")
    if chart_profile.n_points > MAX_ANY_CATEGORIES:
        return Decision("table", f"{chart_profile.n_points} 个类目过多，图表不可读，已改为表格")
    if chart_profile.n_points <= MAX_COLUMN_CATEGORIES and chart_profile.label_width <= LONG_LABEL_WIDTH:
        return Decision("column")
    if chart_profile.n_points <= MAX_BAR_CATEGORIES:
        return Decision("bar")
    return Decision("bar", f"{chart_profile.n_points} 个类目较多，已改为条形图并保留全部数据")


def _note(previous: str | None, message: str, limit: int) -> str:
    if not message:
        return previous or ""
    if previous and message in previous:
        return previous
    if not previous:
        return message[:limit]
    return f"{previous[: max(0, limit - len(message) - 1)]}\n{message}".strip()


def _kpi_from_chart(chart: Chart, chart_profile: ChartProfile) -> Kpi | None:
    if chart.data is None or len(chart.data.rows) != 1:
        return None
    _x_name, y_name, _ = resolve_encoding(chart)
    names = chart.data.column_names
    if y_name is None or y_name not in names:
        return None
    row = chart.data.rows[0]
    index = names.index(y_name)
    if index >= len(row):
        return None
    value = row[index]
    if _numeric(value) is None and not isinstance(value, str):
        return None
    return Kpi(
        label=(chart.title or y_name)[:40],
        value=value,
        hint="由单值图表自动转为指标卡",
    )


def normalize_spec(spec: RenderInput) -> tuple[RenderSpec, list[Adjustment]]:
    """Resolve chart types, fill missing encodings and explain every downgrade."""

    adjustments: list[Adjustment] = []
    charts: list[FinalChart] = []
    kpis: list[Kpi] = list(spec.kpis)

    for chart in spec.charts:
        x_name, y_name, _ = resolve_encoding(chart)
        encoding = dict(chart.encoding)
        if x_name and "x" not in encoding:
            encoding["x"] = x_name
        if y_name and "y" not in encoding:
            encoding["y"] = y_name
        working = chart.model_copy(update={"encoding": encoding})
        chart_profile = profile(working)
        intent = infer_intent(working, chart_profile)

        if intent == "kpi":
            kpi = _kpi_from_chart(working, chart_profile)
            if kpi is not None and len(kpis) < 6:
                kpis.append(kpi)
                adjustments.append(
                    Adjustment(
                        chart_id=chart.id,
                        from_type=chart.type,
                        to_type="kpi",
                        reason="单值数据已转为指标卡",
                    )
                )
                continue

        decision = decide(intent, chart_profile, working.type)
        if working.type is not None and decision.chart_type != working.type:
            adjustments.append(
                Adjustment(
                    chart_id=chart.id,
                    from_type=working.type,
                    to_type=decision.chart_type,
                    reason=decision.reason or "",
                )
            )
        elif working.type is None and decision.reason:
            adjustments.append(
                Adjustment(
                    chart_id=chart.id,
                    from_type=None,
                    to_type=decision.chart_type,
                    reason=decision.reason or "",
                )
            )

        note = _note(working.note, decision.reason or "", 200) if decision.reason else working.note
        charts.append(
            FinalChart(
                id=working.id,
                title=working.title,
                intent=working.intent,
                type=decision.chart_type,  # type: ignore[arg-type]
                expected_points=working.expected_points,
                encoding=working.encoding,
                data=working.data,
                note=note,
            )
        )

    if not charts and not kpis:
        raise DashboardSpecError("所有图表都无法渲染，请至少提供一个可绘制数据集", "charts")

    return (
        RenderSpec(
            spec_version=1,
            title=spec.title,
            subtitle=spec.subtitle,
            as_of=spec.as_of,
            theme=spec.theme,
            kpis=kpis,
            charts=charts,
            filters=spec.filters,
            meta=spec.meta,
        ),
        adjustments,
    )


def profile_dict(spec: RenderSpec) -> dict[str, Any]:
    """Compact debug summary used by tests and logs."""

    return {
        "charts": [
            {"id": chart.id, "type": chart.type, "points": profile(chart).n_points}
            for chart in spec.charts
        ]
    }

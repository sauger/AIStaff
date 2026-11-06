"""Platform-owned render contract for data dashboards.

``RenderInput`` is what a producer (model, skill, HTTP/MCP tool) hands over.
``RenderSpec`` is the frozen, chart-type-resolved form that gets stored and
rendered.  Neither carries SQL, HTML or URLs: rendering is a pure view, so the
contract must never become an execution channel.

The contract is intentionally small (12 top-level fields).  DB-GPT's
``DashboardSchemaV1`` stays a backend-internal generation format; see the plan
for the optional compiler added in a later phase.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scalar = str | int | float | bool | None

MAX_CHARTS = 8
MAX_KPIS = 6
MAX_FILTERS = 5
MAX_TITLE = 80
MAX_SUBTITLE = 160
MAX_AS_OF = 40
MAX_NOTE = 200
MAX_STRING = 200
MAX_INLINE_ROWS = 50
MAX_INLINE_CELLS = 1200
MAX_CHART_ID = 32

ChartType = Literal[
    "line",
    "area",
    "column",
    "bar",
    "stacked_column",
    "pie",
    "donut",
    "scatter",
    "gauge",
    "funnel",
    "table",
]
ColumnType = Literal["string", "number", "date", "datetime", "boolean"]
ChartIntent = Literal["trend", "compare", "composition", "detail", "kpi"]
FilterType = Literal["select", "date_range", "text"]

CHART_TYPES: tuple[str, ...] = (
    "line",
    "area",
    "column",
    "bar",
    "stacked_column",
    "pie",
    "donut",
    "scatter",
    "gauge",
    "funnel",
    "table",
)
Theme = Literal["light", "dark", "tech"]
CHART_INTENTS: tuple[str, ...] = ("trend", "compare", "composition", "detail", "kpi")
ENCODING_KEYS = frozenset({"x", "y", "series"})


class DashboardSpecError(ValueError):
    """Raised when a render input violates the contract."""

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def _check_text(value: str | None, *, path: str, limit: int) -> None:
    if value is None:
        return
    if len(value) > limit:
        raise DashboardSpecError(f"文本超过 {limit} 字符", path)


def _check_scalar(value: Scalar, *, path: str) -> None:
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise DashboardSpecError(f"单元格文本超过 {MAX_STRING} 字符", path)
        lowered = value.lower()
        if "http://" in lowered or "https://" in lowered:
            raise DashboardSpecError("数据单元格不允许包含 URL", path)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise DashboardSpecError("数据单元格不允许 NaN/Infinity", path)


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_STRING)
    type: ColumnType = "string"


class TableData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[Column] = Field(min_length=1, max_length=32)
    rows: list[list[Scalar]] = Field(default_factory=list, max_length=MAX_INLINE_ROWS)

    @model_validator(mode="after")
    def _validate_rows(self) -> TableData:
        width = len(self.columns)
        for row_index, row in enumerate(self.rows):
            if len(row) != width:
                raise DashboardSpecError(
                    f"第 {row_index + 1} 行有 {len(row)} 个值，与 {width} 列不一致",
                    f"data.rows[{row_index}]",
                )
            for cell_index, cell in enumerate(row):
                _check_scalar(cell, path=f"data.rows[{row_index}][{cell_index}]")
        return self

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def column_index(self, name: str) -> int:
        try:
            return self.column_names.index(name)
        except ValueError as exc:  # pragma: no cover - guarded by validators
            raise DashboardSpecError(f"未知列 {name}") from exc


class Chart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MAX_CHART_ID, pattern=r"^[A-Za-z0-9_-]+$")
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    intent: ChartIntent | None = None
    type: ChartType | None = None
    expected_points: int | None = Field(default=None, ge=0, le=100_000)
    encoding: dict[str, str] = Field(default_factory=dict)
    data: TableData | None = None
    note: str | None = Field(default=None, max_length=MAX_NOTE)
    # 1 = half row, 2 = full row on the dashboard grid
    span: Literal[1, 2] = 1

    @model_validator(mode="after")
    def _validate_encoding(self) -> Chart:
        unknown = set(self.encoding) - ENCODING_KEYS
        if unknown:
            raise DashboardSpecError(
                f"encoding 只支持 {sorted(ENCODING_KEYS)}，收到 {sorted(unknown)}",
                "charts[].encoding",
            )
        if self.data is not None:
            names = set(self.data.column_names)
            for key, value in self.encoding.items():
                if value not in names:
                    raise DashboardSpecError(
                        f"encoding.{key} 指向不存在的列 {value}",
                        f"charts[].encoding.{key}",
                    )
        return self


class FinalChart(Chart):
    """Chart after the policy resolved its type."""

    type: ChartType


class Kpi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=40)
    value: Scalar = None
    unit: str | None = Field(default=None, max_length=16)
    delta: float | None = Field(default=None, ge=-1, le=1)
    hint: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _validate_value(self) -> Kpi:
        _check_scalar(self.value, path="kpis[].value")
        return self


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MAX_CHART_ID, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=40)
    type: FilterType = "select"
    options: list[str] = Field(default_factory=list, max_length=50)
    default: str | None = Field(default=None, max_length=MAX_STRING)


class RenderMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = Field(default=None, max_length=MAX_STRING)
    generated_by: str = Field(default="model", max_length=40)

    @model_validator(mode="after")
    def _validate_source(self) -> RenderMeta:
        _check_text(self.source, path="meta.source", limit=MAX_STRING)
        return self


class RenderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    subtitle: str | None = Field(default=None, max_length=MAX_SUBTITLE)
    as_of: str | None = Field(default=None, max_length=MAX_AS_OF)
    theme: Theme = "light"
    kpis: list[Kpi] = Field(default_factory=list, max_length=MAX_KPIS)
    charts: list[Chart] = Field(default_factory=list, max_length=MAX_CHARTS)
    filters: list[FilterSpec] = Field(default_factory=list, max_length=MAX_FILTERS)
    meta: RenderMeta = Field(default_factory=RenderMeta)

    @model_validator(mode="after")
    def _validate_board(self) -> RenderInput:
        if not self.kpis and not self.charts:
            raise DashboardSpecError("大屏至少需要 1 个指标卡或 1 个图表", "")
        chart_ids = [chart.id for chart in self.charts]
        duplicates = {item for item in chart_ids if chart_ids.count(item) > 1}
        if duplicates:
            raise DashboardSpecError(f"图表 id 重复：{sorted(duplicates)}", "charts[].id")
        filter_ids = [item.id for item in self.filters]
        if len(filter_ids) != len(set(filter_ids)):
            raise DashboardSpecError("筛选器 id 重复", "filters[].id")
        cells = 0
        for chart in self.charts:
            if chart.data is None:
                continue
            cells += len(chart.data.rows) * len(chart.data.columns)
        if cells > MAX_INLINE_CELLS:
            raise DashboardSpecError(
                f"内联数据超过 {MAX_INLINE_CELLS} 个单元格，请减少图表或行数",
                "charts[].data",
            )
        return self

    def total_rows(self) -> int:
        return sum(len(chart.data.rows) for chart in self.charts if chart.data is not None)


class RenderSpec(RenderInput):
    """Frozen form: every chart has a resolved type."""

    spec_version: Literal[1] = 1
    charts: list[FinalChart] = Field(default_factory=list, max_length=MAX_CHARTS)


def render_input_json_schema() -> dict[str, Any]:
    """Hand-written flat schema for the model-facing capability.

    ``capability_discovery._model_input_schema`` projects the schema verbatim
    and keeps ``$ref``/``$defs``; several model providers do not resolve them,
    so the descriptor must stay flat (no ``$ref`` anywhere).
    """

    column = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "列名，必须与 rows 的取值位置对应"},
            "type": {
                "type": "string",
                "enum": ["string", "number", "date", "datetime", "boolean"],
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    table = {
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": column, "minItems": 1, "maxItems": 32},
            "rows": {
                "type": "array",
                "description": "二维数组，与 columns 顺序一一对应；单图最多 50 行",
                "items": {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}},
                "maxItems": MAX_INLINE_ROWS,
            },
        },
        "required": ["columns"],
        "additionalProperties": False,
    }
    chart = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$", "maxLength": MAX_CHART_ID},
            "title": {"type": "string", "maxLength": MAX_TITLE},
            "intent": {
                "type": "string",
                "enum": list(CHART_INTENTS),
                "description": "分析意图；系统据此结合数据形状选择图型",
            },
            "type": {
                "type": "string",
                "enum": list(CHART_TYPES),
                "description": "可选。仅在用户明确要求图型时填写；不满足可读性会被告警降级",
            },
            "expected_points": {
                "type": "integer",
                "minimum": 0,
                "description": "你预计的 X 轴类目数（去重后），不确定可省略",
            },
            "encoding": {
                "type": "object",
                "properties": {
                    "x": {"type": "string"},
                    "y": {"type": "string"},
                    "series": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "data": table,
            "note": {"type": "string", "maxLength": MAX_NOTE},
            "span": {
                "type": "integer",
                "enum": [1, 2],
                "description": "1=半行，2=整行；核心图表建议 2",
            },
        },
        "required": ["id"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": MAX_TITLE},
            "subtitle": {"type": "string", "maxLength": MAX_SUBTITLE},
            "as_of": {
                "type": "string",
                "maxLength": MAX_AS_OF,
                "description": "数据截止时间，如 2026-09-30；请如实标注",
            },
            "theme": {
                "type": "string",
                "enum": ["light", "dark", "tech"],
                "description": "tech = 深色科技风大屏（推荐用于对外汇报/投屏）",
            },
            "kpis": {
                "type": "array",
                "maxItems": MAX_KPIS,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "maxLength": 40},
                        "value": {"type": ["string", "number", "null"]},
                        "unit": {"type": "string", "maxLength": 16},
                        "delta": {"type": "number", "minimum": -1, "maximum": 1},
                        "hint": {"type": "string", "maxLength": 40},
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
            },
            "charts": {"type": "array", "maxItems": MAX_CHARTS, "items": chart},
            "filters": {
                "type": "array",
                "maxItems": MAX_FILTERS,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"},
                        "label": {"type": "string", "maxLength": 40},
                        "type": {"type": "string", "enum": ["select", "date_range", "text"]},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "default": {"type": "string"},
                    },
                    "required": ["id", "label"],
                    "additionalProperties": False,
                },
            },
            "meta": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "maxLength": MAX_STRING,
                        "description": "数据来源标识，如 erp.sales；不填会在界面上标注未标注来源",
                    },
                    "generated_by": {"type": "string", "maxLength": 40},
                },
                "additionalProperties": False,
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    }

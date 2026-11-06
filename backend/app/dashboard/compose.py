"""``compose_dashboard`` orchestration: one tool result → one RenderSpec v2.

normalize → split → merge_plan → sanitise → select → trim → layout → spec.
Everything here is a pure function of the arguments; the harness handler
persists the view and emits events.  The model contributes at most a small
plan (block paths, titles, roles, regions, template); chart types, sizes and
coordinates are always decided here.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.dashboard.blocks import Block, Plan, merge_plan, parse_plan, split_blocks, unique_id
from app.dashboard.layout import LayoutItem, LayoutResult, plan_layout
from app.dashboard.normalize import DashboardDataError, Frame, normalize
from app.dashboard.select import Selection, kpis_from_block, select_component
from app.dashboard.spec import (
    GENERATOR_VERSION,
    MAX_AS_OF,
    MAX_CHARTS,
    MAX_INLINE_CELLS,
    MAX_INLINE_ROWS,
    MAX_KPIS,
    MAX_NOTE,
    MAX_SOURCE_PATH,
    MAX_STRING,
    MAX_SUBTITLE,
    MAX_TITLE,
    MAX_WARNINGS,
    SANITIZED_PLACEHOLDER,
    Column,
    DashboardSpecError,
    DashboardWarning,
    FinalChart,
    Kpi,
    RenderMeta,
    RenderSpec,
    TableData,
    Theme,
)

GENERATED_BY = "compose_dashboard"
MAX_OUTLINE_BLOCKS = 12
MAX_OUTLINE_ENTRY = 100
EXPECTED_SHAPES = (
    '演示契约 {"blocks":[{"name":"销售额","value":1},'
    '{"name":"月度趋势","columns":[...],"rows":[...]}]}，或记录数组、{columns,rows}、{key:[...]} 等 JSON'
)

_URL = re.compile(r"https?://\S*", re.IGNORECASE)
_ROLE_PRIORITY = {"trend": 4.0, "composition": 3.0, "ranking": 2.0, "detail": 1.0}
_DEFAULT_PRIORITY = 1.5
_PLANNED_BONUS = 100.0
_HANDWRITTEN_KEYS = frozenset({"charts", "kpis", "layout", "filters", "spec"})
_TEXT_LIMITS = (("title", MAX_TITLE), ("subtitle", MAX_SUBTITLE), ("as_of", MAX_AS_OF), ("source", MAX_STRING))


class ComposeError(Exception):
    """Coded failure handed back to the model as ``{success: false, error}``."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class ComposeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    data: Any = None
    subtitle: str | None = Field(default=None, max_length=MAX_SUBTITLE)
    as_of: str | None = Field(default=None, max_length=MAX_AS_OF)
    theme: Theme = "light"
    source: str | None = Field(default=None, max_length=MAX_STRING)
    # parsed leniently by blocks.parse_plan: a bad plan degrades, never fails
    plan: Any = None
    mode: Literal["render", "outline"] = "render"


@dataclass
class ComposedBlock:
    block: Block
    selection: Selection
    priority: float


@dataclass
class ComposeResult:
    mode: str
    title: str
    blocks_found: int
    template: str | None = None
    spec: RenderSpec | None = None
    used: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[DashboardWarning] = field(default_factory=list)
    outline: dict[str, Any] | None = None


@dataclass
class _Prepared:
    arguments: ComposeArguments
    plan: Plan
    composed: list[ComposedBlock]
    warnings: list[DashboardWarning]
    blocks_found: int
    as_of: str | None


@dataclass
class _Draft:
    item: ComposedBlock
    columns: list[Column]
    rows: list[list[Any]]
    total: int
    keep_tail: bool


# --- small helpers ---------------------------------------------------------------


def _warning(code: str, message: str, path: str | None = None) -> DashboardWarning:
    return DashboardWarning(code=code, message=message[:MAX_NOTE], path=(path or None))


def clean_text(value: Any, limit: int) -> str:
    """Single-line text with URLs masked; the spec never carries links."""

    text = _URL.sub(SANITIZED_PLACEHOLDER, str(value if value is not None else ""))
    return " ".join(text.split())[:limit]


def _names(items: list[str], limit: int = 4) -> str:
    shown = "、".join(items[:limit])
    return f"{shown} 等" if len(items) > limit else shown


def _dedupe_warnings(warnings: list[DashboardWarning]) -> list[DashboardWarning]:
    seen: set[tuple[str, str]] = set()
    unique: list[DashboardWarning] = []
    for warning in warnings:
        key = (warning.code, warning.message)
        if key not in seen:
            seen.add(key)
            unique.append(warning)
    if len(unique) > MAX_WARNINGS:
        rest = len(unique) - (MAX_WARNINGS - 1)
        unique = [*unique[: MAX_WARNINGS - 1], _warning("TRUNCATED", f"另有 {rest} 条提示未列出")]
    return unique


def _parse_arguments(arguments: dict[str, Any]) -> ComposeArguments:
    from app.dashboard.harness import _validation_errors

    handwritten = sorted(_HANDWRITTEN_KEYS & set(arguments))
    if handwritten:
        raise ComposeError(
            "INVALID_ARGUMENTS",
            f"compose_dashboard 不接收 {'/'.join(handwritten)}：图表结构由系统生成，"
            "只需传 data 与可选 plan。确需手写图表时改用 render_dashboard。",
            details={"errors": [{"path": key, "message": "不支持的字段"} for key in handwritten]},
        )
    if arguments.get("data") is None:
        raise ComposeError(
            "INVALID_ARGUMENTS",
            "缺少 data：请把数据工具的结果（或其 sandbox_json_file 引用）原样放进 data。",
            details={"errors": [{"path": "data", "message": "必填"}]},
        )
    prepared = dict(arguments)
    for key, limit in _TEXT_LIMITS:
        if isinstance(prepared.get(key), str):
            prepared[key] = clean_text(prepared[key], limit) or None
    try:
        return ComposeArguments.model_validate(prepared)
    except ValidationError as exc:
        raise ComposeError(
            "INVALID_ARGUMENTS",
            "compose_dashboard 参数不合法，请按 errors 中的 path 修正后重试一次。",
            details={"errors": _validation_errors(exc)},
        ) from exc


def _detect_as_of(data: Any) -> str | None:
    """The demo contract carries ``as_of`` at its root, possibly inside an envelope."""

    current = data
    for _ in range(3):
        if not isinstance(current, dict):
            return None
        for key in ("as_of", "asOf", "data_as_of"):
            value = current.get(key)
            if isinstance(value, str) and value.strip():
                return clean_text(value, MAX_AS_OF)
        current = current.get("data") if isinstance(current.get("data"), dict) else current.get("result")
    return None


def _sanitize_frame(frame: Frame) -> tuple[Frame, int]:
    """Rewrite URL / NaN / over-long cells before any pydantic model sees them.

    ``render_dashboard`` keeps rejecting such cells; compose rewrites them so one
    dirty upstream value cannot sink a whole board.
    """

    touched = 0
    rows: list[list[Any]] = []
    for row in frame.rows:
        cleaned: list[Any] = []
        for cell in row:
            if isinstance(cell, float) and (math.isnan(cell) or math.isinf(cell)):
                cleaned.append(None)
                touched += 1
            elif isinstance(cell, str) and _URL.search(cell):
                cleaned.append(SANITIZED_PLACEHOLDER)
                touched += 1
            elif isinstance(cell, str) and len(cell) > MAX_STRING:
                cleaned.append(f"{cell[: MAX_STRING - 1]}…")
                touched += 1
            else:
                cleaned.append(cell)
        rows.append(cleaned)
    columns = [
        column.model_copy(update={"name": clean_text(column.name, MAX_STRING) or f"c{index + 1}"})
        if _URL.search(column.name)
        else column
        for index, column in enumerate(frame.columns)
    ]
    return replace(frame, rows=rows, columns=columns), touched


def _sanitize_blocks(blocks: list[Block]) -> list[DashboardWarning]:
    warnings: list[DashboardWarning] = []
    for block in blocks:
        block.frame, touched = _sanitize_frame(block.frame)
        block.title = clean_text(block.title, MAX_TITLE) or block.title
        if touched:
            warnings.append(
                _warning(
                    "SANITIZED_CELL",
                    f"「{block.title}」中 {touched} 个单元格含链接、NaN 或超长文本，已替换",
                    block.path,
                )
            )
    return warnings


def _priority(block: Block, selection: Selection) -> float:
    # plan-named blocks first, then trend > composition > ranking > other > detail
    base = _ROLE_PRIORITY.get(selection.role or "", _DEFAULT_PRIORITY)
    return base + (_PLANNED_BONUS if block.planned else 0.0)


# --- pipeline ----------------------------------------------------------------------


def _prepare(arguments: dict[str, Any]) -> _Prepared:
    parsed = _parse_arguments(arguments)
    try:
        normalized = normalize(parsed.data)
    except DashboardDataError as exc:
        raise ComposeError(exc.code, exc.message, retryable=exc.retryable, details=exc.details) from exc

    plan = parse_plan(parsed.plan)
    blocks, plan_warnings = merge_plan(split_blocks(normalized.frames), plan)
    if not blocks:
        details: dict[str, Any] = {"expected": EXPECTED_SHAPES}
        if normalized.empty_paths:
            details["empty_blocks"] = normalized.empty_paths[:6]
        raise ComposeError(
            "EMPTY_DATA",
            "查询结果没有可展示的数据块。请在回复中用文字说明结果为空，不要重试。",
            details=details,
        )
    warnings = [*normalized.warnings, *plan_warnings, *_sanitize_blocks(blocks)]

    composed: list[ComposedBlock] = []
    for block in blocks:
        selection = select_component(block, role=block.plan_role)
        if selection.component != "drop":
            composed.append(ComposedBlock(block, selection, _priority(block, selection)))

    # all-text data: still show it, but say the board found nothing to measure
    if composed and all(
        item.selection.rule == 4
        and item.selection.role == "detail"
        and not any(column.type == "number" for column in item.block.frame.columns)
        for item in composed
    ):
        for item in composed:
            item.selection = replace(item.selection, confidence=0.5)

    return _Prepared(
        arguments=parsed,
        plan=plan,
        composed=composed,
        warnings=warnings,
        blocks_found=len(blocks),
        as_of=parsed.as_of or _detect_as_of(parsed.data),
    )


def _select_kpis(prepared: _Prepared, used_ids: set[str]) -> tuple[list[Kpi], list[dict[str, Any]]]:
    entries: list[tuple[ComposedBlock, Kpi]] = []
    kpi_items = [item for item in prepared.composed if item.selection.component == "kpi"]
    kpi_items.sort(key=lambda item: not item.block.planned)
    for item in kpi_items:
        # no per-block cap here: the board-wide cap below reports what it drops
        entries.extend((item, card) for card in kpis_from_block(item.block, limit=len(item.block.frame.columns)))
    if len(entries) > MAX_KPIS:
        omitted = [card.label for _item, card in entries[MAX_KPIS:]]
        prepared.warnings.append(
            _warning("TRUNCATED", f"指标卡最多 {MAX_KPIS} 个，已省略 {len(omitted)} 个：{_names(omitted)}")
        )
        entries = entries[:MAX_KPIS]
    kpis: list[Kpi] = []
    used: list[dict[str, Any]] = []
    for item, card in entries:
        kpi_id = unique_id(f"kpi-{item.block.id}", used_ids, f"kpi-{len(kpis) + 1}")
        kpi = card.model_copy(
            update={
                "id": kpi_id,
                "label": clean_text(card.label, 40) or "指标",
                "hint": (clean_text(card.hint, 40) or None) if card.hint else None,
            }
        )
        kpis.append(kpi)
        used.append(
            {"id": kpi_id, "path": item.block.path, "title": kpi.label, "component": "kpi", "role": "kpi"}
        )
    return kpis, used


def _select_charts(prepared: _Prepared) -> list[ComposedBlock]:
    charts = [item for item in prepared.composed if item.selection.component != "kpi"]
    if len(charts) <= MAX_CHARTS:
        return charts
    ranked = sorted(enumerate(charts), key=lambda pair: (-pair[1].priority, pair[0]))
    keep = {id(item) for _index, item in ranked[:MAX_CHARTS]}
    omitted = [item.block.title for item in charts if id(item) not in keep]
    prepared.warnings.append(
        _warning("TRUNCATED", f"图表最多 {MAX_CHARTS} 个，已省略 {len(omitted)} 个块：{_names(omitted)}")
    )
    return [item for item in charts if id(item) in keep]


def _trim_rows(drafts: list[_Draft], warnings: list[DashboardWarning]) -> dict[str, str]:
    """50 rows per chart, then the board-wide 1200-cell cap from the longest block down."""

    for draft in drafts:
        if len(draft.rows) > MAX_INLINE_ROWS:
            draft.rows = draft.rows[-MAX_INLINE_ROWS:] if draft.keep_tail else draft.rows[:MAX_INLINE_ROWS]

    def cells() -> int:
        return sum(len(draft.rows) * len(draft.columns) for draft in drafts)

    while cells() > MAX_INLINE_CELLS:
        _index, longest = max(enumerate(drafts), key=lambda pair: (len(pair[1].rows), pair[0]))
        if len(longest.rows) <= 1:
            break
        longest.rows = longest.rows[1:] if longest.keep_tail else longest.rows[:-1]

    notes: dict[str, str] = {}
    for draft in drafts:
        shown = len(draft.rows)
        if shown >= draft.total:
            continue
        if draft.keep_tail:
            note = f"仅展示最近 {shown} 个点（共 {draft.total} 个）"
        else:
            note = f"仅展示前 {shown} 行（共 {draft.total} 行）"
        notes[draft.item.block.id] = note
        warnings.append(_warning("TRUNCATED", f"「{draft.item.block.title}」{note}", draft.item.block.path))
    return notes


def _build_charts(
    kept: list[ComposedBlock], layout: LayoutResult, warnings: list[DashboardWarning]
) -> tuple[list[FinalChart], list[dict[str, Any]]]:
    slots = {slot.id: slot for slot in layout.layout.slots}
    ordered = sorted(
        (item for item in kept if item.block.id in slots),
        key=lambda item: (slots[item.block.id].y, slots[item.block.id].x),
    )
    drafts = [
        _Draft(
            item=item,
            columns=list(item.block.frame.columns),
            rows=list(item.block.frame.rows),
            total=max(item.block.frame.total_rows, len(item.block.frame.rows)),
            keep_tail=item.selection.rule in {5, 6},
        )
        for item in ordered
    ]
    trim_notes = _trim_rows(drafts, warnings)

    charts: list[FinalChart] = []
    used: list[dict[str, Any]] = []
    for draft in drafts:
        block, selection = draft.item.block, draft.item.selection
        is_table = selection.component == "table"
        note = "；".join(part for part in (selection.reason, trim_notes.get(block.id)) if part)
        chart = FinalChart(
            id=block.id,
            title=block.title[:MAX_TITLE],
            intent=selection.intent,  # type: ignore[arg-type]
            type=selection.component,  # type: ignore[arg-type]
            encoding={} if is_table else dict(selection.encoding),
            data=TableData(columns=draft.columns, rows=draft.rows),
            note=note[:MAX_NOTE] or None,
            role=selection.role if selection.role in {*_ROLE_PRIORITY} else None,  # type: ignore[arg-type]
            confidence=selection.confidence,
            source_path=block.path[:MAX_SOURCE_PATH],
            orientation=selection.orientation if selection.component == "bar" else "auto",  # type: ignore[arg-type]
        )
        charts.append(chart)
        used.append(
            {
                "id": chart.id,
                "path": block.path,
                "title": chart.title,
                "component": chart.type,
                "role": chart.role,
                "region": slots[block.id].region,
            }
        )
    return charts, used


def _layout_items(charts: list[ComposedBlock]) -> list[LayoutItem]:
    return [
        LayoutItem(
            id=item.block.id,
            title=item.block.title,
            role=item.selection.role,
            component=item.selection.component,
            planned=item.block.planned,
            region=item.block.region,
            hero=item.block.hero,
            priority=item.priority,
        )
        for item in charts
    ]


def _render(prepared: _Prepared) -> ComposeResult:
    args = prepared.arguments
    charts_in = _select_charts(prepared)
    used_ids = {item.block.id for item in charts_in}
    kpis, used_kpis = _select_kpis(prepared, used_ids)
    layout = plan_layout([kpi.id or "" for kpi in kpis], _layout_items(charts_in), template=prepared.plan.template)
    prepared.warnings.extend(layout.warnings)
    charts, used_charts = _build_charts(charts_in, layout, prepared.warnings)
    if not kpis and not charts:
        raise ComposeError(
            "EMPTY_DATA",
            "查询结果没有可展示的数值或表格。请在回复中用文字说明，不要重试。",
            details={"expected": EXPECTED_SHAPES},
        )
    warnings = _dedupe_warnings(prepared.warnings)
    try:
        spec = RenderSpec(
            spec_version=2,
            title=args.title,
            subtitle=args.subtitle,
            as_of=prepared.as_of,
            theme=args.theme,
            kpis=kpis,
            charts=charts,
            filters=[],
            meta=RenderMeta(
                source=args.source, generated_by=GENERATED_BY, generator_version=GENERATOR_VERSION
            ),
            layout=layout.layout,
            warnings=warnings,
        )
    except (ValidationError, DashboardSpecError) as exc:  # pragma: no cover - engine invariant
        message = exc.message if isinstance(exc, DashboardSpecError) else str(exc)
        raise ComposeError("INVALID_SPEC", f"大屏生成失败：{message[:160]}") from exc
    return ComposeResult(
        mode="render",
        title=spec.title,
        blocks_found=prepared.blocks_found,
        template=layout.layout.template,
        spec=spec,
        used=used_kpis + used_charts,
        warnings=warnings,
    )


def compose_dashboard(arguments: dict[str, Any]) -> ComposeResult:
    """Validate, split, select and lay out; raise ``ComposeError`` on hard failures."""

    if not isinstance(arguments, dict):
        raise ComposeError("INVALID_ARGUMENTS", "参数必须是 JSON 对象。")
    prepared = _prepare(arguments)
    if prepared.arguments.mode == "outline":
        from app.dashboard.outline import build_outline

        return build_outline(prepared)
    return _render(prepared)

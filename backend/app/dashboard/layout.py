"""Deterministic grid layout for ``compose_dashboard`` (plan §5).

Input: KPI ids plus chart items (id, role, component, plan hints).  Output: a
``LayoutPlan`` whose slots satisfy the invariants checked by the spec model —
every id exactly once, ``x+w ≤ 12``, ``y+h ≤ 12`` (unless ``overflow=scroll``)
and no two slots overlapping.  The model never supplies coordinates.

Row 0 is the board header, row 1 the KPI row; charts start below.  When a wall
would run past 12 rows the engine degrades in a fixed order: shrink every
chart by one row, drop the hero, drop the lowest-priority unplanned block, and
only then fall back to a scrolling wall.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from app.dashboard.spec import GRID_COLUMNS, GRID_ROWS, DashboardWarning, LayoutPlan, Slot
from app.dashboard.templates import (
    HERO_SIZE,
    KPI_HEIGHT,
    KPI_ROW,
    MIN_CHART_HEIGHT,
    TEMPLATE_ORDER,
    TEMPLATES,
    grid_size,
)

_DEGRADE_STEPS = ((False, False), (True, False), (True, True))  # (shrink, no_hero)


@dataclass(frozen=True)
class LayoutItem:
    id: str
    title: str = ""
    role: str | None = None
    component: str = "column"
    planned: bool = False
    region: str | None = None
    hero: bool = False
    priority: float = 0.0


@dataclass
class LayoutResult:
    layout: LayoutPlan
    kept: list[str]
    dropped: list[str] = field(default_factory=list)
    warnings: list[DashboardWarning] = field(default_factory=list)


@dataclass
class _Cell:
    item: LayoutItem
    region: str
    w: int
    h: int


def _warning(code: str, message: str) -> DashboardWarning:
    return DashboardWarning(code=code, message=message[:200])


def score_templates(items: list[LayoutItem]) -> dict[str, int]:
    """Plan §5.3 scores, plus a capacity penalty so auto mode never drops blocks it could fit."""

    roles = {item.role for item in items}
    regions = {item.region for item in items}
    count = len(items)
    has_trend = "trend" in roles
    scores = {
        "overview": 4 if has_trend and 1 <= count <= 5 else 0,
        "compare": 3 if count >= 4 else 0,
        "pulse": 5 if has_trend and "ranking" in roles else 0,
        "detail": 5 if any(item.component == "table" for item in items) else 0,
        "stack": 1,
    }
    # explicit regions only exist in one template each
    if regions & {"left", "right"}:
        scores["pulse"] += 6
    if "detail" in regions:
        scores["detail"] += 6
    for name in scores:
        if TEMPLATES[name].capacity < count:
            scores[name] -= 10
    return scores


def _ranked_templates(items: list[LayoutItem]) -> list[str]:
    scores = score_templates(items)
    return sorted(TEMPLATE_ORDER, key=lambda name: (-scores[name], TEMPLATE_ORDER.index(name)))


def _kpi_slots(kpi_ids: list[str]) -> list[Slot]:
    width = 3 if len(kpi_ids) <= 4 else 2
    return [
        Slot(id=kpi_id, kind="kpi", region="kpi_row", x=index * width, y=KPI_ROW, w=width, h=KPI_HEIGHT)
        for index, kpi_id in enumerate(kpi_ids)
    ]


def _pack(cells: list[_Cell], top: int) -> list[Slot]:
    """Left-to-right row packing; uniform rows with a gap are stretched to the full width."""

    rows: list[list[_Cell]] = []
    used = 0
    for cell in cells:
        if rows and used + cell.w <= GRID_COLUMNS:
            rows[-1].append(cell)
            used += cell.w
        else:
            rows.append([cell])
            used = cell.w
    slots: list[Slot] = []
    y = top
    for row in rows:
        spare = GRID_COLUMNS - sum(cell.w for cell in row)
        if spare >= 2 and len({cell.w for cell in row}) == 1 and GRID_COLUMNS % len(row) == 0:
            for cell in row:
                cell.w = GRID_COLUMNS // len(row)
        height = max(cell.h for cell in row)
        x = 0
        for cell in row:
            slots.append(
                Slot(id=cell.item.id, kind="chart", region=cell.region, x=x, y=y, w=cell.w, h=height)
            )
            x += cell.w
        y += height
    return slots


def _shrunk(height: int, shrink: bool) -> int:
    return max(MIN_CHART_HEIGHT, height - 1) if shrink else height


def _explicit_hero(items: list[LayoutItem]) -> LayoutItem | None:
    return next((item for item in items if item.hero or item.region == "hero"), None)


def _place_rows(
    template: str, items: list[LayoutItem], top: int, *, shrink: bool, no_hero: bool
) -> list[Slot]:
    hero = None if no_hero else _explicit_hero(items)
    if hero is None and not no_hero and template == "overview" and items:
        hero = next((item for item in items if item.role == "trend"), items[0])
    ordered = ([hero] if hero else []) + [item for item in items if item is not hero]
    cells: list[_Cell] = []
    for item in ordered:
        if item is hero:
            w, h = HERO_SIZE
            region = "hero"
        else:
            w, h = grid_size(template, "grid", len(items))
            region = "grid"
        cells.append(_Cell(item, region, w, _shrunk(h, shrink)))
    return _pack(cells, top)


def _take(pool: list[LayoutItem], predicate: Callable[[LayoutItem], bool]) -> LayoutItem | None:
    for item in pool:
        if predicate(item):
            pool.remove(item)
            return item
    return None


def _pulse_columns(
    items: list[LayoutItem],
) -> tuple[LayoutItem | None, LayoutItem | None, list[LayoutItem]]:
    """Left = ranking, right = trend/hero; explicit regions win, gaps are filled in order."""

    pool = list(items)
    right = _take(pool, lambda item: item.region == "right")
    left = _take(pool, lambda item: item.region == "left")
    right = right or _take(pool, lambda item: item.hero or item.region == "hero")
    right = right or _take(pool, lambda item: item.role == "trend")
    left = left or _take(pool, lambda item: item.role == "ranking")
    right = right or _take(pool, lambda _item: True)
    left = left or _take(pool, lambda _item: True)
    return left, right, pool


def _detail_table(items: list[LayoutItem]) -> LayoutItem | None:
    return next((item for item in items if item.region == "detail"), None) or next(
        (item for item in items if item.component == "table"), None
    )


def _place_pulse(items: list[LayoutItem], top: int, *, shrink: bool) -> tuple[list[Slot], bool]:
    left, right, pool = _pulse_columns(items)
    main_height = _shrunk(5, shrink)
    slots: list[Slot] = []
    underfilled = left is None or right is None
    if left is not None and right is not None:
        slots.append(Slot(id=left.id, kind="chart", region="left", x=0, y=top, w=4, h=main_height))
        slots.append(Slot(id=right.id, kind="chart", region="right", x=4, y=top, w=8, h=main_height))
    else:
        only = left or right
        if only is not None:
            region = "left" if only is left else "right"
            slots.append(
                Slot(id=only.id, kind="chart", region=region, x=0, y=top, w=GRID_COLUMNS, h=main_height)
            )
    bottom_height = _shrunk(4, shrink)
    cells = [_Cell(item, "grid", 4, bottom_height) for item in pool]
    slots.extend(_pack(cells, top + (main_height if slots else 0)))
    return slots, underfilled


def _place_detail(
    items: list[LayoutItem], top: int, *, shrink: bool, no_hero: bool
) -> tuple[list[Slot], bool]:
    table = _detail_table(items)
    pool = [item for item in items if item is not table]
    slots = _place_rows("detail", pool, top, shrink=shrink, no_hero=no_hero)
    if table is not None:
        y = max((slot.y + slot.h for slot in slots), default=top)
        slots.append(
            Slot(
                id=table.id, kind="chart", region="detail", x=0, y=y, w=GRID_COLUMNS,
                h=_shrunk(6, shrink),
            )
        )
    return slots, table is None


def _place(
    template: str, kpi_ids: list[str], items: list[LayoutItem], *, shrink: bool, no_hero: bool
) -> tuple[list[Slot], bool]:
    top = KPI_ROW + KPI_HEIGHT if kpi_ids else KPI_ROW
    if template == "pulse":
        charts, underfilled = _place_pulse(items, top, shrink=shrink)
    elif template == "detail":
        charts, underfilled = _place_detail(items, top, shrink=shrink, no_hero=no_hero)
    else:
        charts, underfilled = _place_rows(template, items, top, shrink=shrink, no_hero=no_hero), False
    return _kpi_slots(kpi_ids) + charts, underfilled


def _bottom(slots: list[Slot]) -> int:
    return max((slot.y + slot.h for slot in slots), default=0)


def _keep_order(items: list[LayoutItem], keep: set[str]) -> list[LayoutItem]:
    return [item for item in items if item.id in keep]


def _anchors(template: str, items: list[LayoutItem]) -> set[str]:
    """Blocks that define the template (pulse columns, the detail table)."""

    if template == "pulse":
        left, right, _rest = _pulse_columns(items)
        return {item.id for item in (left, right) if item is not None}
    if template == "detail":
        table = _detail_table(items)
        return {table.id} if table is not None else set()
    return set()


def _drop_order(template: str, items: list[LayoutItem]) -> list[LayoutItem]:
    """Unplanned items, lowest priority first (later items lose ties); anchors go last."""

    anchors = _anchors(template, items)
    indexed = [(index, item) for index, item in enumerate(items) if not item.planned]
    return [
        item
        for _index, item in sorted(
            indexed, key=lambda pair: (pair[1].id in anchors, pair[1].priority, -pair[0])
        )
    ]


def _attempt(
    template: str, kpi_ids: list[str], items: list[LayoutItem], *, allow_drop: bool
) -> LayoutResult | None:
    spec = TEMPLATES[template]
    warnings: list[DashboardWarning] = []
    kept: list[LayoutItem] = []
    for item in items:
        if item.region and item.region not in spec.regions:
            warnings.append(
                _warning(
                    "PLAN_REGION_UNKNOWN",
                    f"{template} 模板没有 {item.region} 区域，「{item.title or item.id}」按默认位置摆放",
                )
            )
            item = replace(item, region=None)
        kept.append(item)
    dropped: list[str] = []

    if len(kept) > spec.capacity:
        if not allow_drop:
            return None
        surplus = _drop_order(template, kept)[: len(kept) - spec.capacity]
        if len(kept) - len(surplus) > spec.capacity:
            surplus = sorted(kept, key=lambda item: item.priority)[: len(kept) - spec.capacity]
        dropped.extend(item.id for item in surplus)
        kept = _keep_order(kept, {item.id for item in kept} - set(dropped))
        warnings.append(
            _warning(
                "TRUNCATED",
                f"{template} 模板最多 {spec.capacity} 个图表，已省略 {len(surplus)} 个："
                + "、".join(item.title or item.id for item in surplus),
            )
        )

    while True:
        for shrink, no_hero in _DEGRADE_STEPS:
            slots, underfilled = _place(template, kpi_ids, kept, shrink=shrink, no_hero=no_hero)
            if _bottom(slots) <= GRID_ROWS:
                if underfilled:
                    warnings.append(
                        _warning("TEMPLATE_UNDERFILLED", f"{template} 模板的部分区域没有可用的块，已折叠")
                    )
                return LayoutResult(
                    layout=LayoutPlan(template=template, slots=slots, overflow="fit"),  # type: ignore[arg-type]
                    kept=[item.id for item in kept],
                    dropped=dropped,
                    warnings=warnings,
                )
        if not allow_drop:
            return None
        candidates = _drop_order(template, kept)
        if not candidates:
            break
        victim = candidates[0]
        kept = [item for item in kept if item is not victim]
        dropped.append(victim.id)
        warnings.append(
            _warning("TRUNCATED", f"全屏高度不足，已省略「{victim.title or victim.id}」")
        )

    slots, underfilled = _place(template, kpi_ids, kept, shrink=True, no_hero=True)
    warnings.append(_warning("WALL_SCROLL", "计划指定的块过多，全屏模式需要上下滚动查看"))
    if underfilled:
        warnings.append(_warning("TEMPLATE_UNDERFILLED", f"{template} 模板的部分区域没有可用的块，已折叠"))
    return LayoutResult(
        layout=LayoutPlan(template=template, slots=slots, overflow="scroll"),  # type: ignore[arg-type]
        kept=[item.id for item in kept],
        dropped=dropped,
        warnings=warnings,
    )


def plan_layout(
    kpi_ids: list[str], items: list[LayoutItem], *, template: str | None = None
) -> LayoutResult:
    """Choose a template (or honour the plan's) and place every block on the grid."""

    notices: list[DashboardWarning] = []
    requested = (template or "").strip().lower() or None
    if requested and requested not in TEMPLATES:
        notices.append(_warning("TEMPLATE_UNKNOWN", f"未知模板 {requested}，已自动选择版式"))
        requested = None
    if requested:
        result = _attempt(requested, kpi_ids, items, allow_drop=True)
    else:
        ranked = _ranked_templates(items)
        result = next(
            (
                attempt
                for name in ranked
                if (attempt := _attempt(name, kpi_ids, items, allow_drop=False)) is not None
            ),
            None,
        ) or _attempt(ranked[0], kpi_ids, items, allow_drop=True)
    assert result is not None  # allow_drop=True always yields a layout
    result.warnings = notices + result.warnings
    return result

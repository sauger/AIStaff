"""Layout templates for ``compose_dashboard`` (plan §5.2).

One table drives both the engine and the model-facing vocabulary, in the
spirit of DataRoom's component catalogue (name + synonyms + regions).  Sizes
are grid units on the fixed 12×12 canvas (one unit = 160×90 px at 1920×1080).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.dashboard.spec import GRID_COLUMNS

HEADER_ROW = 0
KPI_ROW = 1
KPI_HEIGHT = 1
HERO_SIZE = (GRID_COLUMNS, 4)
MIN_CHART_HEIGHT = 3


@dataclass(frozen=True)
class Template:
    name: str
    title: str
    synonyms: tuple[str, ...]
    regions: tuple[str, ...]
    capacity: int
    description: str


TEMPLATES: dict[str, Template] = {
    "overview": Template(
        name="overview",
        title="总览",
        synonyms=("总览", "概览", "主图加辅图"),
        regions=("kpi_row", "hero", "grid"),
        capacity=5,
        description="首图 12×4 作主角，其余 6×3",
    ),
    "compare": Template(
        name="compare",
        title="对比",
        synonyms=("四宫格", "对比", "网格"),
        regions=("kpi_row", "hero", "grid"),
        capacity=8,
        description="同权图 6×4；7–8 图时 3×4",
    ),
    "pulse": Template(
        name="pulse",
        title="左右分栏",
        synonyms=("左边排名右边趋势", "左右", "左右分栏"),
        regions=("kpi_row", "hero", "left", "right", "grid"),
        capacity=5,
        description="左 4×5、右 8×5，底部最多 3 个 4×4",
    ),
    "detail": Template(
        name="detail",
        title="明细",
        synonyms=("明细", "表格放最下面"),
        regions=("kpi_row", "hero", "grid", "detail"),
        capacity=5,
        description="图 6×4 在上，明细表 12×6 收尾",
    ),
    "stack": Template(
        name="stack",
        title="纵向堆叠",
        synonyms=("堆叠", "纵向"),
        regions=("kpi_row", "hero", "grid"),
        capacity=3,
        description="每图 12×3 纵向排列",
    ),
}

# tie-break order for template scoring (plan §5.3)
TEMPLATE_ORDER: tuple[str, ...] = ("overview", "compare", "pulse", "detail", "stack")


def grid_size(template: str, region: str, chart_count: int) -> tuple[int, int]:
    """Default (w, h) of a chart slot before degradation."""

    if region == "hero":
        return HERO_SIZE
    if template == "overview":
        return (6, 3)
    if template == "compare":
        return (3, 4) if chart_count >= 7 else (6, 4)
    if template == "pulse":
        return {"left": (4, 5), "right": (8, 5)}.get(region, (4, 4))
    if template == "detail":
        return (GRID_COLUMNS, 6) if region == "detail" else (6, 4)
    return (GRID_COLUMNS, 3)

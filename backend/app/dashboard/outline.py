"""``mode=outline``: list the blocks compose would use, without drawing anything.

Used when the model wants exact control: outline first, then render with a
plan that names blocks by ``path``.  No view is stored and no event is emitted;
the receipt carries block addresses and shapes, never data values.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.dashboard.layout import plan_layout

if TYPE_CHECKING:
    from app.dashboard.compose import ComposeResult, _Prepared

OUTLINE_NOTICE = (
    "以上是可用块清单（未出图）。需要指定标题/角色/版式时，用 plan.blocks[].path 引用这些块，"
    "再以 mode=render 调用一次 compose_dashboard。"
)


def _entry(path: str, title: str, suggest: str, size: str, limit: int) -> dict[str, Any]:
    entry = {"path": path, "title": title, "suggest": suggest, "size": size}
    while len(json.dumps(entry, ensure_ascii=False)) > limit and len(entry["title"]) > 4:
        entry["title"] = entry["title"][:-2] + "…"
    return entry


def build_outline(prepared: _Prepared) -> ComposeResult:
    from app.dashboard.compose import (
        MAX_OUTLINE_BLOCKS,
        MAX_OUTLINE_ENTRY,
        ComposeResult,
        _dedupe_warnings,
        _layout_items,
    )

    entries: list[dict[str, Any]] = []
    for item in prepared.composed[:MAX_OUTLINE_BLOCKS]:
        block, selection = item.block, item.selection
        frame = block.frame
        suggest = selection.component if not selection.role else f"{selection.component}/{selection.role}"
        size = f"{max(frame.total_rows, len(frame.rows))}行×{len(frame.columns)}列"
        entries.append(_entry(block.path, block.title, suggest, size, MAX_OUTLINE_ENTRY))

    charts = [item for item in prepared.composed if item.selection.component != "kpi"]
    kpi_blocks = [item for item in prepared.composed if item.selection.component == "kpi"]
    template = None
    if charts:
        layout = plan_layout(
            [f"k{index}" for index in range(min(len(kpi_blocks), 6))],
            _layout_items(charts[:8]),
            template=prepared.plan.template,
        )
        template = layout.layout.template
    warnings = _dedupe_warnings(prepared.warnings)
    outline = {
        "mode": "outline",
        "title": prepared.arguments.title,
        "block_count": len(prepared.composed),
        "blocks": entries,
        "omitted": max(0, len(prepared.composed) - MAX_OUTLINE_BLOCKS),
        "template": template,
        "warnings": [warning.model_dump(exclude_none=True) for warning in warnings],
        "notice": OUTLINE_NOTICE,
    }
    return ComposeResult(
        mode="outline",
        title=prepared.arguments.title,
        blocks_found=prepared.blocks_found,
        template=template,
        warnings=warnings,
        outline=outline,
    )

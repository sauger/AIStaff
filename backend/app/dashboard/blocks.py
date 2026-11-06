"""Block splitting and plan merging for ``compose_dashboard`` (pipeline step 2).

A block is one frame with a stable address (``path``), a readable title, a
slot-safe id and an optional role hint.  The model's optional "small plan"
only refers to blocks by path or label; it can rename a block, give it a role
or a region, reorder blocks and pick a hero — never chart structure.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.dashboard.normalize import MAX_FRAMES, Frame, PathSegment, parse_path
from app.dashboard.spec import (
    CHART_ROLES,
    MAX_CHART_ID,
    MAX_PLAN_BLOCKS,
    MAX_PLAN_ORDER,
    MAX_TITLE,
    DashboardWarning,
)

# Hints only: shape rules always win (plan §4.3).  Kept in one table so a
# deployment can add customer vocabulary without touching the rules.
ROLE_LEXICON: dict[str, tuple[str, ...]] = {
    "ranking": ("排名", "排行", "榜单", "top", "rank", "ranking"),
    "composition": ("占比", "分布", "构成", "比例", "share", "distribution", "composition"),
    "trend": ("趋势", "走势", "月度", "每日", "trend", "daily", "monthly", "weekly"),
    "detail": ("明细", "清单", "列表", "详情", "detail", "details"),
    "kpi": ("合计", "累计", "总", "销售额", "金额", "total", "count", "kpi"),
}
# kpi words (销售额/金额) appear in almost every label, so they are checked last
ROLE_HINT_ORDER = ("ranking", "composition", "trend", "detail", "kpi")
GENERIC_LABEL_WORDS = frozenset(
    {"data", "list", "items", "item", "result", "results", "rows", "records", "payload", "blocks"}
)


@dataclass
class Block:
    id: str
    path: str
    label: str
    title: str
    frame: Frame
    role_hint: str | None
    score: float
    order: int
    segments: tuple[PathSegment, ...] = ()
    planned: bool = False
    plan_role: str | None = None
    region: str | None = None
    hero: bool = False
    rank: int | None = None


@dataclass(frozen=True)
class PlanBlock:
    path: str | None = None
    label: str | None = None
    title: str | None = None
    role: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class Plan:
    blocks: tuple[PlanBlock, ...] = ()
    template: str | None = None
    order: tuple[str, ...] = ()
    hero: str | None = None
    raw_present: bool = False
    issues: tuple[str, ...] = field(default_factory=tuple)


def _warning(code: str, message: str, path: str | None = None) -> DashboardWarning:
    return DashboardWarning(code=code, message=message[:200], path=(path or None))


# --- labels, ids, hints ----------------------------------------------------------


def _pretty_label(segments: tuple[PathSegment, ...]) -> str:
    """Last meaningful key, humanised: ``monthly_sales`` → ``Monthly sales``."""

    for segment in reversed(segments):
        if isinstance(segment, int):
            continue
        text = " ".join(re.sub(r"[_\-]+", " ", segment).split())
        if not text or text.lower() in GENERIC_LABEL_WORDS:
            continue
        if text.isascii():
            text = text[0].upper() + text[1:]
        return text[:MAX_TITLE]
    return ""


def _slug(path: str) -> str:
    text = re.sub(r"\[(\d+)\]", r"-\1", path)
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    # keep the most specific tail when a path is long
    return text[-MAX_CHART_ID:].strip("-")


def unique_id(base: str, used: set[str], fallback: str) -> str:
    candidate = (base or fallback)[:MAX_CHART_ID]
    suffix = 2
    while candidate in used:
        tail = f"-{suffix}"
        candidate = f"{(base or fallback)[: MAX_CHART_ID - len(tail)]}{tail}"
        suffix += 1
    used.add(candidate)
    return candidate


def _contains_word(text: str, word: str) -> bool:
    if word.isascii():
        return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text) is not None
    return word in text


def role_hint(*texts: str) -> str | None:
    haystack = " ".join(texts).lower()
    for role in ROLE_HINT_ORDER:
        if any(_contains_word(haystack, word) for word in ROLE_LEXICON[role]):
            return role
    return None


def _score(frame: Frame, hint: str | None) -> float:
    rows = len(frame.rows)
    rows_signal = min(1.0, math.log10(rows + 1) / 2)
    numeric = sum(1 for column in frame.columns if column.type == "number")
    cells = sum(len(row) for row in frame.rows) or 1
    nulls = sum(1 for row in frame.rows for cell in row if cell is None)
    return round(
        3 * rows_signal
        + 2 * min(numeric, 4)
        + (2 if hint else 0)
        + 1.5 * (1 - nulls / cells)
        - 2 * frame.depth,
        4,
    )


def dedupe_titles(blocks: list[Block]) -> None:
    seen: dict[str, int] = {}
    for block in blocks:
        count = seen.get(block.title, 0) + 1
        seen[block.title] = count
        if count > 1:
            suffix = f"（{count}）"
            block.title = f"{block.title[: MAX_TITLE - len(suffix)]}{suffix}"


def split_blocks(frames: list[Frame], *, limit: int = MAX_FRAMES) -> list[Block]:
    """Frames → addressable blocks.  Empty frames are dropped, not counted."""

    blocks: list[Block] = []
    used: set[str] = set()
    for frame in frames:
        if not frame.rows:
            continue
        segments = parse_path(frame.path) or ()
        label = frame.label or _pretty_label(segments)
        title = label or f"数据块 {len(blocks) + 1}"
        hint = role_hint(title, " ".join(str(item) for item in segments if isinstance(item, str)))
        blocks.append(
            Block(
                id=unique_id(_slug(frame.path), used, f"block-{len(blocks) + 1}"),
                path=frame.path,
                label=label,
                title=title[:MAX_TITLE],
                frame=frame,
                role_hint=hint,
                score=_score(frame, hint),
                order=len(blocks),
                segments=segments,
            )
        )
    if len(blocks) > limit:
        keep = sorted(blocks, key=lambda item: (-item.score, item.order))[:limit]
        kept = {id(item) for item in keep}
        blocks = [item for item in blocks if id(item) in kept]
    dedupe_titles(blocks)
    return blocks


# --- plan -------------------------------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def parse_plan(raw: Any) -> Plan:
    """Lenient: a malformed plan degrades to "fully automatic", never an error."""

    if raw is None:
        return Plan()
    if not isinstance(raw, dict):
        return Plan(raw_present=True, issues=("plan 不是对象，已按自动编排处理",))
    issues: list[str] = []
    entries: list[PlanBlock] = []
    raw_blocks = raw.get("blocks")
    if isinstance(raw_blocks, list):
        if len(raw_blocks) > MAX_PLAN_BLOCKS:
            issues.append(f"plan.blocks 超过 {MAX_PLAN_BLOCKS} 条，其余已忽略")
        for item in raw_blocks[:MAX_PLAN_BLOCKS]:
            if not isinstance(item, dict):
                continue
            role = _text(item.get("role"), 20)
            role = role.lower() if role else None
            entries.append(
                PlanBlock(
                    path=_text(item.get("path"), 200),
                    label=_text(item.get("label"), 40),
                    title=_text(item.get("title"), MAX_TITLE),
                    role=role if role in CHART_ROLES else None,
                    region=(_text(item.get("region"), 20) or "").lower() or None,
                )
            )
    layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    order = layout.get("order") if isinstance(layout.get("order"), list) else []
    return Plan(
        blocks=tuple(entries),
        template=(_text(layout.get("template"), 20) or "").lower() or None,
        order=tuple(text for text in (_text(item, 200) for item in order[:MAX_PLAN_ORDER]) if text),
        hero=_text(layout.get("hero"), 200),
        raw_present=True,
        issues=tuple(issues),
    )


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-·.:：,，()（）\[\]]+", "", text).lower()


def resolve_block(
    blocks: list[Block], *, path: str | None = None, label: str | None = None
) -> tuple[Block | None, str]:
    """Find one block by path (exact, then unique suffix/prefix) or unique label."""

    if path:
        segments = parse_path(path)
        if segments is not None:
            exact = [block for block in blocks if block.segments == segments]
            if exact:
                return exact[0], "path"
            if segments:
                size = len(segments)
                suffix = [block for block in blocks if block.segments[-size:] == segments]
                if len(suffix) == 1:
                    return suffix[0], "path"
                prefix = [block for block in blocks if block.segments[:size] == segments]
                if len(prefix) == 1:
                    return prefix[0], "path"
    for candidate in (label, path):
        if not candidate:
            continue
        key = _norm(candidate)
        if not key:
            continue
        exact = [block for block in blocks if key in {_norm(block.title), _norm(block.label)}]
        if len(exact) == 1:
            return exact[0], "label"
        if len(exact) > 1:
            continue
        fuzzy = [
            block
            for block in blocks
            if (norm_title := _norm(block.title)) and (key in norm_title or norm_title in key)
        ]
        if len(fuzzy) == 1:
            return fuzzy[0], "label"
    return None, ""


def _available(blocks: list[Block]) -> str:
    listed = "、".join(f"{block.path}（{block.title}）" for block in blocks[:6])
    more = f" 等 {len(blocks)} 块" if len(blocks) > 6 else ""
    return f"{listed}{more}"


def merge_plan(blocks: list[Block], plan: Plan) -> tuple[list[Block], list[DashboardWarning]]:
    """Apply the small plan; unmatched entries become warnings, never errors."""

    warnings = [_warning("PLAN_INVALID", issue) for issue in plan.issues]
    claimed: set[str] = set()

    for entry in plan.blocks:
        block, how = resolve_block(blocks, path=entry.path, label=entry.label)
        reference = entry.path or entry.label or ""
        if block is None:
            warnings.append(
                _warning(
                    "PLAN_BLOCK_NOT_FOUND",
                    f"plan 中的块「{reference}」未找到，可用块：{_available(blocks)}",
                    entry.path,
                )
            )
            continue
        if how == "label" and not entry.path:
            warnings.append(
                _warning("PLAN_LABEL_MATCHED", f"「{reference}」按块名匹配到 {block.path}", block.path)
            )
        if block.id in claimed:
            continue
        claimed.add(block.id)
        block.planned = True
        if entry.title:
            block.title = entry.title
        if entry.role:
            block.plan_role = entry.role
        if entry.region:
            block.region = entry.region

    rank = 0
    for reference in plan.order:
        block, _how = resolve_block(blocks, path=reference, label=reference)
        if block is None:
            warnings.append(
                _warning(
                    "PLAN_BLOCK_NOT_FOUND",
                    f"layout.order 中的「{reference}」未找到，可用块：{_available(blocks)}",
                    reference,
                )
            )
            continue
        if block.rank is None:
            block.rank = rank
            block.planned = True
            rank += 1

    if plan.hero:
        block, _how = resolve_block(blocks, path=plan.hero, label=plan.hero)
        if block is None:
            warnings.append(
                _warning(
                    "PLAN_BLOCK_NOT_FOUND",
                    f"layout.hero「{plan.hero}」未找到，可用块：{_available(blocks)}",
                    plan.hero,
                )
            )
        else:
            block.hero = True
            block.planned = True

    ordered = sorted(
        blocks,
        key=lambda item: (item.rank is None, item.rank if item.rank is not None else 0, item.order),
    )
    dedupe_titles(ordered)
    return ordered, warnings

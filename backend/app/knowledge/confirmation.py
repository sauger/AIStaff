from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.time_utils import as_utc

CANCEL_SELECTION = object()

_CODE = r"([0-9A-Fa-f]{6})(?![0-9A-Fa-f])"
_BARE_CODE_RE = re.compile(rf"^\s*{_CODE}\s*$")
_CANCEL_RE = re.compile(rf"取消(?:\s*(?:新增|覆盖))?\s*(?:(\d+)\s*)?{_CODE}")
_WRITE_RE = re.compile(rf"(覆盖|新增)\s*(?:(\d+)\s*)?{_CODE}")
_NEGATIVE_RE = re.compile(r"(?:不要|先别|别).{0,16}(?:新增|覆盖)")
_REFUSE_CANCEL_RE = re.compile(r"(?:不要|先别|别)\s*取消")
_CONFIRM_INTENT_RE = re.compile(
    r"^\s*(?:请)?(?:确认|执行|提交)(?:一下)?(?:新增|覆盖|入库)?\s*$"
)
_NUMBERED_SELECTION_RE = re.compile(r"\s*(\d+)\s*(?:[.、]\s*.*)?")


@dataclass(frozen=True)
class KnowledgeCommand:
    kind: Literal["create", "replace", "cancel", "bare", "negative"]
    code: str
    index: int | None = None


def parse_knowledge_confirmation(text: str) -> KnowledgeCommand | None:
    """Parse a channel or web confirmation. Negation never becomes a write."""

    raw = str(text or "").strip()
    if not raw:
        return None
    if _NEGATIVE_RE.search(raw) and _CANCEL_RE.search(raw) is None:
        code_match = re.search(_CODE, raw)
        if code_match is None:
            return None
        return KnowledgeCommand("negative", code_match.group(1).upper())
    if _REFUSE_CANCEL_RE.search(raw):
        return None
    cancel = _CANCEL_RE.search(raw)
    if cancel is not None:
        index = int(cancel.group(1)) if cancel.group(1) else None
        return KnowledgeCommand("cancel", cancel.group(2).upper(), index)
    if _NEGATIVE_RE.search(raw):
        code_match = re.search(_CODE, raw)
        if code_match is None:
            return None
        return KnowledgeCommand("negative", code_match.group(1).upper())
    write = _WRITE_RE.search(raw)
    if write is not None:
        action, index_text, code = write.groups()
        return KnowledgeCommand(
            "replace" if action == "覆盖" else "create",
            code.upper(),
            int(index_text) if index_text else None,
        )
    bare = _BARE_CODE_RE.match(raw)
    if bare is not None:
        return KnowledgeCommand("bare", bare.group(1).upper())
    return None


def is_knowledge_confirm_intent(text: str) -> bool:
    return _CONFIRM_INTENT_RE.match(str(text or "").strip()) is not None


def confirmation_hint(code: str, candidates: list[Any] | None) -> str:
    if candidates:
        return f"覆盖 1 {code}"
    return f"新增 {code}"


def pending_created_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return as_utc(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return as_utc(parsed)


def select_pending_knowledge_base(text: str, targets: list[dict[str, Any]]) -> Any:
    """Return a target dict, CANCEL_SELECTION, or None when the text is unrelated."""

    cleaned = str(text or "").strip()
    rows = [item for item in targets if isinstance(item, dict) and item.get("id")]
    if cleaned in {"取消", "算了", "不弄了"}:
        return CANCEL_SELECTION
    numbered = _NUMBERED_SELECTION_RE.fullmatch(cleaned)
    if numbered is not None:
        index = int(numbered.group(1)) - 1
        if 0 <= index < len(rows):
            return rows[index]
        return None
    for item in sorted(rows, key=lambda row: len(str(row.get("name") or "")), reverse=True):
        name = str(item.get("name") or "").strip()
        if name and cleaned == name:
            return item
    return None

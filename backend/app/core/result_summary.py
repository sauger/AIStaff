"""Token-cheap projections of capability results for the model context.

The harness keeps every result in durable invocation records.  Only a compact
summary of older results is replayed into the next model call: enough to know
what happened (status, counts, ids, amounts) without re-sending the payload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

DEFAULT_SUMMARY_CHARS = 300
_MAX_KEYS = 12
_MAX_LIST_ITEMS = 20
_MAX_STRING_CHARS = 120
_ID_KEYS = ("id", "name", "status", "code", "type", "path")
# Bulk payloads are exactly what the caller should re-read on demand; a
# truncated copy costs tokens without letting the model act on it.
_BULK_KEYS = {"content", "text", "body", "raw", "payload", "html", "markdown"}


def summarize_capability_result(
    result: dict[str, Any],
    *,
    max_chars: int = DEFAULT_SUMMARY_CHARS,
) -> dict[str, Any]:
    """Return a bounded, information-dense view of a capability result."""

    summary: dict[str, Any] = {
        "tool_name": result.get("tool_name"),
        "success": bool(result.get("success")),
    }
    if result.get("truncated"):
        summary["truncated"] = True
    error = result.get("error")
    if error not in (None, {}, "", []):
        summary["error"] = _summarize_error(error)
    data = _summarize_data(result.get("data"))
    if data is not None:
        summary["data"] = data
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    summary["history_receipt"] = {
        "omitted_chars": len(serialized),
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }
    return _fit(summary, max_chars)


def _summarize_data(value: Any) -> Any:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, dict):
        summarized: dict[str, Any] = {}
        for key in sorted(value, key=str)[:_MAX_KEYS]:
            if str(key).lower() in _BULK_KEYS:
                continue
            item = _summarize_value(value[key])
            if item is not None:
                summarized[str(key)] = item
        return summarized or None
    if isinstance(value, list):
        return _summarize_list(value)
    return _summarize_scalar(value)


def _summarize_value(value: Any) -> Any:
    if isinstance(value, list):
        return _summarize_list(value)
    if isinstance(value, dict):
        keys = sorted(value, key=str)
        return {
            "keys": keys[:8],
            "count": len(keys),
        }
    return _summarize_scalar(value)


def _summarize_list(value: list[Any]) -> dict[str, Any]:
    items: list[Any] = []
    for item in value[:_MAX_LIST_ITEMS]:
        if isinstance(item, dict):
            projected: dict[str, Any] = {}
            for key in _ID_KEYS:
                if item.get(key) in (None, "", [], {}):
                    continue
                projected[key] = _summarize_scalar(item[key])
                if len(projected) >= 2:
                    break
            items.append(projected or _summarize_value(item))
        else:
            items.append(_summarize_scalar(item))
    return {"count": len(value), "first": items}


def _summarize_scalar(value: Any) -> Any:
    if isinstance(value, (bool, int, float)):
        return value
    text = " ".join(str(value).split())
    if len(text) > _MAX_STRING_CHARS:
        return f"{text[:_MAX_STRING_CHARS]}…"
    return text


def _summarize_error(error: Any) -> Any:
    if isinstance(error, dict):
        return {
            key: _summarize_scalar(error[key])
            for key in ("code", "type", "message")
            if error.get(key) not in (None, "", [], {})
        } or _summarize_value(error)
    return _summarize_scalar(error)


def _fit(summary: dict[str, Any], max_chars: int) -> dict[str, Any]:
    budget = max(80, int(max_chars))
    fitted = summary
    for _attempt in range(40):
        if len(_serialize(fitted)) <= budget:
            return fitted
        if not _shrink_lists(fitted):
            break
    trimmed = dict(fitted)
    trimmed.pop("data", None)
    if len(_serialize(trimmed)) <= budget:
        return trimmed
    receipt = trimmed.get("history_receipt")
    return {
        "tool_name": trimmed.get("tool_name"),
        "success": trimmed.get("success"),
        "truncated": True,
        "history_receipt": receipt,
    }


def _shrink_lists(summary: dict[str, Any]) -> bool:
    """Trim list previews step by step until the summary fits its budget."""

    data = summary.get("data")
    if not isinstance(data, dict):
        return False
    for value in data.values():
        if not isinstance(value, dict):
            continue
        items = value.get("first")
        if not isinstance(items, list) or not items:
            continue
        if len(items) > 5:
            value["first"] = items[:5]
        elif len(items) > 1:
            value["first"] = items[:1]
        else:
            value.pop("first", None)
        return True
    return False


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


__all__ = ["DEFAULT_SUMMARY_CHARS", "summarize_capability_result"]

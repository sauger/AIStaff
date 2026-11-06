"""Shape normalisation for ``compose_dashboard`` (pipeline step 1).

Tool results arrive in many shapes: the demo ``{"blocks": [...]}`` contract,
record arrays, equal-length column arrays, matrices with or without a header,
wrapper objects.  This module turns any of them into ``Frame`` tables
(columns + rows) and leaves every later decision — chart type, size, position —
to the deterministic pipeline.  Pure functions: no IO, no clock.

The demo contract (shape 11) is the primary path; the other ten shapes are a
generic fallback that is deliberately not tuned per customer.  Cell clean-up
(URL / NaN / long text) happens later in ``compose`` so ``render_dashboard``
keeps rejecting such cells.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.dashboard.chart_policy import is_temporal_text
from app.dashboard.spec import (
    MAX_COMPOSE_DATA_BYTES,
    MAX_NESTED_CELL,
    MAX_STRING,
    Column,
    DashboardWarning,
    Scalar,
)

MAX_DEPTH = 5
MAX_NODES = 500
MAX_FRAMES = 24
MAX_COLUMNS = 32
# Parse-time guard only; compose trims each chart to the contract's 50 rows.
MAX_FRAME_ROWS = 1000
MAX_SCALAR_GROUP = 12

_WRAPPER_KEYS = ("data", "result")
# Envelope/pagination fields that must never surface as KPI cards.
_META_KEYS = frozenset(
    {
        "code", "status", "success", "msg", "message", "errcode", "errmsg", "error",
        "request_id", "requestid", "trace_id", "traceid", "page", "page_size", "pagesize",
        "page_no", "pageno", "offset", "limit", "cursor", "timestamp", "ts", "version",
        "as_of", "asof",
    }
)
_LABEL_KEYS = ("name", "title", "label")
_COLUMN_TYPES = frozenset({"string", "number", "date", "datetime", "boolean"})
_REFERENCE_KINDS = frozenset({"sandbox_json_file", "sandbox_text_file"})
_SIMPLE_KEY = re.compile(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff-]*$")
_PATH_TOKEN = re.compile(
    r'\.?(?P<name>[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff-]*)'
    r'|\[(?P<index>\d+)\]'
    r'|\[(?P<quoted>"(?:[^"\\]|\\.)*")\]'
)

PathSegment = str | int


class DashboardDataError(ValueError):
    """The input cannot become a board at all; surfaced as a coded failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details


@dataclass(frozen=True)
class Frame:
    """One table found in the source, addressed by its path."""

    path: str
    label: str
    columns: list[Column]
    rows: list[list[Scalar]]
    shape: str
    synthetic_headers: bool = False
    depth: int = 0
    total_rows: int = 0
    unit: str | None = None
    delta: float | None = None


@dataclass
class NormalizeResult:
    frames: list[Frame] = field(default_factory=list)
    warnings: list[DashboardWarning] = field(default_factory=list)
    empty_paths: list[str] = field(default_factory=list)


# --- paths -------------------------------------------------------------------


def child_path(parent: str, key: PathSegment) -> str:
    """Canonical path of a child: ``result.monthly``, ``items[0]``, ``$["a b"]``."""

    if isinstance(key, int):
        return f"{parent}[{key}]"
    if _SIMPLE_KEY.match(key):
        return key if parent == "$" else f"{parent}.{key}"
    return f"{parent}[{json.dumps(key, ensure_ascii=False)}]"


def format_path(segments: tuple[PathSegment, ...] | list[PathSegment]) -> str:
    path = "$"
    for segment in segments:
        path = child_path(path, segment)
    return path


def parse_path(text: str | None) -> tuple[PathSegment, ...] | None:
    """Parse a dotted path or a JSON Pointer (``/result/monthly``) into segments."""

    raw = str(text or "").strip()
    if raw in {"", "$"}:
        return ()
    if raw.startswith("/"):
        segments: list[PathSegment] = []
        for part in raw[1:].split("/"):
            unescaped = part.replace("~1", "/").replace("~0", "~")
            segments.append(int(unescaped) if unescaped.isdigit() else unescaped)
        return tuple(segments)
    raw = raw.removeprefix("$")
    parsed: list[PathSegment] = []
    position = 0
    while position < len(raw):
        match = _PATH_TOKEN.match(raw, position)
        if match is None:
            return None
        if match.group("name") is not None:
            parsed.append(match.group("name"))
        elif match.group("index") is not None:
            parsed.append(int(match.group("index")))
        else:
            try:
                parsed.append(str(json.loads(match.group("quoted"))))
            except ValueError:
                return None
        position = match.end()
    return tuple(parsed)


# --- input parsing -------------------------------------------------------------


def parse_data(data: Any) -> Any:
    """Accept an object/array or a JSON string (≤4MB) as the tool result."""

    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8", errors="replace")
    if isinstance(data, str):
        if len(data.encode("utf-8")) > MAX_COMPOSE_DATA_BYTES:
            raise DashboardDataError("DATA_TOO_LARGE", "数据超过 4MB 上限，请缩小查询范围后重试。")
        text = data.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise DashboardDataError(
                "DATA_NOT_JSON", "data 是字符串但不是合法 JSON；请原样传入数据工具的结果。"
            ) from exc
    elif isinstance(data, (dict, list)):
        try:
            size = len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise DashboardDataError("DATA_NOT_JSON", "data 无法序列化为 JSON。") from exc
        if size > MAX_COMPOSE_DATA_BYTES:
            raise DashboardDataError("DATA_TOO_LARGE", "数据超过 4MB 上限，请缩小查询范围后重试。")
    if isinstance(data, dict) and data.get("kind") in _REFERENCE_KINDS and "sandbox_path" in data:
        if data.get("kind") == "sandbox_text_file":
            raise DashboardDataError(
                "UNSUPPORTED_DATA_REFERENCE",
                "该结果是文本/CSV 文件，compose_dashboard 只接受 JSON 结果；请让数据工具返回 JSON。",
            )
        raise DashboardDataError(
            "INVALID_TOOL_RESULT_REFERENCE",
            "JSON 结果引用未能还原，请重新调用数据工具后再试。",
            retryable=True,
        )
    return data


# --- cells and columns -----------------------------------------------------------


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _infer_type(values: list[Scalar]) -> str | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    if all(_is_number(value) for value in present):
        return "number"
    if all(isinstance(value, str) and is_temporal_text(value) for value in present):
        return "datetime" if any("T" in str(value) for value in present) else "date"
    return "string"


def _compatible(declared: str | None, inferred: str | None) -> bool:
    if declared is None:
        return False
    if inferred is None or declared == inferred or declared == "string":
        return True
    return declared in {"date", "datetime"} and inferred in {"date", "datetime", "string"}


def _clean_name(value: Any, index: int) -> str:
    text = "".join(char for char in str(value if value is not None else "") if char.isprintable())
    text = " ".join(text.split())[:MAX_STRING]
    return text or f"c{index + 1}"


def _unique_names(names: list[Any]) -> list[str]:
    result: list[str] = []
    for index, name in enumerate(names):
        base = _clean_name(name, index)
        candidate = base
        suffix = 2
        while candidate in result:
            candidate = f"{base[: MAX_STRING - 4]}_{suffix}"
            suffix += 1
        result.append(candidate)
    return result


def _columns(
    names: list[Any], rows: list[list[Scalar]], declared: list[str | None] | None = None
) -> list[Column]:
    columns: list[Column] = []
    for index, name in enumerate(_unique_names(names)):
        inferred = _infer_type([row[index] for row in rows[:200] if index < len(row)])
        given = declared[index] if declared and index < len(declared) else None
        kind = given if _compatible(given, inferred) else (inferred or given or "string")
        columns.append(Column(name=name, type=kind))  # type: ignore[arg-type]
    return columns


def _digest(columns: list[Column], rows: list[list[Scalar]]) -> str:
    payload = json.dumps(
        [[[column.name, column.type] for column in columns], rows],
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _label_of(obj: dict[str, Any]) -> str:
    for key in _LABEL_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:80]
    return ""


def _unit(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:16]
    return None


def _delta(value: Any) -> float | None:
    """Accept 0.12, 12 (percent points) or "12%"; anything else is dropped."""

    if isinstance(value, str):
        text = value.strip().rstrip("%")
        try:
            number = float(text)
        except ValueError:
            return None
        number = number / 100 if value.strip().endswith("%") else number
    elif _is_number(value):
        number = float(value)
    else:
        return None
    if not math.isfinite(number):
        return None
    if abs(number) > 1:
        number = number / 100
    return number if abs(number) <= 1 else None


# --- the walker ------------------------------------------------------------------


class _Normalizer:
    def __init__(self) -> None:
        self.result = NormalizeResult()
        self._nodes = 0
        self._flags: set[str] = set()
        self._digests: set[str] = set()

    @property
    def _stopped(self) -> bool:
        return "nodes" in self._flags or "frames" in self._flags

    def _warn_once(self, flag: str, code: str, message: str, path: str | None = None) -> None:
        if flag in self._flags:
            return
        self._flags.add(flag)
        self.result.warnings.append(DashboardWarning(code=code, message=message, path=path))

    def _enter(self, path: str, depth: int) -> bool:
        if self._stopped:
            return False
        self._nodes += 1
        if self._nodes > MAX_NODES:
            self._warn_once("nodes", "TRUNCATED", f"数据节点超过 {MAX_NODES} 个，后续内容未解析", path)
            return False
        if depth > MAX_DEPTH:
            self._warn_once("depth", "DEPTH_LIMIT", f"嵌套超过 {MAX_DEPTH} 层的内容已忽略", path)
            return False
        return True

    @staticmethod
    def _cell(value: Any, nested: list[bool]) -> Scalar:
        if _is_scalar(value):
            return value
        nested[0] = True
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        return text if len(text) <= MAX_NESTED_CELL else f"{text[: MAX_NESTED_CELL - 1]}…"

    def _add(
        self,
        *,
        path: str,
        label: str,
        names: list[Any],
        rows: list[list[Any]],
        shape: str,
        depth: int,
        declared: list[str | None] | None = None,
        synthetic_headers: bool = False,
        total_rows: int | None = None,
        unit: str | None = None,
        delta: float | None = None,
    ) -> None:
        if not rows or not names:
            self.result.empty_paths.append(path)
            return
        if len(names) > MAX_COLUMNS:
            self._warn_once(f"columns:{path}", "TRUNCATED", f"列数超过 {MAX_COLUMNS}，仅保留前 {MAX_COLUMNS} 列", path)
            names = names[:MAX_COLUMNS]
            declared = declared[:MAX_COLUMNS] if declared else None
        width = len(names)
        nested = [False]
        cells = [
            [self._cell(row[index] if index < len(row) else None, nested) for index in range(width)]
            for row in rows[:MAX_FRAME_ROWS]
        ]
        columns = _columns(names, cells, declared)
        digest = _digest(columns, cells)
        if digest in self._digests:
            return
        if len(self.result.frames) >= MAX_FRAMES:
            self._warn_once("frames", "TRUNCATED", f"候选数据块超过 {MAX_FRAMES} 个，其余已忽略", path)
            return
        self._digests.add(digest)
        if nested[0]:
            self._warn_once(f"nested:{path}", "NESTED_CELL", "嵌套对象已压成文本展示", path)
        self.result.frames.append(
            Frame(
                path=path,
                label=label,
                columns=columns,
                rows=cells,
                shape=shape,
                synthetic_headers=synthetic_headers,
                depth=depth,
                total_rows=len(rows) if total_rows is None else total_rows,
                unit=unit,
                delta=delta,
            )
        )

    # dispatch ----------------------------------------------------------------

    def visit(self, value: Any, path: str, depth: int, label: str = "") -> None:
        if not self._enter(path, depth):
            return
        if isinstance(value, dict):
            self._visit_dict(value, path, depth, label)
        elif isinstance(value, list):
            self._visit_list(value, path, depth, label)
        elif value is not None and value != "":
            self._add(path=path, label=label, names=[label or "数值"], rows=[[value]], shape="scalar", depth=depth)

    def _visit_dict(self, obj: dict[str, Any], path: str, depth: int, label: str) -> None:
        if not obj:
            self.result.empty_paths.append(path)
            return
        blocks = obj.get("blocks")
        if isinstance(blocks, list) and blocks and _looks_like_demo_blocks(blocks):
            base = child_path(path, "blocks")
            for index, item in enumerate(blocks):
                self._visit_block(item, child_path(base, index), depth + 1)
            return
        if _is_table(obj):
            self._table(obj, path, depth, _label_of(obj) or label)
            return
        nested = [key for key, item in obj.items() if not _is_scalar(item)]
        named = _label_of(obj)
        if named and len(nested) == 1:
            key = nested[0]
            self.visit(obj[key], child_path(path, str(key)), depth + 1, named)
            return
        wrapper = _wrapper_key(obj)
        if wrapper is not None:
            # envelopes such as {success, data} do not count as a nesting level
            self.visit(obj[wrapper], child_path(path, wrapper), depth, label)
            return
        columnar = _columnar_keys(obj)
        if columnar:
            length = len(obj[columnar[0]])
            rows = [[obj[key][index] for key in columnar] for index in range(length)]
            self._add(path=path, label=label, names=columnar, rows=rows, shape="columns", depth=depth)
            for key in nested:
                if key not in columnar:
                    self.visit(obj[key], child_path(path, str(key)), depth + 1)
            return
        scalars = {
            str(key): item
            for key, item in obj.items()
            if _is_scalar(item) and str(key).lower() not in _META_KEYS
        }
        if not nested:
            if scalars:
                self._scalars(scalars, path, depth, label)
            else:
                self.result.empty_paths.append(path)
            return
        numeric = {key: item for key, item in scalars.items() if _is_number(item)}
        if numeric:
            self._scalars(numeric, path, depth, label)
        for key in nested:
            self.visit(obj[key], child_path(path, str(key)), depth + 1)

    def _visit_list(self, items: list[Any], path: str, depth: int, label: str) -> None:
        if not items:
            self.result.empty_paths.append(path)
            return
        if all(isinstance(item, dict) for item in items):
            if all(_is_table(item) or _is_named_wrapper(item) for item in items):
                for index, item in enumerate(items):
                    self.visit(item, child_path(path, index), depth + 1)
                return
            if _stable_keys(items):
                self._records(items, path, depth, label)
                return
            for index, item in enumerate(items):
                self.visit(item, child_path(path, index), depth + 1)
            return
        if all(isinstance(item, list) for item in items):
            self._matrix(items, path, depth, label)
            return
        if all(_is_scalar(item) for item in items):
            self._add(
                path=path,
                label=label,
                names=[label or "数值"],
                rows=[[item] for item in items],
                shape="values",
                depth=depth,
            )
            return
        for index, item in enumerate(items):
            if not _is_scalar(item):
                self.visit(item, child_path(path, index), depth + 1)

    # shapes ------------------------------------------------------------------

    def _visit_block(self, item: Any, path: str, depth: int) -> None:
        """Shape 11: one element of the demo ``blocks`` contract."""

        if not self._enter(path, depth):
            return
        if not isinstance(item, dict):
            self.visit(item, path, depth)
            return
        name = _label_of(item)
        if "value" in item and _is_scalar(item.get("value")) and not _is_table(item):
            self._add(
                path=path,
                label=name,
                names=[name or "指标"],
                rows=[[item.get("value")]],
                shape="scalar",
                depth=depth,
                unit=_unit(item.get("unit")),
                delta=_delta(item.get("delta")),
            )
            return
        if _is_table(item):
            self._table(item, path, depth, name)
            return
        self._visit_dict(item, path, depth, name)

    def _table(self, obj: dict[str, Any], path: str, depth: int, label: str) -> None:
        """Shapes 1/2/6: ``{columns, rows}`` with list or object rows."""

        raw_rows = obj.get("rows") if isinstance(obj.get("rows"), list) else obj.get("data")
        keys: list[Any] = []
        declared: list[str | None] = []
        for column in obj.get("columns") or []:
            if isinstance(column, dict):
                keys.append(column.get("name") or column.get("key") or column.get("title"))
                kind = column.get("type")
                declared.append(kind if kind in _COLUMN_TYPES else None)
            else:
                keys.append(column)
                declared.append(None)
        rows: list[list[Any]] = []
        for row in raw_rows or []:
            if isinstance(row, dict):
                rows.append([row.get(str(key)) for key in keys])
            elif isinstance(row, list):
                rows.append(row)
            else:
                rows.append([row])
        self._add(
            path=path,
            label=label,
            names=keys,
            rows=rows,
            shape="table",
            depth=depth,
            declared=declared,
        )

    def _records(self, records: list[dict[str, Any]], path: str, depth: int, label: str) -> None:
        """Shape 3: ``[{col: val}, ...]``; columns in first-seen order."""

        names: list[str] = []
        for record in records[:MAX_FRAME_ROWS]:
            for key in record:
                text = str(key)
                if text not in names:
                    names.append(text)
        rows = [[record.get(name) for name in names] for record in records]
        self._add(path=path, label=label, names=names, rows=rows, shape="records", depth=depth)

    def _matrix(self, items: list[list[Any]], path: str, depth: int, label: str) -> None:
        """Shape 5: a 2-D array; a string-only first row is the header."""

        width = max((len(row) for row in items), default=0)
        first, rest = items[0], items[1:]
        has_header = (
            bool(first)
            and all(isinstance(cell, str) and cell.strip() for cell in first)
            and any(_is_number(cell) for row in rest for cell in row)
        )
        if has_header:
            names: list[Any] = list(first) + [f"c{index + 1}" for index in range(len(first), width)]
            body = rest
        else:
            names = [f"c{index + 1}" for index in range(width)]
            body = items
        self._add(
            path=path,
            label=label,
            names=names,
            rows=body,
            shape="matrix",
            depth=depth,
            synthetic_headers=not has_header,
        )

    def _scalars(self, mapping: dict[str, Scalar], path: str, depth: int, label: str) -> None:
        """Shapes 8/9: a flat ``{key: scalar}`` object."""

        values = list(mapping.values())
        if len(mapping) >= 2 and all(is_temporal_text(key) for key in mapping) and all(
            value is None or _is_number(value) for value in values
        ):
            self._add(
                path=path,
                label=label,
                names=["时间", "数值"],
                rows=[[key, value] for key, value in mapping.items()],
                shape="temporal_map",
                depth=depth,
                declared=["date", "number"],
            )
            return
        if len(mapping) <= MAX_SCALAR_GROUP:
            self._add(
                path=path,
                label=label,
                names=list(mapping),
                rows=[values],
                shape="scalar_group",
                depth=depth,
            )
            return
        self._add(
            path=path,
            label=label,
            names=["项目", "数值"],
            rows=[[key, value] for key, value in mapping.items()],
            shape="mapping",
            depth=depth,
        )


def _looks_like_demo_blocks(items: list[Any]) -> bool:
    return any(
        isinstance(item, dict) and ("value" in item or _is_table(item) or "data" in item)
        for item in items
    )


def _is_table(obj: dict[str, Any]) -> bool:
    if not isinstance(obj.get("columns"), list) or not obj.get("columns"):
        return False
    rows = obj.get("rows") if "rows" in obj else obj.get("data")
    return isinstance(rows, list)


def _is_named_wrapper(obj: dict[str, Any]) -> bool:
    nested = [item for item in obj.values() if not _is_scalar(item)]
    return bool(_label_of(obj)) and len(nested) == 1


def _wrapper_key(obj: dict[str, Any]) -> str | None:
    """Shape 10: ``{success, data}`` / ``{data}`` / ``{result}`` with ≤3 keys."""

    if len(obj) > 3:
        return None
    keys = [key for key in _WRAPPER_KEYS if isinstance(obj.get(key), (dict, list))]
    if len(keys) != 1:
        return None
    others = [item for key, item in obj.items() if key != keys[0]]
    return keys[0] if all(_is_scalar(item) for item in others) else None


def _columnar_keys(obj: dict[str, Any]) -> list[str]:
    """Shape 4: two or more equal-length arrays of plain values."""

    keys = [
        str(key)
        for key, item in obj.items()
        if isinstance(item, list) and item and all(_is_scalar(cell) for cell in item)
    ]
    if len(keys) < 2:
        return []
    if len({len(obj[key]) for key in keys}) != 1:
        return []
    return keys


def _stable_keys(records: list[dict[str, Any]]) -> bool:
    counts: dict[str, int] = {}
    for record in records:
        for key in record:
            counts[str(key)] = counts.get(str(key), 0) + 1
    threshold = len(records) / 2
    return any(count >= threshold for count in counts.values())


def normalize(data: Any) -> NormalizeResult:
    """Run the full walk; ``parse_data`` errors propagate as ``DashboardDataError``."""

    normalizer = _Normalizer()
    normalizer.visit(parse_data(data), "$", 0)
    return normalizer.result


def normalize_source(data: Any) -> tuple[list[Frame], list[DashboardWarning]]:
    result = normalize(data)
    return result.frames, result.warnings

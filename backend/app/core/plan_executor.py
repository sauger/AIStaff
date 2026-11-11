"""Execute a compiled plan without model roundtrips.

Serial by design (S3): the capability invoker shares one SQLAlchemy session and
is not thread safe. Parallel fan-out lands in S4 with per-thread sessions.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.plan_compiler import ExecutionPlan, PlanNode

_REFERENCE_KINDS = frozenset({"sandbox_json_file", "sandbox_text_file"})


class ReferenceError(ValueError):
    """A ``${...}`` reference could not be resolved."""


def _materialize_reference(
    payload: dict[str, Any],
    read_reference: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    """Inline a sandbox result pointer so later nodes can read its fields."""
    data = payload.get("data")
    if not isinstance(data, dict) or str(data.get("kind") or "") not in _REFERENCE_KINDS:
        return payload
    try:
        resolved = read_reference(data)
    except Exception:  # noqa: BLE001 - pointer stays unresolved, refs will fail clearly
        return payload
    if isinstance(resolved, list):
        resolved = {"items": resolved}
    materialized = dict(payload)
    materialized["data"] = resolved
    materialized["source_reference"] = {
        key: data.get(key) for key in ("kind", "sandbox_path", "size", "sha256")
    }
    return materialized


@dataclass
class NodeOutcome:
    node_id: str
    tool: str
    status: str = "ok"  # ok | failed | skipped | cancelled
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    duration_ms: int = 0


@dataclass
class PlanRun:
    outcomes: list[NodeOutcome] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.outcomes if item.status == "ok")


_MAX_FANOUT = 50


def _as_items(value: Any) -> list[Any]:
    """Coerce a resolved reference into the list a fan-out node iterates."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "organizations", "records", "rows", "results", "list"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        for child in value.values():
            if isinstance(child, list) and child and isinstance(child[0], (dict, list)):
                return child
            if isinstance(child, dict):
                nested = _as_items(child)
                if nested:
                    return nested
        return [value]
    return []


def _split_path(expression: str) -> list[str]:
    parts: list[str] = []
    token = ""
    for char in expression:
        if char in ".[]":
            if token:
                parts.append(token)
                token = ""
        else:
            token += char
    if token:
        parts.append(token)
    return parts


def _lookup_relaxed(value: Any, parts: list[str]) -> Any:
    """Best-effort lookup that treats unknown containers as transparent.

    Tool result shapes are not declared to the planner, so ``data.items[0].id``
    may really live at ``data.organizations[0].id``. Unknown keys are skipped
    instead of failing the whole node.
    """
    if not parts:
        return value
    head, rest = parts[0], parts[1:]
    if isinstance(value, dict):
        if head in value:
            return _lookup_relaxed(value[head], rest)
        for child in value.values():
            if isinstance(child, (dict, list)):
                try:
                    return _lookup_relaxed(child, rest)
                except ReferenceError:
                    continue
        raise ReferenceError(f"引用字段不存在：{head}")
    if isinstance(value, list):
        if head.isdigit():
            index = int(head)
            if index < 0 or index >= len(value):
                raise ReferenceError(f"列表下标越界：{index}")
            return _lookup_relaxed(value[index], rest)
        for child in value:
            try:
                return _lookup_relaxed(child, parts)
            except ReferenceError:
                continue
        raise ReferenceError(f"列表里找不到字段：{head}")
    raise ReferenceError(f"无法从 {type(value).__name__} 取字段 {head}")


def _lookup(expression: str, scope: dict[str, Any]) -> Any:
    parts = _split_path(expression)
    if not parts:
        raise ReferenceError(f"空引用：${{{expression}}}")
    root = parts[0]
    if root not in scope:
        raise ReferenceError(f"引用不存在：${{{expression}}}")
    value: Any = scope[root]
    try:
        for part in parts[1:]:
            if isinstance(value, dict):
                if part not in value:
                    raise ReferenceError(f"引用字段不存在：{part}")
                value = value[part]
            elif isinstance(value, list):
                try:
                    index = int(part)
                except ValueError as exc:
                    raise ReferenceError(f"列表下标不是数字：{part}") from exc
                if index < 0 or index >= len(value):
                    raise ReferenceError(f"列表下标越界：{index}")
                value = value[index]
            else:
                raise ReferenceError(f"无法从 {type(value).__name__} 取字段 {part}")
    except ReferenceError:
        return _lookup_relaxed(scope[root], parts[1:])
    return value


def _format_rows(value: list[Any]) -> str:
    """Render a list of flat dicts as a compact markdown table."""
    rows = [item for item in value if isinstance(item, dict)]
    if not rows:
        return ""
    columns = list(rows[0].keys())[:6]
    if not columns:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows[:20]:
        cells = []
        for column in columns:
            cell = _format_value(row.get(column, ""), limit=40).replace("\n", " ")
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _format_value(value: Any, *, limit: int = 2000) -> str:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        table = _format_rows(value)
        if table:
            return table
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def resolve_refs(value: Any, scope: dict[str, Any]) -> Any:
    """Resolve ``${node.path}`` placeholders recursively.

    A string that is exactly one reference keeps the referenced value's type;
    embedded references are stringified.
    """
    if isinstance(value, str):
        from app.core.plan_compiler import REF_PATTERN

        match = REF_PATTERN.fullmatch(value.strip())
        if match is not None:
            return _lookup(match.group(1), scope)
        if REF_PATTERN.search(value):
            def replace(found):
                return _format_value(_lookup(found.group(1), scope))

            return REF_PATTERN.sub(replace, value)
        return value
    if isinstance(value, dict):
        return {key: resolve_refs(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_refs(item, scope) for item in value]
    return value


class PlanExecutor:
    def __init__(
        self,
        invoke: Callable[[str, dict[str, Any]], dict[str, Any]],
        *,
        trace: Callable[[str, dict[str, Any]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        node_timeout: float = 30.0,
        budget_deadline: float | None = None,
        read_reference: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.invoke = invoke
        self.read_reference = read_reference
        self.trace = trace
        self.is_cancelled = is_cancelled
        self.node_timeout = max(0.0, float(node_timeout or 0.0))
        self.budget_deadline = budget_deadline

    def _events(self, event: str, payload: dict[str, Any]) -> None:
        if self.trace is not None:
            self.trace(event, payload)

    def _stopped(self) -> bool:
        cancelled = callable(self.is_cancelled) and self.is_cancelled()
        over_budget = (
            self.budget_deadline is not None
            and time.monotonic() >= self.budget_deadline
        )
        return bool(cancelled or over_budget)

    def run(self, plan: ExecutionPlan, state: dict[str, Any] | None = None) -> PlanRun:
        scope: dict[str, Any] = {"goal": dict((state or {}).get("goal") or {})}
        run = PlanRun()
        stopped = False
        for node in plan.nodes:
            if stopped or self._stopped():
                stopped = True
                run.outcomes.append(
                    NodeOutcome(
                        node_id=node.id,
                        tool=node.tool,
                        status="skipped",
                        error={"code": "PLAN_STOPPED", "message": "已取消或超出本轮预算"},
                    )
                )
                self._events(
                    "plan_node_finished",
                    {"node_id": node.id, "tool": node.tool, "status": "skipped"},
                )
                continue
            outcome = self._run_node(node, scope)
            run.outcomes.append(outcome)
            if outcome.status != "ok":
                stopped = True
                continue
            scope[node.id] = outcome.result
        return run

    def _run_node(self, node: PlanNode, scope: dict[str, Any]) -> NodeOutcome:
        if node.foreach:
            return self._run_fanout(node, scope)
        try:
            args = resolve_refs(dict(node.args or {}), scope)
        except ReferenceError as exc:
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="failed",
                error={"code": "REF_UNRESOLVED", "message": str(exc)},
            )
            self._events(
                "plan_node_finished",
                {
                    "node_id": node.id,
                    "tool": node.tool,
                    "status": outcome.status,
                    "error": outcome.error,
                },
            )
            return outcome
        self._events(
            "plan_node_started",
            {"node_id": node.id, "tool": node.tool},
        )
        started = time.monotonic()
        try:
            result = self.invoke(node.tool, args)
        except Exception as exc:  # noqa: BLE001 - tool boundary
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="failed",
                error={"code": "TOOL_EXCEPTION", "message": str(exc)[:300]},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            self._events(
                "plan_node_finished",
                {
                    "node_id": node.id,
                    "tool": node.tool,
                    "status": outcome.status,
                    "duration_ms": outcome.duration_ms,
                    "error": outcome.error,
                },
            )
            return outcome
        duration_ms = int((time.monotonic() - started) * 1000)
        payload = result if isinstance(result, dict) else {"data": result}
        success = bool(payload.get("success", True))
        if not success:
            error = dict(payload.get("error") or {})
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="failed",
                error=error or {"code": "TOOL_FAILED", "message": "工具执行失败"},
                duration_ms=duration_ms,
            )
        elif self.node_timeout and duration_ms > self.node_timeout * 1000:
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="failed",
                error={
                    "code": "PLAN_NODE_TIMEOUT",
                    "message": f"节点耗时 {duration_ms}ms 超过 {self.node_timeout}s 预算",
                },
                duration_ms=duration_ms,
            )
        else:
            if self.read_reference is not None:
                payload = _materialize_reference(payload, self.read_reference)
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="ok",
                result=payload,
                duration_ms=duration_ms,
            )
        self._events(
            "plan_node_finished",
            {
                "node_id": node.id,
                "tool": node.tool,
                "status": outcome.status,
                "duration_ms": outcome.duration_ms,
            },
        )
        return outcome


    def _run_fanout(self, node: PlanNode, scope: dict[str, Any]) -> NodeOutcome:
        """Run one node once per item of ``node.foreach`` and aggregate results."""
        try:
            source = resolve_refs(node.foreach.strip(), scope)
        except ReferenceError as exc:
            return self._fanout_failure(node, "FOREACH_UNRESOLVED", str(exc))
        items = _as_items(source)[: _MAX_FANOUT]
        if not items:
            return self._fanout_failure(
                node, "FOREACH_EMPTY", "foreach 引用的数组为空，无法执行"
            )
        self._events(
            "plan_node_started",
            {"node_id": node.id, "tool": node.tool, "count": len(items)},
        )
        started = time.monotonic()
        results: list[dict[str, Any]] = []
        failed = 0
        stopped = False
        for index, item in enumerate(items):
            if self._stopped():
                stopped = True
                break
            item_scope = dict(scope)
            item_scope["item"] = item
            item_scope["index"] = index
            try:
                args = resolve_refs(dict(node.args or {}), item_scope)
            except ReferenceError as exc:
                results.append(
                    {
                        "index": index,
                        "item": item,
                        "success": False,
                        "error": {"code": "REF_UNRESOLVED", "message": str(exc)},
                    }
                )
                failed += 1
                continue
            try:
                payload = self.invoke(node.tool, args)
            except Exception as exc:  # noqa: BLE001 - tool boundary
                results.append(
                    {
                        "index": index,
                        "item": item,
                        "success": False,
                        "error": {"code": "TOOL_EXCEPTION", "message": str(exc)[:300]},
                    }
                )
                failed += 1
                continue
            payload = payload if isinstance(payload, dict) else {"data": payload}
            success = bool(payload.get("success", True))
            if success and self.read_reference is not None:
                payload = _materialize_reference(payload, self.read_reference)
            if not success:
                failed += 1
            results.append(
                {
                    "index": index,
                    "item": item,
                    "success": success,
                    "data": payload.get("data"),
                    "error": payload.get("error"),
                }
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        limit_ms = int(self.node_timeout * max(1, len(results)) * 1000)
        if not results:
            return self._fanout_failure(node, "PLAN_STOPPED", "已取消或超出本轮预算")
        if failed >= len(results):
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="failed",
                error={
                    "code": "FANOUT_ALL_FAILED",
                    "message": f"{len(results)} 个元素全部失败",
                    "failed": failed,
                },
                duration_ms=duration_ms,
            )
        elif self.node_timeout and limit_ms and duration_ms > limit_ms:
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="failed",
                error={
                    "code": "PLAN_NODE_TIMEOUT",
                    "message": (
                        f"fan-out 耗时 {duration_ms}ms 超过 "
                        f"{self.node_timeout}s × {len(results)} 预算"
                    ),
                },
                duration_ms=duration_ms,
            )
        else:
            outcome = NodeOutcome(
                node_id=node.id,
                tool=node.tool,
                status="ok",
                result={
                    "success": failed == 0,
                    "data": {
                        "items": [item.get("data") for item in results],
                        "results": results,
                        "count": len(results),
                        "failed": failed,
                        "stopped_early": stopped,
                    },
                },
                duration_ms=duration_ms,
            )
        self._events(
            "plan_node_finished",
            {
                "node_id": node.id,
                "tool": node.tool,
                "status": outcome.status,
                "duration_ms": outcome.duration_ms,
                "count": len(results),
                "failed": failed,
            },
        )
        return outcome

    def _fanout_failure(self, node: PlanNode, code: str, message: str) -> NodeOutcome:
        outcome = NodeOutcome(
            node_id=node.id,
            tool=node.tool,
            status="failed",
            error={"code": code, "message": message},
        )
        self._events(
            "plan_node_finished",
            {
                "node_id": node.id,
                "tool": node.tool,
                "status": outcome.status,
                "error": outcome.error,
            },
        )
        return outcome


def synthesize_reply(plan: ExecutionPlan, outcomes: list[NodeOutcome]) -> str:
    """Resolve the reply template from node results (0 model calls)."""
    scope: dict[str, Any] = {"goal": {}}
    for outcome in outcomes:
        if outcome.status == "ok" and outcome.result is not None:
            scope[outcome.node_id] = outcome.result
    template = (plan.reply or "").strip()
    if template:
        try:
            resolved = resolve_refs(template, scope)
        except ReferenceError:
            return ""
        if isinstance(resolved, str):
            return resolved.strip()
        return _format_value(resolved).strip()
    successful = [item for item in outcomes if item.status == "ok" and item.result]
    if not successful:
        return ""
    if len(successful) == 1 and plan.answer.mode == "direct":
        data = successful[0].result.get("data") if isinstance(successful[0].result, dict) else None
        if isinstance(data, dict):
            for key in ("reply", "answer", "content", "text"):
                if isinstance(data.get(key), str) and data[key].strip():
                    return data[key].strip()
    return ""

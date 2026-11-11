"""Compiled execution: turn one conversation turn into a DAG of capability calls.

The ReAct loop asks the model once per action. This module asks it once for a
plan, validates the plan locally, and lets :mod:`plan_executor` run it without
further model roundtrips. Anything that cannot be validated falls back to the
existing loop.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from app.llm.client import LLMClient
from app.observability.spans import llm_operation

NODE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
REF_PATTERN = re.compile(r"\$\{([^{}]+)\}")
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "plan_compiler_prompt.md"

# Discovery / control capabilities never belong in a compiled plan: the planner
# already sees the full tool surface, and finish() is the reply template.
_FORBIDDEN_NODE_TOOLS = frozenset(
    {
        "capability_search",
        "capability_describe",
        "finish",
        "harness_requirement_check",
        "capability_list",
    }
)
_MAX_ANSWERS = 4000
_BARE_REF = re.compile(r"^\$\{[^{}]+\}$")


class PlanNode(BaseModel):
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    deps: list[str] = Field(default_factory=list)
    # Fan-out: one reference to an array; the node runs once per item and
    # aggregates into data.items. "${item.field}" reads the current element.
    foreach: str = ""
    purpose: str = ""


class PlanAnswer(BaseModel):
    mode: str = "text"
    hint: str = ""


class ExecutionPlan(BaseModel):
    nodes: list[PlanNode] = Field(default_factory=list)
    answer: PlanAnswer = Field(default_factory=PlanAnswer)
    # Reply template. May embed ${node_id.path} references; resolved after the
    # plan runs, never by the model.
    reply: str = ""


class PlanDecision(BaseModel):
    plan: ExecutionPlan | None = None
    error: dict[str, Any] | None = None
    repair_used: bool = False
    latency_ms: int = 0


def _tool_name_set(tools: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("name") or "") for item in tools}


_SIMPLE_REF = re.compile(r"^[A-Za-z0-9_.\[\]-]+$")


def _bad_refs(value: Any, found: list[str]) -> None:
    """Collect references that are not simple ``root.path[0]`` lookups."""
    if isinstance(value, str):
        for match in REF_PATTERN.finditer(value):
            expression = match.group(1).strip()
            if not _SIMPLE_REF.match(expression):
                found.append(expression[:60])
    elif isinstance(value, dict):
        for item in value.values():
            _bad_refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _bad_refs(item, found)


def _ref_roots(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        for match in REF_PATTERN.finditer(value):
            found.add(match.group(1).split(".")[0].split("[")[0])
    elif isinstance(value, dict):
        for item in value.values():
            _ref_roots(item, found)
    elif isinstance(value, list):
        for item in value:
            _ref_roots(item, found)


def validate_plan(
    plan: ExecutionPlan,
    *,
    allowed_tools: set[str],
    blocked_prefixes: list[str] | tuple[str, ...] = (),
    max_nodes: int = 12,
) -> list[str]:
    """Return human-readable problems; an empty list means the plan is runnable."""
    errors: list[str] = []
    if not plan.nodes:
        return ["计划没有任何节点"]
    if len(plan.nodes) > max_nodes:
        errors.append(f"节点数 {len(plan.nodes)} 超过上限 {max_nodes}")
    seen: list[str] = []
    for index, node in enumerate(plan.nodes):
        if not NODE_ID_PATTERN.match(node.id or ""):
            errors.append(f"第 {index + 1} 个节点 id 不合法：{node.id!r}")
        if node.id in seen:
            errors.append(f"节点 id 重复：{node.id}")
        if node.id == "goal":
            errors.append("节点 id 不能叫 goal")
        if node.tool in _FORBIDDEN_NODE_TOOLS:
            errors.append(f"节点 {node.id} 不能调用 {node.tool}")
        elif node.tool not in allowed_tools:
            errors.append(f"节点 {node.id} 的工具不可用：{node.tool}")
        for prefix in blocked_prefixes:
            if prefix and node.tool.startswith(str(prefix)):
                errors.append(f"节点 {node.id} 的工具被禁用：{node.tool}")
        for dep in node.deps:
            if dep not in seen:
                errors.append(f"节点 {node.id} 依赖了未定义或后置的节点：{dep}")
        roots: set[str] = set()
        _ref_roots(node.args, roots)
        if node.foreach:
            roots.discard("item")
            roots.discard("index")
            expression = node.foreach.strip()
            if not _BARE_REF.match(expression):
                errors.append(f"节点 {node.id} 的 foreach 必须是单个引用：{expression[:40]}")
            else:
                _ref_roots(expression, roots)
        for root in roots:
            if root != "goal" and root not in seen:
                errors.append(f"节点 {node.id} 引用了未知节点：{root}")
        seen.append(node.id)
    bad: list[str] = []
    for node in plan.nodes:
        _bad_refs(node.args, bad)
        if node.foreach:
            _bad_refs(node.foreach, bad)
    _bad_refs(plan.reply, bad)
    for expression in bad[:3]:
        errors.append(f"引用只能是 ${'{节点.字段}'} 形式，不能写表达式：{expression}")
    if not plan.reply.strip() and plan.answer.mode != "synthesize":
        errors.append("缺少 reply 回复模板（或把 answer.mode 设为 synthesize）")
    else:
        roots = set()
        _ref_roots(plan.reply, roots)
        for root in roots:
            if root != "goal" and root not in seen:
                errors.append(f"reply 引用了未知节点：{root}")
        if len(plan.reply) > _MAX_ANSWERS:
            errors.append("reply 模板过长")
    return errors


def _descriptor_read_only(descriptor: Any) -> bool:
    """Best-effort read-only check across manifest revisions.

    Older CapabilityDescriptor versions do not carry ``read_only``; fall back to
    metadata side-effect hints and the capability kind.
    """
    explicit = getattr(descriptor, "read_only", None)
    if isinstance(explicit, bool):
        return explicit
    metadata = getattr(descriptor, "metadata", None) or {}
    side_effect = str(metadata.get("side_effect") or "").strip().lower()
    if side_effect:
        return side_effect == "read"
    method = str(metadata.get("method") or "").strip().upper()
    if method:
        return method == "GET"
    return str(getattr(descriptor, "kind", "") or "").strip() in {
        "general_skill",
        "knowledge",
    }


def build_compiled_tools(
    manifest: Any,
    *,
    max_description_chars: int = 300,
    schema_budget_chars: int = 2000,
) -> list[dict[str, Any]]:
    """Compact, stable tool surface for the planner prompt (read-only only)."""
    tools: list[dict[str, Any]] = []
    for descriptor in getattr(manifest, "available", []) or []:
        name = str(getattr(descriptor, "name", "") or "").strip()
        if not name or name in _FORBIDDEN_NODE_TOOLS:
            continue
        if not getattr(descriptor, "available", True):
            continue
        if not _descriptor_read_only(descriptor):
            continue
        description = " ".join(str(getattr(descriptor, "description", "") or "").split())
        if len(description) > max_description_chars:
            description = description[: max_description_chars - 1] + "…"
        metadata = getattr(descriptor, "metadata", None) or {}
        required_skill = str(metadata.get("required_general_skill_slug") or "").strip()
        if required_skill:
            description = (
                f"{description}（需先规划 general_skill.{required_skill} 加载口径）"
            )
        schema = getattr(descriptor, "input_schema", None)
        parameters: dict[str, Any] = {"type": "object", "properties": {}}
        if isinstance(schema, dict):
            try:
                encoded = json.dumps(schema, ensure_ascii=False)
            except (TypeError, ValueError):
                encoded = ""
            if encoded and len(encoded) <= schema_budget_chars:
                parameters = schema
        tools.append(
            {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        )
    tools.sort(key=lambda item: item["name"])
    return tools


def _load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class PlanCompiler:
    """One model call (plus at most one repair) produces an ExecutionPlan."""

    def __init__(
        self,
        *,
        client_factory: Callable[..., Any] = LLMClient,
        max_nodes: int = 12,
        prompt_path: Path | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.max_nodes = max(1, int(max_nodes))
        self._prompt_override = prompt_path

    def _system_prompt(self) -> str:
        if self._prompt_override is not None:
            return self._prompt_override.read_text(encoding="utf-8").strip()
        return _load_prompt()

    def _payload(
        self,
        requirement: Any,
        tools: list[dict[str, Any]],
        state: dict[str, Any],
        *,
        repair: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "goal": requirement.goal,
            "requirements": list(requirement.requirements or []),
            "required_slots": list(requirement.required_slots or []),
            "known_slots": dict(requirement.known_slots or {}),
            "completion_criteria": list(requirement.completion_criteria or []),
            "current_time": requirement.current_time,
            "source_user_message": str(requirement.source_user_message or "")[:2000],
            "tools": tools,
            "prior_summary": str(state.get("prior_summary") or "")[:2000],
        }
        if repair is not None:
            payload["repair"] = repair
        return payload

    def _parse(self, data: Any) -> tuple[ExecutionPlan | None, list[str]]:
        if not isinstance(data, dict):
            return None, ["计划不是 JSON 对象"]
        candidate = data.get("plan") if isinstance(data.get("plan"), dict) else data
        try:
            plan = ExecutionPlan.model_validate(candidate)
        except PydanticValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            return None, [f"计划结构不合法：{first.get('msg', 'unknown')}"]
        return plan, []

    def compile(
        self,
        *,
        requirement: Any,
        model_config: Any,
        tools: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
        cancellation: Any = None,
    ) -> PlanDecision:
        state = state or {}
        allowed = _tool_name_set(tools)
        blocked = list(getattr(requirement, "blocked_tool_prefixes", []) or [])
        started = time.monotonic()
        errors: list[str] = ["计划解析失败"]
        repair_used = False
        prompt = self._system_prompt()
        for attempt in range(2):
            repair = {"errors": errors[:12]} if attempt else None
            payload = self._payload(requirement, tools, state, repair=repair)
            try:
                with llm_operation("harness.plan_compile", attempt=attempt + 1):
                    client = self.client_factory(model_config)
                    data = client.generate_json(prompt, payload, cancellation)
            except Exception as exc:  # noqa: BLE001 - fallback path
                return PlanDecision(
                    error={"code": "PLAN_LLM_FAILED", "message": str(exc)[:300]},
                    repair_used=repair_used,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            plan, parse_errors = self._parse(data)
            if plan is None:
                errors = parse_errors
                continue
            errors = validate_plan(
                plan,
                allowed_tools=allowed,
                blocked_prefixes=blocked,
                max_nodes=self.max_nodes,
            )
            if not errors:
                return PlanDecision(
                    plan=plan,
                    repair_used=repair_used,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            repair_used = True
        return PlanDecision(
            error={"code": "PLAN_INVALID", "message": "; ".join(errors)[:300]},
            repair_used=repair_used,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

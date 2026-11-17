from __future__ import annotations

import logging
import re
from typing import Any

from app.core.capability_discovery import model_descriptor
from app.core.slash_commands import require_capability_for_requirement
from app.core.task_request_compiler import CapabilityManifest, TaskRequirement

logger = logging.getLogger(__name__)

_REFUSE_MODE = "refuse"


def apply_general_skill_routes(
    requirement: TaskRequirement,
    projected_manifest: CapabilityManifest,
    full_manifest: CapabilityManifest,
    routes: object,
) -> list[str]:
    """Apply configured TokenOp-style skill routes to one compiled requirement.

    ``routes`` is stored in ``AgentProfile.metadata_json`` so it can be changed
    through the agent configuration API without a schema migration.  The full
    manifest is used only to find the skill descriptor; the projected manifest
    and requirement remain the model-facing authorization view.
    """

    if not isinstance(routes, list) or not routes:
        return []
    text = _route_text(requirement)
    matches = _matching_routes(routes, text)
    if not matches:
        return []
    # A policy refusal is terminal for routing: do not also activate ordinary
    # data-gathering skills, and remove the tokenop data tools from the model view.
    refusal = next((route for route in matches if _mode(route) == _REFUSE_MODE), None)
    # Prefer the single highest-priority normal route; a later tool that needs
    # another skill will be stopped by the invoker's required-skill guard and
    # can then load that skill explicitly.  Refusal is always terminal.
    selected = [refusal] if refusal is not None else matches[:1]
    applied: list[str] = []
    full_by_name = {item.name: item for item in full_manifest.available}
    for route in selected:
        for skill_name in _route_skill_names(route):
            descriptor = full_by_name.get(skill_name)
            if descriptor is None:
                logger.warning("general skill route references missing skill: %s", skill_name)
                continue
            require_capability_for_requirement(
                requirement,
                projected_manifest,
                descriptor,
                completion_criterion=(
                    route.get("completion_criterion")
                    or f"加载并遵守技能：{model_descriptor(descriptor).name}"
                ),
            )
            applied.append(descriptor.name)
        if route is refusal:
            _remove_data_tools(requirement)
            if projected_manifest is not requirement.capability_manifest:
                _remove_data_tools_from_manifest(projected_manifest)
            blocked = str(route.get("blocked_tool_prefix") or "tokenop.").strip()
            if blocked:
                requirement.blocked_tool_prefixes = _unique(
                    [*requirement.blocked_tool_prefixes, blocked]
                )
            reason = str(route.get("reason") or "").strip()
            requirement.completion_criteria = _unique(
                [
                    *requirement.completion_criteria,
                    (
                        "该请求触发 TokenOp 口径/权限禁令，必须直接拒绝并解释；"
                        "不得输出跨口径合计、净额或毛利数字。"
                        + (f"（{reason}）" if reason else "")
                    ),
                ]
            )
    return applied


def _route_text(requirement: TaskRequirement) -> str:
    parts = [
        requirement.goal,
        requirement.source_user_message,
        *requirement.requirements,
        *requirement.completion_criteria,
    ]
    return "\n".join(str(part or "") for part in parts if str(part or "").strip())


def _matching_routes(routes: list[Any], text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for item in routes:
        if isinstance(item, dict):
            parsed.append(item)
    parsed.sort(key=lambda item: _priority(item), reverse=True)
    matched: list[dict[str, Any]] = []
    for route in parsed:
        patterns = route.get("patterns") or route.get("keywords") or []
        if not isinstance(patterns, list):
            continue
        if any(_pattern_matches(pattern, text) for pattern in patterns):
            matched.append(route)
    return matched


def _pattern_matches(pattern: object, text: str) -> bool:
    value = str(pattern or "").strip()
    if not value:
        return False
    try:
        return re.search(value, text, flags=re.IGNORECASE | re.DOTALL) is not None
    except re.error:
        return value.lower() in text.lower()


def _route_skill_names(route: dict[str, Any]) -> list[str]:
    raw = route.get("skills") or route.get("skill") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if not value:
            continue
        name = value if value.startswith("general_skill.") else f"general_skill.{value}"
        if name not in names:
            names.append(name)
    return names


def _remove_data_tools(requirement: TaskRequirement) -> None:
    _remove_data_tools_from_manifest(requirement.capability_manifest)
    requirement.required_capability_names = [
        name
        for name in requirement.required_capability_names
        if not _is_tokenop_data_tool(name)
    ]


def _remove_data_tools_from_manifest(manifest: CapabilityManifest) -> None:
    manifest.available = [
        item for item in manifest.available if not _is_tokenop_data_tool(item.name)
    ]
    manifest.catalog = [
        item for item in manifest.catalog if not _is_tokenop_data_tool(item.name)
    ]


def _is_tokenop_data_tool(name: str) -> bool:
    return str(name or "").startswith("tokenop.")


def _mode(route: dict[str, Any]) -> str:
    return str(route.get("mode") or "normal").strip().lower()


def _priority(route: dict[str, Any]) -> int:
    try:
        return int(route.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = ["apply_general_skill_routes"]

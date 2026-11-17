"""Compile validated ChangePlans into apply_screen_operations batches.

Op shapes, ``UPDATE_ALLOWED_KEYS`` and the per-type props whitelist mirror
ai-screen's ``app/domain/operations.py`` and the apply tool's JSON schema
(``apps/api/app/mcp_inbound/tools.py``). When ai-screen tightens its schema,
this table must follow — the MCP tool remains the final authority.
"""

from __future__ import annotations

import re
from typing import Any

# ai-screen domain/operations.py:26
COMPONENT_TYPES = {"text", "metric", "line", "bar", "pie", "table", "shape"}
STYLE_KEYS = {"background", "borderColor", "borderWidth", "borderRadius", "opacity"}
STYLE_ALIASES = {"backgroundColor": "background", "bgColor": "background"}

MIN_SIZES = {
    "table": (320, 160),
    "line": (240, 160),
    "bar": (240, 160),
    "pie": (240, 160),
    "metric": (160, 80),
    "text": (40, 24),
    "shape": (1, 1),
}


def normalize_added_component(component_id: str, component: dict[str, Any]) -> dict[str, Any]:
    """Fill storage-time defaults required by ai-screen's Component model.

    Kept deliberately small and deterministic: the pack owns the wire shape,
    the plan LLM only chooses type/name/frame/props.
    """

    item = dict(component)
    item.pop("componentId", None)
    ctype = str(item.get("type") or "")
    item.setdefault("componentVersion", 1)
    item.setdefault("name", component_id)
    props = dict(item.get("props") or {})
    if ctype == "text":
        props.setdefault("text", "")
    elif ctype == "metric":
        props.setdefault("title", str(item.get("name") or component_id))
    elif ctype in {"line", "bar"}:
        props.setdefault("title", str(item.get("name") or component_id))
        props.setdefault("showLegend", False)
        if ctype == "bar":
            props.setdefault("orientation", "vertical")
    elif ctype == "pie":
        props.setdefault("title", str(item.get("name") or component_id))
        props.setdefault("showLegend", False)
        props.setdefault("innerRadius", 0)
    elif ctype == "table":
        props.setdefault("title", str(item.get("name") or component_id))
        columns = props.get("columns")
        if not (
            isinstance(columns, list)
            and columns
            and all(
                isinstance(item, str) and re.fullmatch(r"fld_[a-z0-9_]+", item)
                for item in columns
            )
        ):
            return {}
    elif ctype == "shape":
        props.setdefault("kind", "rectangle")
        props.setdefault("fill", "#FFFFFF")
    if ctype == "text":
        weights = {"normal": 400, "medium": 500, "semibold": 600, "bold": 700}
        raw_weight = props.get("fontWeight")
        if isinstance(raw_weight, str):
            props["fontWeight"] = weights.get(raw_weight.lower(), 400)
        elif raw_weight is not None and raw_weight not in {400, 500, 600, 700}:
            props["fontWeight"] = 400
        raw_size = props.get("fontSize")
        if raw_size is not None:
            try:
                props["fontSize"] = max(12, min(200, int(raw_size)))
            except (TypeError, ValueError):
                props.pop("fontSize", None)
    allowed_props = PROPS_WHITELIST.get(ctype)
    if allowed_props is not None:
        props = {key: value for key, value in props.items() if key in allowed_props}
    if ctype == "bar":
        props["orientation"] = (
            "horizontal" if props.get("orientation") == "horizontal" else "vertical"
        )
    if ctype in {"line", "bar", "pie"}:
        props["showLegend"] = bool(props.get("showLegend", False))
    if ctype == "pie":
        try:
            props["innerRadius"] = max(0, min(80, int(props.get("innerRadius") or 0)))
        except (TypeError, ValueError):
            props["innerRadius"] = 0
    for color_key in ("color",):
        value = props.get(color_key)
        if value is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(value)):
            props.pop(color_key, None)
    series = props.get("seriesColors")
    if series is not None and not (
        isinstance(series, list)
        and series
        and all(re.fullmatch(r"#[0-9a-fA-F]{6}", str(item)) for item in series)
    ):
        props.pop("seriesColors", None)
    item["props"] = props
    style = {}
    for key, value in dict(item.get("style") or {}).items():
        key = STYLE_ALIASES.get(key, key)
        if key in STYLE_KEYS:
            style[key] = value
    item["style"] = style
    item.setdefault("binding", None)
    item.setdefault("locked", False)
    item.setdefault("visible", True)
    frame = item.get("frame")
    if isinstance(frame, dict):
        min_w, min_h = MIN_SIZES.get(ctype, (1, 1))
        frame = dict(frame)
        frame["width"] = max(int(frame.get("width") or 0), min_w)
        frame["height"] = max(int(frame.get("height") or 0), min_h)
        item["frame"] = frame
    return item

UPDATE_ALLOWED_KEYS = {"name", "frame", "props", "style", "visible"}

# ai-screen mcp_inbound/tools.py apply_screen_operations → changes.props
PROPS_WHITELIST: dict[str, set[str]] = {
    "text": {"text", "fontSize", "fontWeight", "align", "color"},
    "metric": {"title", "prefix", "suffix", "decimals"},
    "line": {"title", "showLegend"},
    "bar": {"title", "showLegend"},
    "pie": {"title", "showLegend"},
    "table": {"title", "pageSize"},
}

CHANGE_KINDS = {"update_component", "change_component_type", "add_components", "bind_component"}

_AGGREGATES = {"sum", "avg", "min", "max", "count"}


def binding_structure_error(binding: Any) -> str:
    """Structural check for a component binding; empty string means valid.

    One chart, one datasetVersionId: dimensions are fieldId strings and each
    measure is {fieldId, aggregate, alias}. Field membership in the dataset is
    checked separately against the describe_dataset catalog in plan_change.
    """

    if not isinstance(binding, dict):
        return "binding 必须是 object。"
    if not str(binding.get("datasetVersionId") or "").strip():
        return "binding.datasetVersionId 不能为空。"
    dimensions = binding.get("dimensions")
    if not isinstance(dimensions, list) or not all(
        isinstance(item, str) and item for item in dimensions
    ):
        return "binding.dimensions 必须是 fieldId 字符串数组。"
    measures = binding.get("measures")
    if not isinstance(measures, list) or not measures:
        return "binding.measures 不能为空。"
    for measure in measures:
        if not isinstance(measure, dict):
            return "binding.measures 的每一项必须是 object。"
        if not str(measure.get("fieldId") or "").strip():
            return "binding.measures[].fieldId 不能为空。"
        if measure.get("aggregate") not in _AGGREGATES:
            return "binding.measures[].aggregate 必须是 " + "/".join(sorted(_AGGREGATES)) + "。"
        if not str(measure.get("alias") or "").strip():
            return "binding.measures[].alias 不能为空。"
    return ""


def _component_types_by_id(components: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(item.get("componentId") or ""): str(item.get("type") or "")
        for item in components
        if isinstance(item, dict)
    }


def _binding_for_operation(
    value: Any,
    *,
    components: list[dict[str, Any]],
    component_id: str,
) -> dict[str, Any] | None:
    """Complete a plan binding with the resolved component's immutable fields.

    The LLM only authors datasetVersionId/dimensions/measures; the MCP
    Component schema also requires ``source`` (and benefits from the old
    limit/nullPolicy/timeZone). Resolve is the authority, so merge its binding
    underneath the planned mapping instead of sending a partial object.
    """
    if not isinstance(value, dict):
        return None
    merged: dict[str, Any] = {}
    for item in components:
        if str(item.get("componentId") or "") != component_id:
            continue
        old = item.get("binding")
        if isinstance(old, dict):
            merged.update(old)
        break
    merged.update({key: item for key, item in value.items() if item is not None})
    merged["source"] = str(merged.get("source") or "dataset")
    return merged


def compile_operations(
    plan: dict[str, Any],
    *,
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn a validated ChangePlan into one apply batch (ops only, no envelope)."""

    change_kind = str(plan.get("change_kind") or "")
    targets = [str(item) for item in plan.get("target_component_ids") or []]
    patches = plan.get("patches") if isinstance(plan.get("patches"), dict) else {}
    if change_kind == "update_component":
        operations: list[dict[str, Any]] = []
        for component_id in targets:
            patch = patches.get(component_id)
            if not isinstance(patch, dict):
                continue
            changes = {
                key: value
                for key, value in patch.items()
                if key in UPDATE_ALLOWED_KEYS
            }
            if not changes:
                continue
            operations.append(
                {"op": "update_component", "componentId": component_id, "changes": changes}
            )
        return operations
    if change_kind == "change_component_type":
        component_id = targets[0]
        operation: dict[str, Any] = {
            "op": "change_component_type",
            "componentId": component_id,
            "componentType": str(plan.get("componentType") or ""),
        }
        if plan.get("binding") is not None:
            operation["binding"] = _binding_for_operation(
                plan["binding"], components=components, component_id=component_id
            )
        patch = patches.get(component_id)
        props = patch.get("props") if isinstance(patch, dict) else None
        if isinstance(props, dict) and props:
            operation["props"] = dict(props)
        return [operation]
    if change_kind == "bind_component":
        # 改数据：只替换目标组件的一个数据集绑定（现有 MCP op）。
        return [
            {
                "op": "bind_component",
                "componentId": targets[0],
                "binding": _binding_for_operation(
                    plan.get("binding"), components=components, component_id=targets[0]
                )
                or {},
            }
        ]
    if change_kind == "add_components":
        declared = plan.get("components") if isinstance(plan.get("components"), dict) else {}
        operations = []
        for component_id in targets:
            component = declared.get(component_id)
            if not isinstance(component, dict):
                continue
            if str(component.get("type") or "") not in COMPONENT_TYPES:
                continue
            component = normalize_added_component(component_id, component)
            if not component:
                continue
            operations.append(
                {
                    "op": "add_component",
                    "componentId": component_id,
                    "component": component,
                }
            )
        return operations
    return []


def validate_change_plan(
    plan: Any,
    *,
    bind: dict[str, Any],
    components: list[dict[str, Any]],
) -> tuple[str, str, list[str]]:
    """Validate a ChangePlan against the bound target set (task plan A4.4).

    Returns ``(code, message, ask_fields)``; an empty code means valid.
    Out-of-scope targets are answerable by the user (ask once, never retry the
    write); shape violations are hard failures.
    """

    if not isinstance(plan, dict):
        return ("CHANGE_PLAN_INVALID", "ChangePlan 必须是 object。", [])
    change_kind = str(plan.get("change_kind") or "")
    if change_kind not in CHANGE_KINDS:
        return (
            "CHANGE_PLAN_INVALID",
            f"change_kind 必须是 {'/'.join(sorted(CHANGE_KINDS))}，实际为 {change_kind or '<空>'}。",
            [],
        )
    targets = plan.get("target_component_ids")
    if not isinstance(targets, list) or not targets:
        return ("CHANGE_PLAN_INVALID", "target_component_ids 必须是非空数组。", [])
    targets = [str(item) for item in targets]
    if len(set(targets)) != len(targets):
        return ("CHANGE_PLAN_INVALID", "target_component_ids 不能重复。", [])
    patches = plan.get("patches")
    if patches is not None and not isinstance(patches, dict):
        return ("CHANGE_PLAN_INVALID", "patches 必须是 object。", [])

    mode = str(bind.get("mode") or "")
    known_ids = set(_component_types_by_id(components))
    if mode == "edit":
        bound_targets = {str(item) for item in bind.get("targets") or []}
        out_of_scope = [item for item in targets if item not in bound_targets]
        if out_of_scope:
            return (
                "CHANGE_PLAN_OUT_OF_SCOPE",
                "计划要修改的组件不在绑定目标集合内："
                + "、".join(out_of_scope)
                + "。目标集合外零改动，请确认要修改哪个组件。",
                ["target_component"],
            )
        missing_existing = [item for item in targets if item not in known_ids]
        if missing_existing:
            return (
                "CHANGE_PLAN_OUT_OF_SCOPE",
                "绑定目标在当前大屏上已不存在："
                + "、".join(missing_existing)
                + "。请重新选中要修改的组件。",
                ["target_component"],
            )
        if change_kind == "change_component_type" and len(targets) != 1:
            return (
                "CHANGE_PLAN_INVALID",
                "change_component_type 一次只能转换一个组件。",
                [],
            )
        if change_kind == "bind_component":
            if len(targets) != 1:
                return (
                    "CHANGE_PLAN_INVALID",
                    "bind_component 一次只能改一个组件的数据绑定。",
                    [],
                )
            error = binding_structure_error(plan.get("binding"))
            if error:
                return ("CHANGE_PLAN_INVALID", error, [])
        if change_kind == "update_component":
            missing_patches = [item for item in targets if not isinstance((patches or {}).get(item), dict)]
            if missing_patches:
                return (
                    "CHANGE_PLAN_INVALID",
                    "patches 缺少目标组件的补丁：" + "、".join(missing_patches) + "。",
                    [],
                )
    elif mode == "fill":
        if change_kind != "add_components":
            return (
                "CHANGE_PLAN_INVALID",
                "空白屏生成只能使用 add_components。",
                [],
            )
        existing = [item for item in targets if item in known_ids]
        if existing:
            return (
                "CHANGE_PLAN_OUT_OF_SCOPE",
                "空白屏生成使用了已存在的 componentId：" + "、".join(existing) + "。",
                [],
            )
        declared = plan.get("components")
        if isinstance(declared, dict):
            missing_decl = [item for item in targets if not isinstance(declared.get(item), dict)]
            if missing_decl:
                return (
                    "CHANGE_PLAN_INVALID",
                    "add_components 缺少组件定义：" + "、".join(missing_decl) + "。",
                    [],
                )
            for component_id in targets:
                component = declared.get(component_id)
                if not isinstance(component.get("type"), str) or not component.get("type"):
                    return (
                        "CHANGE_PLAN_INVALID",
                        f"新增组件 {component_id} 缺少 type。",
                        [],
                    )
                if component.get("type") not in COMPONENT_TYPES:
                    return (
                        "CHANGE_PLAN_INVALID",
                        f"新增组件 {component_id} 的 type 不在支持列表内："
                        + "、".join(sorted(COMPONENT_TYPES))
                        + "。",
                        [],
                    )
                if not isinstance(component.get("frame"), dict):
                    return (
                        "CHANGE_PLAN_INVALID",
                        f"新增组件 {component_id} 缺少 frame 对象。",
                        [],
                    )

    if isinstance(patches, dict):
        stray = sorted(set(patches) - set(targets))
        if stray:
            return (
                "CHANGE_PLAN_INVALID",
                "patches 的键必须在 targets 内：" + "、".join(stray) + "。",
                [],
            )
        types_by_id = _component_types_by_id(components)
        for component_id, patch in patches.items():
            if not isinstance(patch, dict):
                return ("CHANGE_PLAN_INVALID", f"patches.{component_id} 必须是 object。", [])
            extra = sorted(set(patch) - UPDATE_ALLOWED_KEYS)
            if extra:
                return (
                    "CHANGE_PLAN_INVALID",
                    f"patches.{component_id} 含不允许的字段：" + "、".join(extra) + "。",
                    [],
                )
            props = patch.get("props")
            if isinstance(props, dict):
                component_type = str(
                    plan.get("componentType")
                    if change_kind == "change_component_type"
                    else types_by_id.get(str(component_id), "")
                )
                allowed_props = PROPS_WHITELIST.get(component_type)
                if allowed_props is not None:
                    bad_props = sorted(set(props) - allowed_props)
                    if bad_props:
                        return (
                            "CHANGE_PLAN_INVALID",
                            f"组件 {component_id}（{component_type}）的 props 白名单外字段："
                            + "、".join(bad_props)
                            + "。",
                            [],
                        )
    if change_kind == "change_component_type" and not str(
        plan.get("componentType") or ""
    ):
        return ("CHANGE_PLAN_INVALID", "change_component_type 需要 componentType。", [])
    if plan.get("binding") is not None and change_kind in {
        "change_component_type",
        "bind_component",
    }:
        error = binding_structure_error(plan.get("binding"))
        if error:
            return ("CHANGE_PLAN_INVALID", error, [])
    if change_kind == "add_components":
        declared = plan.get("components")
        if not isinstance(declared, dict):
            return ("CHANGE_PLAN_INVALID", "add_components 需要 components 对象表。", [])
        missing = [item for item in targets if not isinstance(declared.get(item), dict)]
        if missing:
            return (
                "CHANGE_PLAN_INVALID",
                "add_components 缺少组件定义：" + "、".join(missing) + "。",
                [],
            )
    return ("", "", [])

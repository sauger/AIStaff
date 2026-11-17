"""plan_change: one structured LLM call that produces a validated ChangePlan.

Contract (task plan A4.3/A4.4): input is a projection of the bound screen and
the user's message, output is structured JSON; targets outside the bound set
fail with a follow-up question and are never retried as writes.

Shape errors get exactly one repair attempt. Out-of-scope targets do not:
they are answerable by the user, so the runtime asks once instead of letting
the model retry a write.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.session.turn_context import ScreenTurnContext
from app.workflow_packs.screen.draft_intent import (
    INTENT_SYSTEM_PROMPT,
    DraftIntent,
    IntentSlot,
    Operation,
    build_intent,
    parse_model_intent,
    projected_catalog,
)
from app.workflow_packs.screen.understanding import (
    UNDERSTAND_PROMPT,
    component_slots,
    find_field,
    is_numeric,
    requested_fields,
    template_for_text,
    template_slots,
)
from app.workflow_packs.screen.operations import compile_operations, validate_change_plan
from app.workflows.registry import StructuredStepError, register_structured_step

LlmGenerate = Callable[[str, dict[str, Any]], dict[str, Any]]

PLAN_SYSTEM_PROMPT = (
    "你是大屏变更计划生成器。只输出一个 JSON object，符合给定的 output_schema，"
    "不要输出解释。target_component_ids 只能从 bind.targets（编辑模式）中选取；"
    "空白屏生成（fill 模式）则产出新增组件，使用新的 componentId 并给出完整组件对象。"
    "props 只允许各组件类型白名单内的字段；props.title 是显示标题字符串，"
    "不是字段绑定对象，禁止输出 {\"field\": ...}。"
    "改数据（换数据集/换字段映射）用 change_kind=bind_component，顶层 binding 给出完整新绑定；"
    "改图表类型可用顶层 binding 给出映射（fieldId 必须来自 field_catalog 的 fieldId，"
    "一张图只用一个 datasetVersionId）；用户回答字段映射时按 field_catalog 把名称映射为 fieldId。"
    "不要输出 screenId、revision、requestId 或 operations；这些由运行时填写。"
)

_ALLOWED_PLAN_KEYS = {
    "change_kind",
    "target_component_ids",
    "patches",
    "componentType",
    "components",
    "binding",
    "summary",
}

CHANGE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "change_kind": {
            "type": "string",
            "enum": ["update_component", "change_component_type", "add_components", "bind_component"],
        },
        "target_component_ids": {"type": "array", "items": {"type": "string"}},
        "patches": {
            "type": "object",
            "description": "update/change 时按 componentId 给出 name/frame/props/style 补丁",
        },
        "componentType": {"type": "string", "description": "change_component_type 必填"},
        "binding": {
            "type": "object",
            "description": (
                "bind_component 必填；change_component_type 可选。"
                "{datasetVersionId, dimensions(fieldId[]), measures[{fieldId,aggregate,alias}]}，"
                "fieldId 必须来自 field_catalog"
            ),
        },
        "components": {
            "type": "object",
            "description": "add_components 时按 componentId 给出完整组件对象",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["text", "metric", "line", "bar", "pie", "table", "shape"],
                    }
                },
                "required": ["type"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["change_kind", "target_component_ids"],
}


def _normalize_prop_aliases(
    plan: dict[str, Any], components: list[dict[str, Any]]
) -> None:
    """Map model-friendly prop names onto the domain whitelist.

    The planner sometimes writes a metric's display text as ``props.metric``
    because the component type is "metric"; the domain calls that ``title``.
    """

    types = {
        str(item.get("componentId")): str(item.get("type") or "")
        for item in components
        if isinstance(item, dict)
    }
    patches = plan.get("patches")
    if not isinstance(patches, dict):
        return
    for component_id, patch in patches.items():
        if not isinstance(patch, dict):
            continue
        props = patch.get("props")
        if not isinstance(props, dict):
            continue
        if types.get(str(component_id)) == "metric" and "metric" in props:
            title = props.pop("metric")
            props.setdefault("title", title)
        title = props.get("title")
        if isinstance(title, dict):
            label = title.get("label") or patch.get("name")
            if isinstance(label, str) and label.strip():
                props["title"] = label.strip()
            else:
                props.pop("title", None)



def _binding_view(value: Any) -> dict[str, Any] | None:
    """Binding summary from the resolve/get_screen result (the authority)."""
    if not isinstance(value, dict):
        return None
    view = {
        "datasetVersionId": value.get("datasetVersionId"),
        "dimensions": list(value.get("dimensions") or []),
        "measures": list(value.get("measures") or []),
    }
    return view if view["datasetVersionId"] else None


def _projection(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    resolve_data: dict[str, Any],
    field_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    components = []
    for item in resolve_data.get("components") or []:
        if not isinstance(item, dict):
            continue
        projected = {
            "componentId": item.get("componentId"),
            "name": item.get("name"),
            "type": item.get("type"),
            "binding": _binding_view(item.get("binding")),
        }
        frame = item.get("frame")
        if isinstance(frame, dict) and {"x", "y", "width", "height"} <= set(frame):
            projected["frame"] = {key: frame.get(key) for key in ("x", "y", "width", "height")}
        components.append(projected)
    return {
        "user_message": user_message,
        "bind": {
            "mode": bind.get("mode"),
            "screen_id": bind.get("screen_id"),
            "revision": bind.get("revision"),
            "component_count": bind.get("component_count"),
            "targets": list(bind.get("targets") or []),
            "pending_edit": bind.get("pending_edit"),
        },
        "screen": {
            "name": resolve_data.get("name"),
            "revision": resolve_data.get("revision"),
            "components": components,
        },
        "field_catalog": field_catalog,
        "selected": (
            [
                {
                    "component_id": item.component_id,
                    "name": item.name,
                    "component_type": item.component_type,
                }
                for item in turn_context.selected
            ]
            if turn_context is not None
            else []
        ),
    }


_CHART_TYPES = {"line", "bar", "pie"}


def _field_options(field_catalog: list[dict[str, Any]]) -> str:
    names = []
    for field in field_catalog:
        if isinstance(field, dict) and field.get("name"):
            names.append(f"{field.get('name')}（{field.get('fieldId')}）")
    return "、".join(names[:12])


def _missing_mapping_check(
    plan: dict[str, Any],
    *,
    resolve_components: list[dict[str, Any]],
    targets: list[str],
    field_catalog: list[dict[str, Any]],
    pending: dict[str, Any] | None,
) -> tuple[str, str, list[str], dict[str, Any] | None]:
    """Deterministic field-mapping gate for chart type changes (design §6.3).

    Returns ``(code, message, ask_fields, pending_edit)``; empty code means the
    plan may proceed. The question lists real field names from the catalog and
    carries pending_edit for the next turn. With a pending_edit already in
    flight, an unmapped plan fails once without re-asking.
    """

    if plan.get("change_kind") != "change_component_type":
        return ("", "", [], None)
    new_type = str(plan.get("componentType") or "")
    if new_type not in _CHART_TYPES:
        return ("", "", [], None)
    target_id = targets[0] if targets else ""
    old = next(
        (
            item
            for item in resolve_components
            if isinstance(item, dict) and str(item.get("componentId")) == target_id
        ),
        None,
    )
    old_binding = _binding_view((old or {}).get("binding"))
    effective = (
        _binding_view(plan.get("binding")) if plan.get("binding") is not None else old_binding
    )
    if effective is not None and effective["dimensions"] and effective["measures"]:
        return ("", "", [], None)
    # A table's columns can be derived by the MCP tool; not a missing mapping.
    if plan.get("binding") is None and old is not None and str(old.get("type")) == "table":
        return ("", "", [], None)
    options = _field_options(field_catalog)
    dataset_version_id = ""
    if effective is not None:
        dataset_version_id = str(effective["datasetVersionId"])
    elif old_binding is not None:
        dataset_version_id = str(old_binding["datasetVersionId"])
    if pending is not None:
        # The mapping question was already asked once and the answer did not
        # produce a usable binding: do not write, do not ask again.
        return (
            "FIELD_MAPPING_UNRECOGNIZED",
            "没认出这轮回答里的字段，这次没有改动大屏。请直接说字段名（如「用地区做分类，合同额做数值」）再试。",
            [],
            None,
        )
    pending_edit = {
        "target_component_id": target_id,
        "component_type": new_type,
        "dataset_version_id": dataset_version_id,
        "goal": str(plan.get("summary") or f"改为{new_type}"),
    }
    question = (
        f"「{new_type}」需要一个分类字段和一个数值字段。当前绑定里缺这项映射。"
        f"用哪个字段做分类、哪个做数值？可选字段：{options or '（字段目录为空）'}"
    )
    return ("FIELD_MAPPING_MISSING", question, ["field_mapping"], pending_edit)


def _shape_errors(plan: dict[str, Any]) -> str:
    extra = sorted(set(plan) - _ALLOWED_PLAN_KEYS)
    if extra:
        return "输出含不允许的顶层字段：" + "、".join(extra) + "。"
    return ""


@register_structured_step("plan_change")
def plan_change(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    slots: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
    llm: LlmGenerate,
) -> dict[str, Any]:
    _ = slots
    # The binder names the resolve node it routed ("resolve" or the bound
    # spine's "resolve_bound"); both write a get_screen result.
    screen_step = (
        str(bind.get("resolve_step") or "").strip()
        or str(metadata.get("screen_step") or "resolve").strip()
        or "resolve"
    )
    resolve_data = dict((outputs.get(screen_step) or {}).get("data") or {})
    describe_data = (outputs.get("describe_fields") or {}).get("data") or {}
    field_catalog = [
        item
        for item in (describe_data.get("fields") or [])
        if isinstance(item, dict)
    ]
    pending = bind.get("pending_edit")
    pending = dict(pending) if isinstance(pending, dict) else None
    projection = _projection(
        user_message=user_message,
        turn_context=turn_context,
        bind=bind,
        resolve_data=resolve_data,
        field_catalog=field_catalog,
    )
    instruction = str(metadata.get("instruction") or "")
    last_code = "CHANGE_PLAN_INVALID"
    last_message = "计划未通过校验。"
    for attempt in range(2):
        raw = llm(
            PLAN_SYSTEM_PROMPT,
            {
                "phase": "WorkflowStructuredStep",
                "handler": "plan_change",
                "projection": projection,
                "instruction": instruction,
                "output_schema": CHANGE_PLAN_SCHEMA,
                "repair": last_message if attempt else "",
            },
        )
        if not isinstance(raw, dict):
            last_message = "plan_change 输出不是 JSON object。"
            continue
        plan: dict[str, Any] = dict(raw)
        _normalize_prop_aliases(plan, list(resolve_data.get("components") or []))
        if plan.get("change_kind") == "add_components":
            components = plan.get("components")
            if isinstance(components, dict) and components and not plan.get(
                "target_component_ids"
            ):
                plan["target_component_ids"] = list(components)
        shape_error = _shape_errors(plan)
        if shape_error:
            last_message = shape_error
            continue
        code, message, ask_fields = validate_change_plan(
            plan,
            bind=bind,
            components=list(resolve_data.get("components") or []),
        )
        if code:
            if ask_fields:
                # 目标越界：直接追问，不做第二次 LLM 调用，更不重试写。
                raise StructuredStepError(code, message, ask_fields=ask_fields)
            last_code, last_message = code, message
            continue
        targets = [str(item) for item in plan.get("target_component_ids") or []]
        # 缺映射闸门（设计 §6.3）：改图表类型且生效绑定缺分类/数值时，
        # 只问一次并携带 pending_edit；已有 pending 时未映射成功则不写不重问。
        code, message, ask_fields, pending_edit = _missing_mapping_check(
            plan,
            resolve_components=list(resolve_data.get("components") or []),
            targets=targets,
            field_catalog=field_catalog,
            pending=pending,
        )
        if code:
            raise StructuredStepError(
                code,
                message,
                ask_fields=ask_fields,
                slot_updates=(
                    {"pending_edit": pending_edit} if pending_edit is not None else None
                ),
            )
        # fieldId 必须落在 describe_dataset 字段目录内（目录存在时才校验）。
        binding = plan.get("binding")
        if isinstance(binding, dict) and field_catalog:
            known = {str(field.get("fieldId")) for field in field_catalog}
            used = set(
                [str(item) for item in binding.get("dimensions") or []]
                + [
                    str(measure.get("fieldId"))
                    for measure in binding.get("measures") or []
                    if isinstance(measure, dict)
                ]
            )
            unknown = sorted(used - known)
            if unknown:
                last_code, last_message = (
                    "FIELD_MAPPING_UNRECOGNIZED",
                    "计划引用了字段目录之外的字段：" + "、".join(unknown) + "。",
                )
                continue
        operations = compile_operations(
            plan,
            components=list(resolve_data.get("components") or []),
        )
        if not operations:
            last_code = "CHANGE_PLAN_INVALID"
            last_message = "计划没有编译出任何可执行操作。"
            continue
        plan["operations"] = operations
        return plan
    raise StructuredStepError(
        last_code,
        last_message + "（已自动重试一次，仍未通过校验。）",
    )


_FORBIDDEN_DRAFT_KEYS = {"frame", "x", "y", "document", "operations"}
_DRAFT_KEYS = {
    "goal",
    "screenTitle",
    "sources",
    "slots",
    "assumptions",
    "unmatched",
    "placement",
    "joinRequested",
    "append",
    "layoutPreset",
    "layoutMode",
    "layoutItems",
}
_SLOT_KEYS = {
    "title",
    "role",
    "datasetVersionId",
    "datasetSlug",
    "dimensions",
    "measures",
    "measureAggregations",
    "chartType",
    "timeRange",
}
_PLAN_ROLES = {"metric", "trend", "compare", "share", "detail"}
_PLAN_CHARTS = {"metric", "line", "bar", "pie", "table"}
_AGGREGATES = {"sum", "avg", "max", "min", "count"}

# Explicit title markers only. "帮我生成一个销售 ERP" is a goal, not a title;
# the server derives a conservative title from the goal when none is given.
_TITLE_PATTERNS = (
    re.compile(
        r"(?:大屏|屏|页面)?(?:标题|名称|名字)\s*(?:是|为|叫|：|:)\s*[「“\"']?"
        r"([^，。；;、\n」”\"']{1,60})"
    ),
    re.compile(r"(?:叫做|叫)\s*[「“\"']([^」”\"']{1,60})[」”\"']"),
    re.compile(r"(?:叫做|叫)\s*([^，。；;、\n]{1,40}?)(?:的)?(?:大屏|屏|看板)"),
)
_SOURCE_MARK = re.compile(r"(?:使用|用|基于|来自|接入|取|数据源|数据集)\s*$")
_DIMENSION_MARKERS = ("按", "分组", "分类", "维度", "做横轴", "做纵轴")
_CHART_WORDS = (
    ("折线", "line"),
    ("趋势", "line"),
    ("走势", "line"),
    ("柱状", "bar"),
    ("柱形", "bar"),
    ("条形", "bar"),
    ("对比", "bar"),
    ("排名", "bar"),
    ("饼图", "pie"),
    ("占比", "pie"),
    ("比例", "pie"),
    ("份额", "pie"),
    ("表格", "table"),
    ("明细", "table"),
    ("清单", "table"),
    ("指标卡", "metric"),
)
_AGGREGATION_WORDS = (
    ("平均", "avg"),
    ("均值", "avg"),
    ("最大", "max"),
    ("最高", "max"),
    ("最小", "min"),
    ("最低", "min"),
    ("合计", "sum"),
    ("求和", "sum"),
    ("累计", "sum"),
    ("总计", "sum"),
)


def _compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = _compact(item, 80)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _catalog_datasets(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (catalog.get("datasets") or []) if isinstance(item, dict)]


def _title_from_message(user_message: str) -> str:
    text = _compact(user_message, 500)
    for index, pattern in enumerate(_TITLE_PATTERNS):
        if index == 2 and "大屏" not in text and "屏" not in text and "看板" not in text:
            continue
        match = pattern.search(text)
        if match is None:
            continue
        title = _compact(match.group(1), 60).strip("「」“”\"'：: ")
        if not title or title in {"什么", "啥", "哪个", "什么名字", "一下"}:
            continue
        if title == text:
            continue
        return title
    return ""


def _explicit_sources(message: str, datasets: list[dict[str, Any]]) -> list[str]:
    """Only datasets the user explicitly named (never a policy phrase)."""

    out: list[str] = []
    for dataset in datasets:
        slug = _compact(dataset.get("slug"), 80)
        name = _compact(dataset.get("name"), 80)
        version = _compact(dataset.get("datasetVersionId"), 80)
        for token in (slug, name):
            if not token or token not in message:
                continue
            # "使用已有数据/现有数据" is a policy phrase, never a dataset name.
            if token in {"已有数据", "现有数据", "当前数据"}:
                continue
            index = message.find(token)
            before = message[max(0, index - 12) : index]
            after = message[index + len(token) : index + len(token) + 6]
            slug_hit = bool(slug) and token == slug and any(
                char.isalnum() or char in "_-." for char in token
            )
            if slug_hit or _SOURCE_MARK.search(before) or after.startswith(
                ("数据", "数据集", "里的", "中的", "内")
            ):
                token_value = slug or version
                if token_value:
                    out.append(token_value)
                break
    return _dedupe(out)[:8]


def _field_matches(
    message: str, datasets: list[dict[str, Any]]
) -> list[tuple[int, str, dict[str, Any]]]:
    matches: list[tuple[int, str, dict[str, Any]]] = []
    for dataset in datasets:
        for field in dataset.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = _compact(field.get("displayName"), 80)
            if len(name) < 2 or name not in message:
                continue
            matches.append((message.find(name), name, field))
    matches.sort(key=lambda item: (item[0], -len(item[1])))
    kept: list[tuple[int, str, dict[str, Any]]] = []
    for position, name, field in matches:
        if any(
            name != other and name in other for _, other, _ in kept
        ):
            continue
        if any(
            name == other and field.get("fieldId") == existing.get("fieldId")
            for _, other, existing in kept
        ):
            continue
        kept.append((position, name, field))
    return kept


def _is_dimension_context(message: str, name: str) -> bool:
    return any(
        f"{marker}{name}" in message or f"{name}{marker}" in message
        for marker in _DIMENSION_MARKERS
    )


def _intent_names(
    intent: Any,
    attribute: str,
    *,
    message: str,
    datasets: list[dict[str, Any]],
) -> list[str]:
    if intent is None:
        return []
    raw = getattr(intent, attribute, None) or []
    known = {
        _compact(field.get("displayName"), 80).casefold()
        for dataset in datasets
        for field in dataset.get("fields") or []
        if isinstance(field, dict) and _compact(field.get("displayName"), 80)
    }
    # Only names the user actually said AND the catalog can resolve. The
    # planner must neither turn the screen theme ("ERP") into a metric nor
    # invent a field the dataset does not have (e.g. 合同数量).
    return [
        _compact(item, 80)
        for item in raw
        if item
        and _compact(item, 80) in message
        and _compact(item, 80).casefold() in known
    ]


def _chart_from_message(
    message: str,
    intent: Any,
    *,
    dimensions: list[str],
) -> str:
    if not dimensions:
        return "metric"
    raw = _compact(" ".join(getattr(intent, "charts", None) or []), 120).lower() if intent else ""
    for token, chart in (
        ("折线", "line"),
        ("line", "line"),
        ("趋势", "line"),
        ("柱状", "bar"),
        ("柱形", "bar"),
        ("条形", "bar"),
        ("bar", "bar"),
        ("对比", "bar"),
        ("饼", "pie"),
        ("pie", "pie"),
        ("占比", "pie"),
        ("比例", "pie"),
        ("表格", "table"),
        ("table", "table"),
        ("明细", "table"),
    ):
        if token in message or token in raw:
            return chart
    if len(dimensions) > 1:
        return "table"
    return "bar"


def _measure_aggregations(message: str, measures: list[str]) -> list[str]:
    found: dict[str, str] = {}
    for measure in measures:
        index = message.find(measure)
        if index < 0:
            continue
        before = message[max(0, index - 12) : index]
        after = message[index + len(measure) : index + len(measure) + 8]
        window = before + after
        for word, aggregate in _AGGREGATION_WORDS:
            if word in window:
                found[measure] = aggregate
                break
    if len(measures) == 1 and measures[0] in found:
        return [found[measures[0]]]
    # Only send a list when every measure has an explicit aggregation; a
    # shorter list would be applied by index and silently change the others.
    if measures and all(measure in found for measure in measures):
        return [found[measure] for measure in measures]
    return []


def _build_slots(
    *,
    user_message: str,
    intent: Any,
    datasets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dimensions: list[str] = []
    measures: list[str] = []
    for _, name, field in _field_matches(user_message, datasets):
        is_dimension = (
            _is_dimension_context(user_message, name)
            or field.get("role") == "dimension"
            or str(field.get("type")) != "number"
        )
        if is_dimension:
            dimensions.append(name)
        else:
            measures.append(name)
    dimensions.extend(_intent_names(intent, "dimensions", message=user_message, datasets=datasets))
    measures.extend(_intent_names(intent, "metrics", message=user_message, datasets=datasets))
    dimensions = _dedupe(dimensions)
    measures = _dedupe([item for item in measures if item not in dimensions])
    if not measures:
        return []
    chart = _chart_from_message(user_message, intent, dimensions=dimensions)
    if not dimensions and chart != "table":
        chart = "metric"
    if chart == "metric" and dimensions:
        chart = "bar"
    role = {
        "line": "trend",
        "pie": "share",
        "table": "detail",
        "metric": "metric",
    }.get(chart, "compare")
    slot: dict[str, Any] = {
        "title": _compact("、".join(measures[:3]), 80) or measures[0],
        "role": role,
        "measures": measures[:8],
        "chartType": chart,
    }
    if dimensions:
        slot["dimensions"] = dimensions[:12]
    aggregations = _measure_aggregations(user_message, measures)
    if aggregations:
        slot["measureAggregations"] = aggregations
    return [slot]


def _normalize_slot_title(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def _sorted_slot_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted(name for item in value if (name := _compact(item, 80))))


def _slot_merge_key(slot: dict[str, Any]) -> tuple:
    return (
        _compact(slot.get("datasetVersionId"), 80),
        str(slot.get("role") or ""),
        _normalize_slot_title(slot.get("title")),
        _sorted_slot_names(slot.get("measures")),
        _sorted_slot_names(slot.get("dimensions")),
        str(slot.get("chartType") or ""),
    )


def _upsert_patch_slots(base_slots: list[dict[str, Any]], patch_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace one occurrence of a key, append unknown keys, keep the rest.

    Identical slots share the base key and are told apart by occurrence, so a
    repeated slot is not overwritten by a single patch entry. The caller caps
    the result at 12.
    """

    merged = [dict(item) for item in base_slots]

    def indexed(slots: list[dict[str, Any]]) -> list[tuple[tuple, int, dict[str, Any]]]:
        counts: dict[tuple, int] = {}
        rows: list[tuple[tuple, int, dict[str, Any]]] = []
        for slot in slots:
            key = _slot_merge_key(slot)
            index = counts.get(key, 0)
            counts[key] = index + 1
            rows.append((key, index, slot))
        return rows

    positions = {(key, index): pos for pos, (key, index, _slot) in enumerate(indexed(merged))}
    for key, index, slot in indexed(patch_slots):
        pos = positions.get((key, index))
        if pos is None:
            merged.append(dict(slot))
            positions[(key, index)] = len(merged) - 1
        else:
            merged[pos] = dict(slot)
    return merged


def _sanitize_slot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    slot: dict[str, Any] = {}
    title = _compact(value.get("title"), 80)
    if not title:
        return {}
    slot["title"] = title
    role = str(value.get("role") or "metric")
    slot["role"] = role if role in _PLAN_ROLES else "metric"
    for key, limit in (("datasetVersionId", 80), ("datasetSlug", 80)):
        token = _compact(value.get(key), limit)
        if token:
            slot[key] = token
    for key, limit in (("dimensions", 12), ("measures", 8)):
        raw = value.get(key)
        if isinstance(raw, list):
            items = [_compact(item, 80) for item in raw if _compact(item, 80)]
            if items:
                slot[key] = items[:limit]
    raw_aggregations = value.get("measureAggregations")
    if isinstance(raw_aggregations, list):
        items = [
            str(item)
            for item in raw_aggregations
            if str(item) in _AGGREGATES
        ][:8]
        if items:
            slot["measureAggregations"] = items
    chart = str(value.get("chartType") or "")
    if chart in _PLAN_CHARTS:
        slot["chartType"] = chart
    raw_range = value.get("timeRange")
    if isinstance(raw_range, dict) and raw_range:
        slot["timeRange"] = dict(raw_range)
    return slot


def _sanitize_draft(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    draft: dict[str, Any] = {}
    goal = _compact(value.get("goal"), 200)
    if goal:
        draft["goal"] = goal
    title = _compact(value.get("screenTitle"), 60)
    if title:
        draft["screenTitle"] = title
    raw_sources = value.get("sources")
    if isinstance(raw_sources, list):
        sources = _dedupe([str(item) for item in raw_sources])[:8]
        if sources:
            draft["sources"] = sources
    raw_slots = value.get("slots")
    if isinstance(raw_slots, list):
        slots = [_sanitize_slot(item) for item in raw_slots]
        slots = [item for item in slots if item][:12]
        if slots:
            draft["slots"] = slots
    layout_preset = value.get("layoutPreset")
    if layout_preset in {"auto", "focus", "two_column", "three_column"}:
        draft["layoutPreset"] = layout_preset
    layout_mode = value.get("layoutMode")
    if layout_mode in {"auto", "custom"}:
        draft["layoutMode"] = layout_mode
    raw_items = value.get("layoutItems")
    if isinstance(raw_items, list):
        items: list[dict[str, Any]] = []
        for item in raw_items[:12]:
            if not isinstance(item, dict):
                continue
            box = {
                key: item.get(key)
                for key in ("left", "top", "width", "height", "locked")
                if key in item
            }
            if all(isinstance(box.get(k), int) for k in ("left", "top", "width", "height")):
                items.append(box)
        if items:
            draft["layoutItems"] = items
    for key in ("assumptions", "unmatched"):
        raw = value.get(key)
        if isinstance(raw, list):
            items = [_compact(item, 200) for item in raw if _compact(item, 200)]
            if items:
                draft[key] = items[:20]
    placement = value.get("placement")
    if placement in {"current", "new"}:
        draft["placement"] = placement
    if isinstance(value.get("joinRequested"), bool):
        draft["joinRequested"] = value["joinRequested"]
    if isinstance(value.get("append"), bool):
        draft["append"] = value["append"]
    if draft.get("layoutMode") == "custom":
        items = draft.get("layoutItems") or []
        if len(items) != len(draft.get("slots") or []):
            draft.pop("layoutMode", None)
            draft.pop("layoutItems", None)
    else:
        draft.pop("layoutMode", None)
        draft.pop("layoutItems", None)
    # Clarification payloads are context, never draft fields. ScreenPlanDraft
    # forbids extra keys, so strip anything else as well.
    return {key: draft[key] for key in _DRAFT_KEYS if key in draft}


def _field_type(field: dict[str, Any]) -> str:
    return str(field.get("dataType") or field.get("type") or "").lower()


def _is_number_field(field: dict[str, Any]) -> bool:
    return _field_type(field) in {"number", "integer", "float", "decimal", "numeric"}


def _pick_default_dataset(datasets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer a dataset that can carry the four-module default plan."""

    if not datasets:
        return None
    scored: list[tuple[int, dict[str, Any]]] = []
    for dataset in datasets:
        fields = [item for item in dataset.get("fields") or [] if isinstance(item, dict)]
        numbers = [item for item in fields if _is_number_field(item)]
        dims = [item for item in fields if not _is_number_field(item)]
        names = {_compact(item.get("displayName"), 80) for item in fields}
        score = len(numbers) * 2 + len(dims)
        if "合同额" in names or "合同金额" in names:
            score += 10
        scored.append((score, dataset))
    scored.sort(key=lambda item: -item[0])
    return scored[0][1] if scored and scored[0][0] > 0 else datasets[0]


def _field_by_name(dataset: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any] | None:
    fields = [item for item in dataset.get("fields") or [] if isinstance(item, dict)]
    for wanted in names:
        for field in fields:
            if _compact(field.get("displayName"), 80) == wanted:
                return field
    return None


def _group_title(dimension: str, measure: str) -> str:
    """Human label for a "group by" module, e.g. 各部门合同额."""

    base = dimension[:-2] if dimension.endswith("名称") else dimension
    return f"各{base}{measure}"


def build_default_screen_plan(
    *,
    user_message: str,
    datasets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Build a proposal for the four default modules.

    This is a proposal only: ai-screen still resolves fields, validates the
    draft and persists it. No field is invented; unsupported roles are omitted
    with an assumption instead.
    """

    _ = user_message
    dataset = _pick_default_dataset(datasets)
    if dataset is None:
        return [], ["当前目录没有可用数据集，先补充数据源。"], []
    fields = [item for item in dataset.get("fields") or [] if isinstance(item, dict)]
    numbers = [item for item in fields if _is_number_field(item)]
    dims = [item for item in fields if not _is_number_field(item)]
    if not numbers:
        return [], ["当前目录没有数值字段，暂时只能生成字段说明。"], []

    def label(field: dict[str, Any]) -> str:
        return _compact(field.get("displayName") or field.get("fieldId"), 80)

    preferred = None
    if "进度" in user_message:
        preferred = _field_by_name(dataset, ("进度", "完成率"))
    elif any(term in user_message for term in ("项目数量", "项目数", "项目总数")):
        preferred = _field_by_name(dataset, ("项目数量", "项目数", "项目总数"))
    primary = (
        preferred
        or _field_by_name(dataset, ("合同额", "合同金额"))
        or numbers[0]
    )
    measure = label(primary)
    date_field = next(
        (item for item in dims if _field_type(item) in {"date", "datetime", "time"}),
        None,
    )
    trend_dim = date_field or _field_by_name(dataset, ("阶段", "项目阶段")) or (dims[0] if dims else None)
    compare_dim = _field_by_name(dataset, ("部门", "所属部门", "客户", "客户名称")) or (
        dims[0] if dims else None
    )
    detail_candidates = (
        _field_by_name(dataset, ("项目名称",)),
        _field_by_name(dataset, ("客户", "客户名称")),
        _field_by_name(dataset, ("部门", "所属部门")),
        _field_by_name(dataset, ("阶段", "项目阶段")),
    )
    detail_dims = [item for item in detail_candidates if item is not None]
    detail_dims.extend(item for item in dims if item not in detail_dims)
    detail_measures = [primary]
    progress = _field_by_name(dataset, ("进度", "完成率"))
    if progress is not None and progress is not primary:
        detail_measures.append(progress)

    slots: list[dict[str, Any]] = [
        {
            "title": measure,
            "role": "metric",
            "measures": [measure],
            "chartType": "metric",
        }
    ]
    assumptions: list[str] = []
    if trend_dim is not None:
        trend_dim_name = label(trend_dim)
        slots.append(
            {
                "title": f"{measure}趋势" if date_field is not None else f"{measure}分布",
                "role": "trend",
                "dimensions": [trend_dim_name],
                "measures": [measure],
                "chartType": "line" if date_field is not None else "bar",
            }
        )
        if date_field is None:
            assumptions.append(f"目录没有日期字段，趋势先按「{trend_dim_name}」展示。")
    if compare_dim is not None:
        compare_name = label(compare_dim)
        slots.append(
            {
                "title": _group_title(compare_name, measure),
                "role": "compare",
                "dimensions": [compare_name],
                "measures": [measure],
                "chartType": "bar",
            }
        )
    detail_names = _dedupe([label(item) for item in detail_dims])[:4]
    if detail_names:
        slots.append(
            {
                "title": "项目经营明细" if "项目名称" in detail_names else f"{measure}明细",
                "role": "detail",
                "dimensions": detail_names,
                "measures": _dedupe([label(item) for item in detail_measures])[:2],
                "chartType": "table",
            }
        )
    if len(datasets) > 1:
        assumptions.append(f"目录有多个数据集，默认先使用「{_compact(dataset.get('name'), 80)}」。")
    return slots[:12], assumptions, [_compact(dataset.get("slug"), 80)]


def build_screen_plan_draft(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Build the first ScreenPlanDraft deterministically.

    A broad request carries its ``goal`` and, when the catalog has usable
    fields, the server-side four-module default template. It never invents
    metrics, sources, or datasets; explicit phrases still win.
    """

    _ = bind
    goal = _compact(user_message, 200) or "新大屏"
    draft: dict[str, Any] = {"goal": goal}
    if (
        turn_context is not None
        and turn_context.screen_id
        and (turn_context.component_count or 0) > 0
        and user_message.strip().startswith(("增加", "添加", "新增", "补充", "加"))
    ):
        draft["append"] = True
    title = _title_from_message(user_message)
    if title:
        draft["screenTitle"] = title
    datasets = _catalog_datasets(catalog)
    sources = _explicit_sources(user_message, datasets)
    custom_slots, custom_notes = component_slots(user_message, catalog)
    requested = requested_fields(user_message, catalog)
    has_chart = any(
        word in user_message
        for word in ("文字", "文本", "表格", "柱状", "柱形", "条形", "折线", "趋势", "饼图", "指标卡")
    )
    has_custom_phrase = any(
        phrase in user_message for phrase in ("我想要", "我要", "只要", "这些字段", "自定义", "重新整理")
    )
    custom_request = bool(custom_slots) and (
        has_chart or has_custom_phrase or len(requested) >= 2
    )
    template_id = template_for_text(user_message)
    if any(
        phrase in user_message
        for phrase in ("合同数量", "合同数", "合同笔数", "合同个数", "合同总数")
    ):
        template_id = None
        custom_request = False
    assumptions = list(draft.get("assumptions") or [])
    if custom_request:
        slots = custom_slots
        assumptions.extend(custom_notes)
        custom_sources = []
        for slot in custom_slots:
            slug = str(slot.get("datasetSlug") or "")
            if slug and slug not in custom_sources:
                custom_sources.append(slug)
        if not sources and custom_sources:
            sources = custom_sources
    elif template_id:
        slots = template_slots(template_id, {"datasets": datasets})
        template_sources = []
        for slot in slots:
            slug = str(slot.get("datasetSlug") or "")
            if slug and slug not in template_sources:
                template_sources.append(slug)
        if not sources:
            sources = template_sources
        trend_dims = [
            str(dim)
            for slot in slots
            if slot.get("role") == "trend"
            for dim in (slot.get("dimensions") or [])
        ]
        if trend_dims and not any(
            _field_type(field) in {"date", "datetime", "time"}
            for dataset in datasets
            for field in dataset.get("fields") or []
        ):
            assumptions.append(
                f"目录没有日期字段，趋势先按「{trend_dims[0]}」展示。"
            )
    else:
        slots = _build_slots(
            user_message=user_message,
            intent=turn_context.intent if turn_context is not None else None,
            datasets=datasets,
        )
    if not custom_request and not slots and any(
        phrase in user_message
        for phrase in ("合同数量", "合同数", "合同笔数", "合同个数", "合同总数")
    ):
        # The catalog has no contract entity. Do not substitute 合同额; leave
        # the draft for ai-screen to mark unmatched and return an actionable
        # needs_input/follow-up.
        slots, default_notes, default_sources = [], [], []
    elif not custom_request and not template_id and not slots:
        slots, default_notes, default_sources = build_default_screen_plan(
            user_message=user_message,
            datasets=datasets,
        )
        assumptions.extend(default_notes)
        if (
            not sources
            and default_sources
            and "已有数据" not in user_message
            and "现有数据" not in user_message
        ):
            sources = default_sources
    if sources:
        draft["sources"] = sources
    if slots:
        draft["slots"] = slots
    if assumptions:
        draft["assumptions"] = _dedupe(assumptions)[:20]
    return _sanitize_draft(draft)


def merge_clarification_draft(
    base: Any,
    *,
    draft_patch: Any,
    text: str,
) -> tuple[dict[str, Any], str]:
    """Apply a server draftPatch. Do not interpret natural language.

    ``text`` stays in the signature for older callers and does not create
    slots. Patch slots upsert on datasetVersionId + role + normalized title +
    sorted measures + sorted dimensions + chartType. Same title with a
    different measure or dimension is appended. A repeated identical slot
    keeps its own occurrence instead of being
    overwritten. Slots in the base draft that the patch does not mention are
    kept. The merged list is capped at 12, dropping anything past that.
    Sources still merge. Placement and status are left as they were.

    Returns ``(draft, problem)``. A non-empty ``problem`` means no plan_screen
    call should be made.
    """

    _ = text

    patch = draft_patch if isinstance(draft_patch, dict) else {}
    base_draft = base if isinstance(base, dict) else {}
    if not _compact(base_draft.get("goal"), 200):
        # The server's patch carries the immutable plan identity.  Rebuild
        # from it when the previous turn's session slots were lost (restart,
        # task-frame cleanup) instead of rejecting an otherwise valid answer.
        base_draft = {}
        if _compact(patch.get("goal"), 200):
            base_draft["goal"] = patch["goal"]
            if _compact(patch.get("screenTitle"), 60):
                base_draft["screenTitle"] = patch["screenTitle"]
    if not _compact(base_draft.get("goal"), 200):
        return {}, "方案草稿已失效，请重新生成方案。"
    draft = _sanitize_draft(base_draft)
    if not _compact(draft.get("screenTitle"), 60) and _compact(patch.get("screenTitle"), 60):
        draft["screenTitle"] = patch["screenTitle"]
    sources = list(draft.get("sources") or [])
    for item in patch.get("sources") or []:
        token = _compact(item, 80)
        if token and token not in sources:
            sources.append(token)
    if sources:
        draft["sources"] = sources[:8]
    patch_slots = patch.get("slots")
    if isinstance(patch_slots, list) and patch_slots:
        cleaned = [_sanitize_slot(item) for item in patch_slots if isinstance(item, dict)]
        cleaned = [item for item in cleaned if item]
        if cleaned:
            draft["slots"] = _upsert_patch_slots(list(draft.get("slots") or []), cleaned)[:12]
    draft.pop("clarification", None)
    draft.pop("clarification_answer", None)
    return _sanitize_draft(draft), ""


def _draft_error(draft: dict[str, Any]) -> str:
    if not str(draft.get("goal") or "").strip():
        return "goal 不能为空。"
    slots = draft.get("slots") or []
    if not isinstance(slots, list):
        return "slots 必须是数组。"
    for slot in slots:
        if not isinstance(slot, dict):
            return "slot 必须是对象。"
        if not str(slot.get("title") or "").strip():
            return "slot.title 不能为空。"
        if slot.get("role") not in _PLAN_ROLES:
            return "slot.role 不在允许的角色里。"
        chart = slot.get("chartType")
        if chart is not None and chart not in _PLAN_CHARTS:
            return "slot.chartType 不在允许的图型里。"
    placement = draft.get("placement")
    if placement is not None and placement not in {"current", "new"}:
        return "placement 只能是 current 或 new。"
    return ""


@register_structured_step("draft_screen_plan")
def draft_screen_plan(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    slots: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
    llm: LlmGenerate,
) -> dict[str, Any]:
    _ = metadata, slots
    catalog = dict((outputs.get("describe_catalog") or outputs.get("present_catalog") or {}).get("data") or {})
    draft = build_screen_plan_draft(
        user_message=user_message,
        turn_context=turn_context,
        bind=bind,
        catalog=catalog,
    )
    # Model-first understanding. The model only proposes business names; the
    # ai-screen catalog remains authoritative. Any failure falls back to the
    # deterministic parser, never to a partial write.
    if llm is not None:
        try:
            raw = llm(
                UNDERSTAND_PROMPT,
                {
                    "user_message": user_message,
                    "catalog": projected_catalog(catalog),
                    "draft": draft,
                },
            )
            intent = DraftIntent.model_validate(raw)
            model_slots: list[dict[str, Any]] = []
            for operation in intent.operations:
                if operation.op == "set_slots":
                    model_slots = [
                        item.model_dump(exclude_none=True) for item in operation.slots
                    ]
                elif operation.op == "add_slots":
                    model_slots.extend(
                        item.model_dump(exclude_none=True) for item in operation.slots
                    )
            if (
                model_slots
                and intent.confidence >= 0.75
                and not intent.unresolved
            ):
                draft["slots"] = model_slots[:12]
                model_sources = _dedupe(
                    [
                        str(item.get("datasetSlug") or "")
                        for item in model_slots
                        if item.get("datasetSlug")
                    ]
                )[:8]
                if model_sources:
                    draft["sources"] = model_sources
                draft.pop("assumptions", None)
                draft.pop("unmatched", None)
        except Exception:
            pass
    error = _draft_error(draft)
    if error:
        raise StructuredStepError("INVALID_PLAN", error)
    return {"draft": draft}


@register_structured_step("plan_reply")
def plan_reply(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    slots: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
    llm: LlmGenerate,
) -> dict[str, Any]:
    """Compose the deterministic reply for a plan_screen result.

    The runtime's expression language has no conditionals, so this tiny step
    reads the submit output and emits exactly one of the contract texts.
    """

    _ = user_message, turn_context, bind, slots, llm
    submit_step = str(metadata.get("submit_step") or "submit_plan")
    source_step = str(metadata.get("source_step") or submit_step)
    data = dict((outputs.get(source_step) or {}).get("data") or {})
    status = str(data.get("status") or "")
    clarification = data.get("clarification")
    questions = [
        _compact(item, 300)
        for item in (data.get("questions") or [])
        if _compact(item, 300)
    ]
    reply_kind = str(metadata.get("reply_kind") or "")
    plan_slots = data.get("slots")
    if not isinstance(plan_slots, list):
        plan_slots = (data.get("draft") or {}).get("slots") if isinstance(data.get("draft"), dict) else []
    plan_slots = [item for item in (plan_slots or []) if isinstance(item, dict)]
    source_rows = data.get("sources")
    if not isinstance(source_rows, list) and isinstance(data.get("draft"), dict):
        source_rows = data["draft"].get("sources")
    source_names: list[str] = []
    for source in source_rows or []:
        if isinstance(source, dict):
            name = _compact(source.get("name") or source.get("slug"), 80)
        else:
            name = _compact(source, 80)
        if name and name not in source_names:
            source_names.append(name)
    source_text = "、".join(source_names[:3]) or "当前目录"
    catalog = dict((outputs.get("describe_catalog") or outputs.get("present_catalog") or {}).get("data") or {})
    field_names: list[str] = []
    for dataset in catalog.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        for field in dataset.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = _compact(field.get("displayName") or field.get("fieldId"), 80)
            if name and name not in field_names:
                field_names.append(name)
    if status in {"ready", "needs_confirmation"}:
        if reply_kind == "revise":
            reply = "方案已更新，请确认后我再创建并填充大屏。"
        elif reply_kind == "present":
            if plan_slots:
                reply = (
                    f"当前方案：{len(plan_slots)} 个模块，数据用 {source_text}。"
                    "确认后生成；要调整直接说。"
                )
            else:
                reply = "当前方案可以确认了，请在卡片确认。"
        else:
            goal_text = _compact(data.get("goal"), 120)
            understanding = (
                f"我理解你要做「{goal_text}」。" if goal_text else "我理解你要做一个经营大屏。"
            )
            directions: list[str] = []
            contract_direction = (
                "· 经营总览：合同额指标 + 按部门对比 + 项目明细"
                if "合同额" in field_names or "合同金额" in field_names
                else ""
            )
            progress_direction = (
                "· 进度风险：进度指标 + 按阶段/部门对比 + 项目明细"
                if "进度" in field_names or "完成率" in field_names
                else ""
            )
            customer_direction = (
                "· 合同结构：合同额 + 按阶段 + 按客户"
                if "客户" in field_names or "客户名称" in field_names
                else ""
            )
            primary_measure = " ".join(
                str(item)
                for item in (plan_slots[0].get("measures") or [])
                if item
            ) if plan_slots else ""
            if "进度" in primary_measure:
                ordered = [progress_direction, contract_direction, customer_direction]
            elif "项目数量" in primary_measure or "项目数" in primary_measure:
                ordered = [contract_direction, progress_direction, customer_direction]
            else:
                ordered = [contract_direction, progress_direction, customer_direction]
            directions = [item for item in ordered if item]
            assumptions = data.get("assumptions") or []
            no_date = any("没有日期字段" in _compact(item, 120) for item in assumptions)
            default_label = directions[0].split("：", 1)[0].lstrip("· ") if directions else "默认模板"
            alt_labels = [
                item.split("：", 1)[0].lstrip("· ")
                for item in directions[1:3]
            ]
            if plan_slots:
                summary_line = (
                    f"先按「{default_label}」整理了 {len(plan_slots)} 个模块，"
                    f"数据用 {source_text}。"
                )
                if no_date:
                    summary_line += "趋势暂按阶段。"
                alt_line = (
                    "可换「" + "」/「".join(alt_labels) + "」；要改字段或图表直接说。"
                    if alt_labels
                    else "要改字段或图表直接说。"
                )
                reply = "\n".join([understanding, summary_line, alt_line, "确认后生成。"])
            else:
                reply = "\n".join([understanding, "方案可以确认了，请在卡片确认。"])
    elif status == "needs_input":
        if isinstance(clarification, dict):
            reply = "我先确认一个关键点，请在方案卡片中选择。"
        elif questions:
            reply = questions[0]
        else:
            reply = "方案还缺少必要信息，请补充后再试。"
    elif status == "blocked":
        reply = questions[0] if questions else _compact(
            data.get("summary"), 300
        ) or "这个方案暂时无法生成，请补充说明。"
    else:
        reply = _compact(data.get("summary"), 300) or "方案已提交，请确认。"
    return {
        "reply": reply,
        "clarification": clarification if isinstance(clarification, dict) else None,
        "plan_id": _compact(data.get("planId"), 80),
    }


_CHART_FROM_TEXT = (
    ("饼", "pie"),
    ("折线", "line"),
    ("曲线", "line"),
    ("柱", "bar"),
    ("条形", "bar"),
    ("明细", "table"),
    ("表格", "table"),
    ("指标卡", "metric"),
    ("指标", "metric"),
)


def _chart_from_intent(charts: list[str] | None) -> str:
    text = " ".join(str(item) for item in (charts or []))
    for word, chart in _CHART_FROM_TEXT:
        if word in text:
            return chart
    return "metric"


def _role_for_chart(chart: str) -> str:
    return {
        "bar": "compare",
        "line": "trend",
        "pie": "share",
        "table": "detail",
        "metric": "metric",
    }.get(chart, "metric")


def _intent_from_turn_context(
    turn_context: ScreenTurnContext | None,
    draft: dict[str, Any],
    catalog: dict[str, Any],
) -> DraftIntent | None:
    """Build a proposal from the planner's elliptical-message context.

    The model sees only the current message. For a follow-up such as
    "按阶段", the turn planner already resolved metric/dimension/chart from
    the previous turn's screen intent. Use that context when the model has no
    operation, while still leaving catalog resolution to ai-screen.
    """

    if turn_context is None or turn_context.intent is None:
        return None
    planned = turn_context.intent
    metrics = [str(item).strip() for item in (planned.metrics or []) if str(item).strip()]
    if not metrics:
        return None
    dimensions = [str(item).strip() for item in (planned.dimensions or []) if str(item).strip()]
    chart = _chart_from_intent(planned.charts)
    slots: list[IntentSlot] = []
    unresolved: list[str] = []
    existing = [slot for slot in (draft.get("slots") or []) if isinstance(slot, dict)]
    for metric in metrics:
        found = find_field(metric, catalog)
        if found is None:
            unresolved.append(f"目录里找不到「{metric}」")
            continue
        dataset, field = found
        if not is_numeric(field):
            unresolved.append(f"「{metric}」不是可用的数值指标")
            continue
        label = str(field.get("displayName") or field.get("fieldId") or metric).strip()
        dim_labels: list[str] = []
        for dimension in dimensions:
            dim_found = find_field(dimension, catalog)
            if dim_found is None:
                unresolved.append(f"目录里找不到维度「{dimension}」")
                continue
            dim_labels.append(str(dim_found[1].get("displayName") or dim_found[1].get("fieldId") or dimension))
        if chart != "metric" and not dim_labels:
            for candidate in ("阶段", "部门", "客户", "类型", "项目名称"):
                dim_found = find_field(candidate, catalog)
                if dim_found is not None:
                    dim_labels = [str(dim_found[1].get("displayName") or candidate)]
                    break
        role = _role_for_chart(chart)
        title = label if chart == "metric" else (f"各{dim_labels[0]}{label}" if dim_labels else label)
        slot = IntentSlot(
            title=title,
            role=role,
            datasetSlug=str(dataset.get("slug") or "") or None,
            dimensions=[] if chart == "metric" else dim_labels,
            measures=[label],
            chartType=chart,
        )
        duplicate = any(
            str(item.get("title") or "") == slot.title
            and str(item.get("role") or "") == slot.role
            and [str(v) for v in item.get("measures") or []] == slot.measures
            and [str(v) for v in item.get("dimensions") or []] == slot.dimensions
            for item in existing
        )
        if not duplicate:
            slots.append(slot)
    if not slots and not unresolved:
        return None
    operations = [Operation(op="add_slots", slots=slots)] if slots else []
    return DraftIntent(confidence=0.9 if slots and not unresolved else 0.6, operations=operations, unresolved=unresolved)


@register_structured_step("screen_draft_intent")
def screen_draft_intent(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    slots: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
    llm: LlmGenerate,
) -> dict[str, Any]:
    """Turn one ordinary chat revision into a limited ScreenDraftIntent.

    The workflow has already read the authoritative plan and catalog. This step
    only proposes operations; ai-screen resolves fields, CASes, and persists.
    """

    _ = bind, slots, metadata
    plan_data = dict((outputs.get("get_current_plan") or {}).get("data") or {})
    catalog = dict((outputs.get("describe_catalog_for_revise") or {}).get("data") or {})
    draft = plan_data.get("draft")
    if not isinstance(draft, dict):
        raise StructuredStepError("PLAN_NOT_EDITABLE", "没有读到当前方案草稿")
    # A position swap is a structural layout command, not fuzzy semantic
    # parsing. Resolve it deterministically before involving the model.
    if any(word in user_message for word in ("互换", "交换", "对调")):
        deterministic = build_intent(user_message, draft, catalog)
        if any(op.op == "swap_slots" for op in deterministic.operations):
            return {"intent": deterministic.model_dump()}
    context_intent = _intent_from_turn_context(turn_context, draft, catalog)
    payload = {
        "user_message": user_message,
        "draft": draft,
        "catalog": projected_catalog(catalog),
    }
    if turn_context is not None and turn_context.intent is not None:
        payload["turn_intent"] = {
            "kind": turn_context.intent.kind,
            "metrics": turn_context.intent.metrics,
            "dimensions": turn_context.intent.dimensions,
            "charts": turn_context.intent.charts,
        }
    try:
        raw = llm(INTENT_SYSTEM_PROMPT, payload)
        intent = parse_model_intent(raw)
    except Exception:
        # Model unavailable/invalid only: deterministic exact-name fallback.
        # It is not a second semantic path and never invents fields.
        intent = build_intent(user_message, draft, catalog)
    if context_intent is not None and (
        not intent.operations or intent.confidence < 0.75 or intent.unresolved
    ):
        intent = context_intent
    if not intent.operations and intent.unresolved:
        return {"intent": intent.model_dump(), "unresolved": intent.unresolved}
    return {"intent": intent.model_dump()}


@register_structured_step("screen_query_reply")
def screen_query_reply(
    *,
    user_message: str,
    turn_context: ScreenTurnContext | None,
    bind: dict[str, Any],
    slots: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
    llm: LlmGenerate,
) -> dict[str, Any]:
    """Answer one side data question with catalog summaries only."""

    _ = user_message, turn_context, bind, slots, metadata, llm
    data = dict((outputs.get("query_dataset") or {}).get("data") or {})
    datasets = [item for item in (data.get("datasets") or []) if isinstance(item, dict)]
    if len(datasets) > 1:
        names = "、".join(
            str(item.get("name") or item.get("slug") or "未命名数据集")
            for item in datasets[:6]
        )
        return {
            "reply": (
                f"当前目录有多个数据集：{names}。请告诉我要看哪一个数据集或指标；"
                "我不会随便挑一个回答。当前方案和任务没有变化。"
            )
        }
    parts: list[str] = []
    for dataset in datasets[:1]:
        name = str(dataset.get("name") or dataset.get("slug") or "数据集")
        summary = dataset.get("summary")
        if not isinstance(summary, dict) or not summary:
            continue
        labels = {
            str(field.get("fieldId")): str(field.get("displayName") or field.get("fieldId"))
            for field in (dataset.get("fields") or [])
            if isinstance(field, dict)
        }
        values = []
        for key, value in summary.items():
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                values.append(f"{labels.get(key, key)}={value}")
            if len(values) >= 4:
                break
        if values:
            parts.append(f"{name}：" + "，".join(values))
    if parts:
        reply = "只读数据摘要：" + "；".join(parts) + "。当前方案和任务没有变化。"
    else:
        reply = "当前目录没有可用的数值摘要。当前方案和任务没有变化。"
    return {"reply": reply}

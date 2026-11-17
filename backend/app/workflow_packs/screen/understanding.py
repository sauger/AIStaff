"""Business-level understanding for screen plan requests.

This module is deliberately on the AIStaff side of the boundary: it turns
natural language into business names and component intents. ai-screen still
resolves canonical fields, validates the draft, applies CAS, and persists.
"""

from __future__ import annotations

import re
from typing import Any

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "合同额": ("合同额", "合同金额", "contract", "fld_contract"),
    "合同金额": ("合同金额", "合同额"),
    "项目名称": ("项目名称", "项目名", "project_name"),
    "部门": ("部门", "部门名称", "department"),
    "部门名称": ("部门名称", "部门"),
    "客户": ("客户", "客户名称", "customer"),
    "客户名称": ("客户名称", "客户"),
    "进度": ("进度", "完成率", "progress"),
    "项目数量": ("项目数量", "项目数", "project_count"),
}

CHART_WORDS: tuple[tuple[str, str], ...] = (
    ("折线图", "line"),
    ("折线", "line"),
    ("趋势图", "line"),
    ("趋势", "line"),
    ("柱状图", "bar"),
    ("柱形图", "bar"),
    ("条形图", "bar"),
    ("柱状", "bar"),
    ("饼图", "pie"),
    ("饼状图", "pie"),
    ("占比", "pie"),
    ("比例", "pie"),
    ("指标卡", "metric"),
    ("指标", "metric"),
    ("明细表", "table"),
    ("明细", "table"),
    ("表格", "table"),
    ("文字", "table"),
)

ROLE_FOR_CHART = {
    "metric": "metric",
    "line": "trend",
    "bar": "compare",
    "pie": "share",
    "table": "detail",
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "overview": {
        "label": "经营总览",
        "description": "合同额指标 + 按部门对比 + 项目明细",
        "keywords": ("经营总览", "经营", "总览", "第一个"),
    },
    "progress": {
        "label": "进度风险",
        "description": "进度指标 + 按阶段对比 + 项目明细",
        "keywords": ("进度风险", "进度看板", "第二个"),
    },
    "contract": {
        "label": "合同结构",
        "description": "合同额 + 按阶段 + 按客户",
        "keywords": ("合同结构", "合同看板", "第三个"),
    },
}


def norm(value: object) -> str:
    text = str(value or "").strip().lower()
    return text.replace("fld_", "").replace(" ", "").replace("_", "")


def datasets(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (catalog.get("datasets") or []) if isinstance(item, dict)]


def _fields(catalog: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for dataset in datasets(catalog):
        for field in dataset.get("fields") or []:
            if isinstance(field, dict):
                found.append((dataset, field))
    return found


def is_numeric(field: dict[str, Any]) -> bool:
    kind = str(field.get("dataType") or field.get("type") or "").lower()
    return kind in {"number", "integer", "float", "decimal", "numeric"}


def find_field(name: str, catalog: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    wanted = norm(name)
    if not wanted:
        return None
    variants = {norm(item) for item in FIELD_ALIASES.get(name.strip(), ())}
    variants.add(wanted)
    fields = _fields(catalog)
    for dataset, field in fields:
        names = {norm(field.get("fieldId")), norm(field.get("displayName"))}
        if names & variants:
            return dataset, field
    for dataset, field in fields:
        names = {norm(field.get("fieldId")), norm(field.get("displayName"))}
        if any(
            variant in item or item in variant
            for variant in variants
            for item in names
            if item
        ):
            return dataset, field
    return None


def field_label(field: dict[str, Any], fallback: str = "") -> str:
    return str(field.get("displayName") or field.get("fieldId") or fallback).strip()


def _occurrences(text: str, catalog: dict[str, Any]) -> list[tuple[int, str, dict[str, Any], dict[str, Any]]]:
    lowered = str(text or "")
    found: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for dataset, field in _fields(catalog):
        label = field_label(field)
        if not label:
            continue
        names = {label, *FIELD_ALIASES.get(label, ())}
        for name in sorted(names, key=len, reverse=True):
            if not name:
                continue
            index = lowered.find(name)
            if index < 0:
                continue
            key = str(field.get("fieldId") or label)
            if key in seen:
                break
            # Skip a shorter match wholly inside an already selected name.
            if any(
                index >= start
                and index + len(name) <= start + len(existing)
                and name != existing
                for start, existing, _, _ in found
            ):
                break
            found.append((index, name, dataset, field))
            seen.add(key)
            break
    found.sort(key=lambda item: (item[0], -len(item[1])))
    return found


def requested_fields(text: str, catalog: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(dataset, field) for _, _, dataset, field in _occurrences(text, catalog)]


def looks_like_custom_request(text: str, catalog: dict[str, Any]) -> bool:
    if not requested_fields(text, catalog):
        return False
    return any(
        phrase in text
        for phrase in ("我想要", "我要", "只要", "需要", "这些字段", "字段", "自定义", "重新整理")
    ) or "，" in text or "," in text or " " in text


def _slot(
    dataset: dict[str, Any],
    field: dict[str, Any],
    *,
    chart: str,
    dimensions: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    label = field_label(field)
    dimensions = [item for item in (dimensions or []) if item]
    if chart == "metric":
        dimensions = []
    if title:
        slot_title = title
    elif chart == "metric":
        slot_title = label
    elif chart == "table":
        slot_title = "项目明细" if "项目名称" in dimensions else f"{label}明细"
    elif dimensions:
        slot_title = f"{label}（按{dimensions[0]}）"
    elif chart == "pie":
        slot_title = f"{label}占比"
    else:
        slot_title = label
    return {
        "title": slot_title,
        "role": ROLE_FOR_CHART.get(chart, "detail"),
        "datasetSlug": str(dataset.get("slug") or "") or None,
        "dimensions": dimensions,
        "measures": [label] if is_numeric(field) else [],
        "chartType": chart,
    }


def _clean_slot(slot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in slot.items() if value not in (None, [], "")}


def _chart_in(text: str) -> str | None:
    for word, chart in CHART_WORDS:
        if word in text:
            return chart
    return None


def _dimensions_in(text: str, catalog: dict[str, Any], *, exclude: set[str]) -> list[str]:
    found: list[str] = []
    for _, field in requested_fields(text, catalog):
        label = field_label(field)
        if norm(label) in exclude:
            continue
        if not is_numeric(field) and label not in found:
            found.append(label)
    explicit = re.search(r"按\s*([^，,；;。]+)", text)
    if explicit:
        for name in re.split(r"[、和及/]", explicit.group(1)):
            hit = find_field(name.strip(), catalog)
            if hit is not None:
                label = field_label(hit[1])
                if label not in found:
                    found.append(label)
    return found


def component_slots(text: str, catalog: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Split one message into multiple explicit field/chart clauses."""

    slots: list[dict[str, Any]] = []
    unresolved: list[str] = []
    clauses = [item.strip() for item in re.split(r"[，,；;。\n]", text) if item.strip()]
    for clause in clauses:
        hits = requested_fields(clause, catalog)
        chart = _chart_in(clause)
        if not hits:
            if chart and any(word in clause for word in ("图", "表")):
                unresolved.append(f"无法识别「{clause}」中的字段")
            continue
        numeric = [(dataset, field) for dataset, field in hits if is_numeric(field)]
        text_fields = [(dataset, field) for dataset, field in hits if not is_numeric(field)]
        if chart in {"bar", "line", "pie"}:
            if not numeric:
                unresolved.append(f"「{clause}」没有可用的数值指标")
                continue
            dataset, field = numeric[0]
            dims = _dimensions_in(clause, catalog, exclude={norm(field_label(field))})
            dims = dims or [field_label(item) for _, item in text_fields]
            slots.append(_slot(dataset, field, chart=chart, dimensions=dims))
        elif chart == "table":
            measures = [field_label(field) for _, field in numeric]
            dims = [field_label(field) for _, field in text_fields]
            if numeric:
                dataset, field = numeric[0]
                slot = _slot(dataset, field, chart="table", dimensions=dims)
                slot["measures"] = measures
                slots.append(slot)
            elif dims:
                dataset, field = text_fields[0]
                slot = _slot(dataset, field, chart="table", dimensions=dims)
                slot["measures"] = []
                slots.append(slot)
        elif chart == "metric":
            for dataset, field in numeric:
                slots.append(_slot(dataset, field, chart="metric"))
    if slots:
        return slots, unresolved
    # No explicit chart: aggregate the field list into one detail table.
    fields = requested_fields(text, catalog)
    if not fields:
        return [], unresolved
    numeric = [(dataset, field) for dataset, field in fields if is_numeric(field)]
    text_fields = [(dataset, field) for dataset, field in fields if not is_numeric(field)]
    if not numeric:
        names = "、".join(field_label(field) for _, field in text_fields) or "这些字段"
        return [], [f"「{names}」只有维度，还需要一个数值指标（例如合同额或进度）"]
    dataset, field = numeric[0]
    slot = _slot(
        dataset,
        field,
        chart="table",
        dimensions=[field_label(item) for _, item in text_fields],
    )
    slot["measures"] = [field_label(item) for _, item in numeric]
    return [slot], unresolved


def template_for_text(text: str) -> str | None:
    for template_id, spec in TEMPLATES.items():
        if any(keyword in text for keyword in spec["keywords"]):
            return template_id
    return None


def template_slots(template_id: str, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a fixed, catalog-backed template. Never invent a field."""

    def pick(*names: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for name in names:
            hit = find_field(name, catalog)
            if hit is not None:
                return hit
        return None

    metric_name = "进度" if template_id == "progress" else "合同额"
    measure = pick(metric_name, "合同金额", "合同额")
    if measure is None:
        return []
    dataset, measure_field = measure
    measure_label = field_label(measure_field)
    dims: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for names in (
        ("阶段", "项目阶段"),
        ("月份", "月"),
        ("部门", "部门名称"),
        ("客户", "客户名称"),
        ("项目名称", "项目名"),
    ):
        hit = pick(*names)
        if hit is not None and str(hit[0].get("slug") or "") == str(dataset.get("slug") or ""):
            dims.append(hit)
    slots: list[dict[str, Any]] = [_slot(dataset, measure_field, chart="metric")]

    def by_label(label: str) -> dict[str, Any] | None:
        for dim_dataset, field in dims:
            if field_label(field) == label:
                return _slot(
                    dim_dataset,
                    measure_field,
                    chart="bar",
                    dimensions=[field_label(field)],
                )
        return None

    labels = [field_label(field) for _, field in dims]
    stage = next((label for label in labels if label in {"阶段", "项目阶段", "月份", "月"}), "")
    if not stage and labels:
        stage = labels[0]
    remaining = [label for label in labels if label != stage]
    department = next((label for label in remaining if label in {"部门", "部门名称"}), "")
    if not department and remaining:
        department = remaining[0]
    customer = next((label for label in remaining if label in {"客户", "客户名称"}), "")
    if not customer and len(remaining) > 1:
        customer = remaining[1]
    if stage:
        trend = _slot(dataset, measure_field, chart="bar", dimensions=[stage])
        trend["role"] = "trend"
        trend["title"] = (
            f"{measure_label}趋势"
            if stage in {"月份", "月", "日期", "时间"}
            else f"{measure_label}分布"
        )
        slots.append(trend)
    compare = customer if template_id == "contract" else department
    if compare and compare != stage:
        compare_slot = _slot(dataset, measure_field, chart="bar", dimensions=[compare])
        compare_base = compare[:-2] if compare.endswith("名称") else compare
        compare_slot["title"] = f"各{compare_base}{measure_label}"
        slots.append(compare_slot)
    detail_dims = [field_label(field) for _, field in dims[:3]]
    detail = _slot(dataset, measure_field, chart="table", dimensions=detail_dims)
    detail["title"] = "项目经营明细" if "项目名称" in detail_dims else f"{measure_label}明细"
    contract = pick("合同额", "合同金额")
    if contract is not None and str(contract[0].get("slug") or "") == str(dataset.get("slug") or ""):
        contract_label = field_label(contract[1])
        if contract_label not in detail["measures"]:
            detail["measures"].append(contract_label)
    slots.append(detail)
    return [_clean_slot(slot) for slot in slots]


UNDERSTAND_PROMPT = """你是大屏方案的受限意图解析器。只输出一个 JSON object。
顶层字段只有 version=1, confidence(0..1), operations(最多4个), unresolved(最多8项)。
operations 只能使用 add_slots/remove_slots/replace_slots/set_slots/set_title/set_sources。
slot 只写 title/role/datasetSlug/dimensions/measures/chartType；selector 只写 title/role/datasetSlug。
业务字段名必须来自给定 catalog，禁止输出 fieldId、datasetVersionId、planId、taskId、requestId、
frame、x、y、document、operations 写屏指令或工具调用。无法确定时不要猜，写入 unresolved。
规则：
- 用户明确给出“一组字段，重新整理方案”时，用一个 set_slots（最多12个 slot）整体替换，不要追加。
- 每个“字段 + 图 + 按维度”子句生成一个 slot；柱状/折线/饼图必须有数值指标。
- 只有字段名、没有图时，默认一个 detail/table：文字字段做维度，数值字段做指标。
- 只有维度没有数值时，写 unresolved，追问一个数值指标，不要默认补合同额。
- 同一轮多个明确修改最多4个 operation；不要让一个字段失败阻止其他明确操作。
示例：
- “我想要合同金额 项目名称 部门名称” -> set_slots 一个 detail/table：
  dimensions=[项目名称,部门]，measures=[合同金额]。
- “合同金额按部门做柱状图，项目数量按阶段做饼图” -> set_slots 两个：
  合同额 compare/bar dimensions=[部门]；项目数量 share/pie dimensions=[阶段]。
- “删掉项目进度，补一个合同额” -> remove_slots 项目进度 + add_slots 合同额 metric。
"""

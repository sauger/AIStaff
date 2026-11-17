"""Limited ScreenDraftIntent builder for the demo dialogue path.

This module only proposes business-level operations. ai-screen remains the
authority: it resolves real fields from its catalog, validates the draft,
applies CAS, and persists. Nothing here can confirm a plan or write a screen.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workflow_packs.screen.understanding import (
    component_slots,
    find_field as _find_field,
    is_numeric as _numeric,
    looks_like_custom_request,
    norm as _norm,
    requested_fields,
)

PlanRole = Literal["metric", "trend", "compare", "share", "detail"]
PlanChartType = Literal["metric", "line", "bar", "pie", "table"]


class IntentSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    role: PlanRole
    datasetSlug: str | None = Field(default=None, max_length=80)
    dimensions: list[str] = Field(default_factory=list, max_length=12)
    measures: list[str] = Field(default_factory=list, max_length=8)
    chartType: PlanChartType | None = None


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=80)
    role: PlanRole | None = None
    datasetSlug: str | None = Field(default=None, max_length=80)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal[
        "add_slots", "remove_slots", "replace_slots", "set_slots", "set_title",
        "set_sources", "set_layout", "swap_slots",
    ]
    slots: list[IntentSlot] = Field(default_factory=list, max_length=12)
    selectors: list[Selector] = Field(default_factory=list, max_length=4)
    selector: Selector | None = None
    slot: IntentSlot | None = None
    title: str | None = Field(default=None, max_length=60)
    sources: list[str] = Field(default_factory=list, max_length=8)
    layoutPreset: Literal["auto", "focus", "two_column", "three_column"] | None = None
    positions: list[int] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _shape(self) -> "Operation":
        if self.op in {"add_slots", "set_slots"} and not self.slots:
            raise ValueError(f"{self.op} needs slots")
        if self.op == "remove_slots" and not self.selectors:
            raise ValueError("remove_slots needs selectors")
        if self.op == "replace_slots" and (self.selector is None or self.slot is None):
            raise ValueError("replace_slots needs selector and slot")
        if self.op == "set_title" and not self.title:
            raise ValueError("set_title needs title")
        if self.op == "set_sources" and not self.sources:
            raise ValueError("set_sources needs sources")
        if self.op == "set_layout" and self.layoutPreset is None:
            raise ValueError("set_layout needs layoutPreset")
        if self.op == "swap_slots":
            if len(self.positions) != 2 or min(self.positions) < 1 or self.positions[0] == self.positions[1]:
                raise ValueError("swap_slots needs two different positive positions")
        return self


class DraftIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    operations: list[Operation] = Field(default_factory=list, max_length=4)
    unresolved: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _nonempty(self) -> "DraftIntent":
        if not self.operations and not self.unresolved:
            raise ValueError("intent needs operations or unresolved")
        return self


def _slot_for(field: dict, dataset: dict, name: str) -> IntentSlot:
    label = str(field.get("displayName") or field.get("fieldId") or name).strip()
    is_number = _numeric(field)
    return IntentSlot(
        title=label,
        role="metric" if is_number else "detail",
        datasetSlug=str(dataset.get("slug") or "") or None,
        dimensions=[] if is_number else [label],
        measures=[label] if is_number else [],
        chartType="metric" if is_number else "table",
    )


def _existing_slot(draft: dict[str, Any], name: str) -> dict | None:
    wanted = _norm(name)
    for slot in draft.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        title = _norm(slot.get("title"))
        measures = {_norm(item) for item in slot.get("measures") or []}
        dimensions = {_norm(item) for item in slot.get("dimensions") or []}
        if wanted and (wanted == title or wanted in measures or wanted in dimensions):
            return slot
    return None


def _add_operation(name: str, catalog: dict[str, Any]) -> tuple[Operation | None, str]:
    hit = _find_field(name, catalog)
    if hit is None:
        return None, f"当前目录没有「{name}」"
    dataset, field = hit
    return Operation(op="add_slots", slots=[_slot_for(field, dataset, name)]), ""


def _slot_same(slot: IntentSlot, existing: dict[str, Any] | IntentSlot) -> bool:
    if isinstance(existing, IntentSlot):
        existing = existing.model_dump()
    if _norm(slot.title) != _norm(existing.get("title")):
        return False
    if (slot.chartType or "metric") != (existing.get("chartType") or "metric"):
        return False
    if {_norm(item) for item in slot.measures} != {
        _norm(item) for item in (existing.get("measures") or [])
    }:
        return False
    return {_norm(item) for item in slot.dimensions} == {
        _norm(item) for item in (existing.get("dimensions") or [])
    }


def _dataset_for_slot(slot: IntentSlot, catalog: dict[str, Any]) -> dict[str, Any] | None:
    for dataset in catalog.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        if slot.datasetSlug and str(dataset.get("slug") or "") == slot.datasetSlug:
            return dataset
    return None


def _default_dimension(slot: IntentSlot, catalog: dict[str, Any]) -> str | None:
    if slot.chartType not in {"bar", "line", "pie"} or slot.dimensions:
        return None
    dataset = _dataset_for_slot(slot, catalog)
    if dataset is None:
        return None
    order = ["阶段", "项目阶段", "月份", "月", "部门", "部门名称", "客户", "客户名称", "项目名称", "项目名"]
    if slot.measures and _norm(slot.measures[0]) == _norm("合同额"):
        order = ["部门", "部门名称", "阶段", "项目阶段", "客户", "客户名称", "项目名称", "项目名"]
    by_name = {
        _norm(field.get("displayName") or field.get("fieldId")): str(
            field.get("displayName") or field.get("fieldId")
        )
        for field in dataset.get("fields") or []
        if isinstance(field, dict) and not _numeric(field)
    }
    for name in order:
        if _norm(name) in by_name:
            return by_name[_norm(name)]
    return next(iter(by_name.values()), None)


def _join_verb_operations(
    message: str, draft: dict[str, Any], catalog: dict[str, Any]
) -> tuple[list[Operation], list[str]]:
    match = re.search(r"(?:加入|添加|新增|增加|补充|补|加个|加)\s*(.+)", message)
    if match is None:
        return [], []
    rest = match.group(1).strip()
    if not any(word in rest for word in ("图", "表", "指标卡", "按")):
        slots = [
            _slot_for(field, dataset, str(field.get("displayName") or field.get("fieldId") or ""))
            for dataset, field in requested_fields(rest, catalog)
        ]
        slots = [slot for slot in slots if not any(_slot_same(slot, item) for item in draft.get("slots") or [])]
        if slots:
            return [Operation(op="add_slots", slots=slots[:12])], []
        return [], []
    raw_slots, notes = component_slots(rest, catalog)
    slots: list[IntentSlot] = []
    for raw in raw_slots:
        slot = IntentSlot(**raw)
        dim = _default_dimension(slot, catalog)
        if dim:
            slot = slot.model_copy(update={"dimensions": [dim]})
        if slot.chartType in {"bar", "line", "pie"} and not slot.dimensions:
            notes.append(f"「{slot.title}」缺少可用的对比维度")
            continue
        if not any(_slot_same(slot, item) for item in [*(draft.get("slots") or []), *slots]):
            slots.append(slot)
    if "指标" in rest:
        for dataset, field in _occurrences_for_metric(rest, catalog):
            label = str(field.get("displayName") or field.get("fieldId") or "").strip()
            if not label:
                continue
            metric = IntentSlot(
                title=label,
                role="metric",
                datasetSlug=str(dataset.get("slug") or "") or None,
                dimensions=[],
                measures=[label],
                chartType="metric",
            )
            if not any(_slot_same(metric, item) for item in [*(draft.get("slots") or []), *slots]):
                slots.append(metric)
    if not slots:
        return [], notes
    return [Operation(op="add_slots", slots=slots[:12])], notes


def _occurrences_for_metric(text: str, catalog: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for dataset in catalog.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        for field in dataset.get("fields") or []:
            if not isinstance(field, dict) or not _numeric(field):
                continue
            name = str(field.get("displayName") or field.get("fieldId") or "").strip()
            if name and _norm(name) in _norm(text):
                found.append((dataset, field))
    # Keep the first occurrence per field label, preserving catalog order.
    seen: set[str] = set()
    unique: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for dataset, field in found:
        label = str(field.get("displayName") or field.get("fieldId") or "").strip()
        if label not in seen:
            seen.add(label)
            unique.append((dataset, field))
    return unique


def _slot_index(draft: dict[str, Any], name: str) -> int | None:
    wanted = _norm(name).replace("位置", "")
    if not wanted:
        return None
    slots = [slot for slot in (draft.get("slots") or []) if isinstance(slot, dict)]
    exact_titles = [i for i, slot in enumerate(slots) if _norm(slot.get("title")) == wanted]
    if len(exact_titles) == 1:
        return exact_titles[0]
    exact_fields = [
        i
        for i, slot in enumerate(slots)
        if wanted in {_norm(item) for item in slot.get("measures") or []}
        or wanted in {_norm(item) for item in slot.get("dimensions") or []}
    ]
    if len(exact_fields) == 1:
        return exact_fields[0]
    matches: list[int] = []
    for index, slot in enumerate(slots):
        title = _norm(slot.get("title"))
        if wanted in title or title in wanted:
            matches.append(index)
    return matches[0] if len(matches) == 1 else None


def _swap_operation(message: str, draft: dict[str, Any]) -> Operation | None:
    """Recognize explicit layout-position swaps without a model call."""

    text = " ".join(str(message or "").split())
    number = re.search(
        r"(?:将|把)?\s*(?:布局|方案)?(?:中|里)?\s*(\d+)\s*(?:和|与|跟)\s*(\d+)\s*(?:两个?)?(?:位置|模块|块)?\s*(?:互换|交换|对调|换一下|换)",
        text,
    )
    if number:
        left, right = int(number.group(1)), int(number.group(2))
        return Operation(op="swap_slots", positions=[left, right])
    names = re.search(
        r"(?:将|把)?\s*[「“\"]?(.+?)[」”\"]?\s*(?:和|与|跟)\s*[「“\"]?(.+?)[」”\"]?\s*(?:两个?)?(?:位置|模块|块)?\s*(?:互换|交换|对调|换一下|换)",
        text,
    )
    if names:
        left_name = names.group(1).strip()
        right_name = names.group(2).strip()
        left = _slot_index(draft, left_name)
        right = _slot_index(draft, right_name)
        if left is not None and right is not None and left != right:
            return Operation(op="swap_slots", positions=[left + 1, right + 1])
    return None


def build_intent(text: str, draft: dict[str, Any], catalog: dict[str, Any]) -> DraftIntent:
    """Deterministic demo understanding over the current draft and catalog.

    It never invents a field: every add/replace must resolve in the catalog.
    A phrase it cannot map becomes unresolved, not a partial write.
    """

    message = str(text or "").strip()
    operations: list[Operation] = []
    unresolved: list[str] = []
    swap = _swap_operation(message, draft)
    if swap is not None:
        return DraftIntent(confidence=0.9, operations=[swap])

    title_match = re.search(
        r"(?:标题|名称|名字)(?:改成|改为|叫|是|为)\s*[「“\"]?([^」”\"，。；]+)",
        message,
    )
    if title_match:
        operations.append(Operation(op="set_title", title=title_match.group(1).strip()))

    source_match = re.search(
        r"(?:用|换成|使用)\s*([^，。；]+?)\s*(?:数据|数据集)", message
    )
    if source_match:
        sources = [
            item.strip()
            for item in re.split(r"[、和及]", source_match.group(1))
            if item.strip()
        ]
        known = [
            str(dataset.get("slug") or "")
            for dataset in catalog.get("datasets") or []
            if isinstance(dataset, dict)
        ]
        if sources and all(source in known for source in sources):
            operations.append(Operation(op="set_sources", sources=sources))
        elif sources:
            unresolved.append(f"数据源「{source_match.group(1).strip()}」不在当前目录")

    component_ops: list[Operation] = []
    for clause in re.split(r"[，,；;。\n]", message):
        piece = clause.strip()
        if not piece:
            continue
        match = re.search(
            r"(?:把|将)?\s*([^，,；;。]+?)\s*(?:用|做成|改为|改成)\s*"
            r"(文字|文本|明细|表格|柱状图|柱形图|条形图|折线图|趋势图|饼图|指标卡|指标)"
            r"(?:\s*按\s*([^，,；;。]+))?",
            piece,
        )
        if match is None:
            continue
        name = match.group(1).strip()
        kind = match.group(2)
        dim_text = (match.group(3) or "").strip()
        hit = _find_field(name, catalog)
        if hit is None:
            unresolved.append(f"当前目录没有「{name}」")
            continue
        dataset, field = hit
        label = str(field.get("displayName") or field.get("fieldId") or name).strip()
        is_number = _numeric(field)
        chart = {
            "文字": "table", "文本": "table", "明细": "table", "表格": "table",
            "柱状图": "bar", "柱形图": "bar", "条形图": "bar",
            "折线图": "line", "趋势图": "line", "饼图": "pie",
            "指标卡": "metric", "指标": "metric",
        }[kind]
        role = {
            "table": "detail", "bar": "compare", "line": "trend",
            "pie": "share", "metric": "metric",
        }[chart]
        dimensions = [
            str(item.get("displayName") or item.get("fieldId"))
            for item in (dataset.get("fields") or [])
            if isinstance(item, dict)
            and _norm(item.get("displayName") or item.get("fieldId")) in {
                _norm(part) for part in re.split(r"[、和及/]", dim_text) if part.strip()
            }
        ] if dim_text else []
        if dim_text and not dimensions:
            unresolved.append(f"当前目录没有维度「{dim_text}」")
            continue
        if chart in {"bar", "line", "pie"} and not is_number:
            unresolved.append(f"「{label}」不是数值字段，不能做{kind}")
            continue
        slot = IntentSlot(
            title=label,
            role=role,
            datasetSlug=str(dataset.get("slug") or "") or None,
            dimensions=[] if chart == "metric" else dimensions,
            measures=[label] if is_number else [],
            chartType=chart,
        )
        if chart in {"bar", "line", "pie"} and not slot.dimensions:
            fallback = _default_dimension(slot, catalog)
            if fallback:
                slot = slot.model_copy(update={"dimensions": [fallback]})
            else:
                unresolved.append(f"「{label}」缺少可用的对比维度")
                continue
        existing = _existing_slot(draft, label)
        if existing is not None and existing.get("title"):
            component_ops.append(
                Operation(
                    op="replace_slots",
                    selector=Selector(title=str(existing.get("title"))),
                    slot=slot,
                )
            )
        else:
            component_ops.append(Operation(op="add_slots", slots=[slot]))

    replace_match = re.search(
        r"把\s*[「“\"]?(.+?)[」”\"]?\s*(?:改成|换成|替换成|改为)\s*[「“\"]?(.+?)[」”\"]?(?:[，。；]|$)",
        message,
    )
    if replace_match and not component_ops:
        old, new = replace_match.group(1).strip(), replace_match.group(2).strip()
        if _existing_slot(draft, old) is None:
            unresolved.append(f"当前方案没有「{old}」")
        else:
            added, problem = _add_operation(new, catalog)
            if added is None:
                unresolved.append(problem)
            else:
                operations.append(
                    Operation(
                        op="replace_slots",
                        selector=Selector(title=old),
                        slot=added.slots[0],
                    )
                )
    else:
        remove_match = re.search(r"(?:删掉|删除|去掉|移除|不要)\s*[「“\"]?([^」”\"，。；]+)", message)
        if remove_match:
            old = remove_match.group(1).strip()
            if _existing_slot(draft, old) is None:
                unresolved.append(f"当前方案没有「{old}」")
            else:
                operations.append(Operation(op="remove_slots", selectors=[Selector(title=old)]))
    join_ops, join_notes = _join_verb_operations(message, draft, catalog)
    if join_ops:
        operations.extend(join_ops)
        unresolved.extend(join_notes)

    if component_ops:
        operations.extend(component_ops)

    # A bare field list or a whole-plan description ("我想要合同金额 项目名称
    # 部门名称") replaces the slot set atomically; ai-screen resolves and
    # validates every slot before writing.
    if not operations and not unresolved and looks_like_custom_request(message, catalog):
        custom_slots, custom_notes = component_slots(message, catalog)
        if custom_slots:
            operations.append(
                Operation(
                    op="set_slots",
                    slots=[IntentSlot(**item) for item in custom_slots][:12],
                )
            )
        unresolved.extend(custom_notes)

    if not operations and not unresolved:
        unresolved.append(f"无法把「{message}」映射为受限修改")
    confidence = 0.9 if operations and not unresolved else (0.6 if operations else 0.0)
    return DraftIntent(confidence=confidence, operations=operations, unresolved=unresolved)

INTENT_SYSTEM_PROMPT = """你是大屏方案的受限修改意图解析器。只输出一个 JSON object。
允许的顶层字段只有 version=1, confidence(0..1), operations(最多4个), unresolved(最多8项)。
operations 只能使用 add_slots/remove_slots/replace_slots/set_slots/set_title/set_sources/set_layout/swap_slots。
slot 只写 title/role/datasetSlug/dimensions/measures/chartType；selector 只写 title/role/datasetSlug。
业务字段名必须来自给定 catalog，禁止输出 fieldId、datasetVersionId、planId、taskId、requestId、
frame、x、y、document、operations 写屏指令或工具调用。无法确定时不要猜，写入 unresolved。
置信度低于 0.75 时只输出 unresolved，不自动改稿。
示例：
- “项目名称用文字，合同额用柱状图按部门” -> add_slots 两个：项目名称 detail/table；合同额 compare/bar，dimensions=[部门]。
- “删掉项目进度，补一个合同额” -> remove_slots 项目进度 + add_slots 合同额 metric。
- “我想加入 进度指标 柱状图” -> add_slots 进度 metric + 进度 compare/bar dimensions=[阶段]。
- “把合同额改成柱状图” -> replace_slots 合同额 compare/bar dimensions=[部门]。
- “将布局中 5 和 3 位置互换” -> swap_slots positions=[5,3]。
- “将项目进度 和 各部门合同额位置互换” -> swap_slots positions=[5,3]（先按当前方案模块顺序解析名称）。
- “标题改成合同额看板” -> set_title。
- “我想要合同金额 项目名称 部门名称” -> set_slots 一个 detail/table：dimensions=[项目名称,部门]，measures=[合同金额]。
- “合同金额按部门做柱状图，项目数量按阶段做饼图” -> set_slots 两个：合同额 compare/bar dimensions=[部门]；项目数量 share/pie dimensions=[阶段]。
同一轮多个明确修改可以拆成多个 operation，但最多 4 个；不要让一个字段的失败阻止其他明确操作。
"""


def projected_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Catalog projection safe for a model prompt: names, not resource ids."""

    datasets: list[dict[str, Any]] = []
    for dataset in catalog.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        fields: list[dict[str, Any]] = []
        for field in dataset.get("fields") or []:
            if not isinstance(field, dict):
                continue
            fields.append(
                {
                    "displayName": field.get("displayName") or field.get("fieldId"),
                    "dataType": field.get("dataType") or field.get("type"),
                    "role": field.get("role"),
                }
            )
        datasets.append(
            {
                "slug": dataset.get("slug"),
                "name": dataset.get("name"),
                "fields": fields,
            }
        )
    return {"datasets": datasets}


def parse_model_intent(raw: Any) -> DraftIntent:
    return DraftIntent.model_validate(raw)


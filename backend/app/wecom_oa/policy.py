"""审批配置校验、控件白名单、确认摘要 digest。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

ApplicantMode = Literal["fixed", "chat_partner"]

# v1 对话框可收集的标量控件（KTD5）；匹配大小写不敏感
ALLOWED_WIDGET_TYPES = frozenset(
    {"Text", "Textarea", "Number", "Money", "Date", "Selector"}
)
DENIED_WIDGET_TYPES = frozenset(
    {
        "File",
        "Table",
        "Vacation",
        "Attendance",
        "Location",
        "RelatedApproval",
        "Formula",
        "Contact",
    }
)
_ALLOWED_WIDGET_TYPES_LOWER = frozenset(t.lower() for t in ALLOWED_WIDGET_TYPES)
_DENIED_WIDGET_TYPES_LOWER = frozenset(t.lower() for t in DENIED_WIDGET_TYPES)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
# 9–12 位纯数字视为已经算好的 unix 秒；8 位 YYYYMMDD 走日历解析。
_UNIX_SECONDS_RE = re.compile(r"^\d{9,12}$")
_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_CIVIL_DATE_RE = re.compile(
    r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"
    r"(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

RESOURCE_TYPE = "wecom_oa_approval"


class WecomOaConfigError(ValueError):
    """员工审批配置不合法。"""


def normalize_widget_type(raw: str) -> str:
    return str(raw or "").strip()


def is_allowed_widget(widget_type: str) -> bool:
    wt = normalize_widget_type(widget_type)
    if not wt:
        return False
    lowered = wt.lower()
    if lowered in _DENIED_WIDGET_TYPES_LOWER:
        return False
    return lowered in _ALLOWED_WIDGET_TYPES_LOWER


def validate_approval_config(config: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化员工审批场景配置。"""
    if not isinstance(config, dict):
        raise WecomOaConfigError("审批配置必须是对象")
    template_id = str(config.get("template_id") or "").strip()
    if not template_id:
        raise WecomOaConfigError("template_id 必填")
    scenario_key = str(config.get("scenario_key") or "default").strip() or "default"
    mode = str(config.get("applicant_mode") or "").strip()
    if mode not in {"fixed", "chat_partner"}:
        raise WecomOaConfigError("applicant_mode 必须是 fixed 或 chat_partner")
    mode_a_userid = str(config.get("mode_a_userid") or "").strip() or None
    mode_a_display_name = str(config.get("mode_a_display_name") or "").strip()[:128] or None
    if mode == "fixed" and not mode_a_userid:
        raise WecomOaConfigError("Mode A（fixed）必须配置 mode_a_userid")
    field_mappings = config.get("field_mappings") or []
    if not isinstance(field_mappings, list):
        raise WecomOaConfigError("field_mappings 必须是数组")
    normalized_fields: list[dict[str, Any]] = []
    for idx, item in enumerate(field_mappings):
        if not isinstance(item, dict):
            raise WecomOaConfigError(f"field_mappings[{idx}] 必须是对象")
        widget_type = normalize_widget_type(str(item.get("widget_type") or item.get("control") or ""))
        if not is_allowed_widget(widget_type):
            raise WecomOaConfigError(
                f"不支持的控件类型: {widget_type or '(空)'}；"
                "v1 仅允许 Text/Textarea/Number/Money/Date/Selector"
            )
        control_id = str(item.get("control_id") or item.get("id") or "").strip()
        if not control_id:
            raise WecomOaConfigError(f"field_mappings[{idx}] 缺少 control_id")
        slot_key = str(item.get("slot_key") or item.get("key") or control_id).strip()
        normalized_fields.append(
            {
                "control_id": control_id,
                "widget_type": widget_type,
                "slot_key": slot_key,
                "title": str(item.get("title") or slot_key),
                "required": bool(item.get("required", True)),
            }
        )
    compliance_enabled = bool(config.get("compliance_enabled", False))
    compliance_policy_ref = str(config.get("compliance_policy_ref") or "").strip() or None
    if compliance_enabled and not compliance_policy_ref:
        raise WecomOaConfigError("开启合规时必须配置 compliance_policy_ref")
    return {
        "template_id": template_id,
        "scenario_key": scenario_key,
        "applicant_mode": mode,
        "mode_a_userid": mode_a_userid if mode == "fixed" else None,
        "mode_a_display_name": mode_a_display_name if mode == "fixed" else None,
        "field_mappings": normalized_fields,
        "compliance_enabled": compliance_enabled,
        "compliance_policy_ref": compliance_policy_ref,
        "display_name": str(config.get("display_name") or scenario_key).strip(),
    }


def intent_submission_digest(payload: dict[str, Any]) -> str:
    """对确认载荷做语义摘要；字段差异改变摘要。"""
    canonical = {
        "template_id": payload.get("template_id"),
        "scenario_key": payload.get("scenario_key"),
        "applicant_mode": payload.get("applicant_mode"),
        "creator_userid": payload.get("creator_userid"),
        "fields": payload.get("fields") or {},
    }
    raw = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_confirm_summary(
    *,
    template_id: str,
    applicant_mode: str,
    creator_userid: str,
    creator_display_name: str | None,
    fields: dict[str, Any],
    field_mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    field_lines: list[dict[str, Any]] = []
    title_by_key = {
        str(m.get("slot_key")): str(m.get("title") or m.get("slot_key"))
        for m in (field_mappings or [])
        if isinstance(m, dict)
    }
    for key, value in (fields or {}).items():
        field_lines.append(
            {
                "key": key,
                "title": title_by_key.get(key, key),
                "value": value,
            }
        )
    applicant_label = creator_display_name or creator_userid
    mode_label = "固定员工身份" if applicant_mode == "fixed" else "代聊天对方"
    return {
        "template_id": template_id,
        "applicant_mode": applicant_mode,
        "applicant_mode_label": mode_label,
        "creator_userid": creator_userid,
        "creator_display_name": creator_display_name,
        "applicant_summary": f"{mode_label}：{applicant_label}（{creator_userid}）",
        "fields": field_lines,
    }


def build_apply_data_from_fields(
    field_mappings: list[dict[str, Any]],
    fields: dict[str, Any],
) -> dict[str, Any]:
    """把槽位值映射为企微 apply_data.contents（v1 白名单控件）。"""
    contents: list[dict[str, Any]] = []
    for mapping in field_mappings:
        slot_key = str(mapping.get("slot_key") or "")
        control_id = str(mapping.get("control_id") or "")
        widget = str(mapping.get("widget_type") or "Text")
        if slot_key not in fields:
            if mapping.get("required", True):
                raise WecomOaConfigError(f"缺少必填字段: {slot_key}")
            continue
        value = fields[slot_key]
        contents.append(_control_payload(control_id, widget, value))
    return {"contents": contents}


def _control_payload(control_id: str, widget_type: str, value: Any) -> dict[str, Any]:
    wt = widget_type.lower()
    text = "" if value is None else str(value)
    if wt in {"text", "textarea"}:
        return {
            "control": "Text" if wt == "text" else "Textarea",
            "id": control_id,
            "value": {"text": text},
        }
    if wt == "number":
        return {"control": "Number", "id": control_id, "value": {"new_number": text}}
    if wt == "money":
        return {"control": "Money", "id": control_id, "value": {"new_money": text}}
    if wt == "date":
        # 企微 Date 只认 unix 秒。日历字符串会被当成缺字段（301025 item required）。
        return {
            "control": "Date",
            "id": control_id,
            "value": {"date": {"type": "day", "s_timestamp": _to_wecom_day_timestamp(value)}},
        }
    if wt == "selector":
        options = value if isinstance(value, list) else [value]
        return {
            "control": "Selector",
            "id": control_id,
            "value": {
                "selector": {
                    "type": "single",
                    "options": [{"key": str(opt)} for opt in options if opt is not None],
                }
            },
        }
    raise WecomOaConfigError(f"无法序列化控件: {widget_type}")


def _to_wecom_day_timestamp(value: Any) -> str:
    """把日期收成 Asia/Shanghai 当日 0 点的 unix 秒字符串。

    2026-10-31 → "1793376000"。已经是 unix 秒的值原样保留。
    """
    if isinstance(value, bool) or value is None:
        raise WecomOaConfigError("日期字段无效")
    if isinstance(value, (int, float)):
        seconds = int(value)
        if seconds < 0:
            raise WecomOaConfigError(f"日期时间戳无效: {value}")
        return str(seconds)
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return _shanghai_midnight_unix(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        raise WecomOaConfigError("日期字段为空")
    if _UNIX_SECONDS_RE.fullmatch(text):
        return text
    compact = _COMPACT_DATE_RE.fullmatch(text)
    if compact:
        return _shanghai_midnight_unix(
            int(compact.group(1)),
            int(compact.group(2)),
            int(compact.group(3)),
            text,
        )
    matched = _CIVIL_DATE_RE.fullmatch(text)
    if matched is None:
        raise WecomOaConfigError(f"无法解析日期: {text}")
    return _shanghai_midnight_unix(
        int(matched.group(1)),
        int(matched.group(2)),
        int(matched.group(3)),
        text,
    )


def _shanghai_midnight_unix(
    year: int, month: int, day: int, raw: str | None = None
) -> str:
    try:
        moment = datetime(year, month, day, tzinfo=_SHANGHAI)
    except ValueError as exc:
        label = raw if raw is not None else f"{year:04d}-{month:02d}-{day:02d}"
        raise WecomOaConfigError(f"无法解析日期: {label}") from exc
    return str(int(moment.timestamp()))

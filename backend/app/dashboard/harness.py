"""Harness entry point for the dashboard render capability.

The handler is intentionally thin: validate the contract, resolve chart types,
persist an immutable view, tell the host UI about it, and hand the model a
short receipt.  Anything that would need data access belongs to later phases.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.dashboard.chart_policy import Adjustment, normalize_spec
from app.dashboard.service import create_view
from app.dashboard.spec import DashboardSpecError, RenderInput

MAX_REPORTED_ERRORS = 5


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    error = {"code": code, "message": message, "retryable": False}
    error.update(details)
    return {"success": False, "error": error}


def _validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    reported: list[dict[str, str]] = []
    for item in exc.errors()[:MAX_REPORTED_ERRORS]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        context = item.get("ctx") if isinstance(item.get("ctx"), dict) else {}
        custom = context.get("error")
        if isinstance(custom, DashboardSpecError) and custom.path:
            location = custom.path
        reported.append({"path": location, "message": str(item.get("msg") or "")})
    return reported


def _adjustments_payload(adjustments: list[Adjustment]) -> list[dict[str, Any]]:
    return [
        {
            "chart_id": item.chart_id,
            "from": item.from_type,
            "to": item.to_type,
            "reason": item.reason,
        }
        for item in adjustments
    ]


def invoke_dashboard_tool(invoker: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle the ``render_dashboard`` internal capability call."""

    if not isinstance(arguments, dict):
        return _failure("INVALID_ARGUMENTS", "参数必须是 JSON 对象。")

    try:
        raw = RenderInput.model_validate(arguments)
    except ValidationError as exc:
        return _failure(
            "INVALID_SPEC",
            "大屏规格不合法，请按 errors 中的 path 修正后重试一次。",
            errors=_validation_errors(exc),
        )

    try:
        spec, adjustments = normalize_spec(raw)
    except DashboardSpecError as exc:
        return _failure(
            "INVALID_SPEC",
            exc.message,
            errors=[{"path": exc.path, "message": exc.message}],
        )

    session = getattr(invoker, "session", None)
    session_id = str(getattr(session, "id", "") or "").strip()
    if not session_id:
        return _failure("NO_SESSION", "当前回合没有会话，无法展示大屏。")

    view, reused = create_view(
        invoker.db,
        tenant_id=invoker.tenant_id,
        session_id=session_id,
        spec=spec,
        agent_id=getattr(invoker, "agent_id", None),
        user_id=getattr(session, "user_id", None),
    )

    payload = _adjustments_payload(adjustments)
    invoker._emit_trace(
        "harness_dashboard_view",
        {
            "view_id": view.id,
            "tenant_id": invoker.tenant_id,
            "title": spec.title,
            "kpi_count": len(spec.kpis),
            "chart_count": len(spec.charts),
            "adjustments": payload,
            "reused": reused,
            "tool_name": "render_dashboard",
        },
    )

    notice = "大屏已展示在对话中，请勿在回复里重复粘贴图表数据。"
    if adjustments:
        notice += " 已按可读性调整部分图表类型，界面上会标注原因。"

    return {
        "success": True,
        "data": {
            "view_id": view.id,
            "tenant_id": invoker.tenant_id,
            "title": spec.title,
            "kpi_count": len(spec.kpis),
            "chart_count": len(spec.charts),
            "adjustments": payload,
            "reused": reused,
            "notice": notice,
        },
    }

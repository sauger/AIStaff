"""Harness entry points for the dashboard capabilities.

Both handlers are intentionally thin: build a validated spec (``render`` from a
model-written spec, ``compose`` from a raw tool result), persist an immutable
view, tell the host UI about it, and hand the model a short receipt.  Neither
handler reaches any data source of its own.
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


def _warning_codes(warnings: list[Any]) -> list[str]:
    codes: list[str] = []
    for warning in warnings:
        if warning.code not in codes:
            codes.append(warning.code)
    return codes


def invoke_compose_tool(invoker: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle ``compose_dashboard``: tool result → blocks → layout → stored v2 view.

    ``arguments`` arrive with any ``sandbox_json_file`` reference already
    resolved by the invoker; the audit record keeps the unresolved form, and
    neither the receipt nor the events carry data values.
    """

    from app.dashboard.compose import ComposeError, compose_dashboard

    if not isinstance(arguments, dict):
        return _failure("INVALID_ARGUMENTS", "参数必须是 JSON 对象。")
    try:
        result = compose_dashboard(arguments)
    except ComposeError as exc:
        extra: dict[str, Any] = {"retryable": exc.retryable}
        if exc.details:
            extra["details"] = exc.details
        return _failure(exc.code, exc.message, **extra)

    if result.mode == "outline":
        return {"success": True, "data": result.outline}

    session = getattr(invoker, "session", None)
    session_id = str(getattr(session, "id", "") or "").strip()
    if not session_id:
        return _failure("NO_SESSION", "当前回合没有会话，无法展示大屏。")
    spec = result.spec
    assert spec is not None  # render mode always builds a spec

    view, reused = create_view(
        invoker.db,
        tenant_id=invoker.tenant_id,
        session_id=session_id,
        spec=spec,
        agent_id=getattr(invoker, "agent_id", None),
        user_id=getattr(session, "user_id", None),
    )
    codes = _warning_codes(spec.warnings)
    invoker._emit_trace(
        "harness_dashboard_composed",
        {
            "view_id": view.id,
            "blocks_found": result.blocks_found,
            "blocks_used": len(result.used),
            "template": result.template,
            "warnings": codes,
        },
    )
    invoker._emit_trace(
        "harness_dashboard_view",
        {
            "view_id": view.id,
            "tenant_id": invoker.tenant_id,
            "title": spec.title,
            "kpi_count": len(spec.kpis),
            "chart_count": len(spec.charts),
            "template": result.template,
            "spec_version": spec.spec_version,
            "warnings": codes,
            "reused": reused,
            "tool_name": "compose_dashboard",
        },
    )

    notice = "大屏已展示在对话中，请勿在回复里重复粘贴图表数据。"
    if spec.warnings:
        notice += " 部分块已裁剪、降级或清洗，原因见 warnings，可在回复中简要说明。"
    return {
        "success": True,
        "data": {
            "view_id": view.id,
            "title": spec.title,
            "kpi_count": len(spec.kpis),
            "chart_count": len(spec.charts),
            "template": result.template,
            "blocks": result.used,
            "warnings": [warning.model_dump(exclude_none=True) for warning in spec.warnings],
            "reused": reused,
            "notice": notice,
        },
    }

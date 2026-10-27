"""wecom_oa_approval 能力：preview / confirm / submit / query。"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session, select

from app.db.models import (
    AgentResourceBinding,
    Message,
    WecomApprovalIntent,
    utc_now,
)
from app.wecom_oa.callback import apply_status_update
from app.wecom_oa.client import WecomOaApiError, WecomOaClient
from app.wecom_oa.credentials import get_oa_credential
from app.wecom_oa.identity import resolve_wecom_userid
from app.wecom_oa.intent import (
    WecomOaIntentError,
    confirm_intent,
    create_draft_intent,
    mark_failed,
    mark_submitted,
    mark_submitting,
)
from app.wecom_oa.policy import (
    RESOURCE_TYPE,
    WecomOaConfigError,
    build_apply_data_from_fields,
    build_confirm_summary,
    intent_submission_digest,
    validate_approval_config,
)

logger = logging.getLogger(__name__)

TOOL_NAME = "wecom_oa_approval"
CAPABILITY_ID = "builtin.wecom_oa_approval"

WECOM_OA_DESCRIPTION = (
    "企微 OA 审批代发：preview 生成含申请人的确认摘要；"
    "用户确认后 submit 仅重放不可变快照；query 查询进度。"
    "不得在未确认时提交；Mode B 解析失败不得回退 Mode A。"
)

WECOM_OA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["preview", "submit", "query"],
            "description": "preview=预览确认摘要；submit=确认后提交；query=查进度",
        },
        "scenario_key": {"type": "string", "description": "员工配置中的场景键，默认 default"},
        "fields": {
            "type": "object",
            "description": "槽位字段值（preview 时收集）",
            "additionalProperties": True,
        },
        "intent_id": {"type": "string", "description": "submit/query 时的意图 ID"},
        "confirmed_digest": {
            "type": "string",
            "description": "submit 时必须回传 preview 的 submission_digest",
        },
        "sp_no": {"type": "string", "description": "query 可选，默认取意图上的 sp_no"},
    },
    "required": ["action"],
}


class WecomOaServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "WECOM_OA_ERROR"):
        super().__init__(message)
        self.code = code


def load_agent_approval_config(
    db: Session, *, tenant_id: str, agent_id: str, scenario_key: str = "default"
) -> dict[str, Any]:
    scenario_key = str(scenario_key or "default").strip() or "default"
    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == RESOURCE_TYPE,
            AgentResourceBinding.status == "active",
        )
    ).all()
    for row in bindings:
        meta = row.metadata_json or {}
        # resource_id 或 metadata.scenario_key 定位场景
        key = str(meta.get("scenario_key") or row.resource_id or "default").strip()
        if key == scenario_key:
            return validate_approval_config({**meta, "scenario_key": key})
    # 兼容：AgentProfile.metadata_json.wecom_oa_approval
    from app.db.models import AgentProfile

    agent = db.get(AgentProfile, agent_id)
    if agent and agent.tenant_id == tenant_id:
        blob = (agent.metadata_json or {}).get("wecom_oa_approval")
        if isinstance(blob, dict):
            scenarios = blob.get("scenarios")
            if isinstance(scenarios, list):
                for item in scenarios:
                    if isinstance(item, dict) and str(item.get("scenario_key") or "default") == scenario_key:
                        return validate_approval_config(item)
            elif str(blob.get("scenario_key") or "default") == scenario_key:
                return validate_approval_config(blob)
    raise WecomOaServiceError(
        f"数字员工未配置审批场景: {scenario_key}", code="WECOM_OA_CONFIG_MISSING"
    )


def save_agent_approval_config(
    db: Session,
    *,
    tenant_id: str,
    agent_id: str,
    config: dict[str, Any],
) -> AgentResourceBinding:
    normalized = validate_approval_config(config)
    scenario_key = normalized["scenario_key"]
    row = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == RESOURCE_TYPE,
            AgentResourceBinding.resource_id == scenario_key,
        )
    ).first()
    if row is None:
        row = AgentResourceBinding(
            tenant_id=tenant_id,
            agent_id=agent_id,
            resource_type=RESOURCE_TYPE,
            resource_id=scenario_key,
            status="active",
            metadata_json=normalized,
        )
        db.add(row)
    else:
        row.metadata_json = normalized
        row.status = "active"
        row.updated_at = utc_now()
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def invoke_wecom_oa_approval(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    agent_id: str | None,
    user_id: str | None,
    arguments: dict[str, Any],
    client_factory=None,
) -> dict[str, Any]:
    action = str(arguments.get("action") or "").strip()
    if not agent_id:
        return _err("WECOM_OA_NO_AGENT", "缺少 agent_id，无法加载审批配置")
    if action == "preview":
        return preview_approval(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            scenario_key=str(arguments.get("scenario_key") or "default"),
            fields=dict(arguments.get("fields") or {}),
        )
    if action == "submit":
        return submit_approval(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            intent_id=str(arguments.get("intent_id") or ""),
            confirmed_digest=str(arguments.get("confirmed_digest") or ""),
            client_factory=client_factory,
        )
    if action == "query":
        return query_approval(
            db,
            tenant_id=tenant_id,
            intent_id=str(arguments.get("intent_id") or ""),
            sp_no=str(arguments.get("sp_no") or "") or None,
            client_factory=client_factory,
        )
    return _err("WECOM_OA_BAD_ACTION", f"未知 action: {action}")


def preview_approval(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    agent_id: str,
    user_id: str | None,
    scenario_key: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    try:
        config = load_agent_approval_config(
            db, tenant_id=tenant_id, agent_id=agent_id, scenario_key=scenario_key
        )
    except (WecomOaServiceError, WecomOaConfigError) as exc:
        return _err(getattr(exc, "code", "WECOM_OA_CONFIG_ERROR"), str(exc))

    cred = get_oa_credential(db, tenant_id=tenant_id)
    if cred is None:
        return _err("WECOM_OA_NO_CREDENTIAL", "租户未配置企微 OA 自建应用凭证")

    mode = config["applicant_mode"]
    identity_source = None
    creator_userid = None
    creator_display = None
    if mode == "fixed":
        creator_userid = config["mode_a_userid"]
        creator_display = creator_userid
    else:
        if not user_id:
            return _err(
                "WECOM_OA_NO_CHAT_PARTNER",
                "Mode B 需要当前聊天对方（已登录用户）才能解析申请人",
            )
        resolved = resolve_wecom_userid(
            db,
            tenant_id=tenant_id,
            staffdeck_user_id=user_id,
            corp_scope=cred.corp_id,
        )
        if resolved is None:
            return _err(
                "WECOM_OA_IDENTITY_MISSING",
                "无法解析聊天对方的企微 userid：请联系运营补全身份绑定后重试。"
                "不会回退为 Mode A 固定身份。",
            )
        creator_userid = resolved.wecom_userid
        creator_display = resolved.display_name or resolved.wecom_userid
        identity_source = resolved.source

    try:
        # 校验字段完整性（不调用企微）
        build_apply_data_from_fields(config["field_mappings"], fields)
    except WecomOaConfigError as exc:
        return _err("WECOM_OA_FIELD_ERROR", str(exc))

    intent = create_draft_intent(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        agent_id=agent_id,
        user_id=user_id,
        corp_id=cred.corp_id,
        template_id=config["template_id"],
        scenario_key=config["scenario_key"],
        applicant_mode=mode,
        creator_userid=creator_userid,
        creator_display_name=creator_display,
        fields=fields,
        identity_source=identity_source,
        extra_payload={
            "field_mappings": config["field_mappings"],
            "compliance_enabled": config["compliance_enabled"],
            "compliance_policy_ref": config.get("compliance_policy_ref"),
        },
    )
    summary = build_confirm_summary(
        template_id=config["template_id"],
        applicant_mode=mode,
        creator_userid=creator_userid or "",
        creator_display_name=creator_display,
        fields=fields,
        field_mappings=config["field_mappings"],
    )
    card = {
        "intent_id": intent.id,
        "submission_digest": intent.submission_digest,
        "summary": summary,
        "status": intent.status,
    }
    # 写入会话消息元数据，供前端确认卡渲染
    db.add(
        Message(
            tenant_id=tenant_id,
            session_id=session_id,
            role="assistant",
            content=(
                f"请确认代发企微审批：{summary['applicant_summary']}。"
                "确认后将按此不可变摘要提交。"
            ),
            metadata_json={"wecom_oa_approval_confirm": card},
        )
    )
    db.commit()
    return {
        "ok": True,
        "action": "preview",
        "requires_confirmation": True,
        **card,
    }


def submit_approval(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    agent_id: str,
    user_id: str | None,
    intent_id: str,
    confirmed_digest: str,
    client_factory=None,
    compliance_checker=None,
) -> dict[str, Any]:
    if not intent_id:
        return _err("WECOM_OA_CONFIRMATION_REQUIRED", "submit 需要 intent_id；请先 preview 并经用户确认")
    if not confirmed_digest:
        return _err(
            "WECOM_OA_CONFIRMATION_REQUIRED",
            "缺少 confirmed_digest：未确认不得提交企微审批",
        )

    intent = db.get(WecomApprovalIntent, intent_id)
    if intent is None or intent.tenant_id != tenant_id:
        return _err("WECOM_OA_INTENT_NOT_FOUND", "意图不存在")
    if intent.session_id != session_id:
        return _err("WECOM_OA_SESSION_MISMATCH", "意图不属于当前会话")

    # 未确认：不允许触达 applyevent
    if intent.status == "draft":
        try:
            intent = confirm_intent(
                db,
                intent_id=intent_id,
                tenant_id=tenant_id,
                expected_digest=confirmed_digest,
            )
        except WecomOaIntentError as exc:
            return _err(exc.code, str(exc))
    elif intent.status == "confirmed":
        if confirmed_digest != intent.submission_digest:
            return _err(
                "WECOM_OA_DIGEST_MISMATCH",
                "confirmed_digest 与已确认快照不一致，请重新 preview",
            )
    elif intent.status == "submitted":
        return {
            "ok": True,
            "action": "submit",
            "already_submitted": True,
            "intent_id": intent.id,
            "sp_no": intent.sp_no,
            "status": intent.status,
        }
    else:
        return _err(
            "WECOM_OA_INVALID_STATE",
            f"意图状态 {intent.status} 不可提交",
        )

    # 载荷不可变：重算 digest 必须一致
    live_digest = intent_submission_digest(intent.payload_json or {})
    if live_digest != intent.submission_digest or live_digest != confirmed_digest:
        return _err(
            "WECOM_OA_DIGEST_MISMATCH",
            "意图载荷与确认摘要不一致，已拒绝提交",
        )

    payload = dict(intent.payload_json or {})
    mode = intent.applicant_mode
    creator_userid = intent.creator_userid

    # Mode B：确认后再次校验身份（防止确认后被删绑定）
    if mode == "chat_partner":
        if not user_id:
            intent = mark_failed(db, intent, reason="Mode B 缺少聊天对方用户")
            _write_failure(db, intent, intent.failure_reason or "")
            return _err("WECOM_OA_NO_CHAT_PARTNER", intent.failure_reason or "", intent=intent)
        resolved = resolve_wecom_userid(
            db,
            tenant_id=tenant_id,
            staffdeck_user_id=user_id,
            corp_scope=intent.corp_id,
        )
        if resolved is None or resolved.wecom_userid != creator_userid:
            reason = (
                "确认后无法解析聊天对方企微 userid，或与确认快照不一致；"
                "未提交，不会回退为其他申请人。"
            )
            intent = mark_failed(db, intent, reason=reason)
            _write_failure(db, intent, reason)
            return _err("WECOM_OA_IDENTITY_MISSING", reason, intent=intent)

    # 合规门（Mode B + 配置开启）
    compliance_enabled = bool(payload.get("compliance_enabled"))
    if mode == "chat_partner" and compliance_enabled:
        policy_ref = str(payload.get("compliance_policy_ref") or "")
        checker = compliance_checker or _default_compliance_check
        passed, detail = checker(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            policy_ref=policy_ref,
            fields=dict(payload.get("fields") or {}),
        )
        intent.compliance_checked = True
        intent.compliance_passed = bool(passed)
        db.add(intent)
        db.commit()
        if not passed:
            reason = f"合规检查未通过：{detail}"
            intent = mark_failed(db, intent, reason=reason)
            _write_failure(db, intent, reason)
            return _err("WECOM_OA_COMPLIANCE_FAILED", reason, intent=intent)

    if not creator_userid:
        reason = "申请人 userid 缺失，未提交"
        intent = mark_failed(db, intent, reason=reason)
        _write_failure(db, intent, reason)
        return _err("WECOM_OA_APPLICANT_MISSING", reason, intent=intent)

    cred = get_oa_credential(db, tenant_id=tenant_id, corp_id=intent.corp_id)
    if cred is None:
        reason = "OA 应用凭证缺失，未提交"
        intent = mark_failed(db, intent, reason=reason)
        _write_failure(db, intent, reason)
        return _err("WECOM_OA_NO_CREDENTIAL", reason, intent=intent)

    try:
        apply_data = build_apply_data_from_fields(
            list(payload.get("field_mappings") or []),
            dict(payload.get("fields") or {}),
        )
    except WecomOaConfigError as exc:
        intent = mark_failed(db, intent, reason=str(exc))
        _write_failure(db, intent, str(exc))
        return _err("WECOM_OA_FIELD_ERROR", str(exc), intent=intent)

    intent = mark_submitting(db, intent)
    client_kwargs = {}
    if client_factory is not None:
        client_kwargs["client_factory"] = client_factory
    client = WecomOaClient(cred, **client_kwargs)
    try:
        result = client.apply_event(
            creator_userid=creator_userid,
            template_id=intent.template_id,
            apply_data=apply_data,
            use_template_approver=1,
        )
    except WecomOaApiError as exc:
        reason = f"企微提交失败：{exc}"
        intent = mark_failed(db, intent, reason=reason)
        _write_failure(db, intent, reason)
        return _err(exc.code, reason, intent=intent)

    sp_no = str(result.get("sp_no") or "")
    audit = {
        "creator_userid": creator_userid,
        "user_id": user_id,
        "session_id": session_id,
        "agent_id": agent_id,
        "applicant_mode": mode,
        "identity_source": intent.identity_source,
    }
    intent = mark_submitted(db, intent, sp_no=sp_no, audit=audit)
    db.add(
        Message(
            tenant_id=tenant_id,
            session_id=session_id,
            role="assistant",
            content=f"企微审批已提交，单号 {sp_no}。将继续同步审批进度。",
            metadata_json={
                "wecom_oa_approval_submitted": {
                    "intent_id": intent.id,
                    "sp_no": sp_no,
                    "creator_userid": creator_userid,
                }
            },
        )
    )
    db.commit()

    # 主动查询一次进度（失败不回滚提交）
    try:
        detail = client.get_approval_detail(sp_no)
        info = detail.get("info") if isinstance(detail.get("info"), dict) else detail
        sp_status = str((info or {}).get("sp_status") or "")
        apply_status_update(
            db,
            sp_no=sp_no,
            sp_status=sp_status,
            change_event="initial_query",
            source="query",
            payload={"detail": detail},
            tenant_id=tenant_id,
        )
    except WecomOaApiError as exc:
        logger.warning("wecom_oa initial query failed sp_no=%s: %s", sp_no, exc)

    return {
        "ok": True,
        "action": "submit",
        "intent_id": intent.id,
        "sp_no": sp_no,
        "status": intent.status,
        "creator_userid": creator_userid,
        "audit": audit,
    }


def query_approval(
    db: Session,
    *,
    tenant_id: str,
    intent_id: str,
    sp_no: str | None = None,
    client_factory=None,
) -> dict[str, Any]:
    intent = db.get(WecomApprovalIntent, intent_id) if intent_id else None
    if intent is None or intent.tenant_id != tenant_id:
        return _err("WECOM_OA_INTENT_NOT_FOUND", "意图不存在")
    target_sp = sp_no or intent.sp_no
    if not target_sp:
        return {
            "ok": True,
            "action": "query",
            "intent_id": intent.id,
            "status": intent.status,
            "sp_no": None,
            "progress": intent.last_progress_json or {},
            "message": "尚未提交，无审批单号",
        }
    cred = get_oa_credential(db, tenant_id=tenant_id, corp_id=intent.corp_id)
    if cred is None:
        return _err("WECOM_OA_NO_CREDENTIAL", "OA 凭证缺失")
    kwargs = {}
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    client = WecomOaClient(cred, **kwargs)
    try:
        detail = client.get_approval_detail(target_sp)
    except WecomOaApiError as exc:
        return _err(exc.code, str(exc))
    info = detail.get("info") if isinstance(detail.get("info"), dict) else detail
    sp_status = str((info or {}).get("sp_status") or "")
    event = apply_status_update(
        db,
        sp_no=target_sp,
        sp_status=sp_status,
        change_event="manual_query",
        source="query",
        payload={"detail": detail},
        tenant_id=tenant_id,
    )
    return {
        "ok": True,
        "action": "query",
        "intent_id": intent.id,
        "sp_no": target_sp,
        "status": intent.status,
        "sp_status": sp_status,
        "progress": intent.last_progress_json,
        "event_id": event.id if event else None,
    }


def _default_compliance_check(
    db: Session,
    *,
    tenant_id: str,
    agent_id: str,
    policy_ref: str,
    fields: dict[str, Any],
) -> tuple[bool, str]:
    """复用 knowledge policy_check；失败时保守拒绝。"""
    try:
        from app.knowledge.schema import KnowledgeSearchRequest
        from app.knowledge.service import KnowledgeService

        query = "；".join(f"{k}={v}" for k, v in fields.items())
        service = KnowledgeService(db)
        # policy_ref 可作为知识库/策略提示；最小实现走 policy_check
        result = service.search(
            KnowledgeSearchRequest(
                tenant_id=tenant_id,
                agent_id=agent_id,
                query=f"[policy:{policy_ref}] {query}",
                query_type="policy_check",
            )
        )
        # 在证据/追踪中寻找明确拒绝信号；默认检索成功视为通过
        blob = ""
        if hasattr(result, "model_dump"):
            blob = str(result.model_dump()).lower()
        elif isinstance(result, dict):
            blob = str(result).lower()
        for token in ("reject", "deny", "block", "未通过", "不合规"):
            if token in blob:
                return False, f"policy_check signaled: {token}"
        return True, "policy_check completed"
    except Exception as exc:  # noqa: BLE001 — 合规失败应拒绝提交
        logger.exception("compliance check error")
        return False, f"合规检查异常：{exc}"


def _write_failure(db: Session, intent: WecomApprovalIntent, reason: str) -> None:
    db.add(
        Message(
            tenant_id=intent.tenant_id,
            session_id=intent.session_id,
            role="assistant",
            content=f"企微审批代发失败：{reason}",
            metadata_json={
                "wecom_oa_approval_failed": {
                    "intent_id": intent.id,
                    "status": intent.status,
                    "reason": reason,
                }
            },
        )
    )
    db.commit()


def _err(
    code: str, message: str, *, intent: WecomApprovalIntent | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error_code": code, "error": message}
    if intent is not None:
        out["intent_id"] = intent.id
        out["status"] = intent.status
        out["sp_no"] = intent.sp_no
    return out

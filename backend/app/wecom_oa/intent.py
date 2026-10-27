"""WecomApprovalIntent 生命周期：确认快照与状态机。"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.db.models import WecomApprovalIntent, utc_now
from app.wecom_oa.policy import intent_submission_digest

VALID_TRANSITIONS = {
    "draft": {"confirmed", "failed"},
    "confirmed": {"submitting", "failed"},
    "submitting": {"submitted", "failed"},
    "submitted": {"submitted"},  # 进度更新不改主状态
    "failed": {"confirmed"},  # 允许修正后重新确认
}


class WecomOaIntentError(RuntimeError):
    def __init__(self, message: str, *, code: str = "WECOM_OA_INTENT_ERROR"):
        super().__init__(message)
        self.code = code


def create_draft_intent(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    agent_id: str,
    user_id: str | None,
    corp_id: str,
    template_id: str,
    scenario_key: str,
    applicant_mode: str,
    creator_userid: str | None,
    creator_display_name: str | None,
    fields: dict[str, Any],
    identity_source: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> WecomApprovalIntent:
    payload = {
        "template_id": template_id,
        "scenario_key": scenario_key,
        "applicant_mode": applicant_mode,
        "creator_userid": creator_userid,
        "creator_display_name": creator_display_name,
        "fields": dict(fields or {}),
        **(extra_payload or {}),
    }
    digest = intent_submission_digest(payload)
    row = WecomApprovalIntent(
        tenant_id=tenant_id,
        session_id=session_id,
        agent_id=agent_id,
        user_id=user_id,
        status="draft",
        template_id=template_id,
        scenario_key=scenario_key,
        applicant_mode=applicant_mode,
        creator_userid=creator_userid,
        creator_display_name=creator_display_name,
        payload_json=payload,
        submission_digest=digest,
        corp_id=corp_id,
        identity_source=identity_source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def confirm_intent(
    db: Session,
    *,
    intent_id: str,
    tenant_id: str,
    expected_digest: str,
) -> WecomApprovalIntent:
    row = _get_intent(db, intent_id=intent_id, tenant_id=tenant_id)
    if row.status not in {"draft", "failed"}:
        raise WecomOaIntentError(
            f"意图状态 {row.status} 不可确认", code="WECOM_OA_INVALID_STATE"
        )
    if expected_digest != row.submission_digest:
        raise WecomOaIntentError(
            "确认摘要与意图载荷不一致，请重新预览确认",
            code="WECOM_OA_DIGEST_MISMATCH",
        )
    if not row.creator_userid:
        raise WecomOaIntentError(
            "确认时申请人 userid 尚未解析", code="WECOM_OA_APPLICANT_MISSING"
        )
    # 冻结：确认后 payload 视为不可变
    row.status = "confirmed"
    row.confirmed_at = utc_now()
    row.failure_reason = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mark_submitting(db: Session, intent: WecomApprovalIntent) -> WecomApprovalIntent:
    _transition(intent, "submitting")
    intent.updated_at = utc_now()
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def mark_submitted(
    db: Session,
    intent: WecomApprovalIntent,
    *,
    sp_no: str,
    audit: dict[str, Any] | None = None,
) -> WecomApprovalIntent:
    sp_no = str(sp_no or "").strip()
    if not sp_no:
        raise WecomOaIntentError("不得在无 sp_no 时进入 submitted", code="WECOM_OA_NO_SP_NO")
    _transition(intent, "submitted")
    intent.sp_no = sp_no
    intent.submitted_at = utc_now()
    intent.updated_at = utc_now()
    if audit:
        intent.audit_json = {**(intent.audit_json or {}), **audit}
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def mark_failed(
    db: Session,
    intent: WecomApprovalIntent,
    *,
    reason: str,
) -> WecomApprovalIntent:
    intent.status = "failed"
    intent.failure_reason = reason
    intent.updated_at = utc_now()
    # 明确：失败不得带「已提交」语义
    if not intent.sp_no:
        intent.submitted_at = None
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def get_intent_by_sp_no(
    db: Session, *, tenant_id: str | None, sp_no: str
) -> WecomApprovalIntent | None:
    stmt = select(WecomApprovalIntent).where(WecomApprovalIntent.sp_no == sp_no)
    if tenant_id:
        stmt = stmt.where(WecomApprovalIntent.tenant_id == tenant_id)
    return db.exec(stmt).first()


def list_intents_for_session(
    db: Session, *, tenant_id: str, session_id: str
) -> list[WecomApprovalIntent]:
    return list(
        db.exec(
            select(WecomApprovalIntent)
            .where(
                WecomApprovalIntent.tenant_id == tenant_id,
                WecomApprovalIntent.session_id == session_id,
            )
            .order_by(WecomApprovalIntent.created_at.desc())  # type: ignore[attr-defined]
        ).all()
    )


def _get_intent(db: Session, *, intent_id: str, tenant_id: str) -> WecomApprovalIntent:
    row = db.get(WecomApprovalIntent, intent_id)
    if row is None or row.tenant_id != tenant_id:
        raise WecomOaIntentError("意图不存在", code="WECOM_OA_INTENT_NOT_FOUND")
    return row


def _transition(intent: WecomApprovalIntent, new_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(intent.status, set())
    if new_status not in allowed:
        raise WecomOaIntentError(
            f"非法状态迁移 {intent.status} → {new_status}",
            code="WECOM_OA_INVALID_STATE",
        )
    intent.status = new_status

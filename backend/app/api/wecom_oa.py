"""企微 OA 审批：admin 凭证/临时绑定、员工配置、确认辅助、公开回调。"""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.agents import _ensure_can_manage_agent
from app.api.chat import _get_readable_chat_session
from app.db import engine, get_session
from app.db.models import AgentProfile, User, WecomApprovalIntent
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.permissions import is_admin_user
from app.wecom_oa.apps import get_default_wecom_app
from app.wecom_oa.callback import WecomOaCallbackError
from app.wecom_oa.callback_ingress import (
    accept_app_callback_event,
    callback_http_status,
    verify_app_callback_url,
)
from app.wecom_oa.credentials import WecomOaCredentialError, credential_public_view
from app.wecom_oa.identity import (
    delete_temp_binding,
    list_temp_bindings,
    upsert_temp_binding,
)
from app.wecom_oa.intent import WecomOaIntentError, confirm_intent, list_intents_for_session
from app.wecom_oa.policy import WecomOaConfigError
from app.wecom_oa.service import (
    WecomOaServiceError,
    approval_config_for_edit,
    save_agent_approval_config,
    set_agent_wecom_applicant,
    submit_approval,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enterprise/wecom-oa", tags=["wecom-oa"])
callback_router = APIRouter(prefix="/api/public/wecom-oa", tags=["wecom-oa-public"])
chat_router = APIRouter(prefix="/api/chat/wecom-oa", tags=["chat:wecom-oa"])


class OaCredentialUpsert(BaseModel):
    corp_id: str
    secret: str
    agentid: str | None = None
    callback_token: str | None = None
    callback_aes_key: str | None = None


class TempBindingUpsert(BaseModel):
    corp_scope: str
    staffdeck_user_id: str
    wecom_userid: str
    display_name: str | None = None


class AgentApprovalConfigUpsert(BaseModel):
    template_id: str
    scenario_key: str = "default"
    applicant_mode: str
    mode_a_userid: str | None = None
    mode_a_display_name: str | None = None
    field_mappings: list[dict[str, Any]] = Field(default_factory=list)
    compliance_enabled: bool = False
    compliance_policy_ref: str | None = None
    display_name: str | None = None


class AgentWecomApplicantUpsert(BaseModel):
    wecom_userid: str
    display_name: str | None = None
    scenario_key: str = "default"


class ConfirmRequest(BaseModel):
    intent_id: str
    confirmed_digest: str
    submit: bool = True


def _require_admin(tenant_id: str, user: User) -> None:
    ensure_current_user_tenant(tenant_id, user)
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="仅管理员可管理企微 OA 绑定与凭证")


def _temp_binding_view(row, *, include_updated_at: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "corp_scope": row.corp_scope,
        "staffdeck_user_id": row.staffdeck_user_id,
        "wecom_userid": row.wecom_userid,
        "display_name": row.display_name,
    }
    if include_updated_at:
        out["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return out


@router.get("/credentials")
def get_credentials(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    row = get_default_wecom_app(db, tenant_id=tenant_id)
    if row is None:
        return {"credential": None}
    return {"credential": credential_public_view(row)}


@router.put("/credentials")
def upsert_credentials(
    body: OaCredentialUpsert,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    raise HTTPException(
        status_code=409,
        detail="请改用企业管理侧栏子项「企微应用」（/enterprise/company/wecom-apps）",
    )


@router.get("/temp-identity-bindings")
def get_temp_bindings(
    tenant_id: str = Query(...),
    corp_scope: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    rows = list_temp_bindings(db, tenant_id=tenant_id, corp_scope=corp_scope)
    return {
        "bindings": [
            _temp_binding_view(r, include_updated_at=True) for r in rows
        ]
    }


@router.post("/temp-identity-bindings")
def create_temp_binding(
    body: TempBindingUpsert,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    try:
        row = upsert_temp_binding(
            db,
            tenant_id=tenant_id,
            corp_scope=body.corp_scope,
            staffdeck_user_id=body.staffdeck_user_id,
            wecom_userid=body.wecom_userid,
            display_name=body.display_name,
            actor_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"binding": _temp_binding_view(row)}


@router.delete("/temp-identity-bindings/{binding_id}", status_code=204)
def remove_temp_binding(
    binding_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    _require_admin(tenant_id, current_user)
    if not delete_temp_binding(db, tenant_id=tenant_id, binding_id=binding_id):
        raise HTTPException(status_code=404, detail="绑定不存在")
    return Response(status_code=204)


@router.get("/agents/{agent_id}/approval-config")
def get_agent_config(
    agent_id: str,
    tenant_id: str = Query(...),
    scenario_key: str = Query("default"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_current_user_tenant(tenant_id, current_user)
    agent = db.get(AgentProfile, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="员工不存在")
    return {
        "config": approval_config_for_edit(
            db, tenant_id=tenant_id, agent_id=agent_id, scenario_key=scenario_key
        )
    }


@router.put("/agents/{agent_id}/approval-config")
def put_agent_config(
    agent_id: str,
    body: AgentApprovalConfigUpsert,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_current_user_tenant(tenant_id, current_user)
    agent = db.get(AgentProfile, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="员工不存在")
    _ensure_can_manage_agent(agent, current_user)
    try:
        row = save_agent_approval_config(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            config=body.model_dump(),
        )
    except WecomOaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"config": row.metadata_json}


def _applicant_http_error(exc: WecomOaServiceError) -> NoReturn:
    status = 404 if exc.code == "WECOM_OA_AGENT_NOT_FOUND" else 400
    raise HTTPException(status_code=status, detail=str(exc)) from None


@router.put("/agents/{agent_id}/wecom-applicant")
def put_agent_wecom_applicant(
    agent_id: str,
    body: AgentWecomApplicantUpsert,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_current_user_tenant(tenant_id, current_user)
    agent = db.get(AgentProfile, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="员工不存在")
    _ensure_can_manage_agent(agent, current_user)
    try:
        applicant = set_agent_wecom_applicant(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            wecom_userid=body.wecom_userid,
            display_name=body.display_name,
            scenario_key=body.scenario_key,
        )
    except WecomOaServiceError as exc:
        _applicant_http_error(exc)
    return {"applicant": applicant}


@router.delete("/agents/{agent_id}/wecom-applicant")
def delete_agent_wecom_applicant(
    agent_id: str,
    tenant_id: str = Query(...),
    scenario_key: str = Query("default"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_current_user_tenant(tenant_id, current_user)
    agent = db.get(AgentProfile, agent_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="员工不存在")
    _ensure_can_manage_agent(agent, current_user)
    try:
        applicant = set_agent_wecom_applicant(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            wecom_userid=None,
            scenario_key=scenario_key,
        )
    except WecomOaServiceError as exc:
        _applicant_http_error(exc)
    return {"applicant": applicant}


@chat_router.get("/sessions/{session_id}/intents")
def session_intents(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_current_user_tenant(tenant_id, current_user)
    _get_readable_chat_session(db, tenant_id, current_user, session_id)
    rows = list_intents_for_session(db, tenant_id=tenant_id, session_id=session_id)
    return {
        "intents": [
            {
                "id": r.id,
                "status": r.status,
                "sp_no": r.sp_no,
                "applicant_mode": r.applicant_mode,
                "creator_userid": r.creator_userid,
                "submission_digest": r.submission_digest,
                "failure_reason": r.failure_reason,
                "last_progress": r.last_progress_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@chat_router.post("/confirm")
def confirm_and_maybe_submit(
    body: ConfirmRequest,
    tenant_id: str = Query(...),
    session_id: str = Query(...),
    agent_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_current_user_tenant(tenant_id, current_user)
    _get_readable_chat_session(db, tenant_id, current_user, session_id)
    intent_row = db.get(WecomApprovalIntent, body.intent_id)
    if (
        intent_row is None
        or intent_row.tenant_id != tenant_id
        or intent_row.session_id != session_id
    ):
        raise HTTPException(status_code=404, detail="意图不存在")
    if not body.submit:
        try:
            intent = confirm_intent(
                db,
                intent_id=body.intent_id,
                tenant_id=tenant_id,
                expected_digest=body.confirmed_digest,
            )
        except WecomOaIntentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "intent_id": intent.id, "status": intent.status}
    return submit_approval(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        agent_id=agent_id,
        user_id=current_user.id,
        intent_id=body.intent_id,
        confirmed_digest=body.confirmed_digest,
    )


@chat_router.post("/dismiss")
def dismiss_confirm(
    tenant_id: str = Query(...),
    intent_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_current_user_tenant(tenant_id, current_user)
    intent = db.get(WecomApprovalIntent, intent_id)
    if intent is None or intent.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="意图不存在")
    _get_readable_chat_session(db, tenant_id, current_user, intent.session_id)
    if intent.status in {"draft", "confirmed"} and not intent.sp_no:
        intent.status = "failed"
        intent.failure_reason = "用户取消确认，未提交"
        db.add(intent)
        db.commit()
    return {"ok": True, "intent_id": intent.id, "status": intent.status}


@callback_router.get("/callback/{tenant_id}")
def callback_verify(
    tenant_id: str,
    msg_signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    echostr: str = Query(""),
) -> Response:
    if not all((msg_signature, timestamp, nonce, echostr)):
        raise HTTPException(status_code=400, detail="缺少回调校验参数")
    with Session(engine) as db:
        app = get_default_wecom_app(db, tenant_id=tenant_id)
        if app is None:
            raise HTTPException(status_code=404, detail="未配置默认企微应用")
        logger.info(
            "wecom oa legacy callback tenant_id=%s app_id=%s",
            tenant_id,
            app.id,
        )
        try:
            plain = verify_app_callback_url(
                db,
                app,
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
                echostr=echostr,
            )
        except (WecomOaCallbackError, WecomOaCredentialError) as exc:
            raise HTTPException(
                status_code=callback_http_status(exc), detail=str(exc)
            ) from None
    return Response(content=plain, media_type="text/plain")


@callback_router.post("/callback/{tenant_id}")
async def callback_event(
    tenant_id: str,
    request: Request,
    msg_signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
) -> Response:
    body = await request.body()
    with Session(engine) as db:
        app = get_default_wecom_app(db, tenant_id=tenant_id)
        if app is None:
            raise HTTPException(status_code=404, detail="未配置默认企微应用")
        logger.info(
            "wecom oa legacy callback tenant_id=%s app_id=%s",
            tenant_id,
            app.id,
        )
        try:
            accept_app_callback_event(
                db,
                app,
                body=body,
                msg_signature=msg_signature,
                timestamp=timestamp,
                nonce=nonce,
            )
        except (WecomOaCallbackError, WecomOaCredentialError) as exc:
            raise HTTPException(
                status_code=callback_http_status(exc), detail=str(exc)
            ) from None
    return Response(content="success", media_type="text/plain")

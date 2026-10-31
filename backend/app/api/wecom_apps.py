"""企微自建应用管理接口与按应用回调。"""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlmodel import Session

from app.db import engine, get_session
from app.db.models import User
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.permissions import is_admin_user
from app.wecom_oa.apps import (
    WecomAppError,
    app_public_view,
    check_wecom_app,
    create_wecom_app,
    delete_wecom_app,
    get_active_callback_app,
    get_wecom_app,
    list_wecom_apps,
    set_default_wecom_app,
    update_wecom_app,
)
from app.wecom_oa.callback import WecomOaCallbackError
from app.wecom_oa.callback_ingress import (
    accept_app_callback_event,
    callback_http_status,
    verify_app_callback_url,
)
from app.wecom_oa.credentials import WecomOaCredentialError
from app.wecom_oa.directory import (
    WecomDirectoryError,
    load_department_tree,
    load_department_users,
)
from app.wecom_oa.templates import (
    WecomTemplateError,
    assign_approval_template,
    load_visible_approval_templates,
    lookup_approval_template,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enterprise/wecom-apps", tags=["wecom-apps"])
callback_router = APIRouter(prefix="/api/public/wecom-app", tags=["wecom-app-public"])


class WecomAppCreate(BaseModel):
    name: str
    corp_id: str
    agentid: str
    secret: str
    callback_token: str | None = None
    callback_aes_key: str | None = None


class WecomAppPatch(BaseModel):
    name: str | None = None
    corp_id: str | None = None
    agentid: str | None = None
    status: str | None = None
    secret: str | None = None
    callback_token: str | None = None
    callback_aes_key: str | None = None


class WecomDirectoryDepartment(BaseModel):
    id: int
    name: str
    parent_id: int
    order: int


class WecomDirectoryDepartmentsResponse(BaseModel):
    app_id: str
    app_name: str
    corp_id: str
    departments: list[WecomDirectoryDepartment]


class WecomDirectoryUser(BaseModel):
    userid: str
    name: str
    email: str
    department_ids: list[int]


class WecomDirectoryUsersResponse(BaseModel):
    department_id: int
    users: list[WecomDirectoryUser]


class ApprovalTemplateLookup(BaseModel):
    template_id: str


class ApprovalTemplateAssign(BaseModel):
    template_id: str
    template_name: str | None = None
    agent_ids: list[str]
    scenario_key: str = "default"


def _require_admin(tenant_id: str, user: User) -> None:
    ensure_current_user_tenant(tenant_id, user)
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="仅管理员可管理企微自建应用")


def _raise_app_error(exc: WecomAppError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


def _require_directory_admin(tenant_id: str, user: User) -> None:
    ensure_current_user_tenant(tenant_id, user)
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="仅企业管理员可读取企微通讯录")


def _raise_directory_error(exc: WecomDirectoryError) -> NoReturn:
    logger.warning(
        "wecom directory rejected code=%s status=%s errcode=%s",
        exc.code,
        exc.status_code,
        exc.errcode,
    )
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


def _raise_callback_error(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=callback_http_status(exc), detail=str(exc)) from None


@router.get("")
def list_apps(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    rows = list_wecom_apps(db, tenant_id=tenant_id)
    return {"apps": [app_public_view(row) for row in rows]}


@router.get("/directory/departments", response_model=WecomDirectoryDepartmentsResponse)
def get_directory_departments(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_directory_admin(tenant_id, current_user)
    try:
        return load_department_tree(db, tenant_id=tenant_id)
    except WecomDirectoryError as exc:
        _raise_directory_error(exc)


@router.get(
    "/directory/departments/{department_id}/users",
    response_model=WecomDirectoryUsersResponse,
)
def get_directory_users(
    department_id: int,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_directory_admin(tenant_id, current_user)
    if department_id <= 0:
        raise HTTPException(status_code=400, detail="部门编号无效")
    try:
        return load_department_users(
            db,
            tenant_id=tenant_id,
            department_id=department_id,
        )
    except WecomDirectoryError as exc:
        _raise_directory_error(exc)


@router.post("")
def post_app(
    body: WecomAppCreate,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    try:
        row = create_wecom_app(
            db,
            tenant_id=tenant_id,
            name=body.name,
            corp_id=body.corp_id,
            agentid=body.agentid,
            secret=body.secret,
            callback_token=body.callback_token,
            callback_aes_key=body.callback_aes_key,
            created_by_user_id=current_user.id,
        )
    except WecomAppError as exc:
        _raise_app_error(exc)
    return {"app": app_public_view(row)}


@router.patch("/{app_id}")
def patch_app(
    app_id: str,
    body: WecomAppPatch,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    row = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if row is None:
        raise HTTPException(status_code=404, detail="应用不存在")
    try:
        updated = update_wecom_app(
            db,
            row,
            fields=set(body.model_fields_set),
            name=body.name,
            corp_id=body.corp_id,
            agentid=body.agentid,
            status=body.status,
            secret=body.secret,
            callback_token=body.callback_token,
            callback_aes_key=body.callback_aes_key,
        )
    except WecomAppError as exc:
        _raise_app_error(exc)
    return {"app": app_public_view(updated)}


@router.delete("/{app_id}", status_code=204)
def remove_app(
    app_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    _require_admin(tenant_id, current_user)
    row = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if row is None:
        raise HTTPException(status_code=404, detail="应用不存在")
    try:
        delete_wecom_app(db, row)
    except WecomAppError as exc:
        _raise_app_error(exc)
    return Response(status_code=204)


@router.post("/{app_id}/default")
def post_default(
    app_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    row = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if row is None:
        raise HTTPException(status_code=404, detail="应用不存在")
    try:
        updated = set_default_wecom_app(db, row)
    except WecomAppError as exc:
        _raise_app_error(exc)
    return {"app": app_public_view(updated)}


@router.post("/{app_id}/check")
def post_check(
    app_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    row = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if row is None:
        raise HTTPException(status_code=404, detail="应用不存在")
    try:
        return check_wecom_app(db, row)
    except WecomAppError as exc:
        _raise_app_error(exc)


def _raise_template_error(exc: WecomTemplateError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


@router.get("/{app_id}/approval-templates")
def get_approval_templates(
    app_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    try:
        return load_visible_approval_templates(db, tenant_id=tenant_id, app_id=app_id)
    except WecomTemplateError as exc:
        _raise_template_error(exc)


@router.post("/{app_id}/approval-templates/lookup")
def post_approval_template_lookup(
    app_id: str,
    body: ApprovalTemplateLookup,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    try:
        return lookup_approval_template(
            db,
            tenant_id=tenant_id,
            app_id=app_id,
            template_id=body.template_id,
        )
    except WecomTemplateError as exc:
        _raise_template_error(exc)


@router.post("/{app_id}/approval-templates/assignments")
def post_approval_template_assignments(
    app_id: str,
    body: ApprovalTemplateAssign,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_admin(tenant_id, current_user)
    try:
        rows = assign_approval_template(
            db,
            tenant_id=tenant_id,
            app_id=app_id,
            template_id=body.template_id,
            template_name=body.template_name,
            agent_ids=body.agent_ids,
            scenario_key=body.scenario_key,
        )
    except WecomTemplateError as exc:
        _raise_template_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"assignments": rows}


@callback_router.get("/callback/{tenant_id}/{app_id}")
def callback_verify(
    tenant_id: str,
    app_id: str,
    msg_signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    echostr: str = Query(""),
) -> Response:
    if not all((msg_signature, timestamp, nonce, echostr)):
        raise HTTPException(status_code=400, detail="缺少回调校验参数")
    with Session(engine) as db:
        app = get_active_callback_app(db, tenant_id=tenant_id, app_id=app_id)
        if app is None:
            raise HTTPException(status_code=404, detail="应用不存在")
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
            _raise_callback_error(exc)
    return Response(content=plain, media_type="text/plain")


@callback_router.post("/callback/{tenant_id}/{app_id}")
async def callback_event(
    tenant_id: str,
    app_id: str,
    request: Request,
    msg_signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
) -> Response:
    body = await request.body()
    with Session(engine) as db:
        app = get_active_callback_app(db, tenant_id=tenant_id, app_id=app_id)
        if app is None:
            raise HTTPException(status_code=404, detail="应用不存在")
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
            _raise_callback_error(exc)
    return Response(content="success", media_type="text/plain")

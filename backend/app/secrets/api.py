from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db import get_session
from app.db.models import User
from app.secrets.errors import SecretError
from app.secrets.schema import (
    LoginGuideCopyRequest,
    LoginGuideGalleryItem,
    LoginGuideListResponse,
    LoginGuideRead,
    LoginGuideWriteRequest,
    LoginResult,
    SecretCreateRequest,
    SecretListResponse,
    SecretRead,
    SecretUpdateRequest,
)
from app.secrets.service import (
    can_write_secrets,
    copy_login_guide,
    create_login_guide,
    create_secret,
    delete_login_guide,
    delete_secret,
    ensure_reader,
    ensure_writer,
    list_gallery,
    list_login_guides,
    list_secrets,
    perform_secure_login,
    update_login_guide,
    update_secret,
)
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.tenant import ensure_tenant

secrets_router = APIRouter(
    prefix="/api/enterprise/secrets",
    tags=["enterprise:secrets"],
    dependencies=[Depends(get_current_user)],
)

guides_router = APIRouter(
    prefix="/api/enterprise/login-guides",
    tags=["enterprise:login-guides"],
    dependencies=[Depends(get_current_user)],
)

SessionDep = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _raise(error: SecretError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.as_http_detail()) from error


@secrets_router.get("", response_model=SecretListResponse)
def get_secrets(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> SecretListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return list_secrets(
            db, tenant_id, agent_id, can_write=can_write_secrets(agent, current_user)
        )
    except SecretError as error:
        _raise(error)
        raise


@secrets_router.post("", response_model=SecretRead)
def post_secret(
    request: SecretCreateRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> SecretRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        return create_secret(db, request.tenant_id, agent_id, request)
    except SecretError as error:
        _raise(error)
        raise


@secrets_router.patch("/{secret_id}", response_model=SecretRead)
def patch_secret(
    secret_id: str,
    request: SecretUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> SecretRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        return update_secret(db, request.tenant_id, agent_id, secret_id, request)
    except SecretError as error:
        _raise(error)
        raise


@secrets_router.delete("/{secret_id}")
def remove_secret(
    secret_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> dict[str, str]:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_writer(db, tenant_id, agent_id, current_user)
        delete_secret(db, tenant_id, agent_id, secret_id)
        return {"status": "deleted"}
    except SecretError as error:
        _raise(error)
        raise


@guides_router.get("/gallery", response_model=list[LoginGuideGalleryItem])
def get_login_guide_gallery(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
) -> list[LoginGuideGalleryItem]:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    return list_gallery(db, tenant_id)


@guides_router.get("", response_model=LoginGuideListResponse)
def get_login_guides(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> LoginGuideListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return list_login_guides(
            db, tenant_id, agent_id, can_write=can_write_secrets(agent, current_user)
        )
    except SecretError as error:
        _raise(error)
        raise


@guides_router.post("", response_model=LoginGuideRead)
def post_login_guide(
    request: LoginGuideWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> LoginGuideRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        return create_login_guide(db, request.tenant_id, agent_id, request)
    except SecretError as error:
        _raise(error)
        raise


@guides_router.patch("/{guide_id}", response_model=LoginGuideRead)
def patch_login_guide(
    guide_id: str,
    request: LoginGuideWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> LoginGuideRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        return update_login_guide(db, request.tenant_id, agent_id, guide_id, request)
    except SecretError as error:
        _raise(error)
        raise


@guides_router.delete("/{guide_id}")
def remove_login_guide(
    guide_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> dict[str, str]:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_writer(db, tenant_id, agent_id, current_user)
        delete_login_guide(db, tenant_id, agent_id, guide_id)
        return {"status": "deleted"}
    except SecretError as error:
        _raise(error)
        raise


@guides_router.post("/{guide_id}/test-login", response_model=LoginResult)
def console_test_login(
    guide_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> LoginResult:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_writer(db, tenant_id, agent_id, current_user)
        return perform_secure_login(
            db,
            tenant_id,
            agent_id,
            login_guide_id=guide_id,
            persist_snapshot=True,
        )
    except SecretError as error:
        _raise(error)
        raise


@guides_router.post("/{guide_id}/copy", response_model=LoginGuideRead)
def copy_published_login_guide(
    guide_id: str,
    request: LoginGuideCopyRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> LoginGuideRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        return copy_login_guide(db, request.tenant_id, guide_id, request, current_user)
    except SecretError as error:
        _raise(error)
        raise

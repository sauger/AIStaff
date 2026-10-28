from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.contacts.errors import ContactError
from app.contacts.schema import ContactListResponse, ContactRead, ContactWriteRequest
from app.contacts.service import (
    can_write_contacts,
    create_contact,
    delete_contact,
    ensure_reader,
    ensure_writer,
    list_contacts,
    update_contact,
)
from app.db import get_session
from app.db.models import User
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.tenant import ensure_tenant

router = APIRouter(
    prefix="/api/enterprise/contacts",
    tags=["enterprise:contacts"],
    dependencies=[Depends(get_current_user)],
)

SessionDep = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _raise(error: ContactError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.as_http_detail()) from error


@router.get("", response_model=ContactListResponse)
def get_contacts(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> ContactListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return list_contacts(
            db, tenant_id, agent_id, can_write=can_write_contacts(agent, current_user)
        )
    except ContactError as error:
        _raise(error)
        raise


@router.post("", response_model=ContactRead)
def post_contact(
    request: ContactWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> ContactRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        return create_contact(db, request.tenant_id, agent_id, request)
    except ContactError as error:
        _raise(error)
        raise


@router.patch("/{contact_id}", response_model=ContactRead)
def patch_contact(
    contact_id: str,
    request: ContactWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> ContactRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        return update_contact(db, request.tenant_id, agent_id, contact_id, request)
    except ContactError as error:
        _raise(error)
        raise


@router.delete("/{contact_id}")
def remove_contact(
    contact_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> dict[str, str]:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_writer(db, tenant_id, agent_id, current_user)
        delete_contact(db, tenant_id, agent_id, contact_id)
        return {"status": "deleted"}
    except ContactError as error:
        _raise(error)
        raise

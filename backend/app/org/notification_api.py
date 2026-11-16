"""Authenticated notification settings. Business events use stage_notification internally."""

from __future__ import annotations

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import ChannelBinding, EnterprisePerson, PersonContact
from app.org import notifications as service
from app.org.api import CurrentUser, SessionDep, _guard, _guard_member, _raise, router
from app.org.errors import OrgError
from app.security.permissions import is_admin_user


class BindingRequest(BaseModel):
    tenant_id: str
    contact_id: str
    binding_id: str


class CodeRequest(BaseModel):
    tenant_id: str
    binding_id: str


class TestRequest(BaseModel):
    tenant_id: str
    request_id: str = Field(min_length=1, max_length=100)


def access(db, tenant_id, person_id, user):
    _guard_member(db, tenant_id, user)
    person = service.person_for(db, tenant_id, person_id)
    if not is_admin_user(user) and person.primary_user_id != user.id:
        raise HTTPException(403, "只能管理本人的企微通知。")
    return person


@router.get("/notification-self")
def notification_self(db: SessionDep, current_user: CurrentUser, tenant_id: str = Query(...)):
    _guard_member(db, tenant_id, current_user)
    person = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant_id,
            EnterprisePerson.primary_user_id == current_user.id,
        )
    ).first()
    return {"person_id": person.id if person else None}


@router.get("/notification-channels")
def notification_channels(db: SessionDep, current_user: CurrentUser, tenant_id: str = Query(...)):
    _guard_member(db, tenant_id, current_user)
    rows = []
    for channel in db.exec(
        select(ChannelBinding).where(
            ChannelBinding.tenant_id == tenant_id,
            ChannelBinding.channel == "wecom",
            ChannelBinding.status == "active",
        )
    ).all():
        try:
            service.channel_for(db, tenant_id, channel.id)
        except OrgError:
            continue
        rows.append(
            {
                "id": channel.id,
                "name": channel.name or "企微机器人",
                "scope": service.external_account_scope(db, channel),
                "scope_label": service.scope_label(channel),
                "connected": channel.connected,
            }
        )
    return {"channels": rows}


@router.get("/notification-contacts")
def notification_contacts(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    binding_id: str = Query(...),
):
    _guard(db, tenant_id, current_user)
    try:
        channel = service.channel_for(db, tenant_id, binding_id)
        rows = []
        for contact in db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                PersonContact.status == "active",
                PersonContact.merged_into_id.is_(None),
                PersonContact.wecom_corp_id == service.external_account_scope(db, channel),
            )
        ).all():
            if (
                contact.wecom_userid
                and not contact.wecom_userid.startswith("group:")
                and service.matching_sessions(db, contact, channel)
            ):
                rows.append(
                    {
                        "id": contact.id,
                        "name": contact.display_name,
                        "userid": contact.wecom_userid,
                        "scope": contact.wecom_corp_id,
                        "scope_label": service.scope_label(channel),
                    }
                )
        return {"contacts": rows}
    except OrgError as error:
        _raise(error)


@router.get("/people/{person_id}/wecom-notification")
def read_notification(
    person_id: str, db: SessionDep, current_user: CurrentUser, tenant_id: str = Query(...)
):
    try:
        access(db, tenant_id, person_id, current_user)
        return service.status_view(db, tenant_id, person_id)
    except OrgError as error:
        _raise(error)


@router.put("/people/{person_id}/wecom-notification")
def bind_notification(
    person_id: str, request: BindingRequest, db: SessionDep, current_user: CurrentUser
):
    _guard(db, request.tenant_id, current_user)
    try:
        service.bind(
            db,
            request.tenant_id,
            person_id,
            request.contact_id,
            request.binding_id,
            current_user.id,
        )
        db.commit()
        return service.status_view(db, request.tenant_id, person_id)
    except OrgError as error:
        db.rollback()
        _raise(error)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该联系人已被绑定，请刷新后核对。") from None


@router.delete("/people/{person_id}/wecom-notification")
def unbind_notification(
    person_id: str, db: SessionDep, current_user: CurrentUser, tenant_id: str = Query(...)
):
    try:
        access(db, tenant_id, person_id, current_user)
        service.revoke(db, tenant_id, person_id, current_user.id)
        db.commit()
        return service.status_view(db, tenant_id, person_id)
    except OrgError as error:
        db.rollback()
        _raise(error)


@router.post("/people/{person_id}/wecom-notification/code")
def create_notification_code(
    person_id: str, request: CodeRequest, db: SessionDep, current_user: CurrentUser
):
    try:
        access(db, request.tenant_id, person_id, current_user)
        result = service.issue_code(
            db, request.tenant_id, person_id, request.binding_id, current_user
        )
        db.commit()
        return result
    except OrgError as error:
        db.rollback()
        _raise(error)


@router.post("/people/{person_id}/wecom-notification/test")
def test_notification(
    person_id: str, request: TestRequest, db: SessionDep, current_user: CurrentUser
):
    try:
        access(db, request.tenant_id, person_id, current_user)
        link, channel, _ = service.resolve_target(db, request.tenant_id, person_id)
        row = service.stage_notification(
            db,
            tenant_id=request.tenant_id,
            recipient_person_id=person_id,
            source_agent_id=channel.agent_id,
            event_key=f"test:{link.id}:{request.request_id}",
            text="这是一条 StaffDeck 员工企微通知测试消息，无需进行任何业务操作。",
            is_test=True,
        )
        db.commit()
        return {"delivery_id": row.id, "status": row.status}
    except OrgError as error:
        db.rollback()
        _raise(error)

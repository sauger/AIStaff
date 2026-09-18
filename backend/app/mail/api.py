from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db import get_session
from app.db.models import User
from app.mail.errors import MailError
from app.mail.schema import (
    MAIL_DEFAULT_PAGE_SIZE,
    MAIL_MAX_PAGE_SIZE,
    MailboxConfigRequest,
    MailboxEnabledRequest,
    MailboxStatusRead,
    MailComposeRequest,
    MailListResponse,
    MailMessageRead,
    MailProbeResult,
    MailSendResult,
    MailTeachRequest,
)
from app.mail.service import (
    can_send_mail,
    clear_mailbox,
    compose,
    ensure_reader,
    ensure_sender,
    get_mailbox,
    get_message,
    list_drafts,
    list_inbox,
    list_sent,
    mailbox_status,
    message_read,
    probe_mailbox,
    reply_defaults,
    save_mailbox,
    send_draft,
)
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.tenant import ensure_tenant

router = APIRouter(
    prefix="/api/enterprise/mail",
    tags=["enterprise:mail"],
    dependencies=[Depends(get_current_user)],
)

SessionDep = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _raise(error: MailError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.as_http_detail()) from error


@router.get("/mailbox", response_model=MailboxStatusRead)
def get_mailbox_status(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> MailboxStatusRead:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return mailbox_status(
            db,
            tenant_id,
            agent_id,
            can_configure=can_send_mail(agent, current_user),
            can_send=can_send_mail(agent, current_user),
        )
    except MailError as error:
        _raise(error)
        raise


@router.put("/mailbox", response_model=MailboxStatusRead)
def upsert_mailbox(
    request: MailboxConfigRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> MailboxStatusRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_sender(db, request.tenant_id, agent_id, current_user)
        status, _probe = save_mailbox(db, request.tenant_id, agent_id, request)
        return status
    except MailError as error:
        _raise(error)
        raise


@router.delete("/mailbox", response_model=MailboxStatusRead)
def delete_mailbox(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> MailboxStatusRead:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_sender(db, tenant_id, agent_id, current_user)
        return clear_mailbox(db, tenant_id, agent_id)
    except MailError as error:
        _raise(error)
        raise


@router.post("/mailbox/probe", response_model=MailProbeResult)
def probe_saved_mailbox(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> MailProbeResult:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_sender(db, tenant_id, agent_id, current_user)
        from app.mail.service import get_mailbox as load_mailbox

        row = load_mailbox(db, tenant_id, agent_id)
        if row is None:
            raise MailError("MAIL_NOT_CONFIGURED", "这个员工还没有配置邮箱。")
        return probe_mailbox(row)
    except MailError as error:
        _raise(error)
        raise


@router.put("/mailbox/enabled", response_model=MailboxStatusRead)
def set_mailbox_enabled_status(
    request: MailboxEnabledRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> MailboxStatusRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        agent = ensure_sender(db, request.tenant_id, agent_id, current_user)
        row = get_mailbox(db, request.tenant_id, agent_id)
        if row is None:
            raise MailError("MAIL_NOT_CONFIGURED", "这个员工还没有配置邮箱，不能停用或启用。")
        from app.mail.inbound import set_mailbox_enabled

        set_mailbox_enabled(db, row, enabled=request.enabled)
        return mailbox_status(
            db,
            request.tenant_id,
            agent_id,
            can_configure=can_send_mail(agent, current_user),
            can_send=can_send_mail(agent, current_user),
        )
    except MailError as error:
        _raise(error)
        raise


@router.get("/inbox", response_model=MailListResponse)
def get_inbox(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAIL_MAX_PAGE_SIZE)] = MAIL_DEFAULT_PAGE_SIZE,
) -> MailListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return list_inbox(
            db,
            tenant_id,
            agent_id,
            can_send=can_send_mail(agent, current_user),
            sync=True,
            page=page,
            page_size=page_size,
        )
    except MailError as error:
        _raise(error)
        raise


@router.get("/sent", response_model=MailListResponse)
def get_sent(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAIL_MAX_PAGE_SIZE)] = MAIL_DEFAULT_PAGE_SIZE,
) -> MailListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return list_sent(
            db,
            tenant_id,
            agent_id,
            can_send=can_send_mail(agent, current_user),
            page=page,
            page_size=page_size,
        )
    except MailError as error:
        _raise(error)
        raise


@router.get("/drafts", response_model=MailListResponse)
def get_drafts(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> MailListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return list_drafts(
            db, tenant_id, agent_id, can_send=can_send_mail(agent, current_user)
        )
    except MailError as error:
        _raise(error)
        raise


@router.get("/messages/{message_id}", response_model=MailMessageRead)
def read_message(
    message_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    mark_read: bool = Query(True),
) -> MailMessageRead:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        row = get_message(
            db,
            tenant_id,
            agent_id,
            message_id,
            mark_read=mark_read and can_send_mail(agent, current_user),
        )
        return message_read(row, can_teach=can_send_mail(agent, current_user))
    except MailError as error:
        _raise(error)
        raise


@router.get("/messages/{message_id}/reply")
def get_reply_defaults(
    message_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> dict[str, object]:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_reader(db, tenant_id, agent_id, current_user)
        row = get_message(db, tenant_id, agent_id, message_id, mark_read=False)
        return reply_defaults(row)
    except MailError as error:
        _raise(error)
        raise


@router.get("/pending-owner", response_model=MailListResponse)
def get_pending_owner(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> MailListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        from app.mail.inbound import list_pending_owner_messages, mailbox_is_enabled
        from app.mail.service import get_mailbox as load_mailbox

        mailbox = load_mailbox(db, tenant_id, agent_id)
        rows = list_pending_owner_messages(db, tenant_id, agent_id)
        can_send = can_send_mail(agent, current_user)
        return MailListResponse(
            agent_id=agent_id,
            folder="inbox",
            configured=mailbox is not None,
            can_send=can_send and (mailbox is None or mailbox_is_enabled(mailbox)),
            empty_reason="没有待主人处理的来信。" if not rows else None,
            messages=[message_read(item, include_body=False, can_teach=can_send) for item in rows],
            page=1,
            page_size=max(len(rows), 1),
            total=len(rows),
            mailbox_enabled=mailbox is not None and mailbox_is_enabled(mailbox),
            pending_owner_count=len(rows),
        )
    except MailError as error:
        _raise(error)
        raise


@router.post("/messages/{message_id}/teach", response_model=MailMessageRead)
def teach_message(
    message_id: str,
    request: MailTeachRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> MailMessageRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        agent = ensure_sender(db, request.tenant_id, agent_id, current_user)
        row = get_message(db, request.tenant_id, agent_id, message_id, mark_read=False)
        mailbox = get_mailbox(db, request.tenant_id, agent_id)
        if mailbox is None:
            raise MailError("MAIL_NOT_CONFIGURED", "这个员工还没有配置邮箱。")
        from app.mail.inbound import teach_from_console

        taught = teach_from_console(db, mailbox, agent, row, request)
        return message_read(taught, can_teach=True)
    except MailError as error:
        _raise(error)
        raise


@router.post("/messages", response_model=MailSendResult)
def send_or_draft(
    request: MailComposeRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> MailSendResult:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_sender(db, request.tenant_id, agent_id, current_user)
        request.source = "console"
        return compose(
            db,
            request.tenant_id,
            agent_id,
            request,
            confirmed=not request.as_draft,
        )
    except MailError as error:
        _raise(error)
        raise


@router.post("/drafts/{draft_id}/send", response_model=MailSendResult)
def confirm_draft(
    draft_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
) -> MailSendResult:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_sender(db, tenant_id, agent_id, current_user)
        return send_draft(db, tenant_id, agent_id, draft_id, console=True)
    except MailError as error:
        _raise(error)
        raise

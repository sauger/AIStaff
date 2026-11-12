"""Shared header synchronization and lazy message content loading."""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import EmployeeMailbox, EmployeeMailMessage, utc_now
from app.mail.errors import MailError
from app.mail.inbound import mailbox_is_enabled, require_enabled_mailbox
from app.mail.schema import MailListResponse
from app.mail.transport import get_transport


def fetch_header_rows(
    db: Session,
    mailbox,
    *,
    offset: int = 0,
    limit: int = 20,
    subject: str = "",
    from_address: str = "",
    enroll_inbound: bool = False,
):
    from app.mail.service import _connection

    require_enabled_mailbox(mailbox)
    fetched = get_transport().fetch_headers(
        _connection(mailbox),
        subject=subject,
        from_address=from_address,
        offset=offset,
        limit=limit,
    )
    rows, seen = [], set()
    for item in fetched.messages:
        dedupe = item.rfc_message_id or f"uid:{item.uid}"
        row = db.exec(
            select(EmployeeMailMessage).where(
                EmployeeMailMessage.tenant_id == mailbox.tenant_id,
                EmployeeMailMessage.agent_id == mailbox.agent_id,
                EmployeeMailMessage.folder == "inbox",
                EmployeeMailMessage.dedupe_key == dedupe,
            )
        ).first()
        if row is None:
            row = EmployeeMailMessage(
                tenant_id=mailbox.tenant_id,
                agent_id=mailbox.agent_id,
                folder="inbox",
                status="unread" if item.unseen else "read",
                direction="inbound",
                source="imap",
                from_address=item.from_address,
                to_json=item.to,
                cc_json=item.cc,
                bcc_json=[],
                subject=item.subject,
                body_text="",
                attachments_json=[],
                dedupe_key=dedupe,
                received_at=item.date or utc_now(),
                imap_uid=item.uid,
                rfc_message_id=item.rfc_message_id or None,
                triage_state="pending" if enroll_inbound else "headers_only",
                metadata_json={"content_state": "headers_only"},
            )
        elif enroll_inbound and row.triage_state == "headers_only":
            row.triage_state = "pending"
        # Refresh headers without clearing already-loaded content, files, or triage state.
        row.imap_uid, row.subject = item.uid, item.subject
        row.from_address, row.to_json, row.cc_json = item.from_address, item.to, item.cc
        if item.date:
            row.received_at = item.date
        db.add(row)
        if row.id not in seen:
            rows.append(row)
            seen.add(row.id)
    db.commit()
    return rows, fetched.total


def sync_header_page(db: Session, mailbox: EmployeeMailbox, *, offset: int, limit: int):
    rows, total = fetch_header_rows(db, mailbox, offset=offset, limit=limit, enroll_inbound=True)
    mailbox.last_synced_at = utc_now()
    mailbox.last_error = None
    mailbox.updated_at = utc_now()
    db.add(mailbox)
    db.commit()
    return rows, total


def list_headers(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    subject: str = "",
    from_address: str = "",
) -> MailListResponse:
    from app.mail.service import get_mailbox, message_read

    mailbox = get_mailbox(db, tenant_id, agent_id)
    if mailbox is None:
        return MailListResponse(
            agent_id=agent_id,
            folder="inbox",
            configured=False,
            can_send=False,
            empty_reason="这个员工还没有配置邮箱。",
        )
    rows, total = fetch_header_rows(
        db,
        mailbox,
        offset=(page - 1) * page_size,
        limit=page_size,
        subject=subject,
        from_address=from_address,
    )
    return MailListResponse(
        agent_id=agent_id,
        folder="inbox",
        configured=True,
        can_send=True,
        headers_only=True,
        page=page,
        page_size=page_size,
        total=total,
        messages=[message_read(row, include_body=False) for row in rows],
    )


def _check_selected_identity(row: EmployeeMailMessage, fetched) -> None:
    if fetched is None:
        raise MailError("MAIL_NOT_FOUND", "远端邮件已不存在，请重新检索。")
    if row.rfc_message_id and row.rfc_message_id != fetched.rfc_message_id:
        raise MailError("MAIL_UID_CHANGED", "邮箱邮件编号已变化，请重新检索后再读取。")


def ensure_message_body(db: Session, row: EmployeeMailMessage) -> EmployeeMailMessage:
    from app.mail.service import _connection, get_mailbox

    if (row.metadata_json or {}).get("content_state") != "headers_only":
        return row
    mailbox = get_mailbox(db, row.tenant_id, row.agent_id)
    if mailbox is None:
        raise MailError("MAIL_NOT_CONFIGURED", "这个员工还没有配置邮箱。")
    require_enabled_mailbox(mailbox)
    fetched = get_transport().fetch_body(_connection(mailbox), row.imap_uid)
    _check_selected_identity(row, fetched)
    # A concurrent explicit read may already have loaded body and attachments.
    db.refresh(row)
    if (row.metadata_json or {}).get("content_state") != "headers_only":
        return row
    row.body_text = fetched.body_text
    row.metadata_json = {**(row.metadata_json or {}), "content_state": "body_only"}
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_message_content(db: Session, row: EmployeeMailMessage) -> EmployeeMailMessage:
    from app.mail.service import _connection, _upsert_inbox, get_mailbox

    state = (row.metadata_json or {}).get("content_state")
    if state not in {"headers_only", "body_only"}:
        return row
    mailbox = get_mailbox(db, row.tenant_id, row.agent_id)
    if mailbox is None:
        raise MailError("MAIL_NOT_CONFIGURED", "这个员工还没有配置邮箱。")
    if state == "body_only" and not mailbox_is_enabled(mailbox):
        return row  # Cached historical text remains readable; attachments stay unloaded.
    require_enabled_mailbox(mailbox)
    fetched = get_transport().fetch_message(_connection(mailbox), row.imap_uid)
    _check_selected_identity(row, fetched)
    db.refresh(row)
    if (row.metadata_json or {}).get("content_state") not in {"headers_only", "body_only"}:
        return row
    _upsert_inbox(db, mailbox, fetched)
    db.refresh(row)
    return row

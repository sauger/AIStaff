from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from app.cabinet.errors import CabinetError
from app.cabinet.paths import join_path
from app.cabinet.service import (
    MAIL_INBOUND_FOLDER,
    MAIL_OUTBOUND_FOLDER,
    decode_base64_content,
    ensure_mail_attachment_zone,
    get_entry,
    read_file_bytes,
    save_bytes,
    unique_mail_filename,
)
from app.db.models import (
    AgentProfile,
    EmployeeMailbox,
    EmployeeMailMessage,
    utc_now,
)
from app.mail.confirm import (
    draft_send_allowed,
    looks_like_email,
    parse_address_list,
    send_requires_confirmation,
)
from app.mail.errors import MailError
from app.mail.schema import (
    MailAttachmentInput,
    MailAttachmentRead,
    MailboxConfigRequest,
    MailboxStatusRead,
    MailComposeRequest,
    MailListResponse,
    MailMessageRead,
    MailProbeResult,
    MailSendResult,
)
from app.mail.transport import (
    FetchedMessage,
    MailboxConnection,
    OutboundAttachment,
    get_transport,
    validate_encryption,
)
from app.security.encryption import decrypt_secret, encrypt_secret
from app.security.permissions import agent_owned_by_user, is_admin_user

POLL_INTERVAL_SECONDS = 60


def get_agent(db: Session, tenant_id: str, agent_id: str) -> AgentProfile:
    row = db.get(AgentProfile, agent_id)
    if row is None or row.tenant_id != tenant_id:
        raise MailError("MAIL_AGENT_NOT_FOUND", "员工不存在。", status_code=404)
    return row


def can_read_mail(agent: AgentProfile, user: Any) -> bool:
    return is_admin_user(user) or agent_owned_by_user(agent, user)


def can_send_mail(agent: AgentProfile, user: Any) -> bool:
    return agent_owned_by_user(agent, user)


def ensure_reader(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_read_mail(agent, user):
        return agent
    raise MailError("MAIL_FORBIDDEN", "不能打开该员工的邮件。", status_code=403)


def ensure_sender(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_send_mail(agent, user):
        return agent
    raise MailError(
        "MAIL_SEND_FORBIDDEN",
        "没有权限配置该员工邮箱或代为发送邮件。",
        status_code=403,
    )


def get_mailbox(db: Session, tenant_id: str, agent_id: str) -> EmployeeMailbox | None:
    return db.exec(
        select(EmployeeMailbox).where(
            EmployeeMailbox.tenant_id == tenant_id,
            EmployeeMailbox.agent_id == agent_id,
        )
    ).first()


def mailbox_status(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    can_configure: bool,
    can_send: bool,
) -> MailboxStatusRead:
    row = get_mailbox(db, tenant_id, agent_id)
    if row is None:
        return MailboxStatusRead(
            agent_id=agent_id,
            configured=False,
            can_configure=can_configure,
            can_send=False,
        )
    return MailboxStatusRead(
        agent_id=agent_id,
        configured=True,
        email_address=row.email_address,
        imap_host=row.imap_host,
        imap_port=row.imap_port,
        imap_encryption=row.imap_encryption,
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_encryption=row.smtp_encryption,
        username=row.username,
        password_configured=bool(row.password_encrypted),
        can_configure=can_configure,
        can_send=can_send,
        last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
        last_error=row.last_error,
    )


def save_mailbox(
    db: Session,
    tenant_id: str,
    agent_id: str,
    request: MailboxConfigRequest,
) -> tuple[MailboxStatusRead, MailProbeResult | None]:
    address = _require_email(request.email_address, field="发件地址")
    username = str(request.username or "").strip()
    if not username:
        raise MailError("MAIL_USERNAME_REQUIRED", "请填写邮箱用户名。")
    imap_encryption = validate_encryption(request.imap_encryption, field="IMAP")
    smtp_encryption = validate_encryption(request.smtp_encryption, field="SMTP")
    row = get_mailbox(db, tenant_id, agent_id)
    password = str(request.password or "").strip()
    if not password and (row is None or not row.password_encrypted):
        raise MailError("MAIL_PASSWORD_REQUIRED", "请填写邮箱密码。密码不会回显。")
    if row is None:
        row = EmployeeMailbox(
            tenant_id=tenant_id,
            agent_id=agent_id,
            email_address=address,
            imap_host=request.imap_host.strip(),
            imap_port=request.imap_port,
            imap_encryption=imap_encryption,
            smtp_host=request.smtp_host.strip(),
            smtp_port=request.smtp_port,
            smtp_encryption=smtp_encryption,
            username=username,
            password_encrypted=encrypt_secret(password),
        )
        db.add(row)
    else:
        row.email_address = address
        row.imap_host = request.imap_host.strip()
        row.imap_port = request.imap_port
        row.imap_encryption = imap_encryption
        row.smtp_host = request.smtp_host.strip()
        row.smtp_port = request.smtp_port
        row.smtp_encryption = smtp_encryption
        row.username = username
        if password:
            row.password_encrypted = encrypt_secret(password)
        row.updated_at = utc_now()
        db.add(row)
    db.commit()
    db.refresh(row)
    probe: MailProbeResult | None = None
    if request.probe:
        probe = probe_mailbox(row)
        row.last_error = _probe_error(probe)
        row.updated_at = utc_now()
        db.add(row)
        db.commit()
        db.refresh(row)
    status = mailbox_status(db, tenant_id, agent_id, can_configure=True, can_send=True)
    return status, probe


def clear_mailbox(db: Session, tenant_id: str, agent_id: str) -> MailboxStatusRead:
    row = get_mailbox(db, tenant_id, agent_id)
    if row is not None:
        db.delete(row)
        db.commit()
    return mailbox_status(db, tenant_id, agent_id, can_configure=True, can_send=False)


def probe_mailbox(row: EmployeeMailbox) -> MailProbeResult:
    connection = _connection(row)
    transport = get_transport()
    result = MailProbeResult()
    try:
        transport.probe_imap(connection)
        result.imap_ok = True
    except MailError as exc:
        result.imap_error = exc.message
    try:
        transport.probe_smtp(connection)
        result.smtp_ok = True
    except MailError as exc:
        result.smtp_error = exc.message
    return result


def list_inbox(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    can_send: bool,
    sync: bool = True,
) -> MailListResponse:
    mailbox = get_mailbox(db, tenant_id, agent_id)
    if mailbox is None:
        return MailListResponse(
            agent_id=agent_id,
            folder="inbox",
            configured=False,
            can_send=False,
            empty_reason="这个员工还没有配置邮箱。请先在邮件页填写 IMAP 和 SMTP。",
            messages=[],
        )
    if sync:
        try:
            sync_inbox(db, mailbox)
        except MailError as exc:
            mailbox.last_error = exc.message
            mailbox.updated_at = utc_now()
            db.add(mailbox)
            db.commit()
            raise
    rows = _folder_rows(db, tenant_id, agent_id, "inbox")
    return MailListResponse(
        agent_id=agent_id,
        folder="inbox",
        configured=True,
        can_send=can_send,
        empty_reason="收件箱是空的。" if not rows else None,
        messages=[message_read(item, include_body=False) for item in rows],
    )


def list_sent(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    can_send: bool,
) -> MailListResponse:
    mailbox = get_mailbox(db, tenant_id, agent_id)
    if mailbox is None:
        return MailListResponse(
            agent_id=agent_id,
            folder="sent",
            configured=False,
            can_send=False,
            empty_reason="这个员工还没有配置邮箱。请先在邮件页填写 IMAP 和 SMTP。",
            messages=[],
        )
    rows = _folder_rows(db, tenant_id, agent_id, "sent")
    return MailListResponse(
        agent_id=agent_id,
        folder="sent",
        configured=True,
        can_send=can_send,
        empty_reason="还没有已发送的邮件。" if not rows else None,
        messages=[message_read(item, include_body=False) for item in rows],
    )


def list_drafts(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    can_send: bool,
) -> MailListResponse:
    mailbox = get_mailbox(db, tenant_id, agent_id)
    if mailbox is None:
        return MailListResponse(
            agent_id=agent_id,
            folder="draft",
            configured=False,
            can_send=False,
            empty_reason="这个员工还没有配置邮箱。请先在邮件页填写 IMAP 和 SMTP。",
            messages=[],
        )
    rows = [
        item
        for item in _folder_rows(db, tenant_id, agent_id, "draft")
        if item.status == "pending_confirm"
    ]
    return MailListResponse(
        agent_id=agent_id,
        folder="draft",
        configured=True,
        can_send=can_send,
        empty_reason="没有待确认的草稿。" if not rows else None,
        messages=[message_read(item, include_body=True) for item in rows],
    )


def get_message(
    db: Session,
    tenant_id: str,
    agent_id: str,
    message_id: str,
    *,
    mark_read: bool = False,
) -> EmployeeMailMessage:
    row = db.get(EmployeeMailMessage, message_id)
    if row is None or row.tenant_id != tenant_id or row.agent_id != agent_id:
        raise MailError("MAIL_NOT_FOUND", "找不到这封邮件。", status_code=404)
    if mark_read and row.folder == "inbox" and row.status == "unread":
        row.status = "read"
        row.updated_at = utc_now()
        db.add(row)
        db.commit()
        db.refresh(row)
        mailbox = get_mailbox(db, tenant_id, agent_id)
        if mailbox is not None and row.imap_uid:
            try:
                get_transport().mark_seen(_connection(mailbox), row.imap_uid)
            except Exception:  # noqa: BLE001, S110 - local body stays readable if IMAP seen fails.
                pass
    return row


def compose(
    db: Session,
    tenant_id: str,
    agent_id: str,
    request: MailComposeRequest,
    *,
    confirmed: bool,
    invoker: Any | None = None,
) -> MailSendResult:
    mailbox = _require_mailbox(db, tenant_id, agent_id)
    to = parse_address_list(request.to)
    if not to:
        raise MailError(
            "MAIL_ADDRESS_REQUIRED",
            "请提供至少一个收件人邮箱地址。v1 不能按人名查找通讯录。",
        )
    cc = parse_address_list(request.cc)
    bcc = parse_address_list(request.bcc)
    attachments, prepared = _prepare_outbound_attachments(
        db, tenant_id, agent_id, request.attachments
    )
    wait = request.as_draft or (
        not confirmed and send_requires_confirmation(invoker, {"as_draft": request.as_draft})
    )
    if wait:
        row = _store_message(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            folder="draft",
            status="pending_confirm",
            direction="outbound",
            source=request.source,
            from_address=mailbox.email_address,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=request.subject or "",
            body=request.body or "",
            in_reply_to=request.in_reply_to,
            attachments=attachments,
            confirm_required=True,
            dedupe_key=f"draft:{utc_now().isoformat()}",
        )
        return MailSendResult(
            delivered=False,
            draft=True,
            message=message_read(row),
            notice="已起草，尚未发送。请确认收件人、主题、正文和附件后再发送。",
        )
    return _deliver(
        db,
        mailbox,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=request.subject or "",
        body=request.body or "",
        attachments=attachments,
        prepared=prepared,
        source=request.source,
        in_reply_to=request.in_reply_to,
    )


def send_draft(
    db: Session,
    tenant_id: str,
    agent_id: str,
    draft_id: str,
    *,
    console: bool,
    invoker: Any | None = None,
) -> MailSendResult:
    mailbox = _require_mailbox(db, tenant_id, agent_id)
    row = get_message(db, tenant_id, agent_id, draft_id)
    if row.folder != "draft" or row.status not in {"pending_confirm", "draft"}:
        raise MailError("MAIL_NOT_DRAFT", "这封邮件不是待确认草稿，不能再当草稿发出。")
    if not draft_send_allowed(invoker, console=console):
        raise MailError(
            "MAIL_CONFIRM_REQUIRED",
            "这封草稿还需要人类确认后才能发送。请在对话里明确同意发送，或在控制台点发送。",
        )
    prepared = _reload_outbound_bytes(db, tenant_id, agent_id, row.attachments_json)
    result = _deliver(
        db,
        mailbox,
        to=list(row.to_json or []),
        cc=list(row.cc_json or []),
        bcc=list(row.bcc_json or []),
        subject=row.subject,
        body=row.body_text,
        attachments=list(row.attachments_json or []),
        prepared=prepared,
        source=row.source,
        in_reply_to=row.in_reply_to,
        rfc_message_id=row.rfc_message_id,
    )
    row.folder = "draft"
    row.status = "sent"
    row.confirmed_at = utc_now()
    row.updated_at = utc_now()
    row.metadata_json = {**(row.metadata_json or {}), "delivered_id": result.message.id}
    db.add(row)
    db.commit()
    return result


def reply_defaults(row: EmployeeMailMessage) -> dict[str, Any]:
    quoted = "\n".join(f"> {line}" for line in (row.body_text or "").splitlines())
    body = f"\n\n{row.from_address} 写道：\n{quoted}" if quoted.strip(">") else ""
    subject = row.subject or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}" if subject else "Re:"
    return {
        "to": [row.from_address] if looks_like_email(row.from_address) else [],
        "subject": subject,
        "body": body,
        "in_reply_to": row.id,
    }


def sync_inbox(db: Session, mailbox: EmployeeMailbox) -> None:
    fetched = get_transport().fetch_inbox(_connection(mailbox))
    for item in fetched:
        _upsert_inbox(db, mailbox, item)
    mailbox.last_synced_at = utc_now()
    mailbox.last_error = None
    mailbox.updated_at = utc_now()
    db.add(mailbox)
    db.commit()


def sync_all_mailboxes(db: Session) -> None:
    rows = db.exec(select(EmployeeMailbox)).all()
    for mailbox in rows:
        try:
            sync_inbox(db, mailbox)
        except MailError as exc:
            mailbox.last_error = exc.message
            mailbox.updated_at = utc_now()
            db.add(mailbox)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - poller must keep walking other mailboxes.
            mailbox.last_error = f"收取收件箱失败：{exc.__class__.__name__}"
            mailbox.updated_at = utc_now()
            db.add(mailbox)
            db.commit()


def message_read(row: EmployeeMailMessage, *, include_body: bool = True) -> MailMessageRead:
    attachments = [MailAttachmentRead.model_validate(item) for item in (row.attachments_json or [])]
    return MailMessageRead(
        id=row.id,
        folder=row.folder,  # type: ignore[arg-type]
        status=row.status,
        direction=row.direction,
        source=row.source,
        from_address=row.from_address,
        to=list(row.to_json or []),
        cc=list(row.cc_json or []),
        bcc=list(row.bcc_json or []),
        subject=row.subject,
        body_text=row.body_text if include_body else "",
        unread=row.folder == "inbox" and row.status == "unread",
        sent_at=row.sent_at.isoformat() if row.sent_at else None,
        received_at=row.received_at.isoformat() if row.received_at else None,
        smtp_error=row.smtp_error,
        imap_append_note=row.imap_append_note,
        attachments=attachments,
        confirm_required=bool(row.confirm_required and row.status == "pending_confirm"),
        in_reply_to=row.in_reply_to,
        created_at=row.created_at.isoformat(),
    )


def purge_agent_mail(db: Session, tenant_id: str, agent_id: str) -> None:
    mailbox = get_mailbox(db, tenant_id, agent_id)
    if mailbox is not None:
        db.delete(mailbox)
    rows = db.exec(
        select(EmployeeMailMessage).where(
            EmployeeMailMessage.tenant_id == tenant_id,
            EmployeeMailMessage.agent_id == agent_id,
        )
    ).all()
    for row in rows:
        db.delete(row)
    db.commit()


def prompt_context(db: Session, tenant_id: str, agent_id: str) -> str:
    mailbox = get_mailbox(db, tenant_id, agent_id)
    lines = [
        "该员工有岗位邮箱身份。收发是系统 IMAP+SMTP 引擎，不是飞书/企微渠道。",
        "按原始邮箱地址发信即可，不要按人名猜地址，也不要去知识库或通讯录里找人。",
        "列出收件箱：mail_list；读信：mail_read；起草：mail_draft；发送：mail_send。",
        "用户说先起草、确认后再发，或技能标明 confirm_before_send_mail 时，只能出草稿，确认前不得 SMTP 投递。",
        "附件会进入文件柜「邮件附件/收件」或「邮件附件/发件」，不要放进对话附件或模板目录。",
        "邮箱密码不可见，也不要写入技能或回复。",
    ]
    if mailbox is None:
        lines.append("这个员工还没有配置邮箱。若用户要求发信或读信，必须说明未配置，不得假装已发送或已收取。")
        return "\n".join(lines)
    lines.append(f"已配置发件地址：{mailbox.email_address}。")
    pending = db.exec(
        select(EmployeeMailMessage).where(
            EmployeeMailMessage.tenant_id == tenant_id,
            EmployeeMailMessage.agent_id == agent_id,
            EmployeeMailMessage.folder == "draft",
            EmployeeMailMessage.status == "pending_confirm",
        )
    ).all()
    if pending:
        lines.append(f"当前有 {len(pending)} 封待确认草稿，确认前不要再次投递。")
    return "\n".join(lines)


def _deliver(
    db: Session,
    mailbox: EmployeeMailbox,
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    attachments: list[dict[str, Any]],
    prepared: list[OutboundAttachment],
    source: str,
    in_reply_to: str | None,
    rfc_message_id: str | None = None,
) -> MailSendResult:
    now = utc_now()
    try:
        rfc_id, append_note = get_transport().send(
            _connection(mailbox),
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            attachments=prepared,
            in_reply_to=rfc_message_id,
        )
    except MailError as exc:
        row = _store_message(
            db,
            tenant_id=mailbox.tenant_id,
            agent_id=mailbox.agent_id,
            folder="sent",
            status="failed",
            direction="outbound",
            source=source,
            from_address=mailbox.email_address,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            attachments=attachments,
            smtp_error=exc.message,
            sent_at=now,
            dedupe_key=f"fail:{now.isoformat()}",
        )
        raise MailError(exc.code, exc.message, status_code=exc.status_code, details={
            **exc.details,
            "message_id": row.id,
        }) from exc
    stored_attachments = _store_outbound_copies(
        db, mailbox.tenant_id, mailbox.agent_id, attachments, prepared, token=rfc_id
    )
    row = _store_message(
        db,
        tenant_id=mailbox.tenant_id,
        agent_id=mailbox.agent_id,
        folder="sent",
        status="sent",
        direction="outbound",
        source=source,
        from_address=mailbox.email_address,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        attachments=stored_attachments,
        sent_at=now,
        rfc_message_id=rfc_id,
        imap_append_note=append_note,
        dedupe_key=f"sent:{rfc_id or now.isoformat()}",
    )
    notice = "邮件已发送。"
    if append_note:
        notice = f"邮件已发送。{append_note}"
    return MailSendResult(delivered=True, draft=False, message=message_read(row), notice=notice)


def _upsert_inbox(db: Session, mailbox: EmployeeMailbox, fetched: FetchedMessage) -> None:
    dedupe = fetched.rfc_message_id or f"uid:{fetched.uid}"
    existing = db.exec(
        select(EmployeeMailMessage).where(
            EmployeeMailMessage.tenant_id == mailbox.tenant_id,
            EmployeeMailMessage.agent_id == mailbox.agent_id,
            EmployeeMailMessage.folder == "inbox",
            EmployeeMailMessage.dedupe_key == dedupe,
        )
    ).first()
    attachments = _store_inbound_attachments(
        db, mailbox.tenant_id, mailbox.agent_id, fetched, token=fetched.uid or dedupe
    )
    if existing is None:
        _store_message(
            db,
            tenant_id=mailbox.tenant_id,
            agent_id=mailbox.agent_id,
            folder="inbox",
            status="unread" if fetched.unseen else "read",
            direction="inbound",
            source="imap",
            from_address=fetched.from_address,
            to=fetched.to,
            cc=fetched.cc,
            bcc=fetched.bcc,
            subject=fetched.subject,
            body=fetched.body_text,
            attachments=attachments,
            received_at=fetched.date or utc_now(),
            imap_uid=fetched.uid,
            rfc_message_id=fetched.rfc_message_id or None,
            dedupe_key=dedupe,
        )
        return
    existing.imap_uid = fetched.uid
    existing.body_text = fetched.body_text
    existing.subject = fetched.subject
    existing.attachments_json = attachments
    existing.from_address = fetched.from_address
    existing.to_json = fetched.to
    existing.cc_json = fetched.cc
    existing.updated_at = utc_now()
    if fetched.unseen and existing.status == "read":
        pass
    elif fetched.unseen:
        existing.status = "unread"
    db.add(existing)
    db.commit()


def _store_inbound_attachments(
    db: Session,
    tenant_id: str,
    agent_id: str,
    fetched: FetchedMessage,
    *,
    token: str,
) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    if fetched.attachments:
        ensure_mail_attachment_zone(db, tenant_id, agent_id)
    for item in fetched.attachments:
        stored.append(
            _save_attachment_bytes(
                db,
                tenant_id,
                agent_id,
                folder=MAIL_INBOUND_FOLDER,
                filename=item.filename,
                data=item.data,
                content_type=item.content_type,
                token=token,
            )
        )
    return stored


def _prepare_outbound_attachments(
    db: Session,
    tenant_id: str,
    agent_id: str,
    items: list[MailAttachmentInput],
) -> tuple[list[dict[str, Any]], list[OutboundAttachment]]:
    meta: list[dict[str, Any]] = []
    prepared: list[OutboundAttachment] = []
    if items:
        ensure_mail_attachment_zone(db, tenant_id, agent_id)
    for item in items:
        filename = str(item.filename or "").strip()
        data: bytes | None = None
        content_type = item.content_type or "application/octet-stream"
        source_path = str(item.cabinet_path or "").strip()
        if source_path:
            try:
                source, data = read_file_bytes(db, tenant_id, agent_id, source_path)
            except CabinetError as exc:
                raise MailError(exc.code, exc.message, status_code=exc.status_code, details=exc.details) from exc
            filename = filename or source.name
            content_type = source.content_type or content_type
        elif item.content_base64:
            filename = filename or "attachment"
            data = decode_base64_content(filename, item.content_base64)
        if not filename or data is None:
            continue
        prepared.append(OutboundAttachment(filename=filename, content_type=content_type, data=data))
        meta.append(
            {
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(data),
                "source_cabinet_path": source_path or None,
                "saved": False,
                "cabinet_path": None,
                "error": None,
            }
        )
    return meta, prepared


def _store_outbound_copies(
    db: Session,
    tenant_id: str,
    agent_id: str,
    meta: list[dict[str, Any]],
    prepared: list[OutboundAttachment],
    *,
    token: str,
) -> list[dict[str, Any]]:
    if prepared:
        ensure_mail_attachment_zone(db, tenant_id, agent_id)
    stored: list[dict[str, Any]] = []
    for index, item in enumerate(prepared):
        info = dict(meta[index]) if index < len(meta) else {
            "filename": item.filename,
            "content_type": item.content_type,
            "size_bytes": len(item.data),
        }
        saved = _save_attachment_bytes(
            db,
            tenant_id,
            agent_id,
            folder=MAIL_OUTBOUND_FOLDER,
            filename=item.filename,
            data=item.data,
            content_type=item.content_type,
            token=token,
        )
        info.update(saved)
        stored.append(info)
    return stored


def _reload_outbound_bytes(
    db: Session,
    tenant_id: str,
    agent_id: str,
    attachments: list[dict[str, Any]] | None,
) -> list[OutboundAttachment]:
    prepared: list[OutboundAttachment] = []
    for item in attachments or []:
        path = str(item.get("cabinet_path") or item.get("source_cabinet_path") or "")
        filename = str(item.get("filename") or "attachment")
        if not path:
            continue
        try:
            _row, data = read_file_bytes(db, tenant_id, agent_id, path)
        except CabinetError as exc:
            raise MailError(
                "MAIL_ATTACHMENT_MISSING",
                f"草稿附件「{filename}」无法读取：{exc.message}",
                details={"filename": filename},
            ) from exc
        prepared.append(
            OutboundAttachment(
                filename=filename,
                content_type=str(item.get("content_type") or "application/octet-stream"),
                data=data,
            )
        )
    return prepared


def _save_attachment_bytes(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    folder: str,
    filename: str,
    data: bytes,
    content_type: str,
    token: str,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "saved": False,
        "cabinet_path": None,
        "error": None,
    }
    try:
        name = unique_mail_filename(db, tenant_id, agent_id, folder, filename, token=token)
        row = save_bytes(
            db,
            tenant_id,
            agent_id,
            join_path(folder, name),
            data,
            overwrite=False,
            source="mail",
            content_type=content_type,
            create_parents=True,
        )
        info["saved"] = True
        info["cabinet_path"] = row.path
        info["filename"] = row.name
        info["size_bytes"] = row.size_bytes
    except CabinetError as exc:
        info["error"] = (
            f"邮件「附件 {filename}」未能写入文件柜：{exc.message}"
        )
    return info


def _store_message(
    db: Session,
    *,
    tenant_id: str,
    agent_id: str,
    folder: str,
    status: str,
    direction: str,
    source: str,
    from_address: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    attachments: list[dict[str, Any]],
    dedupe_key: str,
    in_reply_to: str | None = None,
    sent_at: datetime | None = None,
    received_at: datetime | None = None,
    smtp_error: str | None = None,
    imap_append_note: str | None = None,
    imap_uid: str | None = None,
    rfc_message_id: str | None = None,
    confirm_required: bool = False,
) -> EmployeeMailMessage:
    row = EmployeeMailMessage(
        tenant_id=tenant_id,
        agent_id=agent_id,
        folder=folder,
        status=status,
        direction=direction,
        source=source,
        imap_uid=imap_uid,
        rfc_message_id=rfc_message_id,
        dedupe_key=dedupe_key,
        from_address=from_address,
        to_json=to,
        cc_json=cc,
        bcc_json=bcc,
        subject=subject,
        body_text=body,
        in_reply_to=in_reply_to,
        sent_at=sent_at,
        received_at=received_at,
        smtp_error=smtp_error,
        imap_append_note=imap_append_note,
        attachments_json=attachments,
        confirm_required=confirm_required,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _folder_rows(
    db: Session, tenant_id: str, agent_id: str, folder: str
) -> list[EmployeeMailMessage]:
    stmt = select(EmployeeMailMessage).where(
        EmployeeMailMessage.tenant_id == tenant_id,
        EmployeeMailMessage.agent_id == agent_id,
        EmployeeMailMessage.folder == folder,
    )
    if folder == "inbox":
        stmt = stmt.order_by(
            col(EmployeeMailMessage.received_at).desc(),
            col(EmployeeMailMessage.created_at).desc(),
        )
    elif folder == "sent":
        stmt = stmt.order_by(
            col(EmployeeMailMessage.sent_at).desc(),
            col(EmployeeMailMessage.created_at).desc(),
        )
    else:
        stmt = stmt.order_by(col(EmployeeMailMessage.created_at).desc())
    return list(stmt_exec(db, stmt))


def stmt_exec(db: Session, stmt: Any) -> list[EmployeeMailMessage]:
    return list(db.exec(stmt).all())


def _require_mailbox(db: Session, tenant_id: str, agent_id: str) -> EmployeeMailbox:
    row = get_mailbox(db, tenant_id, agent_id)
    if row is None:
        raise MailError(
            "MAIL_NOT_CONFIGURED",
            "这个员工还没有配置邮箱，不能收发邮件。请先在该员工邮件页配置 IMAP 和 SMTP。",
        )
    return row


def _connection(row: EmployeeMailbox) -> MailboxConnection:
    try:
        password = decrypt_secret(row.password_encrypted)
    except ValueError as exc:
        raise MailError(
            "MAIL_PASSWORD_UNREADABLE",
            "邮箱密码无法解密。请重新在邮件页填写密码。",
        ) from exc
    return MailboxConnection(
        email_address=row.email_address,
        username=row.username,
        password=password,
        imap_host=row.imap_host,
        imap_port=row.imap_port,
        imap_encryption=row.imap_encryption,
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_encryption=row.smtp_encryption,
    )


def _require_email(value: str, *, field: str) -> str:
    address = str(value or "").strip()
    if not looks_like_email(address):
        raise MailError("MAIL_ADDRESS_INVALID", f"{field} 不是合法邮箱地址。")
    return address


def _probe_error(result: MailProbeResult) -> str | None:
    parts: list[str] = []
    if result.imap_error:
        parts.append(f"IMAP：{result.imap_error}")
    if result.smtp_error:
        parts.append(f"SMTP：{result.smtp_error}")
    return "；".join(parts) if parts else None


def attachment_saved_in_chat_inbox(db: Session, tenant_id: str, agent_id: str, filename: str) -> bool:
    from app.cabinet.service import CHAT_INBOX_FOLDER

    return get_entry(db, tenant_id, agent_id, join_path(CHAT_INBOX_FOLDER, filename)) is not None

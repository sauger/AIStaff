from __future__ import annotations

from typing import Any

from app.core.task_request_compiler import CapabilityDescriptor
from app.mail.confirm import parse_address_list, send_requires_confirmation
from app.mail.errors import MailError
from app.mail.schema import MailAttachmentInput, MailComposeRequest
from app.mail.service import (
    compose,
    get_mailbox,
    get_message,
    list_drafts,
    list_inbox,
    list_sent,
    message_read,
    reply_defaults,
    send_draft,
)

MAIL_TOOL_NAMES = (
    "mail_list",
    "mail_read",
    "mail_draft",
    "mail_send",
)


def mail_capability_descriptors() -> list[CapabilityDescriptor]:
    return [
        CapabilityDescriptor(
            capability_id="builtin.mail.list",
            name="mail_list",
            kind="internal",
            description=(
                "List this employee's mailbox. folder=inbox lists IMAP INBOX, "
                "folder=sent lists this product's sent log, folder=draft lists "
                "pending confirmation drafts. This is email, not Feishu/WeCom chat."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "enum": ["inbox", "sent", "draft"],
                        "default": "inbox",
                    }
                },
                "additionalProperties": False,
            },
            metadata={"provider": "mail", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.mail.read",
            name="mail_read",
            kind="internal",
            description=(
                "Read one mail message by id from this employee's mailbox. "
                "Returns body text and whether attachments were saved to the "
                "cabinet 邮件附件 zone. Do not invent mail from IM history."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "minLength": 1},
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
            metadata={"provider": "mail", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.mail.draft",
            name="mail_draft",
            kind="internal",
            description=(
                "Create a mail draft for this employee. Recipients must be raw "
                "email addresses. If the user only gave a person's name, ask for "
                "the address; do not guess from the knowledge base. Does not send."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}},
                    "cc": {"type": "array", "items": {"type": "string"}},
                    "bcc": {"type": "array", "items": {"type": "string"}},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "cabinet_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Existing cabinet files to attach (copied into 邮件附件/发件).",
                    },
                    "in_reply_to": {"type": "string"},
                },
                "required": ["to"],
                "additionalProperties": False,
            },
            metadata={"provider": "mail", "side_effect": "write"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.mail.send",
            name="mail_send",
            kind="internal",
            description=(
                "Send mail for this employee via SMTP, or create a draft when "
                "confirmation is required. Recipients must be raw email addresses. "
                "If the user asked to draft first, or the active skill has "
                "confirm_before_send_mail, you MUST pass as_draft=true or only "
                "create a draft. To deliver a pending draft after the human "
                "clearly agrees (e.g. 发吧), pass draft_id. Console send is a "
                "separate confirmed path. Never put mailbox passwords in arguments."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}},
                    "cc": {"type": "array", "items": {"type": "string"}},
                    "bcc": {"type": "array", "items": {"type": "string"}},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "cabinet_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "in_reply_to": {"type": "string"},
                    "as_draft": {"type": "boolean", "default": False},
                    "draft_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
            metadata={"provider": "mail", "side_effect": "write"},
        ),
    ]


def invoke_mail_tool(
    invoker: Any,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    agent_id = str(getattr(invoker, "agent_id", "") or "").strip()
    if not agent_id:
        return _failure("MAIL_AGENT_REQUIRED", "当前运行没有绑定数字员工，不能使用邮箱。")
    try:
        if name == "mail_list":
            return _list(invoker, agent_id, arguments)
        if name == "mail_read":
            return _read(invoker, agent_id, arguments)
        if name == "mail_draft":
            return _draft(invoker, agent_id, arguments)
        if name == "mail_send":
            return _send(invoker, agent_id, arguments)
    except MailError as exc:
        return _failure(exc.code, exc.message, **exc.details)
    return _failure("UNSUPPORTED_INTERNAL_CAPABILITY", "不支持的邮件能力。")


def _list(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    folder = str(arguments.get("folder") or "inbox").strip() or "inbox"
    if folder == "sent":
        listing = list_sent(invoker.db, invoker.tenant_id, agent_id, can_send=True)
    elif folder == "draft":
        listing = list_drafts(invoker.db, invoker.tenant_id, agent_id, can_send=True)
    else:
        listing = list_inbox(
            invoker.db, invoker.tenant_id, agent_id, can_send=True, sync=True
        )
    mailbox = get_mailbox(invoker.db, invoker.tenant_id, agent_id)
    if mailbox is None:
        return _failure("MAIL_NOT_CONFIGURED", listing.empty_reason or "这个员工还没有配置邮箱。")
    return {
        "success": True,
        "data": {
            "folder": listing.folder,
            "empty_reason": listing.empty_reason,
            "messages": [
                _summary(item.model_dump(mode="json")) for item in listing.messages
            ],
            "notice": "这是当前员工自己的岗位邮箱。不要使用其他员工的邮箱。",
        },
    }


def _read(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    message_id = str(arguments.get("message_id") or "").strip()
    if not message_id:
        return _failure("MAIL_NOT_FOUND", "请指定要读取的邮件 id。")
    row = get_message(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        message_id,
        mark_read=True,
    )
    payload = message_read(row).model_dump(mode="json")
    if row.folder == "inbox":
        defaults = reply_defaults(row)
        payload["reply_to"] = defaults["to"]
        payload["reply_subject"] = defaults["subject"]
    payload["notice"] = (
        "正文如下。附件若已入柜会给出文件柜路径；失败原因会单独列出。"
        "这不是即时通讯记录。"
    )
    return {"success": True, "data": payload}


def _draft(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    request = _compose_request(invoker.tenant_id, arguments, as_draft=True, source="chat")
    result = compose(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        request,
        confirmed=False,
        invoker=invoker,
    )
    return {
        "success": True,
        "data": {
            **result.message.model_dump(mode="json"),
            "delivered": False,
            "draft": True,
            "notice": result.notice,
        },
    }


def _send(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    draft_id = str(arguments.get("draft_id") or "").strip()
    if draft_id:
        result = send_draft(
            invoker.db,
            invoker.tenant_id,
            agent_id,
            draft_id,
            console=False,
            invoker=invoker,
        )
        return _send_payload(result)
    as_draft = bool(arguments.get("as_draft")) or send_requires_confirmation(invoker, arguments)
    request = _compose_request(
        invoker.tenant_id,
        arguments,
        as_draft=as_draft,
        source="skill" if getattr(invoker, "active_skill", None) else "chat",
    )
    result = compose(
        invoker.db,
        invoker.tenant_id,
        agent_id,
        request,
        confirmed=not as_draft,
        invoker=invoker,
    )
    return _send_payload(result)


def _compose_request(
    tenant_id: str,
    arguments: dict[str, Any],
    *,
    as_draft: bool,
    source: str,
) -> MailComposeRequest:
    to = parse_address_list(arguments.get("to"))
    attachments = [
        MailAttachmentInput(cabinet_path=str(path))
        for path in (arguments.get("cabinet_paths") or [])
        if str(path).strip()
    ]
    return MailComposeRequest(
        tenant_id=tenant_id,
        to=to,
        cc=parse_address_list(arguments.get("cc")),
        bcc=parse_address_list(arguments.get("bcc")),
        subject=str(arguments.get("subject") or ""),
        body=str(arguments.get("body") or ""),
        attachments=attachments,
        as_draft=as_draft,
        in_reply_to=str(arguments.get("in_reply_to") or "") or None,
        source=source,  # type: ignore[arg-type]
    )


def _send_payload(result: Any) -> dict[str, Any]:
    payload = result.message.model_dump(mode="json")
    payload["delivered"] = result.delivered
    payload["draft"] = result.draft
    payload["notice"] = result.notice
    return {"success": True, "data": payload}


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "from_address": item.get("from_address"),
        "to": item.get("to"),
        "subject": item.get("subject"),
        "unread": item.get("unread"),
        "status": item.get("status"),
        "sent_at": item.get("sent_at"),
        "received_at": item.get("received_at"),
        "smtp_error": item.get("smtp_error"),
        "attachment_names": [
            str(entry.get("filename") or "")
            for entry in (item.get("attachments") or [])
            if entry.get("filename")
        ],
    }


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": False}
    error.update(details)
    return {"success": False, "error": error}

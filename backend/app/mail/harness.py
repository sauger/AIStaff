from __future__ import annotations

from typing import Any

from app.core.task_request_compiler import CapabilityDescriptor
from app.mail.confirm import parse_address_list, send_requires_confirmation
from app.mail.errors import MailError
from app.mail.schema import MAIL_MAX_PAGE_SIZE, MailAttachmentInput, MailComposeRequest
from app.mail.service import (
    compose,
    get_mailbox,
    get_message,
    list_drafts,
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
                "pending confirmation drafts. Use page and page_size to browse older mail; "
                "follow next_page until null. For a known title, FIRST pass subject to search "
                "the server. Inbox listing returns headers only; mail_read fetches selected "
                "body and saves its attachments. Mention matching corrected versions. "
                "This is email, not Feishu/WeCom chat."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "enum": ["inbox", "sent", "draft"],
                        "default": "inbox",
                    },
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    "subject": {"type": "string", "maxLength": 512},
                    "from_address": {"type": "string", "maxLength": 320}
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
                "Read selected mail by message_id or batch message_ids (up to 8) from this employee's mailbox. "
                "Batch already-known independent IDs, e.g. original and corrected mail, in one call. "
                "For finding/downloading only, set include_body=false. This saves attachments and "
                "verifies actual cabinet files by size and SHA256. all_attachments_verified=true "
                "is authoritative: finish without cabinet_read unless attachment contents were requested. "
                "Do not invent mail from IM history."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "minLength": 1},
                    "message_ids": {"type": "array", "items": {"type": "string", "minLength": 1},
                                    "minItems": 1, "maxItems": 8, "uniqueItems": True},
                    "include_body": {"type": "boolean", "default": True},
                },
                "oneOf": [
                    {"required": ["message_id"], "not": {"required": ["message_ids"]}},
                    {"required": ["message_ids"], "not": {"required": ["message_id"]}},
                ],
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
    unknown = set(arguments) - {"folder", "page", "page_size", "subject", "from_address"}
    page = arguments.get("page", 1)
    page_size = arguments.get("page_size", 20)
    if (unknown or type(page) is not int or page < 1
            or type(page_size) is not int or not 1 <= page_size <= MAIL_MAX_PAGE_SIZE
            or arguments.get("folder", "inbox") not in {"inbox", "sent", "draft"}):
        return {"success": False, "error": {
            "code": "INVALID_ARGUMENTS",
            "message": "仅支持 folder、page（正整数）、page_size（1—50）、subject 和 from_address。",
            "retryable": False,
        }}
    folder = str(arguments.get("folder") or "inbox").strip() or "inbox"
    subject = arguments.get("subject", "")
    from_address = arguments.get("from_address", "")
    if (not isinstance(subject, str) or not isinstance(from_address, str)
            or len(subject) > 512 or len(from_address) > 320
            or any(c in subject + from_address for c in "\r\n\x00")
            or (folder != "inbox" and (subject or from_address))):
        return {"success": False, "error": {
            "code": "INVALID_ARGUMENTS", "message": "检索条件无效，仅收件箱支持标题和发件人检索。",
            "retryable": False,
        }}
    if folder == "draft" and (page != 1 or "page_size" in arguments):
        return {"success": False, "error": {
            "code": "INVALID_ARGUMENTS", "message": "草稿列表不支持分页。", "retryable": False,
        }}
    if folder == "sent":
        listing = list_sent(
            invoker.db, invoker.tenant_id, agent_id, can_send=True, page=page, page_size=page_size
        )
    elif folder == "draft":
        listing = list_drafts(invoker.db, invoker.tenant_id, agent_id, can_send=True)
    else:
        from app.mail.header_query import list_headers

        listing = list_headers(
            invoker.db, invoker.tenant_id, agent_id, page=page, page_size=page_size,
            subject=subject, from_address=from_address,
        )
    mailbox = get_mailbox(invoker.db, invoker.tenant_id, agent_id)
    if mailbox is None:
        return _failure("MAIL_NOT_CONFIGURED", listing.empty_reason or "这个员工还没有配置邮箱。")
    return {
        "success": True,
        "data": {
            "folder": listing.folder,
            "empty_reason": listing.empty_reason,
            "page": listing.page,
            "page_size": listing.page_size,
            "total": listing.total,
            "has_more": listing.page * listing.page_size < listing.total,
            "next_page": (
                listing.page + 1 if listing.page * listing.page_size < listing.total else None
            ),
            "last_error": listing.last_error,
            "messages": [
                _summary(item.model_dump(mode="json")) for item in listing.messages
            ],
            "headers_only": folder == "inbox",
            "notice": (
                "这是当前员工自己的岗位邮箱。收件箱列表只读取邮件头，空附件列表不代表没有附件；"
                "用 mail_read 读取选中的邮件并保存附件。注意同标题的更正邮件。"
            ),
        },
    }


def _read(invoker: Any, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    include_body = arguments.get("include_body", True)
    ids = arguments.get("message_ids")
    if (set(arguments) - {"message_id", "message_ids", "include_body"}
            or type(include_body) is not bool
            or (ids is not None and ("message_id" in arguments or not isinstance(ids, list)
                or not 1 <= len(ids) <= 8
                or any(not isinstance(value, str) or not value.strip() for value in ids)
                or len(set(ids)) != len(ids)))):
        return _failure("INVALID_ARGUMENTS", "请提供一个 message_id 或最多8个不同的 message_ids。")
    if ids is not None:
        messages, errors = [], []
        for message_id in ids:
            try:
                result = _read(invoker, agent_id, {"message_id": message_id, "include_body": include_body})
            except MailError as exc:
                result = _failure(exc.code, exc.message)
            if result.get("success"):
                messages.append(result["data"])
            else:
                errors.append({"message_id": message_id, "error": result.get("error")})
        return {"success": not errors, "data": {
            "messages": messages, "errors": errors,
            "all_attachments_verified": not errors and all(
                item.get("all_attachments_verified") is True for item in messages),
        }, "error": ({"code": "MAIL_BATCH_PARTIAL_FAILURE", "message": "部分邮件未能读取。"}
                     if errors else None)}
    if not isinstance(arguments.get("message_id"), str):
        return _failure("INVALID_ARGUMENTS", "请指定要读取的邮件 id。")
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
    from app.mail.receipts import verify_attachments

    payload = message_read(row).model_dump(mode="json")
    payload["attachments"] = verify_attachments(
        invoker.db, invoker.tenant_id, agent_id, payload.get("attachments") or [],
    )
    payload["attachment_verification_version"] = 1
    payload["attachments_loaded"] = (row.metadata_json or {}).get("content_state") not in {
        "headers_only", "body_only",
    }
    payload["all_attachments_verified"] = payload["attachments_loaded"] and all(
        item.get("verified") is True for item in payload["attachments"]
    )
    payload["body_included"] = include_body
    if not include_body:
        payload.pop("body_text", None)
    if row.folder == "inbox":
        defaults = reply_defaults(row)
        payload["reply_to"] = defaults["to"]
        payload["reply_subject"] = defaults["subject"]
    payload["notice"] = (
        "附件已由服务端核对实际文件和校验值。all_attachments_verified=true 时，"
        "仅查找和下载的任务可直接完成，无需再调用 cabinet_read；要求分析附件时才读取内容。"
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
    if getattr(invoker, "inbound_mail_auto_complete", False):
        source = "inbound_skill"
    elif getattr(invoker, "active_skill", None):
        source = "skill"
    else:
        source = "chat"
    request = _compose_request(
        invoker.tenant_id,
        arguments,
        as_draft=as_draft,
        source=source,
        in_reply_to_default=str(getattr(invoker, "inbound_mail_message_id", "") or "")
        or None,
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
    in_reply_to_default: str | None = None,
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
        in_reply_to=str(arguments.get("in_reply_to") or "") or in_reply_to_default or None,
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

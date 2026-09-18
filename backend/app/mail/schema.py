from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EncryptionMode = Literal["ssl", "starttls", "none"]
MailFolder = Literal["inbox", "sent", "draft"]


class MailboxConfigRequest(BaseModel):
    tenant_id: str
    email_address: str = Field(min_length=3, max_length=320)
    imap_host: str = Field(min_length=1, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_encryption: EncryptionMode = "ssl"
    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_encryption: EncryptionMode = "starttls"
    username: str = Field(min_length=1, max_length=320)
    password: str | None = None
    probe: bool = True


class MailboxStatusRead(BaseModel):
    agent_id: str
    configured: bool
    email_address: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_encryption: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_encryption: str | None = None
    username: str | None = None
    password_configured: bool = False
    can_configure: bool = False
    can_send: bool = False
    last_synced_at: str | None = None
    last_error: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MailProbeResult(BaseModel):
    imap_ok: bool = False
    smtp_ok: bool = False
    imap_error: str | None = None
    smtp_error: str | None = None


class MailAttachmentRead(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int = 0
    cabinet_path: str | None = None
    saved: bool = False
    error: str | None = None


class MailAttachmentInput(BaseModel):
    filename: str | None = None
    content_base64: str | None = None
    cabinet_path: str | None = None
    content_type: str | None = None


class MailMessageRead(BaseModel):
    id: str
    folder: MailFolder
    status: str
    direction: str
    source: str
    from_address: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str
    body_text: str = ""
    unread: bool = False
    sent_at: str | None = None
    received_at: str | None = None
    smtp_error: str | None = None
    imap_append_note: str | None = None
    attachments: list[MailAttachmentRead] = Field(default_factory=list)
    confirm_required: bool = False
    in_reply_to: str | None = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class MailListResponse(BaseModel):
    agent_id: str
    folder: MailFolder
    configured: bool
    can_send: bool
    empty_reason: str | None = None
    messages: list[MailMessageRead] = Field(default_factory=list)


class MailComposeRequest(BaseModel):
    tenant_id: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""
    attachments: list[MailAttachmentInput] = Field(default_factory=list)
    as_draft: bool = False
    in_reply_to: str | None = None
    source: Literal["console", "chat", "skill"] = "console"


class MailSendResult(BaseModel):
    delivered: bool
    draft: bool = False
    message: MailMessageRead
    notice: str

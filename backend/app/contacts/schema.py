from __future__ import annotations

from pydantic import BaseModel, Field


class ContactWriteRequest(BaseModel):
    tenant_id: str
    name: str = ""
    phone: str | None = None
    email: str | None = None
    title: str | None = None
    city: str | None = None
    notes: str | None = None
    wecom_userid: str | None = None
    feishu_openid: str | None = None
    dingtalk_userid: str | None = None


class ContactRead(BaseModel):
    id: str
    agent_id: str
    name: str
    phone: str | None = None
    email: str | None = None
    title: str | None = None
    city: str | None = None
    notes: str | None = None
    wecom_userid: str | None = None
    feishu_openid: str | None = None
    dingtalk_userid: str | None = None
    primary_user_id: str | None = None
    created_at: str
    updated_at: str


class ContactListResponse(BaseModel):
    agent_id: str
    can_write: bool = False
    contacts: list[ContactRead] = Field(default_factory=list)

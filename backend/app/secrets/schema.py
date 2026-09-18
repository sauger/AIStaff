from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SecretType = Literal["password", "session_snapshot"]

SECRET_TYPE_PASSWORD = "password"
SECRET_TYPE_SESSION_SNAPSHOT = "session_snapshot"
ALLOWED_SECRET_TYPES = {SECRET_TYPE_PASSWORD, SECRET_TYPE_SESSION_SNAPSHOT}


class SecretCreateRequest(BaseModel):
    tenant_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    secret_type: SecretType
    username: str | None = None
    value: str = Field(min_length=1)


class SecretUpdateRequest(BaseModel):
    tenant_id: str
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    username: str | None = None
    value: str | None = None


class SecretRead(BaseModel):
    id: str
    agent_id: str
    name: str
    description: str = ""
    secret_type: SecretType
    value_configured: bool = True
    linked_login_guide_id: str | None = None
    linked_login_guide_name: str | None = None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class SecretListResponse(BaseModel):
    agent_id: str
    can_write: bool = False
    secrets: list[SecretRead] = Field(default_factory=list)


class LoginGuideWriteRequest(BaseModel):
    tenant_id: str
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2000)
    username_selector: str = ""
    username_label: str = "用户名"
    password_selector: str = ""
    password_label: str = "密码"
    submit_selector: str = ""
    submit_label: str = "登录"
    default_secret_name: str | None = None
    published: bool = False


class LoginGuideRead(BaseModel):
    id: str
    agent_id: str
    name: str
    url: str
    username_selector: str = ""
    username_label: str = "用户名"
    password_selector: str = ""
    password_label: str = "密码"
    submit_selector: str = ""
    submit_label: str = "登录"
    default_secret_name: str | None = None
    default_secret_id: str | None = None
    published: bool = False
    copied_from_id: str | None = None
    can_write: bool = False
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class LoginGuideListResponse(BaseModel):
    agent_id: str
    can_write: bool = False
    guides: list[LoginGuideRead] = Field(default_factory=list)


class LoginGuideGalleryItem(BaseModel):
    id: str
    name: str
    url: str
    username_selector: str = ""
    username_label: str = "用户名"
    password_selector: str = ""
    password_label: str = "密码"
    submit_selector: str = ""
    submit_label: str = "登录"
    source_agent_id: str
    source_agent_name: str
    created_at: str
    updated_at: str


class LoginGuideCopyRequest(BaseModel):
    tenant_id: str
    target_agent_id: str


class LoginResult(BaseModel):
    success: bool
    login_guide_name: str
    secret_name_used: str | None = None
    secret_type_used: str | None = None
    switched_to_snapshot: bool = False
    snapshot_created: bool = False
    snapshot_updated: bool = False
    snapshot_name: str | None = None
    captcha_or_2fa: bool = False
    code: str | None = None
    message: str

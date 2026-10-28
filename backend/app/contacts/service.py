from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.contacts.errors import ContactError
from app.contacts.schema import ContactListResponse, ContactRead, ContactWriteRequest
from app.db.models import AgentProfile, Contact, utc_now
from app.security.permissions import agent_owned_by_user, is_admin_user

_NAME_LIMIT = 80
_SHORT_LIMIT = 120
_NOTE_LIMIT = 4000
_SECONDARY_FIELDS = (
    ("wecom_userid", "该企微 userid 已在这本通讯录中。"),
    ("feishu_openid", "该飞书 openid 已在这本通讯录中。"),
    ("dingtalk_userid", "该钉钉 userid 已在这本通讯录中。"),
)


def get_agent(db: Session, tenant_id: str, agent_id: str) -> AgentProfile:
    row = db.get(AgentProfile, agent_id)
    if row is None or row.tenant_id != tenant_id:
        raise ContactError("CONTACT_AGENT_NOT_FOUND", "员工不存在。", status_code=404)
    return row


def can_read_contacts(agent: AgentProfile, user: Any) -> bool:
    return is_admin_user(user) or agent_owned_by_user(agent, user)


def can_write_contacts(agent: AgentProfile, user: Any) -> bool:
    return agent_owned_by_user(agent, user)


def ensure_reader(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_read_contacts(agent, user):
        return agent
    raise ContactError("CONTACT_FORBIDDEN", "不能打开该员工的通讯录。", status_code=403)


def ensure_writer(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_write_contacts(agent, user):
        return agent
    raise ContactError("CONTACT_WRITE_FORBIDDEN", "没有权限编辑该员工的通讯录。", status_code=403)


def list_contacts(
    db: Session, tenant_id: str, agent_id: str, *, can_write: bool
) -> ContactListResponse:
    rows = db.exec(
        select(Contact)
        .where(Contact.tenant_id == tenant_id, Contact.agent_id == agent_id)
        .order_by(Contact.name, Contact.created_at)
    ).all()
    return ContactListResponse(
        agent_id=agent_id,
        can_write=can_write,
        contacts=[contact_read(row) for row in rows],
    )


def create_contact(
    db: Session, tenant_id: str, agent_id: str, request: ContactWriteRequest
) -> ContactRead:
    values = _editable_values(request)
    _ensure_unique_secondary(db, tenant_id, agent_id, values)
    row = Contact(tenant_id=tenant_id, agent_id=agent_id, **values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return contact_read(row)


def update_contact(
    db: Session,
    tenant_id: str,
    agent_id: str,
    contact_id: str,
    request: ContactWriteRequest,
) -> ContactRead:
    row = _require_contact(db, tenant_id, agent_id, contact_id)
    values = _editable_values(request)
    _ensure_unique_secondary(db, tenant_id, agent_id, values, exclude_id=row.id)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return contact_read(row)


def delete_contact(db: Session, tenant_id: str, agent_id: str, contact_id: str) -> None:
    row = _require_contact(db, tenant_id, agent_id, contact_id)
    db.delete(row)
    db.commit()


def contact_read(row: Contact) -> ContactRead:
    return ContactRead(
        id=row.id,
        agent_id=row.agent_id,
        name=row.name,
        phone=row.phone,
        email=row.email,
        title=row.title,
        city=row.city,
        notes=row.notes,
        wecom_userid=row.wecom_userid,
        feishu_openid=row.feishu_openid,
        dingtalk_userid=row.dingtalk_userid,
        primary_user_id=row.primary_user_id,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _require_contact(db: Session, tenant_id: str, agent_id: str, contact_id: str) -> Contact:
    row = db.get(Contact, contact_id)
    if row is None or row.tenant_id != tenant_id or row.agent_id != agent_id:
        raise ContactError("CONTACT_NOT_FOUND", "联系人不存在。", status_code=404)
    return row


def _editable_values(request: ContactWriteRequest) -> dict[str, str | None]:
    return {
        "name": _require_name(request.name),
        "phone": _optional(request.phone, limit=_SHORT_LIMIT),
        "email": _optional(request.email, limit=_SHORT_LIMIT),
        "title": _optional(request.title, limit=_SHORT_LIMIT),
        "city": _optional(request.city, limit=_SHORT_LIMIT),
        "notes": _optional(request.notes, limit=_NOTE_LIMIT),
        "wecom_userid": _optional(request.wecom_userid, limit=_SHORT_LIMIT),
        "feishu_openid": _optional(request.feishu_openid, limit=_SHORT_LIMIT),
        "dingtalk_userid": _optional(request.dingtalk_userid, limit=_SHORT_LIMIT),
    }


def _require_name(value: str | None) -> str:
    name = str(value or "").strip()
    if not name:
        raise ContactError("CONTACT_NAME_REQUIRED", "请填写姓名。", status_code=400)
    if len(name) > _NAME_LIMIT:
        raise ContactError("CONTACT_NAME_TOO_LONG", "姓名过长。", status_code=400)
    return name


def _optional(value: str | None, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise ContactError("CONTACT_FIELD_TOO_LONG", "字段超出长度限制。", status_code=400)
    return text


def _ensure_unique_secondary(
    db: Session,
    tenant_id: str,
    agent_id: str,
    values: dict[str, str | None],
    *,
    exclude_id: str | None = None,
) -> None:
    for field, message in _SECONDARY_FIELDS:
        value = values.get(field)
        if not value:
            continue
        existing = db.exec(
            select(Contact).where(
                Contact.tenant_id == tenant_id,
                Contact.agent_id == agent_id,
                getattr(Contact, field) == value,
            )
        ).first()
        if existing is not None and existing.id != exclude_id:
            raise ContactError("CONTACT_DUPLICATE_KEY", message, status_code=409)

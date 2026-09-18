from __future__ import annotations

import re
from typing import Any

from sqlmodel import col, select

from app.db.models import GeneralSkill, Message, Skill

DRAFT_INTENT_PHRASES = (
    "先起草",
    "先写草稿",
    "确认后再发",
    "确认后再发送",
    "我确认后再",
    "等我确认",
    "先不要发",
    "不要先发",
    "draft first",
    "confirm before send",
    "don't send yet",
    "do not send yet",
)

SEND_CONFIRM_PHRASES = (
    "发吧",
    "发送吧",
    "发出去",
    "可以发",
    "确认发送",
    "同意发送",
    "现在发",
    "send it",
    "send now",
    "confirm send",
)


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def skill_requires_mail_confirm(skill: Any | None) -> bool:
    if skill is None:
        return False
    blobs: list[dict[str, Any]] = []
    for attr in ("runtime_config_json", "metadata_json", "permissions_json", "content_json"):
        value = getattr(skill, attr, None)
        if isinstance(value, dict):
            blobs.append(value)
    return any(_mail_confirm_flag(item) for item in blobs)


def _mail_confirm_flag(blob: dict[str, Any]) -> bool:
    value = blob.get("confirm_before_send_mail")
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() in {"true", "yes", "1"}


def conversation_requests_draft(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in DRAFT_INTENT_PHRASES)


def conversation_confirms_send(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in SEND_CONFIRM_PHRASES)


def latest_user_text(invoker: Any) -> str:
    override = str(getattr(invoker, "latest_user_text", "") or "").strip()
    if override:
        return override
    db = getattr(invoker, "db", None)
    session = getattr(invoker, "session", None)
    if db is None or session is None or not getattr(session, "id", None):
        return ""
    try:
        rows = db.exec(
            select(Message)
            .where(Message.session_id == session.id, Message.role == "user")
            .order_by(col(Message.created_at).desc())
        ).all()
    except (AttributeError, TypeError, ValueError):
        return ""
    return "\n".join(str(row.content or "") for row in rows[:4])


def iter_confirm_skills(invoker: Any) -> list[Any]:
    skills: list[Any] = []
    active = getattr(invoker, "active_skill", None)
    if active is not None:
        skills.append(active)
    db = getattr(invoker, "db", None)
    tenant_id = str(getattr(invoker, "tenant_id", "") or "")
    activated = set(getattr(invoker, "_activated_names", set()) or set())
    if db is None or not tenant_id or not activated:
        return skills
    try:
        rows = db.exec(select(GeneralSkill).where(GeneralSkill.tenant_id == tenant_id)).all()
    except (AttributeError, TypeError, ValueError):
        return skills
    for row in rows:
        names = {row.slug, row.name, row.id}
        if names & activated:
            skills.append(row)
    try:
        sop_rows = db.exec(select(Skill).where(Skill.tenant_id == tenant_id)).all()
    except (AttributeError, TypeError, ValueError):
        return skills
    for row in sop_rows:
        if row.skill_id in activated or row.name in activated:
            skills.append(row)
    return skills


def send_requires_confirmation(invoker: Any, arguments: dict[str, Any]) -> bool:
    if truthy(arguments.get("as_draft")):
        return True
    if getattr(invoker, "confirm_before_send_mail", False):
        return True
    if conversation_requests_draft(latest_user_text(invoker)):
        return True
    return any(skill_requires_mail_confirm(skill) for skill in iter_confirm_skills(invoker))


def draft_send_allowed(invoker: Any, *, console: bool) -> bool:
    if console:
        return True
    return conversation_confirms_send(latest_user_text(invoker))


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(str(value or "").strip()))


def parse_address_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        pieces = re.split(r"[,\s;]+", values)
    elif isinstance(values, (list, tuple)):
        pieces = [str(item) for item in values]
    else:
        pieces = [str(values)]
    result: list[str] = []
    for item in pieces:
        address = item.strip()
        if not address:
            continue
        if not looks_like_email(address):
            from app.mail.errors import MailError

            raise MailError(
                "MAIL_ADDRESS_REQUIRED",
                (
                    f"「{address}」不是邮箱地址。请提供原始邮箱（例如 name@example.com）。"
                    "v1 不能按人名查找通讯录。"
                ),
                details={"value": address},
            )
        if address not in result:
            result.append(address)
    return result

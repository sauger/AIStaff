"""Lazy Contact↔User link for inbound messages.

Does not create contacts and does not replace ChannelIdentity.
TODO: WecomTempIdentityBinding stays the temporary wecom lookup; do not migrate it here.

v1 secondary keys are the raw external id from the message. They do not include
channel account scope, so the same userid in two corps can match one contact.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import Contact, utc_now

# Channel external id → nullable secondary lookup column. Unknown channels
# (personal wechat, group chats) only match an existing primary_user_id.
SECONDARY_FIELD_BY_CHANNEL = {
    "wecom": "wecom_userid",
    "feishu": "feishu_openid",
    "dingtalk": "dingtalk_userid",
}


def link_inbound_contact(
    db: Session,
    *,
    tenant_id: str,
    channel: str,
    external_id: str,
    user_id: str,
) -> list[Contact]:
    """Link an existing contact after resolve_or_provision_user.

    Group senders (``group:`` external ids) are skipped. Each employee's address
    book is resolved on its own: match ``primary_user_id`` first, otherwise the
    channel's secondary id. An empty ``primary_user_id`` is filled from the
    resolved user; an empty secondary id may be filled from this message.
    Existing values are left in place. No contact is created when nothing matches.

    Changes stay on the caller's session. This helper does not flush: a unique
    conflict during flush would roll back the inbound transaction, including the
    user that was just provisioned.
    """
    external_id = str(external_id or "").strip()
    if not tenant_id or not user_id or not external_id or external_id.startswith("group:"):
        return []

    rows = list(db.exec(select(Contact).where(Contact.tenant_id == tenant_id)).all())
    if not rows:
        return []

    field = SECONDARY_FIELD_BY_CHANNEL.get(channel)
    by_agent: dict[str, list[Contact]] = {}
    for row in rows:
        by_agent.setdefault(row.agent_id, []).append(row)

    linked: list[Contact] = []
    now = utc_now()
    for agent_rows in by_agent.values():
        primary_matches = [row for row in agent_rows if row.primary_user_id == user_id]
        if primary_matches:
            for row in primary_matches:
                if (
                    field
                    and not _field(row, field)
                    and not _other_has_secondary(agent_rows, row, field, external_id)
                ):
                    setattr(row, field, external_id)
                    row.updated_at = now
                    db.add(row)
                linked.append(row)
            continue
        if not field:
            continue
        for row in agent_rows:
            if _field(row, field) != external_id or row.primary_user_id:
                continue
            if any(other.primary_user_id == user_id for other in agent_rows):
                continue
            row.primary_user_id = user_id
            row.updated_at = now
            db.add(row)
            linked.append(row)
    return linked


def clear_primary_user(db: Session, user_id: str) -> None:
    """Drop the account link when that user is deleted. The contact row stays."""
    if not user_id:
        return
    rows = db.exec(select(Contact).where(Contact.primary_user_id == user_id)).all()
    now = utc_now()
    for row in rows:
        row.primary_user_id = None
        row.updated_at = now
        db.add(row)


def _field(row: Contact, name: str) -> str | None:
    value = getattr(row, name)
    text = str(value or "").strip()
    return text or None


def _other_has_secondary(
    rows: list[Contact], current: Contact, field: str, external_id: str
) -> bool:
    return any(row.id != current.id and _field(row, field) == external_id for row in rows)

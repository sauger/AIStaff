"""Lazy Contact↔User link for inbound messages.

Does not create contacts and does not replace ChannelIdentity.
TODO: WecomTempIdentityBinding stays the temporary wecom lookup; do not migrate it here.

v1 secondary keys are the raw external id from the message. They do not include
channel account scope, so the same userid in two corps can match one contact.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import Contact, utc_now

logger = logging.getLogger(__name__)

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

    Writes are best-effort. Each change is flushed inside a savepoint so a unique
    conflict rolls back only that contact write and leaves the inbound transaction,
    including a just-provisioned user, usable.
    """
    external_id = str(external_id or "").strip()
    if not tenant_id or not user_id or not external_id or external_id.startswith("group:"):
        return []

    field = SECONDARY_FIELD_BY_CHANNEL.get(channel)
    rows = _matching_contacts(db, tenant_id, user_id, field, external_id)
    if not rows:
        return []

    by_agent: dict[str, list[Contact]] = {}
    for row in rows:
        by_agent.setdefault(row.agent_id, []).append(row)

    linked: list[Contact] = []
    for agent_rows in by_agent.values():
        primary_matches = [row for row in agent_rows if row.primary_user_id == user_id]
        if primary_matches:
            for row in primary_matches:
                if (
                    field
                    and not _field(row, field)
                    and not _other_has_secondary(agent_rows, row, field, external_id)
                ):
                    if _apply_soft_fill(db, row, channel, **{field: external_id}):
                        linked.append(row)
                else:
                    linked.append(row)
            continue
        if not field:
            continue
        for row in agent_rows:
            if _field(row, field) != external_id or row.primary_user_id:
                continue
            if any(other.primary_user_id == user_id for other in agent_rows):
                continue
            if _apply_soft_fill(db, row, channel, primary_user_id=user_id):
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


def _matching_contacts(
    db: Session,
    tenant_id: str,
    user_id: str,
    field: str | None,
    external_id: str,
) -> list[Contact]:
    """Load only rows that can match this message.

    The inbound hook runs before the conversation's employee is chosen, so this
    is keyed by tenant plus ``primary_user_id`` and, when the channel has one,
    the secondary id. Per-employee precedence is applied to that set.
    """
    filters = [Contact.primary_user_id == user_id]
    if field:
        filters.append(getattr(Contact, field) == external_id)
    match = filters[0] if len(filters) == 1 else or_(*filters)
    return list(db.exec(select(Contact).where(Contact.tenant_id == tenant_id, match)).all())


def _apply_soft_fill(db: Session, row: Contact, channel: str, **changes: str) -> bool:
    """Flush one contact update in a savepoint. Conflicts are logged and dropped."""
    # Open the savepoint before the row is dirty. begin_nested() flushes other
    # pending work (the provisioned user) into the outer transaction first.
    nested = db.begin_nested()
    try:
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = utc_now()
        db.add(row)
        db.flush()
    except IntegrityError:
        nested.rollback()
        db.expire(row)
        logger.warning(
            "contact soft-fill skipped after unique conflict contact=%s channel=%s",
            row.id,
            channel,
        )
        return False
    nested.commit()
    return True


def _field(row: Contact, name: str) -> str | None:
    value = getattr(row, name)
    text = str(value or "").strip()
    return text or None


def _other_has_secondary(
    rows: list[Contact], current: Contact, field: str, external_id: str
) -> bool:
    return any(row.id != current.id and _field(row, field) == external_id for row in rows)

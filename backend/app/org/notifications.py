"""Person-addressed WeCom notifications. No login, roster or approval identity mutations."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any, NoReturn

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.channels.adapters.base import ChannelInbound
from app.channels.service_identity import (
    external_account_key,
    external_account_scope,
    p2p_external_conv_id,
)
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelBindingAgent,
    ChannelDelivery,
    ChatSession,
    EnterprisePerson,
    PersonContact,
    PersonNotificationBinding,
    PersonNotificationCode,
    User,
    new_id,
    utc_now,
)
from app.org.errors import OrgError
from app.org.service import _sqlite_write_lock
from app.time_utils import as_utc


def fail(code: str, message: str, status: int = 409) -> NoReturn:
    raise OrgError(code, message, status_code=status)


def person_for(db: Session, tenant_id: str, person_id: str) -> EnterprisePerson:
    person = db.get(EnterprisePerson, person_id)
    if not person or person.tenant_id != tenant_id:
        fail("PERSON_NOT_FOUND", "企业人员不存在。", 404)
    return person


def canonical_contact(db: Session, tenant_id: str, contact_id: str) -> PersonContact:
    seen: set[str] = set()
    while contact_id not in seen:
        seen.add(contact_id)
        contact = db.get(PersonContact, contact_id)
        if not contact or contact.tenant_id != tenant_id:
            break
        if not contact.merged_into_id:
            if contact.status != "active" or not contact.wecom_userid:
                break
            if contact.wecom_userid.startswith("group:"):
                break
            return contact
        contact_id = contact.merged_into_id
    fail("IDENTITY_CONFLICT", "企微联系人已失效或存在身份冲突。")


def channel_for(db: Session, tenant_id: str, binding_id: str) -> ChannelBinding:
    channel = db.get(ChannelBinding, binding_id)
    if not channel or channel.tenant_id != tenant_id or channel.channel != "wecom":
        fail("CHANNEL_INVALID", "通知通道不属于当前企业。")
    if channel.status != "active" or not channel.external_account_key:
        fail("CHANNEL_OFFLINE", "通知通道已停用或尚未完成配置。")
    config = channel.config_json or {}
    corp = str(config.get("corp_id") or "").strip()
    bot = str(config.get("bot_id") or "").strip()
    scope = external_account_scope(db, channel)
    # An observed bot-scoped private identity is usable without inventing a corporate UserID.
    # Scope upgrades still invalidate the stored snapshot and require explicit re-binding.
    if corp:
        if scope != corp:
            fail("SCOPE_CONFLICT", "机器人企业范围与配置不一致。")
    elif (
        not bot
        or scope != bot
        or channel.external_account_key != external_account_key("wecom", config)
    ):
        fail("SCOPE_MISSING", "无法确认机器人身份范围，请先核对渠道配置。")
    return channel


def scope_label(channel: ChannelBinding) -> str:
    corp = str((channel.config_json or {}).get("corp_id") or "").strip()
    return corp or "当前机器人范围（未配置企业 ID）"


def active_link(db: Session, tenant_id: str, person_id: str) -> PersonNotificationBinding | None:
    return db.exec(
        select(PersonNotificationBinding).where(
            PersonNotificationBinding.tenant_id == tenant_id,
            PersonNotificationBinding.person_id == person_id,
            PersonNotificationBinding.revoked_at.is_(None),
        )
    ).first()


def validate_identity(db: Session, person: EnterprisePerson, contact: PersonContact) -> None:
    if person.status != "active":
        fail("PERSON_INACTIVE", "人员已停用，不能接收通知。")
    if contact.enterprise_person_id not in {None, person.id}:
        fail("IDENTITY_CONFLICT", "该企微联系人已属于其他企业人员。")
    known_accounts = {contact.web_user_id, contact.channel_user_id} - {None, ""}
    if known_accounts:
        holder = db.exec(
            select(EnterprisePerson).where(
                EnterprisePerson.tenant_id == person.tenant_id,
                EnterprisePerson.id != person.id,
                EnterprisePerson.primary_user_id.in_(known_accounts),
            )
        ).first()
        if holder:
            fail("IDENTITY_CONFLICT", "该企微联系人的账号已属于其他企业人员。")
    # Reuse existing roster conflict checks, without choose_roster_primary / account changes.
    from app.org.wecom_link import resolve_wecom_person_link

    resolve_wecom_person_link(
        db,
        tenant_id=person.tenant_id,
        wecom_userid=contact.wecom_userid,
        exclude_person_id=person.id,
    )
    if person.wecom_userid and person.wecom_userid != contact.wecom_userid:
        fail("IDENTITY_CONFLICT", "人员已有不同的企微身份，请先核对原有关联。")
    for link in db.exec(
        select(PersonNotificationBinding).where(
            PersonNotificationBinding.tenant_id == person.tenant_id,
            PersonNotificationBinding.revoked_at.is_(None),
            PersonNotificationBinding.person_id != person.id,
        )
    ).all():
        if link.contact_id == contact.id or (
            link.scope == contact.wecom_corp_id and link.external_user_id == contact.wecom_userid
        ):
            fail("IDENTITY_CONFLICT", "该企微联系人已绑定其他人员的通知。")
        try:
            other = canonical_contact(db, person.tenant_id, link.contact_id)
        except OrgError:
            continue  # An unrelated stale binding must not disable this person's notifications.
        if other.id == contact.id:
            fail("IDENTITY_CONFLICT", "该企微联系人已绑定其他人员的通知。")


def revoke(db: Session, tenant_id: str, person_id: str, actor: str) -> None:
    _sqlite_write_lock(db)
    person_for(db, tenant_id, person_id)
    link = active_link(db, tenant_id, person_id)
    if link:
        link.revoked_at = utc_now()
        link.revoked_by_user_id = actor
        db.add(link)
        for delivery in db.exec(
            select(ChannelDelivery).where(
                ChannelDelivery.tenant_id == tenant_id,
                ChannelDelivery.kind == "person_notification",
                ChannelDelivery.status == "pending",
            )
        ).all():
            if delivery.artifact_json.get("notification_binding_id") == link.id:
                delivery.status = "failed"
                delivery.last_error = "通知绑定已解除或变更，请按新目标重新发起。"
                delivery.next_attempt_at = None
                db.add(delivery)
    db.exec(
        update(PersonNotificationCode)
        .where(
            PersonNotificationCode.tenant_id == tenant_id,
            PersonNotificationCode.person_id == person_id,
            PersonNotificationCode.used_at.is_(None),
        )
        .values(used_at=utc_now())
    )
    db.flush()


def bind(
    db: Session, tenant_id: str, person_id: str, contact_id: str, binding_id: str, actor: str
) -> PersonNotificationBinding:
    _sqlite_write_lock(db)
    person = person_for(db, tenant_id, person_id)
    contact = canonical_contact(db, tenant_id, contact_id)
    channel = channel_for(db, tenant_id, binding_id)
    scope = external_account_scope(db, channel)
    if contact.wecom_corp_id != scope:
        fail("SCOPE_CONFLICT", "联系人与通知通道的企业范围不一致。")
    validate_identity(db, person, contact)
    # Administrator must select an observed private bot identity, not a directory string.
    sessions = matching_sessions(db, contact, channel)
    if not sessions:
        fail("TARGET_MISSING", "尚未识别到该成员与指定机器人的有效私聊，请先私聊机器人。")
    revoke(db, tenant_id, person_id, actor)
    link = PersonNotificationBinding(
        tenant_id=tenant_id,
        person_id=person_id,
        contact_id=contact.id,
        binding_id=channel.id,
        account_key=channel.external_account_key,
        scope=scope,
        external_user_id=contact.wecom_userid,
        created_by_user_id=actor,
    )
    db.add(link)
    db.flush()
    return link


def matching_sessions(
    db: Session, contact: PersonContact, channel: ChannelBinding
) -> list[ChatSession]:
    expected = p2p_external_conv_id("wecom", contact.wecom_corp_id, contact.wecom_userid)
    matches = []
    for session in db.exec(
        select(ChatSession)
        .where(
            ChatSession.tenant_id == contact.tenant_id,
            ChatSession.channel_binding_id == channel.id,
            ChatSession.channel == "wecom",
            ChatSession.status == "active",
            ChatSession.channel_account_key == channel.external_account_key,
            ChatSession.external_conv_id == expected,
        )
        .order_by(ChatSession.id)
    ).all():
        target = session.channel_target_json or {}
        if target.get("is_group") or target.get("to_user_id") != contact.wecom_userid:
            continue
        if not session.contact_id:
            continue
        try:
            root = canonical_contact(db, contact.tenant_id, session.contact_id)
        except OrgError:
            continue
        if root.id == contact.id:
            matches.append(session)
    return matches


def resolve_target(
    db: Session, tenant_id: str, person_id: str
) -> tuple[PersonNotificationBinding, ChannelBinding, ChatSession]:
    person = person_for(db, tenant_id, person_id)
    if person.status != "active":
        fail("PERSON_INACTIVE", "人员已停用，不能接收通知。")
    link = active_link(db, tenant_id, person_id)
    if not link:
        fail("NOT_BOUND", "尚未绑定企微通知。")
    contact = canonical_contact(db, tenant_id, link.contact_id)
    validate_identity(db, person, contact)
    channel = channel_for(db, tenant_id, link.binding_id)
    if (
        link.scope != contact.wecom_corp_id
        or link.external_user_id != contact.wecom_userid
        or link.scope != external_account_scope(db, channel)
        or link.account_key != channel.external_account_key
    ):
        fail("IDENTITY_CONFLICT", "企微身份或通道账号已变化，请重新核对绑定。")
    if not channel.connected:
        fail("CHANNEL_OFFLINE", "通知通道离线。")
    sessions = matching_sessions(db, contact, channel)
    if not sessions:
        fail("TARGET_MISSING", "缺少有效私聊投递目标，请重新私聊机器人。")
    # All matching sessions have the exact same scoped bot/private target; never latest-chat routing.
    return link, channel, sessions[0]


def stage_notification(
    db: Session,
    *,
    tenant_id: str,
    recipient_person_id: str,
    source_agent_id: str,
    event_key: str,
    text: str,
    is_test: bool = False,
) -> ChannelDelivery:
    """Trusted business-event seam. Caller must authorize its business event before calling."""
    _sqlite_write_lock(db)
    agent = db.get(AgentProfile, source_agent_id)
    if not agent or agent.tenant_id != tenant_id or agent.status != "active":
        fail("AGENT_INVALID", "来源数字员工不属于当前企业。", 403)
    if not event_key.strip() or not text.strip() or len(text.encode()) > 1700:
        fail("NOTIFICATION_INVALID", "请提供事件标识及不超过 1700 字节的通知内容。", 400)
    key = (
        "person-notify:"
        + hashlib.sha256(
            repr((tenant_id, source_agent_id, recipient_person_id, event_key)).encode()
        ).hexdigest()
    )
    existing = db.exec(
        select(ChannelDelivery).where(ChannelDelivery.idempotency_key == key)
    ).first()
    if existing:
        return existing
    link, channel, session = resolve_target(db, tenant_id, recipient_person_id)
    mounted = db.exec(
        select(ChannelBindingAgent).where(
            ChannelBindingAgent.tenant_id == tenant_id,
            ChannelBindingAgent.binding_id == channel.id,
            ChannelBindingAgent.agent_id == source_agent_id,
        )
    ).first()
    if channel.agent_id != source_agent_id and not mounted:
        fail("AGENT_CHANNEL_CONFLICT", "来源数字员工未接入所选通知通道。", 403)
    row = ChannelDelivery(
        tenant_id=tenant_id,
        binding_id=channel.id,
        session_id=session.id,
        kind="person_notification",
        text=text,
        next_attempt_at=utc_now(),
        idempotency_key=key,
        target_json={"to_user_id": link.external_user_id, "is_group": False},
        artifact_json={
            "notification_binding_id": link.id,
            "recipient_person_id": recipient_person_id,
            "source_agent_id": source_agent_id,
            "is_test": is_test,
        },
    )
    db.add(row)
    db.flush()
    return row


def validate_delivery(db: Session, delivery: ChannelDelivery) -> None:
    meta = delivery.artifact_json or {}
    link, channel, _session = resolve_target(
        db, delivery.tenant_id, meta.get("recipient_person_id", "")
    )
    if (
        meta.get("notification_binding_id") != link.id
        or delivery.binding_id != channel.id
        or delivery.target_json != {"to_user_id": link.external_user_id, "is_group": False}
    ):
        fail("TARGET_CHANGED", "通知目标已变更，原投递已停止。")
    agent = db.get(AgentProfile, meta.get("source_agent_id", ""))
    mounted = db.exec(
        select(ChannelBindingAgent).where(
            ChannelBindingAgent.tenant_id == delivery.tenant_id,
            ChannelBindingAgent.binding_id == channel.id,
            ChannelBindingAgent.agent_id == meta.get("source_agent_id", ""),
        )
    ).first()
    if (
        not agent
        or agent.tenant_id != delivery.tenant_id
        or agent.status != "active"
        or (channel.agent_id != agent.id and not mounted)
    ):
        fail("AGENT_CHANNEL_CONFLICT", "来源数字员工或通道授权已变化，通知已停止。")
    stored = db.get(ChatSession, delivery.session_id)
    contact = canonical_contact(db, delivery.tenant_id, link.contact_id)
    if not stored or stored.id not in {s.id for s in matching_sessions(db, contact, channel)}:
        fail("TARGET_MISSING", "原通知私聊已失效。")


def issue_code(
    db: Session, tenant_id: str, person_id: str, binding_id: str, user: User
) -> dict[str, str]:
    _sqlite_write_lock(db)
    person = person_for(db, tenant_id, person_id)
    if person.primary_user_id != user.id or person.status != "active":
        fail("SELF_ONLY", "只能为本人有效的员工档案生成认领码。", 403)
    channel = channel_for(db, tenant_id, binding_id)
    db.exec(
        update(PersonNotificationCode)
        .where(
            PersonNotificationCode.tenant_id == tenant_id,
            PersonNotificationCode.person_id == person_id,
            PersonNotificationCode.used_at.is_(None),
        )
        .values(used_at=utc_now())
    )
    code = secrets.token_hex(12).upper()
    row = PersonNotificationCode(
        tenant_id=tenant_id,
        person_id=person_id,
        user_id=user.id,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        binding_id=channel.id,
        account_key=channel.external_account_key,
        scope=external_account_scope(db, channel),
        expires_at=utc_now() + timedelta(minutes=10),
    )
    db.add(row)
    db.flush()
    return {"command": f"/通知绑定 {code}", "expires_at": row.expires_at.isoformat()}


def consume_code(db: Session, channel: ChannelBinding, inbound: ChannelInbound, code: str) -> str:
    from app.contacts.identity import ensure_wecom_contact

    if channel.channel != "wecom" or inbound.is_group:
        return "通知认领仅支持指定企微机器人的私聊。"
    _sqlite_write_lock(db)
    record = db.exec(
        select(PersonNotificationCode).where(
            PersonNotificationCode.tenant_id == channel.tenant_id,
            PersonNotificationCode.code_hash
            == hashlib.sha256(code.strip().upper().encode()).hexdigest(),
        )
    ).first()
    if not record:
        return "通知认领码无效或已过期。"
    try:
        current = channel_for(db, channel.tenant_id, channel.id)
        person = person_for(db, channel.tenant_id, record.person_id)
        if (
            record.binding_id != current.id
            or record.account_key != current.external_account_key
            or record.scope != external_account_scope(db, current)
            or person.primary_user_id != record.user_id
            or person.status != "active"
        ):
            fail("CODE_SCOPE", "认领码与当前企业、机器人或员工身份不匹配。")
        with db.begin_nested():
            claimed = db.exec(
                update(PersonNotificationCode)
                .where(
                    PersonNotificationCode.id == record.id,
                    PersonNotificationCode.used_at.is_(None),
                    PersonNotificationCode.expires_at > utc_now(),
                )
                .values(used_at=utc_now())
            )
            if claimed.rowcount != 1:
                return "通知认领码无效或已过期。"
            contact = ensure_wecom_contact(
                db,
                tenant_id=channel.tenant_id,
                wecom_userid=inbound.from_user_id,
                corp_id=record.scope,
                display_name=None,
                user=None,
            )
            if contact is None:
                fail("IDENTITY_CONFLICT", "无法唯一识别企微联系人。")
            # This private command itself proves the bot namespace and the private destination.
            session = ChatSession(
                id=new_id("session"),
                tenant_id=channel.tenant_id,
                contact_id=contact.id,
                agent_id=channel.agent_id,
                user_id=contact.channel_user_id,
                channel="wecom",
                channel_binding_id=channel.id,
                channel_account_key=channel.external_account_key,
                external_conv_id=p2p_external_conv_id("wecom", record.scope, inbound.from_user_id),
                channel_target_json={"to_user_id": inbound.from_user_id, "is_group": False},
            )
            db.add(session)
            db.flush()
            bind(db, channel.tenant_id, person.id, contact.id, channel.id, record.user_id)
            return "企微通知认领成功。可在员工页面发送测试通知，登录账号及审批身份保持不变。"
    except OrgError as error:
        return error.message
    except IntegrityError:
        return "该企微联系人已被绑定，请刷新员工页面后核对。"


def status_view(db: Session, tenant_id: str, person_id: str) -> dict[str, Any]:
    person = person_for(db, tenant_id, person_id)
    link = active_link(db, tenant_id, person_id)
    result = {
        "binding_status": "bound" if link else "unbound",
        "reachability": "unverified",
        "contact_name": None,
        "scope": None,
        "scope_label": None,
        "member_userid": None,
        "channel_name": None,
        "verified_at": None,
        "last_delivery": None,
        "reason": None,
    }
    if person.status != "active":
        result["binding_status"] = "inactive"
    if link:
        contact = db.get(PersonContact, link.contact_id)
        channel = db.get(ChannelBinding, link.binding_id)
        result.update(
            contact_name=contact.display_name if contact else None,
            scope=link.scope,
            scope_label=scope_label(channel) if channel else None,
            member_userid=link.external_user_id,
            channel_name=channel.name if channel else None,
        )
        rows = db.exec(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.tenant_id == tenant_id,
                ChannelDelivery.kind == "person_notification",
            )
            .order_by(ChannelDelivery.created_at.desc())
        ).all()
        deliveries = [r for r in rows if r.artifact_json.get("notification_binding_id") == link.id]
        if deliveries:
            row = deliveries[0]
            result["last_delivery"] = {
                "id": row.id,
                "status": row.status,
                "error": "发送失败，请核对通道后重试。" if row.last_error else None,
                "delivered_at": as_utc(row.delivered_at).isoformat() if row.delivered_at else None,
            }
            if row.status == "failed":
                result["reachability"] = "failed"
        tests = [
            r for r in deliveries if r.artifact_json.get("is_test") and r.status == "delivered"
        ]
        if tests:
            result["verified_at"] = (
                as_utc(tests[0].delivered_at).isoformat() if tests[0].delivered_at else None
            )
            if result["reachability"] != "failed":
                result["reachability"] = "test_succeeded"
        try:
            resolve_target(db, tenant_id, person_id)
        except OrgError as error:
            result["reason"] = error.message
            result["reachability"] = error.code.lower()
            if error.code == "IDENTITY_CONFLICT" or error.code.startswith("ORG_WECOM_"):
                result["binding_status"] = "conflict"
    return result

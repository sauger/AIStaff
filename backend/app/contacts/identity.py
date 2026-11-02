"""租户级联系人认人。

入站按通道 + 渠道 ID 查 ``contacts``（``PersonContact``）。命中即用；未命中且
组织字段未被占用时才建 ``kind=personal``。新路径不写 ``channel_identities``。

兼容（读旧数据，不把 CI 当认人主键）：
- 企微若已有 ChannelIdentity，解析用户时仍读它，避免已绑定网页账号掉线。
- 启动迁移把历史企微 CI 回填成联系人；回填不修改 CI 行。
- 绑定码仍会改写遗留 CI，同时把网页账号挂到联系人的 ``web_user_id``。
- 群聊桶不是联系人，继续走原来的 CI 懒建。
- ``channel_user_id`` 记住会话用的 User，网页登录只进 ``web_user_id``。

Honcho peer 仍是 ``user:{User.id}``。改为 ``contact:{contact_id}`` 留到 P1。
"""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import (
    ContactMergeSuggestion,
    EnterprisePerson,
    PersonContact,
    User,
    utc_now,
)

logger = logging.getLogger(__name__)

_MERGE_DEPTH = 5
_PLACEHOLDER_PREFIXES = (
    "企业微信用户 ",
    "企业微信群聊 ",
    "企微用户 ",
    "微信用户 ",
    "微信群聊 ",
    "网页用户 ",
    "邮件联系人 ",
)


def normalize_email(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def normalize_wecom_userid(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_corp_id(value: str | None) -> str:
    return str(value or "").strip()


def is_placeholder_display_name(value: str | None) -> bool:
    text = str(value or "").strip()
    return any(text.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def bind_turn_contact(db: Session, request: object) -> object:
    """给已解析的本轮说话人补上 contact_id。群聊和未解析说话人不建联系人。"""

    speaker = getattr(request, "speaker", None)
    if speaker is None or getattr(speaker, "status", None) != "resolved":
        return request
    contact_id = str(getattr(speaker, "contact_id", "") or "").strip()
    channel = str(getattr(request, "channel", "") or "web").strip() or "web"
    # 来信会话的 contact 是发件人，执行技能的说话人仍是邮箱主人，不能把两边当成同一个人。
    if not contact_id and channel != "mail_inbound":
        session_id = str(getattr(request, "session_id", "") or "").strip()
        if session_id:
            from app.db.models import ChatSession

            session = db.get(ChatSession, session_id)
            if (
                session is not None
                and session.tenant_id == getattr(request, "tenant_id", None)
                and session.contact_id
            ):
                contact_id = session.contact_id
    is_group = bool(getattr(request, "channel_is_group", False))
    if not contact_id and channel == "wecom" and not is_group:
        external_id = normalize_wecom_userid(getattr(request, "channel_external_user_id", None))
        user_id = str(getattr(speaker, "user_id", "") or "").strip()
        user = db.get(User, user_id) if user_id else None
        if external_id and not external_id.startswith("group:") and user is not None:
            contact = ensure_wecom_contact(
                db,
                tenant_id=str(getattr(request, "tenant_id", "") or ""),
                wecom_userid=external_id,
                corp_id=normalize_corp_id(
                    getattr(request, "channel_external_account_scope", None)
                ),
                display_name=None,
                user=user,
            )
            contact_id = contact.id if contact is not None else ""
    if not contact_id and channel in {"web", ""} and not is_group:
        user_id = str(getattr(speaker, "user_id", "") or "").strip()
        user = db.get(User, user_id) if user_id else None
        if user is not None and user.tenant_id == getattr(request, "tenant_id", None):
            contact = ensure_web_contact(db, user)
            contact_id = contact.id if contact is not None else ""
    if not contact_id:
        return request
    return request.model_copy(
        update={"speaker": speaker.model_copy(update={"contact_id": contact_id})}
    )


def lookup_wecom_contact_id(
    db: Session,
    *,
    tenant_id: str,
    wecom_userid: str,
    corp_id: str | None,
) -> str | None:
    userid = normalize_wecom_userid(wecom_userid)
    tenant = str(tenant_id or "").strip()
    if not tenant or not userid or userid.startswith("group:"):
        return None
    contact = _find_wecom_contact(db, tenant, userid, normalize_corp_id(corp_id))
    return contact.id if contact is not None else None


def ensure_wecom_contact(
    db: Session,
    *,
    tenant_id: str,
    wecom_userid: str,
    corp_id: str | None,
    display_name: str | None,
    user: User | None,
) -> PersonContact | None:
    userid = normalize_wecom_userid(wecom_userid)
    tenant = str(tenant_id or "").strip()
    if not tenant or not userid or userid.startswith("group:"):
        return None
    corp = normalize_corp_id(corp_id)
    found = _find_wecom_contact(db, tenant, userid, corp)
    if found is not None:
        _touch_channel_user(found, user, corp)
        _prefer_display_name(found, display_name)
        db.add(found)
        db.flush()
        return found
    person = _active_person_by_wecom(db, tenant, userid)
    if person is not None:
        contact = sync_org_contact_from_person(db, person, corp_id=corp, user=user)
        if contact is not None:
            return contact
    blocked = _blocking_org(db, tenant, wecom_userid=userid, web_user_id=_web_id(user))
    if blocked is not None:
        _touch_channel_user(blocked, user, corp)
        db.add(blocked)
        db.flush()
        return blocked
    if _org_field_ambiguous(db, tenant, wecom_userid=userid):
        logger.warning(
            "skip personal wecom contact; org channel field is ambiguous tenant=%s userid=%s",
            tenant,
            userid,
        )
        return None
    return _insert_personal(
        db,
        tenant_id=tenant,
        display_name=_wecom_display_name(display_name, user, userid),
        source="inbound_wecom",
        wecom_userid=userid,
        wecom_corp_id=corp,
        user=user,
    )


def ensure_web_contact(db: Session, user: User) -> PersonContact | None:
    tenant = str(user.tenant_id or "").strip()
    if not tenant or not user.id:
        return None
    found = _find_by_web_user(db, tenant, user.id)
    if found is not None:
        _touch_channel_user(found, user, found.wecom_corp_id or "")
        _prefer_display_name(found, user.display_name)
        db.add(found)
        db.flush()
        return found
    person = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant,
            EnterprisePerson.primary_user_id == user.id,
            EnterprisePerson.status == "active",
        )
    ).first()
    if person is not None:
        contact = sync_org_contact_from_person(db, person, user=user)
        if contact is not None:
            return contact
    blocked = _blocking_org(db, tenant, web_user_id=user.id)
    if blocked is not None:
        return blocked
    name = str(user.display_name or user.username or "").strip() or f"网页用户 {user.id[-8:]}"
    return _insert_personal(
        db,
        tenant_id=tenant,
        display_name=name,
        source="web_signup",
        web_user_id=user.id,
        user=user,
    )


def ensure_email_contact(
    db: Session,
    *,
    tenant_id: str,
    email: str | None,
    display_name: str | None = None,
) -> PersonContact | None:
    tenant = str(tenant_id or "").strip()
    normalized = normalize_email(email)
    if not tenant or normalized is None or "@" not in normalized:
        return None
    found = _find_by_email(db, tenant, normalized)
    if found is not None:
        _prefer_display_name(found, display_name)
        db.add(found)
        db.flush()
        return found
    people = list(
        db.exec(
            select(EnterprisePerson).where(
                EnterprisePerson.tenant_id == tenant,
                EnterprisePerson.status == "active",
                func.lower(EnterprisePerson.email) == normalized,
            )
        ).all()
    )
    if len(people) == 1:
        contact = sync_org_contact_from_person(db, people[0], email=normalized)
        if contact is not None:
            return contact
    blocked = _blocking_org(db, tenant, email=normalized)
    if blocked is not None:
        return blocked
    if len(people) > 1:
        logger.warning(
            "skip personal email contact; multiple enterprise people share %s",
            normalized,
        )
        return None
    local = normalized.split("@", 1)[0] or normalized
    return _insert_personal(
        db,
        tenant_id=tenant,
        display_name=str(display_name or "").strip() or f"邮件联系人 {local}",
        source="inbound_email",
        email=normalized,
        user=None,
    )


def sync_org_contact_from_person(
    db: Session,
    person: EnterprisePerson,
    *,
    corp_id: str | None = None,
    user: User | None = None,
    email: str | None = None,
) -> PersonContact | None:
    """确保企业人员有一条 kind=org 联系人。字段被别人占用时不抢，只记合并建议。"""

    nested = db.begin_nested()
    try:
        contact = _sync_org_contact_unlocked(
            db,
            person,
            corp_id=corp_id,
            user=user,
            email=email,
        )
        db.flush()
    except IntegrityError:
        nested.rollback()
        logger.warning(
            "org contact sync skipped after unique conflict person=%s",
            person.id,
        )
        return _find_org_by_person(db, person.tenant_id, person.id)
    nested.commit()
    return contact


def attach_web_user_to_wecom_contact(
    db: Session,
    *,
    tenant_id: str,
    wecom_userid: str,
    corp_id: str | None,
    user: User,
) -> PersonContact | None:
    contact = ensure_wecom_contact(
        db,
        tenant_id=tenant_id,
        wecom_userid=wecom_userid,
        corp_id=corp_id,
        display_name=user.display_name,
        user=user,
    )
    if contact is None or user.source != "web":
        return contact
    holder = _find_by_web_user(db, tenant_id, user.id)
    if holder is not None and holder.id != contact.id:
        _record_merge_suggestion(db, tenant_id, contact, holder, "same_web_user")
        return contact
    if contact.web_user_id in {None, user.id}:
        contact.web_user_id = user.id
        contact.channel_user_id = user.id
        _prefer_display_name(contact, user.display_name)
        contact.updated_at = utc_now()
        db.add(contact)
        db.flush()
    return contact


def detach_web_user_from_wecom_contact(
    db: Session,
    *,
    tenant_id: str,
    wecom_userid: str,
    corp_id: str | None,
    web_user_id: str,
    channel_user: User | None,
) -> None:
    contact = _find_wecom_contact(db, tenant_id, wecom_userid, normalize_corp_id(corp_id))
    if contact is None or contact.web_user_id != web_user_id:
        return
    contact.web_user_id = None
    if channel_user is not None:
        contact.channel_user_id = channel_user.id
    contact.updated_at = utc_now()
    db.add(contact)
    db.flush()


def record_merge_suggestion(
    db: Session,
    *,
    tenant_id: str,
    left: PersonContact,
    right: PersonContact,
    reason: str,
) -> ContactMergeSuggestion | None:
    return _record_merge_suggestion(db, tenant_id, left, right, reason)


def _sync_org_contact_unlocked(
    db: Session,
    person: EnterprisePerson,
    *,
    corp_id: str | None,
    user: User | None,
    email: str | None,
) -> PersonContact:
    tenant = person.tenant_id
    corp = normalize_corp_id(corp_id)
    normalized_email = normalize_email(email if email is not None else person.email)
    userid = normalize_wecom_userid(person.wecom_userid)
    web_user_id = _web_user_id_for_person(db, person, user)
    contact = _find_org_by_person(db, tenant, person.id)
    if contact is None and userid:
        contact = _find_wecom_contact(db, tenant, userid, corp or "")
        if contact is not None and contact.kind != "org":
            _record_merge_suggestion(
                db,
                tenant,
                contact,
                _placeholder_org_for_suggestion(db, person, corp),
                "same_wecom",
            )
            contact = _find_org_by_person(db, tenant, person.id)
    if contact is None:
        contact = PersonContact(
            tenant_id=tenant,
            kind="org",
            display_name=_person_display_name(person),
            status="active" if person.status == "active" else "archived",
            source="enterprise_sync",
            enterprise_person_id=person.id,
            wecom_corp_id=corp,
        )
        db.add(contact)
        db.flush()
    contact.kind = "org"
    contact.enterprise_person_id = person.id
    contact.source = contact.source or "enterprise_sync"
    contact.display_name = _person_display_name(person)
    contact.status = "active" if person.status == "active" else "archived"
    if corp and not contact.wecom_corp_id:
        contact.wecom_corp_id = corp
    _assign_if_free(db, contact, "wecom_userid", userid, "same_wecom")
    _assign_if_free(db, contact, "email", normalized_email, "same_email")
    _assign_if_free(db, contact, "web_user_id", web_user_id, "same_web_user")
    if person.phone and not contact.phone:
        contact.phone = person.phone
    actor = user or _primary_user(db, person)
    _touch_channel_user(contact, actor, contact.wecom_corp_id or corp)
    contact.updated_at = utc_now()
    db.add(contact)
    return contact


def _placeholder_org_for_suggestion(
    db: Session,
    person: EnterprisePerson,
    corp: str,
) -> PersonContact:
    existing = _find_org_by_person(db, person.tenant_id, person.id)
    if existing is not None:
        return existing
    contact = PersonContact(
        tenant_id=person.tenant_id,
        kind="org",
        display_name=_person_display_name(person),
        status="active" if person.status == "active" else "archived",
        source="enterprise_sync",
        enterprise_person_id=person.id,
        wecom_corp_id=corp,
    )
    db.add(contact)
    db.flush()
    return contact


def _assign_if_free(
    db: Session,
    contact: PersonContact,
    field: str,
    value: str | None,
    reason: str,
) -> None:
    if not value:
        return
    current = getattr(contact, field)
    if current == value:
        return
    if current:
        return
    holder = _holder_of(db, contact.tenant_id, field, value)
    if holder is not None and holder.id != contact.id:
        if holder.kind == "personal":
            _record_merge_suggestion(db, contact.tenant_id, holder, contact, reason)
        return
    setattr(contact, field, value)


def _holder_of(
    db: Session,
    tenant_id: str,
    field: str,
    value: str,
) -> PersonContact | None:
    column = getattr(PersonContact, field)
    rows = list(
        db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                column == value,
            )
        ).all()
    )
    canonical = []
    for row in rows:
        resolved = _canonicalize(db, row)
        if resolved is not None and resolved.id not in {item.id for item in canonical}:
            canonical.append(resolved)
    if len(canonical) == 1:
        return canonical[0]
    return None


def _insert_personal(
    db: Session,
    *,
    tenant_id: str,
    display_name: str,
    source: str,
    user: User | None,
    wecom_userid: str | None = None,
    wecom_corp_id: str = "",
    web_user_id: str | None = None,
    email: str | None = None,
) -> PersonContact:
    web_id = web_user_id or _web_id(user)
    contact = PersonContact(
        tenant_id=tenant_id,
        kind="personal",
        display_name=display_name,
        status="active",
        source=source,
        wecom_userid=wecom_userid,
        wecom_corp_id=wecom_corp_id or "",
        web_user_id=web_id,
        email=email,
        channel_user_id=user.id if user is not None else web_id,
    )
    nested = db.begin_nested()
    try:
        db.add(contact)
        db.flush()
    except IntegrityError:
        nested.rollback()
        existing = None
        if wecom_userid:
            existing = _find_wecom_contact(db, tenant_id, wecom_userid, wecom_corp_id)
        if existing is None and web_id:
            existing = _find_by_web_user(db, tenant_id, web_id)
        if existing is None and email:
            existing = _find_by_email(db, tenant_id, email)
        if existing is not None:
            return existing
        raise
    nested.commit()
    return contact


def _find_wecom_contact(
    db: Session,
    tenant_id: str,
    userid: str,
    corp: str,
) -> PersonContact | None:
    rows = [
        _canonicalize(db, row)
        for row in db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                PersonContact.wecom_userid == userid,
            )
        ).all()
    ]
    unique = _dedupe(rows)
    orgs = [row for row in unique if row.kind == "org"]
    exact_orgs = [row for row in orgs if (row.wecom_corp_id or "") == (corp or "")]
    if len(exact_orgs) == 1:
        return exact_orgs[0]
    if len(orgs) == 1:
        # 组织内字段已占用：即使 corp 还没写上，也不再克隆 personal。
        if corp and not (orgs[0].wecom_corp_id or ""):
            orgs[0].wecom_corp_id = corp
        return orgs[0]
    exact = [row for row in unique if (row.wecom_corp_id or "") == (corp or "")]
    if len(exact) == 1:
        return exact[0]
    if corp:
        blank = [row for row in unique if not (row.wecom_corp_id or "")]
        if len(blank) == 1 and not exact:
            blank[0].wecom_corp_id = corp
            return blank[0]
    return None


def _find_by_web_user(db: Session, tenant_id: str, user_id: str) -> PersonContact | None:
    rows = [
        _canonicalize(db, row)
        for row in db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                PersonContact.web_user_id == user_id,
            )
        ).all()
    ]
    unique = _dedupe(rows)
    orgs = [row for row in unique if row.kind == "org"]
    if len(orgs) == 1:
        return orgs[0]
    return unique[0] if len(unique) == 1 else None


def _find_by_email(db: Session, tenant_id: str, email: str) -> PersonContact | None:
    rows = [
        _canonicalize(db, row)
        for row in db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                func.lower(PersonContact.email) == email,
            )
        ).all()
    ]
    unique = _dedupe(rows)
    orgs = [row for row in unique if row.kind == "org"]
    if len(orgs) == 1:
        return orgs[0]
    return unique[0] if len(unique) == 1 else None


def _find_org_by_person(
    db: Session,
    tenant_id: str,
    person_id: str,
) -> PersonContact | None:
    row = db.exec(
        select(PersonContact).where(
            PersonContact.tenant_id == tenant_id,
            PersonContact.enterprise_person_id == person_id,
            PersonContact.merged_into_id.is_(None),
        )
    ).first()
    return _canonicalize(db, row) if row is not None else None


def _blocking_org(
    db: Session,
    tenant_id: str,
    *,
    wecom_userid: str | None = None,
    web_user_id: str | None = None,
    email: str | None = None,
) -> PersonContact | None:
    matches: list[PersonContact] = []
    if wecom_userid:
        rows = db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                PersonContact.kind == "org",
                PersonContact.wecom_userid == wecom_userid,
                PersonContact.merged_into_id.is_(None),
            )
        ).all()
        matches.extend(rows)
    if web_user_id:
        row = db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                PersonContact.kind == "org",
                PersonContact.web_user_id == web_user_id,
                PersonContact.merged_into_id.is_(None),
            )
        ).first()
        if row is not None:
            matches.append(row)
    if email:
        row = db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                PersonContact.kind == "org",
                func.lower(PersonContact.email) == email,
                PersonContact.merged_into_id.is_(None),
            )
        ).first()
        if row is not None:
            matches.append(row)
    unique = _dedupe([_canonicalize(db, row) for row in matches])
    orgs = [row for row in unique if row is not None and row.kind == "org"]
    if len(orgs) == 1:
        return orgs[0]
    return None


def _org_field_ambiguous(
    db: Session,
    tenant_id: str,
    *,
    wecom_userid: str | None = None,
) -> bool:
    if not wecom_userid:
        return False
    rows = db.exec(
        select(PersonContact).where(
            PersonContact.tenant_id == tenant_id,
            PersonContact.kind == "org",
            PersonContact.wecom_userid == wecom_userid,
            PersonContact.merged_into_id.is_(None),
        )
    ).all()
    return len(_dedupe(rows)) > 1


def _active_person_by_wecom(
    db: Session,
    tenant_id: str,
    userid: str,
) -> EnterprisePerson | None:
    rows = list(
        db.exec(
            select(EnterprisePerson).where(
                EnterprisePerson.tenant_id == tenant_id,
                EnterprisePerson.wecom_userid == userid,
                EnterprisePerson.status == "active",
            )
        ).all()
    )
    if len(rows) == 1:
        return rows[0]
    return None


def _canonicalize(
    db: Session,
    row: PersonContact | None,
    depth: int = 0,
) -> PersonContact | None:
    if row is None or depth > _MERGE_DEPTH:
        return row
    merged_into = str(row.merged_into_id or "").strip()
    if not merged_into:
        return row
    target = db.get(PersonContact, merged_into)
    if target is None:
        return row
    return _canonicalize(db, target, depth + 1)


def _dedupe(rows: list[PersonContact | None]) -> list[PersonContact]:
    unique: list[PersonContact] = []
    seen: set[str] = set()
    for row in rows:
        if row is None or row.id in seen:
            continue
        seen.add(row.id)
        unique.append(row)
    return unique


def _touch_channel_user(contact: PersonContact, user: User | None, corp: str) -> None:
    if user is None:
        return
    stored = contact.wecom_corp_id or ""
    incoming = corp or ""
    compatible = stored == incoming or stored == "" or incoming == ""
    if contact.channel_user_id is None and compatible:
        contact.channel_user_id = user.id
    if user.source == "web" and contact.web_user_id is None and compatible:
        contact.web_user_id = user.id
    if stored == "" and incoming and contact.wecom_userid:
        contact.wecom_corp_id = incoming
    contact.updated_at = utc_now()


def _prefer_display_name(contact: PersonContact, display_name: str | None) -> None:
    candidate = str(display_name or "").strip()
    if not candidate:
        return
    current = str(contact.display_name or "").strip()
    if not current or (is_placeholder_display_name(current) and not is_placeholder_display_name(candidate)):
        contact.display_name = candidate
        contact.updated_at = utc_now()


def _wecom_display_name(display_name: str | None, user: User | None, userid: str) -> str:
    candidate = str(display_name or "").strip()
    if candidate and not is_placeholder_display_name(candidate):
        return candidate
    if user is not None:
        named = str(user.display_name or "").strip()
        if named and not is_placeholder_display_name(named):
            return named
    if candidate:
        return candidate
    suffix = userid[-8:] if len(userid) > 8 else userid
    return f"企微用户 {suffix}"


def _person_display_name(person: EnterprisePerson) -> str:
    return str(person.name or "").strip() or "未命名联系人"


def _primary_user(db: Session, person: EnterprisePerson) -> User | None:
    if not person.primary_user_id:
        return None
    user = db.get(User, person.primary_user_id)
    if user is None or user.tenant_id != person.tenant_id:
        return None
    return user


def _web_user_id_for_person(
    db: Session,
    person: EnterprisePerson,
    user: User | None,
) -> str | None:
    primary = _primary_user(db, person)
    if primary is not None and primary.source == "web":
        return primary.id
    return _web_id(user)


def _web_id(user: User | None) -> str | None:
    if user is None or user.source != "web":
        return None
    return user.id


def _record_merge_suggestion(
    db: Session,
    tenant_id: str,
    left: PersonContact,
    right: PersonContact,
    reason: str,
) -> ContactMergeSuggestion | None:
    if left.id == right.id:
        return None
    first, second = sorted((left, right), key=lambda row: row.id)
    existing = db.exec(
        select(ContactMergeSuggestion).where(
            ContactMergeSuggestion.tenant_id == tenant_id,
            ContactMergeSuggestion.left_contact_id == first.id,
            ContactMergeSuggestion.right_contact_id == second.id,
            ContactMergeSuggestion.reason == reason,
            ContactMergeSuggestion.status == "pending",
        )
    ).first()
    if existing is not None:
        return existing
    suggestion = ContactMergeSuggestion(
        tenant_id=tenant_id,
        left_contact_id=first.id,
        right_contact_id=second.id,
        reason=reason,
        status="pending",
    )
    nested = db.begin_nested()
    try:
        db.add(suggestion)
        db.flush()
    except IntegrityError:
        nested.rollback()
        return None
    nested.commit()
    return suggestion

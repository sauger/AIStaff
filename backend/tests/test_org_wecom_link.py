"""企业人员的企微 userid 连到已有会话账号，花名册才能按 User.id 命中。

不改花名册读取规则：没有 primary_user_id 时，即使记下了 userid 也不编造姓名。
也不改 ChannelIdentity 指向；绑定码仍负责把渠道身份改挂到网页账号。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.channels.service_identity import resolve_or_provision_user
from app.contacts.identity import ensure_web_contact, ensure_wecom_contact
from app.db.models import (
    ChannelIdentity,
    ContactMergeSuggestion,
    EnterprisePerson,
    PersonContact,
    Tenant,
    User,
)
from app.org.api import get_people, patch_person, post_person
from app.org.schema import EnterprisePersonWriteRequest
from app.org.speaker_roster import lookup_speaker_roster
from app.session.session_schema import ChatTurnRequest
from app.session.turn_speaker import attach_speaker_roster, resolve_wecom_bot_speaker


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session) -> tuple[User, User]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.add(Tenant(id="tenant_other", name="Other"))
    owner = User(
        id="user_owner",
        tenant_id="tenant_demo",
        username="owner",
        display_name="Owner",
        password_hash="x",
    )
    admin = User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="admin",
        display_name="Admin",
        role="admin",
        password_hash="x",
    )
    db.add(owner)
    db.add(admin)
    db.commit()
    return owner, admin


def _person(name: str = "陈龙", **overrides) -> EnterprisePersonWriteRequest:
    values = {
        "tenant_id": "tenant_demo",
        "name": name,
        "employee_no": None,
        "phone": None,
        "email": None,
        "status": "active",
        "primary_user_id": None,
        "wecom_userid": None,
    }
    values.update(overrides)
    return EnterprisePersonWriteRequest(**values)


def _shadow(
    db: Session,
    *,
    user_id: str = "user_shadow",
    external_id: str = "18016077898",
    scope: str = "ww_demo",
    display_name: str = "企业微信用户 77898",
    tenant_id: str = "tenant_demo",
    channel: str = "wecom",
) -> User:
    user = User(
        id=user_id,
        tenant_id=tenant_id,
        username=f"{channel}_{external_id}_{scope}",
        display_name=display_name,
        source=channel,
        password_hash="x",
    )
    db.add(user)
    db.add(
        ChannelIdentity(
            id=f"chident_{user_id}_{scope}",
            tenant_id=tenant_id,
            channel=channel,
            external_account_scope=scope,
            external_user_id=external_id,
            staffdeck_user_id=user.id,
            display_name=display_name,
        )
    )
    db.commit()
    return user


def _identity(db: Session, user_id: str, external_id: str = "18016077898") -> ChannelIdentity:
    row = db.exec(
        select(ChannelIdentity).where(
            ChannelIdentity.staffdeck_user_id == user_id,
            ChannelIdentity.external_user_id == external_id,
        )
    ).one()
    return row


def _roster_name(db: Session, user_id: str) -> str | None:
    request = ChatTurnRequest(
        tenant_id="tenant_demo",
        user_id=user_id,
        message="你好",
        channel="wecom",
        channel_external_user_id="18016077898",
        speaker=resolve_wecom_bot_speaker(
            user_id=user_id,
            is_group=False,
            external_user_id="18016077898",
        ),
    )
    attached = attach_speaker_roster(db, request)
    assert attached.speaker is not None
    assert attached.speaker.status == "resolved"
    return attached.speaker.roster_name


def test_create_links_existing_wecom_shadow_and_roster_matches() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        shadow = _shadow(db)
        created = post_person(
            _person(wecom_userid="  18016077898  "),
            db=db,
            current_user=admin,
        )
        assert created.primary_user_id == shadow.id
        assert created.wecom_userid == "18016077898"
        assert created.primary_user_name == "陈龙"

        roster = lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id)
        assert roster.status == "matched"
        assert roster.name == "陈龙"
        assert _roster_name(db, shadow.id) == "陈龙"
        assert _identity(db, shadow.id).staffdeck_user_id == shadow.id
        assert db.get(User, shadow.id).display_name == "陈龙"


def test_explicit_web_primary_stays_when_wecom_speaker_exists() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        shadow = _shadow(db, display_name="企业微信用户 77898")
        created = post_person(
            _person(name="陈龙", wecom_userid="18016077898"),
            db=db,
            current_user=admin,
        )
        assert created.primary_user_id == shadow.id

        updated = patch_person(
            created.id,
            _person(name="陈龙", primary_user_id=admin.id, wecom_userid="18016077898"),
            db=db,
            current_user=admin,
        )
        assert updated.primary_user_id == admin.id
        assert updated.wecom_userid == "18016077898"
        assert updated.wecom_link_notice is None
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=admin.id).name == "陈龙"
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id).status == "unmatched"
        assert _identity(db, shadow.id).staffdeck_user_id == shadow.id

        org = db.exec(
            select(PersonContact).where(PersonContact.enterprise_person_id == created.id)
        ).one()
        assert org.kind == "org"
        assert org.web_user_id == admin.id
        assert org.wecom_userid == "18016077898"
        web = ensure_web_contact(db, db.get(User, admin.id))
        wecom = ensure_wecom_contact(
            db,
            tenant_id="tenant_demo",
            wecom_userid="18016077898",
            corp_id="ww_demo",
            display_name=None,
            user=shadow,
        )
        assert web is not None and wecom is not None
        assert web.id == org.id
        assert wecom.id == org.id
        assert web.display_name == "陈龙"


def test_adding_wecom_userid_keeps_selected_web_primary() -> None:
    engine = _engine()
    with Session(engine) as db:
        owner, admin = _seed(db)
        created = post_person(_person(name="陈龙", primary_user_id=owner.id), db=db, current_user=admin)
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=owner.id).status == "matched"
        shadow = _shadow(db, display_name="企业微信用户 77898")

        updated = patch_person(
            created.id,
            _person(name="陈龙", primary_user_id=owner.id, wecom_userid="18016077898"),
            db=db,
            current_user=admin,
        )
        assert updated.primary_user_id == owner.id
        assert updated.wecom_userid == "18016077898"
        assert updated.wecom_link_notice is None
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=owner.id).name == "陈龙"
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id).status == "unmatched"
        org = db.exec(
            select(PersonContact).where(PersonContact.enterprise_person_id == created.id)
        ).one()
        assert org.web_user_id == owner.id
        assert org.wecom_userid == "18016077898"
        assert _identity(db, shadow.id).staffdeck_user_id == shadow.id


def test_changing_web_primary_rewrites_org_contact_web_user() -> None:
    engine = _engine()
    with Session(engine) as db:
        owner, admin = _seed(db)
        created = post_person(
            _person(name="陈龙", primary_user_id=owner.id),
            db=db,
            current_user=admin,
        )
        updated = patch_person(
            created.id,
            _person(name="陈龙", primary_user_id=admin.id),
            db=db,
            current_user=admin,
        )
        assert updated.primary_user_id == admin.id
        org = db.exec(
            select(PersonContact).where(PersonContact.enterprise_person_id == created.id)
        ).one()
        assert org.web_user_id == admin.id
        web = ensure_web_contact(db, db.get(User, admin.id))
        previous = ensure_web_contact(db, db.get(User, owner.id))
        assert web is not None and previous is not None
        assert web.id == org.id
        assert previous.id != org.id
        assert previous.kind == "personal"


def test_taken_web_user_does_not_pretend_the_member_link_saved() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        shadow = _shadow(db)
        created = post_person(
            _person(name="陈龙", wecom_userid="18016077898"),
            db=db,
            current_user=admin,
        )
        assert created.primary_user_id == shadow.id
        db.add(
            PersonContact(
                id="pcontact_admin",
                tenant_id="tenant_demo",
                kind="personal",
                display_name="Administrator",
                source="web_signup",
                web_user_id=admin.id,
                channel_user_id=admin.id,
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as error:
            patch_person(
                created.id,
                _person(name="陈龙", primary_user_id=admin.id, wecom_userid="18016077898"),
                db=db,
                current_user=admin,
            )
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "ORG_WEB_USER_CONTACT_TAKEN"
        assert "Administrator" in error.value.detail["message"]
        person = db.get(EnterprisePerson, created.id)
        assert person is not None
        assert person.primary_user_id == shadow.id
        org = db.exec(
            select(PersonContact).where(PersonContact.enterprise_person_id == created.id)
        ).one()
        assert org.web_user_id is None
        suggestion = db.exec(select(ContactMergeSuggestion)).one()
        assert suggestion.status == "pending"
        assert suggestion.reason == "same_web_user"
        assert {suggestion.left_contact_id, suggestion.right_contact_id} == {
            org.id,
            "pcontact_admin",
        }
        web = ensure_web_contact(db, db.get(User, admin.id))
        assert web is not None
        assert web.id == "pcontact_admin"
        assert web.display_name == "Administrator"


def test_userid_without_channel_identity_does_not_invent_a_roster_name() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        created = post_person(_person(wecom_userid="18016077898"), db=db, current_user=admin)
        assert created.primary_user_id is None
        assert created.wecom_userid == "18016077898"
        assert lookup_speaker_roster(
            db, tenant_id="tenant_demo", user_id="user_missing"
        ).status == "unmatched"

        shadow = resolve_or_provision_user(
            db, "tenant_demo", "wecom", "18016077898", "企业微信用户 77898", "ww_demo"
        )
        db.commit()
        person = db.get(EnterprisePerson, created.id)
        assert person is not None
        assert person.primary_user_id == shadow.id
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id).name == "陈龙"
        assert db.get(User, shadow.id).display_name == "陈龙"


def test_pending_link_does_not_replace_an_existing_login_or_other_tenant() -> None:
    engine = _engine()
    with Session(engine) as db:
        owner, admin = _seed(db)
        created = post_person(
            _person(primary_user_id=owner.id, wecom_userid="18016077898"),
            db=db,
            current_user=admin,
        )
        _shadow(
            db,
            user_id="user_other",
            tenant_id="tenant_other",
            scope="ww_other",
        )
        shadow = resolve_or_provision_user(
            db, "tenant_demo", "wecom", "18016077898", None, "ww_demo"
        )
        db.commit()
        person = db.get(EnterprisePerson, created.id)
        assert person is not None
        assert person.primary_user_id == owner.id
        assert shadow.tenant_id == "tenant_demo"
        shadow_roster = lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id)
        assert shadow_roster.status == "unmatched"
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=owner.id).name == "陈龙"

        cleared = patch_person(created.id, _person(), db=db, current_user=admin)
        assert cleared.primary_user_id == owner.id
        assert cleared.wecom_userid is None
        assert cleared.wecom_link_notice is None
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=owner.id).name == "陈龙"
        shadow_roster = lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id)
        assert shadow_roster.status == "unmatched"


def test_pending_link_skips_when_scopes_point_at_different_users() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        created = post_person(_person(wecom_userid="18016077898"), db=db, current_user=admin)
        _shadow(db, user_id="user_a", scope="ww_a")
        shadow_b = resolve_or_provision_user(
            db, "tenant_demo", "wecom", "18016077898", None, "ww_b"
        )
        db.commit()
        person = db.get(EnterprisePerson, created.id)
        assert person is not None
        assert person.primary_user_id is None
        assert shadow_b.id != "user_a"
        assert lookup_speaker_roster(
            db, tenant_id="tenant_demo", user_id=shadow_b.id
        ).status == "unmatched"
        assert lookup_speaker_roster(
            db, tenant_id="tenant_demo", user_id="user_a"
        ).status == "unmatched"


def test_ambiguous_scopes_and_duplicate_userid_are_rejected() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        _shadow(db, user_id="user_a", scope="ww_a")
        _shadow(db, user_id="user_b", scope="ww_b", display_name="企业微信用户 b")
        with pytest.raises(HTTPException) as error:
            post_person(_person(wecom_userid="18016077898"), db=db, current_user=admin)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "ORG_WECOM_USER_AMBIGUOUS"
        assert db.exec(select(EnterprisePerson)).first() is None

        first = post_person(
            _person(name="张三", wecom_userid="zhangsan"),
            db=db,
            current_user=admin,
        )
        with pytest.raises(HTTPException) as taken:
            post_person(_person(name="李四", wecom_userid="zhangsan"), db=db, current_user=admin)
        assert taken.value.status_code == 409
        assert taken.value.detail["code"] == "ORG_WECOM_USERID_TAKEN"
        assert db.get(EnterprisePerson, first.id) is not None


def test_same_user_in_two_scopes_links_once_and_group_userid_is_rejected() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        shadow = _shadow(db, scope="ww_a")
        db.add(
            ChannelIdentity(
                id="chident_same_user_b",
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="ww_b",
                external_user_id="18016077898",
                staffdeck_user_id=shadow.id,
                display_name="企业微信用户 77898",
            )
        )
        db.add(
            ChannelIdentity(
                id="chident_feishu",
                tenant_id="tenant_demo",
                channel="feishu",
                external_account_scope="",
                external_user_id="18016077898",
                staffdeck_user_id="user_admin",
                display_name="飞书用户",
            )
        )
        db.commit()
        created = post_person(_person(wecom_userid="18016077898"), db=db, current_user=admin)
        assert created.primary_user_id == shadow.id

        with pytest.raises(HTTPException) as error:
            post_person(_person(name="群", wecom_userid="group:room"), db=db, current_user=admin)
        assert error.value.status_code == 400
        assert error.value.detail["code"] == "ORG_WECOM_USERID_INVALID"


def test_clearing_wecom_userid_keeps_shadow_primary() -> None:
    engine = _engine()
    with Session(engine) as db:
        _owner, admin = _seed(db)
        shadow = _shadow(db)
        created = post_person(_person(wecom_userid="18016077898"), db=db, current_user=admin)
        assert created.primary_user_id == shadow.id

        cleared = patch_person(
            created.id,
            _person(primary_user_id=None, wecom_userid=None),
            db=db,
            current_user=admin,
        )
        assert cleared.wecom_userid is None
        assert cleared.primary_user_id == shadow.id
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id).name == "陈龙"

        kept = patch_person(
            created.id,
            _person(primary_user_id=shadow.id, wecom_userid=None),
            db=db,
            current_user=admin,
        )
        assert kept.primary_user_id == shadow.id
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=shadow.id).name == "陈龙"


def test_bound_web_identity_stays_web_primary() -> None:
    engine = _engine()
    with Session(engine) as db:
        owner, admin = _seed(db)
        db.add(
            ChannelIdentity(
                id="chident_bound_web",
                tenant_id="tenant_demo",
                channel="wecom",
                external_account_scope="ww_demo",
                external_user_id="18016077898",
                staffdeck_user_id=owner.id,
                display_name="Owner",
            )
        )
        db.commit()
        created = post_person(
            _person(primary_user_id=owner.id, wecom_userid="18016077898"),
            db=db,
            current_user=admin,
        )
        assert created.primary_user_id == owner.id
        assert created.wecom_userid == "18016077898"
        assert created.wecom_link_notice is None
        identity = db.get(ChannelIdentity, "chident_bound_web")
        assert identity is not None
        assert identity.staffdeck_user_id == owner.id
        assert lookup_speaker_roster(db, tenant_id="tenant_demo", user_id=owner.id).name == "陈龙"

        updated = patch_person(
            created.id,
            _person(primary_user_id=owner.id, wecom_userid="18016077898"),
            db=db,
            current_user=admin,
        )
        assert updated.primary_user_id == owner.id
        assert updated.wecom_link_notice is None
        identity = db.get(ChannelIdentity, "chident_bound_web")
        assert identity is not None
        assert identity.staffdeck_user_id == owner.id


def test_member_directory_hides_wecom_userid() -> None:
    engine = _engine()
    with Session(engine) as db:
        owner, admin = _seed(db)
        post_person(_person(wecom_userid="18016077898"), db=db, current_user=admin)
        visible = get_people(tenant_id="tenant_demo", db=db, current_user=owner)
        assert visible.people[0].name == "陈龙"
        dumped = visible.people[0].model_dump()
        assert "wecom_userid" not in dumped
        assert "wecom_link_notice" not in dumped
        admin_rows = get_people(tenant_id="tenant_demo", db=db, current_user=admin)
        assert admin_rows.people[0].wecom_userid == "18016077898"

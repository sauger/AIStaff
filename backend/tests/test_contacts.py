from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.channels.adapters.base import ChannelInbound
from app.channels.service_intake import process_inbound
from app.contacts.api import get_contacts, patch_contact, post_contact, remove_contact
from app.contacts.linking import link_inbound_contact
from app.contacts.schema import ContactWriteRequest
from app.db.models import AgentProfile, ChannelBinding, Contact, Tenant, User


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session) -> tuple[User, User, User, AgentProfile, AgentProfile]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    owner = User(
        id="user_owner",
        tenant_id="tenant_demo",
        username="owner",
        display_name="Owner",
        password_hash="x",
    )
    other = User(
        id="user_other",
        tenant_id="tenant_demo",
        username="other",
        display_name="Other",
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
    agent_a = AgentProfile(
        id="agent_a",
        tenant_id="tenant_demo",
        name="员工A",
        is_overall=False,
        metadata_json={"owner_user_id": owner.id},
    )
    agent_b = AgentProfile(
        id="agent_b",
        tenant_id="tenant_demo",
        name="员工B",
        is_overall=False,
        metadata_json={"owner_user_id": other.id},
    )
    db.add(owner)
    db.add(other)
    db.add(admin)
    db.add(agent_a)
    db.add(agent_b)
    db.commit()
    return owner, other, admin, agent_a, agent_b


@pytest.fixture
def contact_db():
    engine = _engine()
    with Session(engine) as db:
        yield db, _seed(db), engine


def _write(**overrides) -> ContactWriteRequest:
    payload = {
        "tenant_id": "tenant_demo",
        "name": "张三",
        "phone": "13800000000",
        "email": "zhangsan@example.com",
        "title": "销售",
        "city": "上海",
        "notes": "重点客户",
        "wecom_userid": "zhangsan",
        "feishu_openid": "ou_zhangsan",
        "dingtalk_userid": "dt_zhangsan",
    }
    payload.update(overrides)
    return ContactWriteRequest(**payload)


def _contact(db: Session, contact_id: str) -> Contact:
    row = db.get(Contact, contact_id)
    assert row is not None
    db.refresh(row)
    return row


def test_contact_table_keeps_channel_ids_and_user_link_nullable(contact_db) -> None:
    db, _seeded, _engine = contact_db
    columns = {column["name"]: column for column in inspect(db.get_bind()).get_columns("contacts")}
    for name in (
        "name",
        "phone",
        "email",
        "title",
        "city",
        "notes",
        "wecom_userid",
        "feishu_openid",
        "dingtalk_userid",
        "primary_user_id",
        "tenant_id",
        "agent_id",
    ):
        assert name in columns
    for name in ("phone", "email", "wecom_userid", "feishu_openid", "dingtalk_userid", "primary_user_id"):
        assert columns[name]["nullable"] is True
    assert columns["id"]["primary_key"]

    first = Contact(tenant_id="tenant_demo", agent_id="agent_a", name="甲")
    second = Contact(tenant_id="tenant_demo", agent_id="agent_a", name="乙")
    db.add(first)
    db.add(second)
    db.commit()
    assert first.primary_user_id is None
    assert first.wecom_userid is None
    assert second.id != first.id


def test_owner_creates_lists_and_edits_contact_without_requiring_user(contact_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _engine = contact_db
    created = post_contact(_write(wecom_userid="  zhangsan  ", email=""), agent_id=agent_a.id, db=db, current_user=owner)
    assert created.primary_user_id is None
    assert created.wecom_userid == "zhangsan"
    assert created.email is None
    assert created.name == "张三"

    listing = get_contacts(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.can_write is True
    assert [item.name for item in listing.contacts] == ["张三"]

    updated = patch_contact(
        created.id,
        _write(name="张三丰", phone="", notes="改后备注", wecom_userid="zhangsan"),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert updated.name == "张三丰"
    assert updated.phone is None
    assert updated.notes == "改后备注"
    assert updated.primary_user_id is None
    stored = _contact(db, created.id)
    assert stored.primary_user_id is None


def test_duplicate_secondary_key_on_same_employee_is_rejected(contact_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _engine = contact_db
    post_contact(_write(name="张三"), agent_id=agent_a.id, db=db, current_user=owner)
    with pytest.raises(HTTPException) as error:
        post_contact(_write(name="同号"), agent_id=agent_a.id, db=db, current_user=owner)
    assert error.value.status_code == 409


def test_same_channel_id_can_exist_on_another_employee(contact_db) -> None:
    db, (owner, other, _admin, agent_a, agent_b), _engine = contact_db
    post_contact(_write(), agent_id=agent_a.id, db=db, current_user=owner)
    created = post_contact(_write(name="另一本通讯录"), agent_id=agent_b.id, db=db, current_user=other)
    assert created.wecom_userid == "zhangsan"


def test_non_owner_cannot_read_or_write_contacts(contact_db) -> None:
    db, (owner, other, _admin, agent_a, _agent_b), _engine = contact_db
    created = post_contact(_write(), agent_id=agent_a.id, db=db, current_user=owner)
    with pytest.raises(HTTPException) as read_error:
        get_contacts(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=other)
    assert read_error.value.status_code == 403
    with pytest.raises(HTTPException) as write_error:
        patch_contact(created.id, _write(name="越权"), agent_id=agent_a.id, db=db, current_user=other)
    assert write_error.value.status_code == 403


def test_admin_can_read_but_cannot_write(contact_db) -> None:
    db, (owner, _other, admin, agent_a, _agent_b), _engine = contact_db
    created = post_contact(_write(), agent_id=agent_a.id, db=db, current_user=owner)
    listing = get_contacts(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=admin)
    assert listing.can_write is False
    assert listing.contacts[0].id == created.id
    with pytest.raises(HTTPException) as error:
        remove_contact(created.id, tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=admin)
    assert error.value.status_code == 403


def test_owner_delete_removes_only_that_contact(contact_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _engine = contact_db
    created = post_contact(_write(), agent_id=agent_a.id, db=db, current_user=owner)
    assert remove_contact(
        created.id, tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    ) == {"status": "deleted"}
    assert db.get(Contact, created.id) is None


def test_blank_name_is_rejected(contact_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _engine = contact_db
    with pytest.raises(HTTPException) as error:
        post_contact(_write(name="   "), agent_id=agent_a.id, db=db, current_user=owner)
    assert error.value.status_code == 400


def test_inbound_link_skips_group_sender(contact_db) -> None:
    db, _seeded, _engine = contact_db
    row = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="群",
        wecom_userid="group:room_1",
    )
    db.add(row)
    db.commit()
    linked = link_inbound_contact(
        db,
        tenant_id="tenant_demo",
        channel="wecom",
        external_id="group:room_1",
        user_id="user_owner",
    )
    db.commit()
    assert linked == []
    assert _contact(db, row.id).primary_user_id is None


def test_inbound_link_by_primary_user_id_fills_empty_secondary_key(contact_db) -> None:
    db, _seeded, _engine = contact_db
    row = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="张三",
        primary_user_id="user_owner",
    )
    db.add(row)
    db.commit()
    link_inbound_contact(
        db,
        tenant_id="tenant_demo",
        channel="wecom",
        external_id="zhangsan",
        user_id="user_owner",
    )
    db.commit()
    stored = _contact(db, row.id)
    assert stored.primary_user_id == "user_owner"
    assert stored.wecom_userid == "zhangsan"
    assert db.exec(select(Contact)).all().__len__() == 1


def test_inbound_link_by_primary_user_id_does_not_overwrite_secondary_key(contact_db) -> None:
    db, _seeded, _engine = contact_db
    row = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="张三",
        primary_user_id="user_owner",
        wecom_userid="already-set",
    )
    db.add(row)
    db.commit()
    link_inbound_contact(
        db,
        tenant_id="tenant_demo",
        channel="wecom",
        external_id="zhangsan",
        user_id="user_owner",
    )
    db.commit()
    assert _contact(db, row.id).wecom_userid == "already-set"


def test_inbound_link_by_secondary_id_sets_empty_primary_user(contact_db) -> None:
    db, _seeded, _engine = contact_db
    row = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="张三",
        feishu_openid="ou_zhangsan",
    )
    db.add(row)
    db.commit()
    link_inbound_contact(
        db,
        tenant_id="tenant_demo",
        channel="feishu",
        external_id="ou_zhangsan",
        user_id="user_owner",
    )
    db.commit()
    stored = _contact(db, row.id)
    assert stored.primary_user_id == "user_owner"
    assert stored.feishu_openid == "ou_zhangsan"
    assert db.exec(select(Contact)).all().__len__() == 1


def test_inbound_link_skips_when_no_contact_exists(contact_db) -> None:
    db, _seeded, _engine = contact_db
    linked = link_inbound_contact(
        db,
        tenant_id="tenant_demo",
        channel="dingtalk",
        external_id="dt_missing",
        user_id="user_owner",
    )
    db.commit()
    assert linked == []
    assert db.exec(select(Contact)).all() == []


def test_inbound_skips_secondary_fill_when_another_contact_holds_it(contact_db) -> None:
    db, _seeded, _engine = contact_db
    holder = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="已占用",
        wecom_userid="zhangsan",
    )
    linked = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="已关联",
        primary_user_id="user_owner",
    )
    db.add(holder)
    db.add(linked)
    db.commit()
    link_inbound_contact(
        db,
        tenant_id="tenant_demo",
        channel="wecom",
        external_id="zhangsan",
        user_id="user_owner",
    )
    db.commit()
    assert _contact(db, linked.id).wecom_userid is None
    assert _contact(db, holder.id).primary_user_id is None
    assert db.exec(select(Contact)).all()


def test_deleting_user_clears_contact_account_link(contact_db) -> None:
    from app.api.auth import delete_user

    db, (_owner, other, admin, _agent_a, _agent_b), _engine = contact_db
    row = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="张三",
        primary_user_id=other.id,
        wecom_userid="zhangsan",
    )
    db.add(row)
    db.commit()
    assert delete_user(other.id, tenant_id="tenant_demo", current_user=admin, db=db) == {"ok": True}
    stored = _contact(db, row.id)
    assert stored.primary_user_id is None
    assert stored.wecom_userid == "zhangsan"
    assert db.get(User, other.id) is None


def test_inbound_secondary_match_does_not_replace_existing_primary_user(contact_db) -> None:
    db, _seeded, _engine = contact_db
    row = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="张三",
        dingtalk_userid="dt_zhangsan",
        primary_user_id="user_other",
    )
    db.add(row)
    db.commit()
    link_inbound_contact(
        db,
        tenant_id="tenant_demo",
        channel="dingtalk",
        external_id="dt_zhangsan",
        user_id="user_owner",
    )
    db.commit()
    assert _contact(db, row.id).primary_user_id == "user_other"


def test_process_inbound_links_contact_for_direct_sender_only(contact_db, monkeypatch) -> None:
    import app.channels.adapters.wecom  # noqa: F401  register the wecom adapter

    db, _seeded, engine = contact_db

    class _Loop:
        def __init__(self, _db, *, event_sink=None):
            self.db = _db

        def handle_turn(self, request):
            return None

    monkeypatch.setattr("app.core.agent_loop.AgentLoop", _Loop)
    direct = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="直聊",
        wecom_userid="zhangsan",
    )
    group_row = Contact(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        name="群",
        wecom_userid="group:room_1",
    )
    db.add(direct)
    db.add(group_row)
    binding = ChannelBinding(
        tenant_id="tenant_demo",
        agent_id="agent_a",
        channel="wecom",
        status="active",
        config_json={},
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    db.expunge(binding)

    direct_inbound = ChannelInbound(
        channel="wecom",
        event_id="evt_direct",
        from_user_id="zhangsan",
        to_user_id="bot",
        session_id="zhangsan",
        group_id="",
        context_token="zhangsan",
        text="你好",
        is_group=False,
        raw={},
    )
    group_inbound = ChannelInbound(
        channel="wecom",
        event_id="evt_group",
        from_user_id="someone",
        to_user_id="bot",
        session_id="room_1",
        group_id="room_1",
        context_token="room_1",
        text="群消息",
        is_group=True,
        raw={},
    )
    assert process_inbound(binding, direct_inbound, db_engine=engine) is True
    assert process_inbound(binding, group_inbound, db_engine=engine) is True
    assert _contact(db, direct.id).primary_user_id
    assert _contact(db, group_row.id).primary_user_id is None

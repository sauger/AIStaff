"""P0 租户级联系人：命中、新建 personal、组织内优先，且新入站不写 channel_identities。"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select
from sqlmodel import create_engine as sql_create_engine

import app.channels.service_intake as intake_module
import app.core.agent_loop as agent_loop_module
from app.channels.adapters.wecom import normalize_wecom_frame
from app.channels.crypto import encrypt_channel_secret
from app.channels.service_identity import resolve_or_provision_user
from app.channels.service_intake import process_inbound
from app.contacts.identity import (
    ensure_email_contact,
    ensure_web_contact,
    ensure_wecom_contact,
)
from app.db import database
from app.db.models import (
    ChannelBinding,
    ChannelIdentity,
    ChatSession,
    Contact,
    ContactExtension,
    EnterprisePerson,
    PersonContact,
    Tenant,
    User,
)
from app.org.speaker_roster import lookup_speaker_roster
from app.session.session_schema import ChatTurnRequest, TurnSpeaker
from app.session.turn_speaker import attach_speaker_roster


def _engine():
    engine = sql_create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _tenant(db: Session) -> None:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.commit()


def _user(db: Session, user_id: str, *, source: str = "web", name: str = "张三") -> User:
    user = User(
        id=user_id,
        tenant_id="tenant_demo",
        username=user_id,
        display_name=name,
        source=source,
        password_hash="x",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_person_contact_partial_uniques_allow_empty_channel_fields() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        db.add(
            PersonContact(
                tenant_id="tenant_demo",
                kind="personal",
                display_name="甲",
                source="manual",
            )
        )
        db.add(
            PersonContact(
                tenant_id="tenant_demo",
                kind="personal",
                display_name="乙",
                source="manual",
            )
        )
        db.commit()
        db.add(
            PersonContact(
                tenant_id="tenant_demo",
                kind="personal",
                display_name="丙",
                email="a@example.com",
            )
        )
        db.commit()
        db.add(
            PersonContact(
                tenant_id="tenant_demo",
                kind="org",
                display_name="丁",
                email="a@example.com",
            )
        )
        try:
            db.commit()
            raised = False
        except IntegrityError:
            db.rollback()
            raised = True
        assert raised


def test_wecom_miss_creates_personal_and_does_not_write_channel_identity() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        first = resolve_or_provision_user(
            db, "tenant_demo", "wecom", "ZhangSan", "企业微信用户 ZhangSan", "ww_demo"
        )
        db.commit()
        second = resolve_or_provision_user(
            db, "tenant_demo", "wecom", "ZhangSan", None, "ww_demo"
        )
        db.commit()
        assert first.id == second.id
        assert db.exec(select(ChannelIdentity)).all() == []
        contacts = list(db.exec(select(PersonContact)).all())
        assert len(contacts) == 1
        contact = contacts[0]
        assert contact.kind == "personal"
        assert contact.wecom_userid == "ZhangSan"
        assert contact.wecom_corp_id == "ww_demo"
        assert contact.source == "inbound_wecom"
        assert contact.channel_user_id == first.id
        assert contact.web_user_id is None
        assert contact.merged_into_id is None


def test_org_wecom_field_blocks_personal_clone() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        person = EnterprisePerson(
            tenant_id="tenant_demo",
            name="陈龙",
            wecom_userid="chenlong",
            status="active",
        )
        db.add(person)
        db.commit()
        db.refresh(person)
        from app.contacts.identity import sync_org_contact_from_person

        org = sync_org_contact_from_person(db, person, corp_id="ww_demo")
        db.commit()
        assert org is not None
        assert org.kind == "org"
        user = resolve_or_provision_user(
            db, "tenant_demo", "wecom", "chenlong", "企业微信用户 chenlong", "ww_demo"
        )
        db.commit()
        contacts = list(db.exec(select(PersonContact)).all())
        assert len(contacts) == 1
        assert contacts[0].id == org.id
        assert contacts[0].kind == "org"
        assert contacts[0].display_name == "陈龙"
        assert contacts[0].enterprise_person_id == person.id
        assert db.exec(select(ChannelIdentity)).all() == []
        assert user.id


def test_email_hit_uses_org_and_normalizes_address() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        person = EnterprisePerson(
            tenant_id="tenant_demo",
            name="李四",
            email="LiSi@Example.com",
            status="active",
        )
        db.add(person)
        db.commit()
        db.refresh(person)
        from app.contacts.identity import sync_org_contact_from_person

        org = sync_org_contact_from_person(db, person)
        db.commit()
        found = ensure_email_contact(
            db,
            tenant_id="tenant_demo",
            email="  lisi@example.com ",
        )
        db.commit()
        assert found is not None
        assert org is not None
        assert found.id == org.id
        assert found.kind == "org"
        assert found.email == "lisi@example.com"
        assert len(list(db.exec(select(PersonContact)).all())) == 1


def test_unknown_email_creates_personal() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        found = ensure_email_contact(
            db,
            tenant_id="tenant_demo",
            email="guest@example.com",
            display_name="客人",
        )
        db.commit()
        assert found is not None
        assert found.kind == "personal"
        assert found.email == "guest@example.com"
        assert found.display_name == "客人"
        assert found.source == "inbound_email"


def test_web_user_reuses_org_contact() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        user = _user(db, "user_web", name="王五")
        person = EnterprisePerson(
            tenant_id="tenant_demo",
            name="王五",
            primary_user_id=user.id,
            status="active",
        )
        db.add(person)
        db.commit()
        db.refresh(person)
        from app.contacts.identity import sync_org_contact_from_person

        org = sync_org_contact_from_person(db, person, user=user)
        db.commit()
        found = ensure_web_contact(db, user)
        db.commit()
        assert org is not None and found is not None
        assert found.id == org.id
        assert found.kind == "org"
        assert found.web_user_id == user.id
        assert list(db.exec(select(PersonContact)).all()) == [found]


def test_speaker_prefers_contact_display_name_without_primary_user() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        user = _user(db, "user_shadow", source="wecom", name="企业微信用户 zhang")
        contact = ensure_wecom_contact(
            db,
            tenant_id="tenant_demo",
            wecom_userid="zhang",
            corp_id="ww_demo",
            display_name="张三",
            user=user,
        )
        db.commit()
        assert contact is not None
        assert lookup_speaker_roster(
            db, tenant_id="tenant_demo", user_id=user.id
        ).status == "unmatched"
        request = attach_speaker_roster(
            db,
            ChatTurnRequest(
                tenant_id="tenant_demo",
                user_id=user.id,
                message="你好",
                channel="wecom",
                speaker=TurnSpeaker(
                    status="resolved",
                    user_id=user.id,
                    channel="wecom",
                    source="channel_identity",
                    contact_id=contact.id,
                ),
            ),
        )
        assert request.speaker is not None
        assert request.speaker.roster_status == "matched"
        assert request.speaker.roster_name == "张三"
        assert request.speaker.contact_id == contact.id


def test_extension_is_unique_per_agent_and_contact() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        contact = PersonContact(
            tenant_id="tenant_demo",
            kind="personal",
            display_name="甲",
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        db.add(
            ContactExtension(
                tenant_id="tenant_demo",
                agent_id="agent_a",
                contact_id=contact.id,
                notes="只给员工 A",
            )
        )
        db.commit()
        db.add(
            ContactExtension(
                tenant_id="tenant_demo",
                agent_id="agent_a",
                contact_id=contact.id,
                notes="重复",
            )
        )
        try:
            db.commit()
            raised = False
        except IntegrityError:
            db.rollback()
            raised = True
        assert raised


def test_merged_contact_follows_keeper() -> None:
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        keeper = PersonContact(
            id="pcontact_keep",
            tenant_id="tenant_demo",
            kind="org",
            display_name="保留",
            status="active",
        )
        dropped = PersonContact(
            id="pcontact_drop",
            tenant_id="tenant_demo",
            kind="personal",
            display_name="被并",
            status="archived",
            wecom_userid="same",
            wecom_corp_id="ww",
            merged_into_id="pcontact_keep",
        )
        db.add(keeper)
        db.commit()
        db.add(dropped)
        db.commit()
        # 被并行走 merged 指针；渠道字段留在保留方，查找应落到保留方。
        found = ensure_wecom_contact(
            db,
            tenant_id="tenant_demo",
            wecom_userid="same",
            corp_id="ww",
            display_name=None,
            user=None,
        )
        assert found is not None
        assert found.id == keeper.id


class _RecordingAgentLoop:
    calls: list = []  # noqa: RUF012

    def __init__(self, db, *, event_sink=None):
        self.db = db

    def handle_turn(self, request):
        type(self).calls.append(request)
        self.db.commit()


def test_wecom_inbound_stamps_session_contact_without_channel_identity(monkeypatch) -> None:
    _RecordingAgentLoop.calls = []
    monkeypatch.setattr(agent_loop_module, "AgentLoop", _RecordingAgentLoop)
    monkeypatch.setattr(intake_module, "_send_wechat_typing", lambda *args, **kwargs: None)
    engine = _engine()
    with Session(engine) as db:
        _tenant(db)
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_1",
            channel="wecom",
            status="active",
            credentials_enc=encrypt_channel_secret("bot_secret"),
            config_json={"bot_id": "aib_bot1", "corp_id": "ww_demo"},
        )
        db.add(binding)
        db.commit()
        db.refresh(binding)
        db.expunge(binding)
    frame = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req_p0"},
        "body": {
            "msgid": "msg_p0",
            "aibotid": "aib_bot1",
            "chattype": "single",
            "from": {"userid": "zhangsan"},
            "msgtype": "text",
            "text": {"content": "你好"},
        },
    }
    assert process_inbound(binding, normalize_wecom_frame(frame), db_engine=engine) is True
    request = _RecordingAgentLoop.calls[0]
    assert request.speaker is not None
    assert request.speaker.contact_id
    with Session(engine) as db:
        assert db.exec(select(ChannelIdentity)).all() == []
        session = db.get(ChatSession, request.session_id)
        assert session is not None
        assert session.contact_id == request.speaker.contact_id
        contact = db.get(PersonContact, session.contact_id)
        assert contact is not None
        assert contact.kind == "personal"
        assert contact.wecom_userid == "zhangsan"
        assert contact.wecom_corp_id == "ww_demo"


def test_address_book_table_moves_aside_and_ci_backfills_contact(tmp_path, monkeypatch) -> None:
    path = tmp_path / "contacts-migration.db"
    raw_engine = create_engine(f"sqlite:///{path}")
    with raw_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE contacts ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, agent_id VARCHAR, name VARCHAR, "
                "phone VARCHAR, email VARCHAR, title VARCHAR, city VARCHAR, notes VARCHAR, "
                "wecom_userid VARCHAR, feishu_openid VARCHAR, dingtalk_userid VARCHAR, "
                "primary_user_id VARCHAR, created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, agent_id, name, created_at, updated_at) "
                "VALUES ('contact_old', 'tenant_demo', 'agent_1', '通讯录甲', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE users (id VARCHAR PRIMARY KEY, tenant_id VARCHAR, source VARCHAR, "
                "display_name VARCHAR)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, source, display_name) "
                "VALUES ('user_web', 'tenant_demo', 'web', '张三')"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE enterprise_people (id VARCHAR PRIMARY KEY, tenant_id VARCHAR, "
                "name VARCHAR, wecom_userid VARCHAR, status VARCHAR)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE channel_identities ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, channel VARCHAR, "
                "external_account_scope VARCHAR, external_user_id VARCHAR, "
                "staffdeck_user_id VARCHAR, display_name VARCHAR, "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO channel_identities (id, tenant_id, channel, external_account_scope, "
                "external_user_id, staffdeck_user_id, display_name, created_at, updated_at) "
                "VALUES ('chident_1', 'tenant_demo', 'wecom', 'ww_demo', 'zhangsan', 'user_web', "
                "'张三', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(text("CREATE TABLE sessions (id VARCHAR PRIMARY KEY, tenant_id VARCHAR)"))
        conn.execute(text("CREATE TABLE memories (id VARCHAR PRIMARY KEY, tenant_id VARCHAR)"))
    # 模拟 init_db：create_all 已按新模型建出空的 agent_contacts，旧 contacts 仍在。
    Contact.__table__.create(raw_engine)

    monkeypatch.setattr(database, "engine", raw_engine)
    monkeypatch.setattr(database, "database_url", f"sqlite:///{path}")
    database._migrate_identity_contacts()

    with raw_engine.connect() as conn:
        address_book = conn.execute(
            text("SELECT id, name, agent_id FROM agent_contacts")
        ).mappings().one()
        assert address_book["id"] == "contact_old"
        assert address_book["name"] == "通讯录甲"
        contact_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(contacts)"))}
        assert "kind" in contact_columns
        assert "agent_id" not in contact_columns
        contact = conn.execute(
            text(
                "SELECT kind, wecom_userid, wecom_corp_id, web_user_id, source "
                "FROM contacts"
            )
        ).mappings().one()
        assert contact["kind"] == "personal"
        assert contact["wecom_userid"] == "zhangsan"
        assert contact["wecom_corp_id"] == "ww_demo"
        assert contact["web_user_id"] == "user_web"
        assert contact["source"] == "channel_identity_backfill"
        assert "contact_id" in {
            row[1] for row in conn.execute(text("PRAGMA table_info(sessions)"))
        }
        assert inspect(conn).has_table("contact_extensions")
        assert inspect(conn).has_table("contact_merge_suggestions")
        # 回填不改 CI。
        identity = conn.execute(
            text("SELECT staffdeck_user_id FROM channel_identities")
        ).one()
        assert identity[0] == "user_web"

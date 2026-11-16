from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelDelivery,
    ChatSession,
    EnterprisePerson,
    PersonContact,
    PersonNotificationBinding,
    PersonNotificationCode,
    Tenant,
    User,
    utc_now,
)
from app.org import notifications as n
from app.org.errors import OrgError
from app.org.notification_api import BindingRequest, bind_notification, read_notification


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="t", name="Test"))
        db.add(User(id="u", tenant_id="t", username="member", password_hash="x"))
        db.add(User(id="admin", tenant_id="t", username="admin", password_hash="x", role="admin"))
        db.add(AgentProfile(id="agent", tenant_id="t", name="ITO"))
        db.add(EnterprisePerson(id="p", tenant_id="t", name="同名", primary_user_id="u"))
        db.add(EnterprisePerson(id="p2", tenant_id="t", name="同名"))
        db.add(
            ChannelBinding(
                id="bot",
                tenant_id="t",
                agent_id="agent",
                channel="wecom",
                status="active",
                connected=True,
                external_account_key="key",
                identity_scope_key="corp",
                config_json={"corp_id": "corp"},
            )
        )
        db.add(
            PersonContact(
                id="c", tenant_id="t", display_name="同名", wecom_corp_id="corp", wecom_userid="wx1"
            )
        )
        db.add(
            ChatSession(
                id="session",
                tenant_id="t",
                contact_id="c",
                agent_id="agent",
                channel="wecom",
                channel_binding_id="bot",
                channel_account_key="key",
                external_conv_id="wecom_corp_p2p_wx1",
                channel_target_json={"to_user_id": "wx1", "is_group": False},
            )
        )
        db.commit()
        yield db
    engine.dispose()


def bind(db, person="p"):
    return n.bind(db, "t", person, "c", "bot", "admin")


def stage(db, key="ito:run1:finance-confirmed:v1:p"):
    return n.stage_notification(
        db,
        tenant_id="t",
        recipient_person_id="p",
        source_agent_id="agent",
        event_key=key,
        text="财务确认测试",
        is_test=True,
    )


def test_binding_preserves_accounts_and_needs_delivery_verification(db):
    bind(db)
    assert db.get(EnterprisePerson, "p").primary_user_id == "u"
    assert db.get(EnterprisePerson, "p").wecom_userid is None
    assert db.get(PersonContact, "c").web_user_id is None
    view = n.status_view(db, "t", "p")
    assert view["binding_status"] == "bound"
    assert view["reachability"] == "unverified"
    row = stage(db)
    assert row.target_json == {"to_user_id": "wx1", "is_group": False}
    row.status = "delivered"
    row.delivered_at = utc_now()
    db.add(row)
    db.flush()
    assert n.status_view(db, "t", "p")["reachability"] == "test_succeeded"


def test_no_login_and_same_name_does_not_autobind(db):
    bind(db, "p2")
    assert n.active_link(db, "t", "p") is None
    assert db.get(EnterprisePerson, "p2").primary_user_id is None
    with pytest.raises(OrgError, match="其他人员"):
        bind(db)


@pytest.mark.parametrize(
    "entity,field,value",
    [
        (PersonContact, "tenant_id", "other"),
        (PersonContact, "wecom_corp_id", "other"),
        (PersonContact, "enterprise_person_id", "p2"),
        (PersonContact, "status", "archived"),
        (ChannelBinding, "tenant_id", "other"),
        (ChannelBinding, "identity_scope_key", "other"),
        (EnterprisePerson, "status", "left"),
        (EnterprisePerson, "wecom_userid", "different"),
    ],
)
def test_reject_invalid_identity(db, entity, field, value):
    ids = {PersonContact: "c", ChannelBinding: "bot", EnterprisePerson: "p"}
    obj = db.get(entity, ids[entity])
    setattr(obj, field, value)
    db.add(obj)
    db.commit()
    with pytest.raises(OrgError):
        bind(db)


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel_account_key", "other"),
        ("channel_binding_id", "other"),
        ("channel_target_json", {"to_user_id": "wx1", "is_group": True}),
        ("channel_target_json", {"to_user_id": "wrong"}),
        ("external_conv_id", "wecom_corp_group_wx1"),
        ("status", "archived"),
    ],
)
def test_never_falls_back_to_other_or_group_session(db, field, value):
    row = db.get(ChatSession, "session")
    setattr(row, field, value)
    db.add(row)
    db.commit()
    with pytest.raises(OrgError):
        bind(db)


def test_event_dedup_and_rebind_cancel_pending(db):
    first = bind(db)
    row = stage(db)
    assert stage(db).id == row.id
    db.commit()
    second = bind(db)
    assert first.id != second.id
    assert db.get(ChannelDelivery, row.id).status == "failed"
    assert db.get(PersonNotificationBinding, first.id).revoked_at is not None
    with pytest.raises(OrgError):
        n.validate_delivery(db, row)
    assert stage(db).id == row.id  # Same business event cannot silently target a new recipient.
    assert stage(db, "ito:run1:finance-confirmed:v2:p").id != row.id


@pytest.mark.parametrize(
    "action", ["revoke", "left", "offline", "account_changed", "session_archived"]
)
def test_validate_pending_again_before_send(db, action):
    bind(db)
    row = stage(db)
    if action == "revoke":
        n.revoke(db, "t", "p", "admin")
    elif action == "left":
        person = db.get(EnterprisePerson, "p")
        person.status = "left"
        db.add(person)
    elif action == "offline":
        channel = db.get(ChannelBinding, "bot")
        channel.connected = False
        db.add(channel)
    elif action == "account_changed":
        channel = db.get(ChannelBinding, "bot")
        channel.external_account_key = "changed"
        db.add(channel)
    else:
        session = db.get(ChatSession, "session")
        session.status = "archived"
        db.add(session)
    db.flush()
    with pytest.raises(OrgError):
        n.validate_delivery(db, row)


def test_codes_expire_single_use_and_never_change_primary(db):
    user = db.get(User, "u")
    code = n.issue_code(db, "t", "p", "bot", user)["command"].split()[-1]
    db.commit()
    channel = db.get(ChannelBinding, "bot")
    inbound = SimpleNamespace(is_group=False, from_user_id="wx1")
    assert "成功" in n.consume_code(db, channel, inbound, code)
    assert db.get(EnterprisePerson, "p").primary_user_id == "u"
    assert "无效" in n.consume_code(db, channel, inbound, code)
    next_code = n.issue_code(db, "t", "p", "bot", user)["command"].split()[-1]
    row = db.exec(
        select(PersonNotificationCode).where(PersonNotificationCode.used_at.is_(None))
    ).one()
    row.expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()
    assert "过期" in n.consume_code(db, channel, inbound, next_code)


def test_code_wrong_bot_group_other_user_and_revocation(db):
    user = db.get(User, "u")
    with pytest.raises(OrgError):
        n.issue_code(db, "t", "p2", "bot", user)
    code = n.issue_code(db, "t", "p", "bot", user)["command"].split()[-1]
    db.commit()
    channel = db.get(ChannelBinding, "bot")
    assert "仅支持" in n.consume_code(db, channel, SimpleNamespace(is_group=True), code)
    other = ChannelBinding(
        id="other",
        tenant_id="t",
        agent_id="agent",
        channel="wecom",
        status="active",
        external_account_key="other",
        identity_scope_key="corp",
        config_json={"corp_id": "corp"},
    )
    db.add(other)
    db.commit()
    assert "不匹配" in n.consume_code(db, other, SimpleNamespace(is_group=False), code)
    n.revoke(db, "t", "p", "u")
    db.commit()
    assert "无效" in n.consume_code(
        db, channel, SimpleNamespace(is_group=False, from_user_id="wx1"), code
    )


def test_api_authz(db):
    user = db.get(User, "u")
    with pytest.raises(HTTPException) as exc:
        bind_notification(
            "p", BindingRequest(tenant_id="t", contact_id="c", binding_id="bot"), db, user
        )
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        read_notification("p2", db, user, "t")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        read_notification("p", db, user, "other")
    assert read_notification("p", db, user, "t")["binding_status"] == "unbound"


def test_contact_merge_same_identity_is_resolved(db):
    bind(db)
    old = db.get(PersonContact, "c")
    old.merged_into_id = "root"
    db.add(old)
    db.flush()
    db.add(
        PersonContact(
            id="root", tenant_id="t", display_name="Root", wecom_corp_id="corp", wecom_userid="wx1"
        )
    )
    db.flush()
    assert stage(db).target_json["to_user_id"] == "wx1"


def test_outbox_stops_unknown_retries_and_invalidated_targets(db, monkeypatch):
    from app.channels import service_outbox as outbox

    bind(db)
    row = stage(db)
    row.status = "sending"
    row.attempts = 2
    states = []
    monkeypatch.setattr(outbox, "_finish_delivery_claim", lambda *a, **kw: states.append(kw))
    outbox._deliver_one_locked(db, row)
    assert states[-1]["last_error"] == "remote_state_unknown"
    row.attempts = 1
    n.revoke(db, "t", "p", "admin")
    outbox._deliver_one_locked(db, row)
    assert states[-1]["status"] == "failed"


def test_worker_reports_success_only_after_adapter_accepts(db, monkeypatch):
    from app.channels import adapters
    from app.channels import service_outbox as outbox

    bind(db)
    row = stage(db)
    row.status = "sending"
    row.attempts = 1
    sent = []
    finished = []
    adapter = SimpleNamespace(send=lambda *args, **kwargs: sent.append((args, kwargs)))
    monkeypatch.setattr(adapters, "get_channel_adapter", lambda _: adapter)
    monkeypatch.setattr(
        outbox, "_finish_delivery_claim", lambda *args, **kwargs: finished.append(kwargs)
    )
    outbox._deliver_one_locked(db, row)
    assert len(sent) == 1
    assert sent[0][0][1] == {"to_user_id": "wx1", "is_group": False}
    assert finished[-1]["status"] == "delivered"
    assert finished[-1]["delivered_at"] is not None


def test_staged_notification_is_claimed_and_sent_once_by_worker(db, monkeypatch):
    from app.channels import adapters
    from app.channels import service_outbox as outbox

    bind(db)
    row = stage(db)
    db.commit()
    sent = []
    adapter = SimpleNamespace(send=lambda *args, **kwargs: sent.append((args, kwargs)))
    monkeypatch.setattr(adapters, "get_channel_adapter", lambda _: adapter)

    assert outbox._deliver_due(db) == 1
    db.refresh(row)
    assert row.status == "delivered"
    assert row.attempts == 1
    assert row.delivered_at is not None
    assert len(sent) == 1
    assert sent[0][0][1] == {"to_user_id": "wx1", "is_group": False}
    assert outbox._deliver_due(db) == 0
    assert len(sent) == 1


def test_unrelated_archived_contact_does_not_block_other_people(db):
    db.add(PersonContact(id="stale", tenant_id="t", display_name="Old", status="archived"))
    db.add(
        PersonNotificationBinding(
            tenant_id="t",
            person_id="p2",
            contact_id="stale",
            binding_id="bot",
            account_key="key",
            scope="corp",
            external_user_id="old",
            created_by_user_id="admin",
        )
    )
    db.commit()
    bind(db)
    assert stage(db).status == "pending"


def test_purpose_specific_code_does_not_enter_login_binding_path():
    from app.channels.service_routing import parse_command

    assert parse_command("/通知绑定 ABC").kind == "notification_bind"
    assert parse_command("/绑定 ABC").kind == "bind"


def test_sqlite_new_tables_are_additive_and_restart_safe(db):
    bind(db)
    db.commit()
    SQLModel.metadata.create_all(db.get_bind())
    assert db.get(EnterprisePerson, "p").primary_user_id == "u"
    assert n.active_link(db, "t", "p").contact_id == "c"


def test_notification_http_route_registration_and_authorization(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.org.api import router
    from app.security.auth import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, "admin")
    client = TestClient(app)
    path = "/api/enterprise/org/people/p/wecom-notification"
    response = client.put(path, json={"tenant_id": "t", "contact_id": "c", "binding_id": "bot"})
    assert response.status_code == 200
    assert response.json()["reachability"] == "unverified"
    response = client.post(path + "/test", json={"tenant_id": "t", "request_id": "once"})
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert (
        client.post(path + "/test", json={"tenant_id": "t", "request_id": "once"}).json()
        == response.json()
    )
    app.dependency_overrides[get_current_user] = lambda: db.get(User, "u")
    assert client.get(path + "?tenant_id=t").status_code == 200
    assert client.get(path.replace("/p/", "/p2/") + "?tenant_id=t").status_code == 403
    assert (
        client.put(
            path, json={"tenant_id": "t", "contact_id": "c", "binding_id": "bot"}
        ).status_code
        == 403
    )
    assert client.delete(path + "?tenant_id=t").json()["binding_status"] == "unbound"


def test_contact_account_already_owned_by_another_employee_is_conflict(db):
    contact = db.get(PersonContact, "c")
    contact.web_user_id = "u"
    db.add(contact)
    db.commit()
    with pytest.raises(OrgError, match="账号已属于其他"):
        bind(db, "p2")


def test_pending_notification_stops_after_source_agent_disabled(db):
    bind(db)
    row = stage(db)
    agent = db.get(AgentProfile, "agent")
    agent.status = "disabled"
    db.add(agent)
    db.flush()
    with pytest.raises(OrgError, match="来源数字员工"):
        n.validate_delivery(db, row)


def test_verified_bot_scope_does_not_claim_corporate_identity(db):
    channel = db.get(ChannelBinding, "bot")
    channel.config_json = {"bot_id": "corp"}
    channel.external_account_key = "wecom:bot:corp"
    session = db.get(ChatSession, "session")
    session.channel_account_key = channel.external_account_key
    db.add_all([channel, session])
    db.commit()
    bind(db)
    assert n.status_view(db, "t", "p")["scope_label"] == "当前机器人范围（未配置企业 ID）"
    assert stage(db).status == "pending"
    channel.config_json = {"bot_id": "other-bot"}
    db.add(channel)
    db.flush()
    with pytest.raises(OrgError):
        n.resolve_target(db, "t", "p")


def test_scope_upgrade_requires_new_binding(db):
    bind(db)
    channel = db.get(ChannelBinding, "bot")
    channel.identity_scope_key = "new-corp"
    channel.config_json = {"corp_id": "new-corp"}
    db.add(channel)
    db.flush()
    with pytest.raises(OrgError, match="身份或通道账号已变化"):
        n.resolve_target(db, "t", "p")

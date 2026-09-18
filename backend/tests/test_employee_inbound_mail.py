from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select
from test_employee_mail import (
    FakeTransport,
    _compose,
    _configure,
    _engine,
    _invoker,
    _seed,
)

from app.agents.branching import mark_resource_private_for_agent
from app.agents.schema import AgentProfileCreateRequest
from app.api.agents import create_agent
from app.channels.adapters.base import ChannelInbound
from app.db.models import (
    AgentResourceBinding,
    ChannelBinding,
    ChannelDelivery,
    ChannelIdentity,
    EmployeeMailMessage,
    EmployeeMailTriageRule,
    GeneralSkill,
)
from app.mail.api import (
    get_inbox,
    get_mailbox_status,
    get_pending_owner,
    get_sent,
    read_message,
    set_mailbox_enabled_status,
    teach_message,
)
from app.mail.confirm import send_requires_confirmation
from app.mail.errors import MailError
from app.mail.harness import invoke_mail_tool
from app.mail.inbound import (
    InboundComposeInvoker,
    inbound_compose_request,
    set_inbound_skill_runner,
    try_handle_channel_mail_teaching,
)
from app.mail.schema import MailboxEnabledRequest, MailTeachRequest
from app.mail.service import compose, get_mailbox, prompt_context, sync_all_mailboxes
from app.mail.transport import FetchedMessage, set_transport
from app.security.encryption import decrypt_secret


def _inbox_message(**overrides) -> FetchedMessage:
    payload = {
        "uid": "1",
        "rfc_message_id": "<one@example.com>",
        "from_address": "vendor@example.com",
        "to": ["agent-a@example.com"],
        "cc": [],
        "bcc": [],
        "subject": "供应商报销申请",
        "body_text": "请报销差旅 3200 元，发票见附件。",
        "date": None,
        "unseen": True,
        "attachments": [],
    }
    payload.update(overrides)
    return FetchedMessage(**payload)


def _bind_skill(db: Session, agent_id: str, **overrides) -> GeneralSkill:
    payload = {
        "tenant_id": "tenant_demo",
        "slug": "reimburse",
        "name": "报销申请",
        "description": "处理供应商报销",
        "skill_markdown": "# 报销申请\n请完成报销并回信。",
        "status": "published",
        "runtime_config_json": {
            "inbound_auto_run": True,
            "inbound_match_hint": "供应商报销申请",
        },
        "metadata_json": {},
    }
    payload.update(overrides)
    skill = GeneralSkill(**payload)
    mark_resource_private_for_agent(skill, agent_id, skill.metadata_json or {})
    db.add(skill)
    db.flush()
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=agent_id,
            resource_type="general_skill",
            resource_id=skill.id,
            status="active",
            metadata_json={"scope": "agent_private", "owner_agent_id": agent_id},
        )
    )
    db.commit()
    db.refresh(skill)
    return skill


def _replying_runner(db, agent, skill, message) -> None:
    compose(
        db,
        agent.tenant_id,
        agent.id,
        inbound_compose_request(
            agent.tenant_id,
            to=[message.from_address],
            subject=f"Re: {message.subject}",
            body=f"已按技能「{skill.name}」处理。",
            in_reply_to=message.id,
        ),
        confirmed=True,
        invoker=InboundComposeInvoker(
            db=db,
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            active_skill=skill,
        ),
    )


@pytest.fixture
def inbound_mail_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _engine()
    transport = FakeTransport()
    set_transport(transport)
    set_inbound_skill_runner(_replying_runner)
    with Session(engine) as db:
        users = _seed(db)
        yield db, users, transport
    set_inbound_skill_runner(None)
    set_transport(None)


def _owner_setup(inbound_mail_db):
    db, (owner, other, admin, agent_a, agent_b), transport = inbound_mail_db
    _configure(db, owner, agent_a.id, password="mailbox-secret")
    return db, owner, other, admin, agent_a, agent_b, transport


def test_ae1_opted_in_skill_runs_and_sends(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(db, agent_a.id)
    transport.inbox = [_inbox_message()]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "skill"
    assert listing.messages[0].triage_label and "报销申请" in listing.messages[0].triage_label
    sent = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert sent.messages
    assert sent.messages[0].status == "sent"
    assert transport.sent
    assert "mailbox-secret" not in str(listing.model_dump())


def test_ae2_skill_without_flag_is_not_selected(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(
        db,
        agent_a.id,
        slug="closer",
        name="更像报销的技能",
        runtime_config_json={"inbound_match_hint": "供应商报销申请"},
    )
    _bind_skill(
        db,
        agent_a.id,
        slug="reimburse",
        name="报销申请",
        runtime_config_json={
            "inbound_auto_run": True,
            "inbound_match_hint": "供应商报销申请",
        },
    )
    transport.inbox = [_inbox_message()]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_skill_name == "报销申请"
    assert listing.messages[0].triage_disposition == "skill"


def test_ae3_no_opted_in_skills_asks_owner(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(
        db,
        agent_a.id,
        runtime_config_json={"inbound_match_hint": "供应商报销申请"},
    )
    transport.inbox = [_inbox_message()]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "ask_owner"
    assert listing.pending_owner_count == 1


def test_ae4_unmatched_notifies_via_bound_channel(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    binding = ChannelBinding(
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        channel="feishu",
        status="active",
    )
    db.add(binding)
    db.flush()
    db.add(
        ChannelIdentity(
            tenant_id="tenant_demo",
            channel="feishu",
            external_account_scope="",
            external_user_id="ou_owner",
            staffdeck_user_id=owner.id,
        )
    )
    db.commit()
    transport.inbox = [_inbox_message(subject="你好", body_text="请问一下", rfc_message_id="<ask@example.com>")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "ask_owner"
    notice = db.exec(select(ChannelDelivery).where(ChannelDelivery.kind == "mail_triage_notice")).first()
    assert notice is not None
    assert "你好" in notice.text
    assert "vendor@example.com" in notice.text
    assert "mailbox-secret" not in notice.text
    assert "/enterprise/mail" in notice.text
    pending = get_pending_owner(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert pending.total == 1


def test_ae5_high_confidence_spam_is_ignored(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    binding = ChannelBinding(
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        channel="feishu",
        status="active",
    )
    db.add(binding)
    db.commit()
    transport.inbox = [
        _inbox_message(
            uid="spam",
            rfc_message_id="<spam@example.com>",
            from_address="promo@ads.example",
            subject="恭喜中奖 免费领取",
            body_text="unsubscribe 点击领奖 viagra",
        )
    ]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "ignore"
    assert listing.messages[0].triage_reason
    assert db.exec(select(ChannelDelivery)).first() is None
    detail = read_message(
        listing.messages[0].id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
        mark_read=True,
    )
    assert "领奖" in detail.body_text


def test_ae6_vague_mail_must_ask_owner(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(db, agent_a.id)
    transport.inbox = [
        _inbox_message(
            uid="vague",
            rfc_message_id="<vague@example.com>",
            from_address="stranger@example.net",
            subject="请问",
            body_text="你好，方便聊一下吗？",
        )
    ]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "ask_owner"


def test_ae7_two_matching_skills_ask_owner(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(db, agent_a.id, slug="reimburse-a", name="报销A")
    _bind_skill(db, agent_a.id, slug="reimburse-b", name="报销B")
    transport.inbox = [_inbox_message()]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "ask_owner"
    assert "都像" in (listing.messages[0].triage_reason or "")
    assert transport.sent == []


def test_ae8_channel_reply_teaches_ignore(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    binding = ChannelBinding(
        id="chan_feishu",
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        channel="feishu",
        status="active",
    )
    db.add(binding)
    db.add(
        ChannelIdentity(
            tenant_id="tenant_demo",
            channel="feishu",
            external_account_scope="",
            external_user_id="ou_owner",
            staffdeck_user_id=owner.id,
        )
    )
    db.commit()
    transport.inbox = [_inbox_message(subject="请问", body_text="方便吗")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    mail = listing.messages[0]
    db.add(
        ChannelDelivery(
            tenant_id="tenant_demo",
            binding_id=binding.id,
            session_id=f"mail-triage:{mail.id}",
            message_id="om_notice_1",
            kind="mail_triage_notice",
            text="ask",
            status="delivered",
            target_json={"receive_id": "ou_owner", "mail_message_id": mail.id},
            idempotency_key="mailnt-ae8",
        )
    )
    db.commit()
    inbound = ChannelInbound(
        channel="feishu",
        event_id="evt-1",
        from_user_id="ou_owner",
        to_user_id="bot",
        session_id="oc_1",
        group_id="",
        context_token="",
        text="这是垃圾，以后从该发件人来的同类忽略",
        is_group=False,
        raw={},
        parent_id="om_notice_1",
    )
    ack = try_handle_channel_mail_teaching(db, binding, inbound)
    assert ack and "忽略" in ack
    detail = read_message(
        mail.id, tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner, mark_read=False
    )
    assert detail.triage_disposition == "ignore"
    transport.inbox = [
        _inbox_message(
            uid="2",
            rfc_message_id="<two@example.com>",
            subject="又来一封",
            body_text="还是我",
        )
    ]
    second = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    ignored = next(item for item in second.messages if item.id != mail.id)
    assert ignored.triage_disposition == "ignore"


def test_ae9_console_teach_skill_and_rule_visible(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    skill = _bind_skill(db, agent_a.id)
    transport.inbox = [_inbox_message(subject="请问", body_text="这是什么")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    mail = listing.messages[0]
    taught = teach_message(
        mail.id,
        MailTeachRequest(tenant_id="tenant_demo", action="skill", skill_id=skill.id),
        db=db,
        current_user=owner,
        agent_id=agent_a.id,
    )
    assert taught.triage_disposition == "skill"
    assert transport.sent
    rule = db.exec(select(EmployeeMailTriageRule)).first()
    assert rule is not None
    assert rule.skill_id == skill.id
    transport.inbox.append(
        _inbox_message(uid="3", rfc_message_id="<three@example.com>", subject="又一份", body_text="补充材料")
    )
    later = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert any(item.triage_disposition == "skill" and item.id != mail.id for item in later.messages)


def test_ae10_ask_again_keeps_asking(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    transport.inbox = [_inbox_message(subject="请问", body_text="方便吗")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    mail = listing.messages[0]
    taught = teach_message(
        mail.id,
        MailTeachRequest(tenant_id="tenant_demo", action="ask_again"),
        db=db,
        current_user=owner,
        agent_id=agent_a.id,
    )
    assert taught.triage_disposition == "ask_owner"
    transport.inbox.append(
        _inbox_message(uid="4", rfc_message_id="<four@example.com>", subject="再问", body_text="还在吗")
    )
    later = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert all(
        item.triage_disposition == "ask_owner"
        for item in later.messages
        if item.from_address == "vendor@example.com"
    )


def test_ae11_chat_draft_first_still_requires_confirm(inbound_mail_db) -> None:
    db, _owner, _other, _admin, agent_a, _agent_b, _transport = _owner_setup(inbound_mail_db)
    _bind_skill(db, agent_a.id)
    invoker = _invoker(db, agent_a.id, latest_user_text="先起草，我确认后再发")
    assert send_requires_confirmation(invoker, {}) is True
    result = invoke_mail_tool(
        invoker,
        "mail_send",
        {"to": ["sales@example.com"], "subject": "报价", "body": "请查收"},
    )
    assert result["success"] is True
    assert result["data"]["draft"] is True
    assert result["data"]["delivered"] is False


def test_ae12_smtp_failure_marks_failed_and_notifies(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(db, agent_a.id)
    transport.smtp_error = MailError("MAIL_SMTP_FAILED", "SMTP 被拒绝")
    binding = ChannelBinding(
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        channel="feishu",
        status="active",
    )
    db.add(binding)
    db.add(
        ChannelIdentity(
            tenant_id="tenant_demo",
            channel="feishu",
            external_account_scope="",
            external_user_id="ou_owner",
            staffdeck_user_id=owner.id,
        )
    )
    db.commit()
    transport.inbox = [_inbox_message()]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "failed"
    sent = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert sent.messages[0].status == "failed"
    notice = db.exec(select(ChannelDelivery).where(ChannelDelivery.kind == "mail_triage_notice")).first()
    assert notice is not None
    assert "SMTP" in notice.text or "失败" in notice.text


def test_ae13_disabled_mailbox_stops_fetch_send_triage(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    set_mailbox_enabled_status(
        MailboxEnabledRequest(tenant_id="tenant_demo", enabled=False),
        db=db,
        current_user=owner,
        agent_id=agent_a.id,
    )
    transport.inbox = [_inbox_message(uid="new", rfc_message_id="<new@example.com>")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages == []
    assert listing.mailbox_enabled is False
    assert transport.fetch_calls == []
    with pytest.raises(Exception) as error:
        _compose(db, owner, agent_a.id)
    assert error.value.status_code == 400
    assert "停用" in error.value.detail["message"]
    assert transport.sent == []
    sync_all_mailboxes(db)
    assert transport.fetch_calls == []


def test_ae14_disabled_mailbox_keeps_history_readable(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    transport.inbox = [_inbox_message()]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    mail_id = listing.messages[0].id
    _compose(db, owner, agent_a.id)
    set_mailbox_enabled_status(
        MailboxEnabledRequest(tenant_id="tenant_demo", enabled=False),
        db=db,
        current_user=owner,
        agent_id=agent_a.id,
    )
    inbox = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert any(item.id == mail_id for item in inbox.messages)
    detail = read_message(
        mail_id, tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner, mark_read=False
    )
    assert "报销" in detail.body_text
    sent = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert sent.messages


def test_ae15_enable_resumes_triage(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(db, agent_a.id)
    set_mailbox_enabled_status(
        MailboxEnabledRequest(tenant_id="tenant_demo", enabled=False),
        db=db,
        current_user=owner,
        agent_id=agent_a.id,
    )
    set_mailbox_enabled_status(
        MailboxEnabledRequest(tenant_id="tenant_demo", enabled=True),
        db=db,
        current_user=owner,
        agent_id=agent_a.id,
    )
    transport.inbox = [_inbox_message()]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "skill"


def test_ae16_no_channel_keeps_pending_and_does_not_ignore(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    transport.inbox = [_inbox_message(subject="请问", body_text="帮忙看下")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert listing.messages[0].triage_disposition == "ask_owner"
    assert listing.messages[0].triage_notified is False
    pending = get_pending_owner(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert pending.total == 1


def test_ae17_admin_can_read_cannot_teach_or_disable(inbound_mail_db) -> None:
    db, _owner, _other, admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    transport.inbox = [_inbox_message(subject="请问", body_text="帮忙看下")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=admin)
    assert listing.messages[0].body_text == ""
    detail = read_message(
        listing.messages[0].id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=admin,
        mark_read=False,
    )
    assert detail.triage_disposition == "ask_owner"
    assert detail.can_teach is False
    with pytest.raises(HTTPException):
        set_mailbox_enabled_status(
            MailboxEnabledRequest(tenant_id="tenant_demo", enabled=False),
            db=db,
            current_user=admin,
            agent_id=agent_a.id,
        )
    with pytest.raises(HTTPException):
        teach_message(
            listing.messages[0].id,
            MailTeachRequest(tenant_id="tenant_demo", action="ignore"),
            db=db,
            current_user=admin,
            agent_id=agent_a.id,
        )


def test_ae18_copy_agent_drops_mailbox_rules_and_history(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    _bind_skill(db, agent_a.id)
    transport.inbox = [_inbox_message(subject="请问", body_text="帮忙看下")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    teach_message(
        listing.messages[0].id,
        MailTeachRequest(tenant_id="tenant_demo", action="ignore"),
        db=db,
        current_user=owner,
        agent_id=agent_a.id,
    )
    copied = create_agent(
        AgentProfileCreateRequest(
            tenant_id="tenant_demo",
            name="复制来信员工",
            copy_from_agent_id=agent_a.id,
            source_mode="copy",
        ),
        db=db,
        current_user=owner,
    )
    status = get_mailbox_status(
        tenant_id="tenant_demo", agent_id=copied.id, db=db, current_user=owner
    )
    assert status.configured is False
    copied_inbox = get_inbox(tenant_id="tenant_demo", agent_id=copied.id, db=db, current_user=owner)
    assert copied_inbox.messages == []
    rules = db.exec(
        select(EmployeeMailTriageRule).where(EmployeeMailTriageRule.agent_id == copied.id)
    ).all()
    assert rules == []


def test_ae19_password_stays_out_of_prompt_and_notices(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    mailbox = get_mailbox(db, "tenant_demo", agent_a.id)
    assert mailbox is not None
    secret = decrypt_secret(mailbox.password_encrypted)
    transport.inbox = [_inbox_message(subject="请问", body_text="帮忙看下")]
    listing = get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    prompt = prompt_context(db, "tenant_demo", agent_a.id)
    assert secret not in prompt
    assert secret not in str(listing.model_dump())
    row = db.exec(select(EmployeeMailMessage)).first()
    assert row is not None
    assert secret not in (row.triage_reason or "")
    assert secret not in str(row.metadata_json)


def test_ae20_inbox_stays_paginated(inbound_mail_db) -> None:
    db, owner, _other, _admin, agent_a, _agent_b, transport = _owner_setup(inbound_mail_db)
    transport.inbox = [
        _inbox_message(
            uid=str(index),
            rfc_message_id=f"<{index}@example.com>",
            subject=f"来信 {index}",
            body_text="内容",
        )
        for index in range(25)
    ]
    page = get_inbox(
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
        page=1,
        page_size=20,
    )
    assert page.page_size == 20
    assert len(page.messages) == 20
    assert page.total == 25
    assert transport.fetch_calls[-1]["limit"] == 20


def test_inbound_auto_complete_bypasses_skill_confirm_flag(inbound_mail_db) -> None:
    db, _owner, _other, _admin, agent_a, _agent_b, _transport = _owner_setup(inbound_mail_db)
    invoker = _invoker(db, agent_a.id, confirm_before_send_mail=True, inbound_mail_auto_complete=True)
    assert send_requires_confirmation(invoker, {}) is False
    chat = _invoker(db, agent_a.id, confirm_before_send_mail=True)
    assert send_requires_confirmation(chat, {}) is True

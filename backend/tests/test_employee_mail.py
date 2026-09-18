from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.branching import (
    ensure_private_resource_binding,
    mark_resource_private_for_agent,
)
from app.agents.schema import AgentProfileCreateRequest
from app.api.agents import create_agent
from app.cabinet.service import (
    CHAT_INBOX_FOLDER,
    MAIL_INBOUND_FOLDER,
    MAIL_OUTBOUND_FOLDER,
    MAX_FILE_BYTES,
    get_entry,
    list_folder,
    save_bytes,
)
from app.core.capability_manifest import (
    RESERVED_HARNESS_CAPABILITY_NAMES,
    CapabilityManifestBuilder,
)
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    ChatSession,
    EmployeeMailbox,
    EmployeeMailMessage,
    GeneralSkill,
    Message,
    ModelConfig,
    Skill,
    Tenant,
    User,
    utc_now,
)
from app.mail.api import (
    confirm_draft,
    get_inbox,
    get_mailbox_status,
    get_sent,
    read_message,
    send_or_draft,
    upsert_mailbox,
)
from app.mail.errors import MailError
from app.mail.harness import invoke_mail_tool
from app.mail.schema import MailAttachmentInput, MailboxConfigRequest, MailComposeRequest
from app.mail.service import mailbox_status, message_read, prompt_context
from app.mail.transport import (
    FetchedAttachment,
    FetchedMessage,
    MailboxConnection,
    _safe_exc,
    set_transport,
)
from app.security.encryption import decrypt_secret


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


@dataclass
class FakeTransport:
    inbox: list[FetchedMessage] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    imap_error: MailError | None = None
    smtp_error: MailError | None = None
    append_note: str | None = None

    def probe_imap(self, mailbox: MailboxConnection) -> None:
        if self.imap_error:
            raise self.imap_error

    def probe_smtp(self, mailbox: MailboxConnection) -> None:
        if self.smtp_error:
            raise self.smtp_error

    def fetch_inbox(self, mailbox: MailboxConnection) -> list[FetchedMessage]:
        if self.imap_error:
            raise self.imap_error
        return list(self.inbox)

    def mark_seen(self, mailbox: MailboxConnection, uid: str) -> None:
        for item in self.inbox:
            if item.uid == uid:
                item.unseen = False

    def send(
        self,
        mailbox: MailboxConnection,
        *,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body: str,
        attachments: list,
        in_reply_to: str | None = None,
    ) -> tuple[str, str | None]:
        if self.smtp_error:
            raise self.smtp_error
        self.sent.append(
            {
                "from": mailbox.email_address,
                "to": to,
                "cc": cc,
                "bcc": bcc,
                "subject": subject,
                "body": body,
                "attachments": [item.filename for item in attachments],
                "password": mailbox.password,
            }
        )
        return f"<mail-{len(self.sent)}@example.com>", self.append_note

    def append_sent(self, mailbox: MailboxConnection, raw: bytes) -> None:
        return None


@pytest.fixture
def mail_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _engine()
    transport = FakeTransport()
    set_transport(transport)
    with Session(engine) as db:
        users = _seed(db)
        yield db, users, transport
    set_transport(None)


def _configure(db: Session, user: User, agent_id: str, password: str = "secret-pass") -> None:
    upsert_mailbox(
        MailboxConfigRequest(
            tenant_id="tenant_demo",
            email_address="agent-a@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="agent-a@example.com",
            password=password,
            probe=True,
        ),
        agent_id=agent_id,
        db=db,
        current_user=user,
    )


def _compose(
    db: Session,
    user: User,
    agent_id: str,
    **kwargs,
):
    payload = {
        "tenant_id": "tenant_demo",
        "to": ["sales@example.com"],
        "subject": "报价",
        "body": "请查收",
        **kwargs,
    }
    return send_or_draft(
        MailComposeRequest(**payload),
        agent_id=agent_id,
        db=db,
        current_user=user,
    )


def _invoker(db: Session, agent_id: str, **extra):
    values = {
        "db": db,
        "tenant_id": "tenant_demo",
        "agent_id": agent_id,
        "active_skill": None,
        "session": SimpleNamespace(id="sess_1"),
        "latest_user_text": "",
        "confirm_before_send_mail": False,
        "_activated_names": set(),
    }
    values.update(extra)
    return SimpleNamespace(**values)


def test_owner_saves_mailbox_without_echoing_password(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _transport = mail_db
    _configure(db, owner, agent_a.id, password="super-secret-password")
    status = get_mailbox_status(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert status.configured is True
    assert status.email_address == "agent-a@example.com"
    dumped = status.model_dump()
    assert "super-secret-password" not in str(dumped)
    assert "password" not in dumped
    row = db.exec(select(EmployeeMailbox)).first()
    assert row is not None
    assert row.password_encrypted != "super-secret-password"
    assert decrypt_secret(row.password_encrypted) == "super-secret-password"


def test_transport_error_text_redacts_mailbox_password() -> None:
    mailbox = MailboxConnection(
        email_address="agent-a@example.com",
        username="agent-a@example.com",
        password="super-secret-password",
        imap_host="imap.example.com",
        imap_port=993,
        imap_encryption="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_encryption="starttls",
    )
    text = _safe_exc(RuntimeError("LOGIN failed for super-secret-password"), mailbox)
    assert "super-secret-password" not in text
    assert "******" in text


def test_mailbox_password_stays_out_of_skills(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _transport = mail_db
    _configure(db, owner, agent_a.id, password="not-for-skills")
    skill = GeneralSkill(
        tenant_id="tenant_demo",
        slug="quote",
        name="报价技能",
        skill_markdown="# 报价",
        runtime_config_json={"confirm_before_send_mail": True},
    )
    db.add(skill)
    db.commit()
    assert "not-for-skills" not in (skill.skill_markdown or "")
    assert "not-for-skills" not in str(skill.runtime_config_json)
    prompt = prompt_context(db, "tenant_demo", agent_a.id)
    assert "not-for-skills" not in prompt
    assert "agent-a@example.com" in prompt


def test_unconfigured_inbox_is_empty_state_not_error(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _transport = mail_db
    listing = get_inbox(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert listing.configured is False
    assert listing.messages == []
    assert "还没有配置邮箱" in (listing.empty_reason or "")


def test_unconfigured_send_does_not_call_smtp(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    with pytest.raises(Exception) as error:
        _compose(db, owner, agent_a.id)
    assert error.value.status_code == 400
    assert error.value.detail["code"] == "MAIL_NOT_CONFIGURED"
    assert transport.sent == []
    assert db.exec(select(EmployeeMailMessage)).all() == []


def test_inbox_lists_unread_and_opens_body(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.inbox = [
        FetchedMessage(
            uid="12",
            rfc_message_id="<inq@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="询价",
            body_text="请报价",
            date=None,
            unseen=True,
            attachments=[],
        )
    ]
    listing = get_inbox(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert listing.messages[0].unread is True
    assert listing.messages[0].from_address == "buyer@example.com"
    opened = read_message(
        listing.messages[0].id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert opened.body_text == "请报价"
    assert opened.unread is False


def test_console_send_appears_in_sent_log(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    result = _compose(db, owner, agent_a.id)
    assert result.delivered is True
    assert transport.sent[0]["to"] == ["sales@example.com"]
    sent = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert sent.messages[0].status == "sent"
    assert sent.messages[0].to == ["sales@example.com"]


def test_console_reply_defaults_to_original_sender(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.inbox = [
        FetchedMessage(
            uid="1",
            rfc_message_id="<in@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="询价",
            body_text="请报价",
            date=None,
            unseen=True,
        )
    ]
    listing = get_inbox(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    from app.mail.api import get_reply_defaults

    defaults = get_reply_defaults(
        listing.messages[0].id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert defaults["to"] == ["buyer@example.com"]
    assert str(defaults["subject"]).startswith("Re:")
    result = _compose(
        db,
        owner,
        agent_a.id,
        to=defaults["to"],
        subject=defaults["subject"],
        body="报价见附件",
        in_reply_to=listing.messages[0].id,
    )
    assert result.delivered is True
    assert transport.sent[-1]["to"] == ["buyer@example.com"]


def test_chat_send_without_confirm_delivers(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    result = invoke_mail_tool(
        _invoker(db, agent_a.id, latest_user_text="给 sales@example.com 发报价"),
        "mail_send",
        {"to": ["sales@example.com"], "subject": "报价", "body": "见正文"},
    )
    assert result["success"] is True
    assert result["data"]["delivered"] is True
    assert len(transport.sent) == 1
    sent = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert len(sent.messages) == 1


def test_chat_draft_then_confirm(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    invoker = _invoker(db, agent_a.id, latest_user_text="先起草邮件，我确认后再发送")
    drafted = invoke_mail_tool(
        invoker,
        "mail_send",
        {"to": ["sales@example.com"], "subject": "草稿", "body": "内容"},
    )
    assert drafted["success"] is True
    assert drafted["data"]["draft"] is True
    assert drafted["data"]["delivered"] is False
    assert transport.sent == []
    sent_before = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert sent_before.messages == []
    invoker.latest_user_text = "发吧"
    sent = invoke_mail_tool(
        invoker, "mail_send", {"draft_id": drafted["data"]["id"]}
    )
    assert sent["success"] is True
    assert sent["data"]["delivered"] is True
    assert len(transport.sent) == 1


def test_prior_draft_phrase_does_not_block_later_turn(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    now = utc_now()
    db.add(
        Message(
            tenant_id="tenant_demo",
            session_id="sess_1",
            role="user",
            content="先起草邮件，我确认后再发送",
            created_at=now - timedelta(seconds=30),
        )
    )
    db.add(
        Message(
            tenant_id="tenant_demo",
            session_id="sess_1",
            role="user",
            content="给 sales@example.com 发报价，主题报价，正文见附件",
            created_at=now,
        )
    )
    db.commit()
    result = invoke_mail_tool(
        _invoker(db, agent_a.id),
        "mail_send",
        {"to": ["sales@example.com"], "subject": "报价", "body": "见正文"},
    )
    assert result["success"] is True
    assert result["data"]["delivered"] is True
    assert len(transport.sent) == 1


def test_skill_confirm_flag_blocks_send(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    skill = Skill(
        tenant_id="tenant_demo",
        skill_id="quote_mail",
        name="报价发信",
        content_json={"confirm_before_send_mail": True},
        status="published",
    )
    result = invoke_mail_tool(
        _invoker(db, agent_a.id, active_skill=skill, latest_user_text="给 sales@example.com 发"),
        "mail_send",
        {"to": ["sales@example.com"], "subject": "报价", "body": "内容"},
    )
    assert result["data"]["draft"] is True
    assert transport.sent == []


def test_general_skill_harness_name_blocks_send_without_chat_confirm(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    skill = GeneralSkill(
        tenant_id="tenant_demo",
        slug="quote",
        name="报价技能",
        skill_markdown="# 报价",
        status="published",
        runtime_config_json={"confirm_before_send_mail": True},
    )
    db.add(skill)
    db.commit()
    result = invoke_mail_tool(
        _invoker(
            db,
            agent_a.id,
            active_skill=None,
            latest_user_text="给 sales@example.com 发报价",
            _activated_names={"general_skill.quote"},
        ),
        "mail_send",
        {"to": ["sales@example.com"], "subject": "报价", "body": "内容"},
    )
    assert result["success"] is True
    assert result["data"]["draft"] is True
    assert result["data"]["delivered"] is False
    assert transport.sent == []


def test_loaded_general_skill_confirm_flag_blocks_harness_send(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    skill = GeneralSkill(
        tenant_id="tenant_demo",
        slug="quote",
        name="报价技能",
        skill_markdown="# 报价\n给客户发报价邮件。",
        status="published",
        permissions_json={"confirm_before_send_mail": True},
    )
    mark_resource_private_for_agent(skill, agent_a.id)
    db.add(skill)
    db.flush()
    ensure_private_resource_binding(
        db,
        "tenant_demo",
        agent_a.id,
        "general_skill",
        skill.id,
        "active",
        metadata_json=skill.metadata_json,
    )
    db.commit()
    db.refresh(skill)
    session = ChatSession(
        id="sess_1",
        tenant_id="tenant_demo",
        user_id=owner.id,
        agent_id=agent_a.id,
    )
    invoker = HarnessCapabilityInvoker(
        db,
        tenant_id="tenant_demo",
        session=session,
        task_frame_id="task-mail-confirm",
        model_config=ModelConfig(
            id="model-mail",
            tenant_id="tenant_demo",
            name="test",
            api_key_encrypted="x",
            model="test",
        ),
        manifest=CapabilityManifestBuilder(db).build("tenant_demo", agent_a.id, None, None),
        active_skill=None,
        active_step_id=None,
        agent_id=agent_a.id,
        initially_activated_names={"capability_describe", "mail_send"},
    )
    described = invoker.invoke(
        "capability_describe",
        {"capabilities": ["general_skill.quote"]},
    )
    assert described["success"] is True
    loaded = invoker.invoke(
        "general_skill.quote",
        {"query": "给 sales@example.com 发报价", "operation": "read"},
    )
    assert loaded["success"] is True
    result = invoker.invoke(
        "mail_send",
        {"to": ["sales@example.com"], "subject": "报价", "body": "内容"},
    )
    assert result["success"] is True
    assert result["data"]["draft"] is True
    assert result["data"]["delivered"] is False
    assert transport.sent == []


def test_raw_address_send_without_contacts(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    result = _compose(db, owner, agent_a.id, to=["a@b.com"])
    assert result.delivered is True
    assert transport.sent[0]["to"] == ["a@b.com"]


def test_name_only_recipient_is_rejected(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    result = invoke_mail_tool(
        _invoker(db, agent_a.id, latest_user_text="给张三发邮件"),
        "mail_send",
        {"to": ["张三"], "subject": "你好", "body": "内容"},
    )
    assert result["success"] is False
    assert result["error"]["code"] == "MAIL_ADDRESS_REQUIRED"
    assert transport.sent == []


def test_inbound_attachment_lands_in_mail_zone_not_chat_inbox(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.inbox = [
        FetchedMessage(
            uid="9",
            rfc_message_id="<file@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="询价附件",
            body_text="见附件",
            date=None,
            unseen=True,
            attachments=[
                FetchedAttachment(filename="询价.pdf", content_type="application/pdf", data=b"%PDF"),
            ],
        )
    ]
    listing = get_inbox(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    opened = read_message(
        listing.messages[0].id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    path = opened.attachments[0].cabinet_path
    assert path is not None
    assert path.startswith(f"{MAIL_INBOUND_FOLDER}/")
    assert get_entry(db, "tenant_demo", agent_a.id, path) is not None
    assert get_entry(db, "tenant_demo", agent_a.id, f"{CHAT_INBOX_FOLDER}/询价.pdf") is None
    root = list_folder(db, "tenant_demo", agent_a.id, "", can_write=True)
    assert "询价.pdf" not in [item.name for item in root.entries]


def test_inbound_attachment_not_duplicated_on_resync(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.inbox = [
        FetchedMessage(
            uid="9",
            rfc_message_id="<file@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="询价附件",
            body_text="见附件",
            date=None,
            unseen=True,
            attachments=[
                FetchedAttachment(filename="询价.pdf", content_type="application/pdf", data=b"%PDF"),
            ],
        )
    ]
    get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    inbound = list_folder(db, "tenant_demo", agent_a.id, MAIL_INBOUND_FOLDER, can_write=True)
    files = [item for item in inbound.entries if item.kind == "file"]
    assert [item.name for item in files] == ["询价.pdf"]


def test_outbound_attachment_copies_template_without_moving_it(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _transport = mail_db
    _configure(db, owner, agent_a.id)
    original = save_bytes(
        db,
        "tenant_demo",
        agent_a.id,
        "报价模板.xlsx",
        b"template-bytes",
        source="console",
    )
    result = _compose(
        db,
        owner,
        agent_a.id,
        attachments=[MailAttachmentInput(cabinet_path=original.path)],
    )
    assert result.delivered is True
    outbound = result.message.attachments[0]
    assert outbound.saved is True
    assert outbound.cabinet_path is not None
    assert outbound.cabinet_path.startswith(f"{MAIL_OUTBOUND_FOLDER}/")
    still = get_entry(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    assert still is not None
    from app.cabinet.service import read_file_bytes

    _row, data = read_file_bytes(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    assert data == b"template-bytes"


def test_oversized_attachment_keeps_body_readable(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    huge = b"x" * (MAX_FILE_BYTES + 12)
    transport.inbox = [
        FetchedMessage(
            uid="3",
            rfc_message_id="<huge@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="大附件",
            body_text="正文仍应可读",
            date=None,
            unseen=True,
            attachments=[
                FetchedAttachment(filename="过大.bin", content_type="application/octet-stream", data=huge),
            ],
        )
    ]
    listing = get_inbox(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    opened = read_message(
        listing.messages[0].id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert opened.body_text == "正文仍应可读"
    assert opened.attachments[0].saved is False
    assert "超过单文件上限" in (opened.attachments[0].error or "")
    assert get_entry(db, "tenant_demo", agent_a.id, f"{MAIL_INBOUND_FOLDER}/过大.bin") is None


def test_admin_can_read_others_mail_but_cannot_send_or_configure(mail_db) -> None:
    db, (owner, _other, admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.inbox = [
        FetchedMessage(
            uid="1",
            rfc_message_id="<a@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="可见",
            body_text="管理员可看",
            date=None,
            unseen=True,
        )
    ]
    listing = get_inbox(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=admin
    )
    assert listing.can_send is False
    assert listing.messages[0].subject == "可见"
    opened = read_message(
        listing.messages[0].id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=admin,
    )
    assert opened.body_text == "管理员可看"
    assert opened.unread is True
    with pytest.raises(Exception) as send_error:
        _compose(db, admin, agent_a.id)
    assert send_error.value.status_code == 403
    with pytest.raises(Exception) as config_error:
        _configure(db, admin, agent_a.id, password="hijack")
    assert config_error.value.status_code == 403


def test_admin_can_send_from_own_employee(mail_db) -> None:
    db, (_owner, _other, admin, _agent_a, _agent_b), transport = mail_db
    own = AgentProfile(
        id="agent_admin",
        tenant_id="tenant_demo",
        name="管理员自己的员工",
        is_overall=False,
        metadata_json={"owner_user_id": admin.id},
    )
    db.add(own)
    db.commit()
    _configure(db, admin, own.id)
    result = _compose(db, admin, own.id)
    assert result.delivered is True
    assert transport.sent


def test_non_admin_cannot_see_other_employee_mail_subjects(mail_db) -> None:
    db, (owner, other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.inbox = [
        FetchedMessage(
            uid="1",
            rfc_message_id="<secret@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="机密主题",
            body_text="机密正文",
            date=None,
            unseen=True,
        )
    ]
    get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    with pytest.raises(Exception) as error:
        get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=other)
    assert error.value.status_code == 403
    assert "机密主题" not in str(error.value.detail)
    assert "机密正文" not in str(error.value.detail)


def test_runtime_cannot_use_another_employee_mailbox(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.inbox = [
        FetchedMessage(
            uid="1",
            rfc_message_id="<a@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="只属于A",
            body_text="secret-body",
            date=None,
            unseen=True,
        )
    ]
    get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    listed = invoke_mail_tool(_invoker(db, agent_b.id), "mail_list", {"folder": "inbox"})
    assert listed["success"] is False
    assert listed["error"]["code"] == "MAIL_NOT_CONFIGURED"
    assert "只属于A" not in str(listed)
    assert "secret-pass" not in str(listed)


def test_copy_agent_does_not_copy_mailbox_or_mail_files(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id, password="keep-on-a")
    transport.inbox = [
        FetchedMessage(
            uid="1",
            rfc_message_id="<file@example.com>",
            from_address="buyer@example.com",
            to=["agent-a@example.com"],
            cc=[],
            bcc=[],
            subject="附件信",
            body_text="见附件",
            date=None,
            unseen=True,
            attachments=[FetchedAttachment(filename="询价.pdf", content_type="application/pdf", data=b"pdf")],
        )
    ]
    get_inbox(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    _compose(db, owner, agent_a.id)
    copied = create_agent(
        AgentProfileCreateRequest(
            tenant_id="tenant_demo",
            name="复制员工",
            copy_from_agent_id=agent_a.id,
            source_mode="copy",
        ),
        db=db,
        current_user=owner,
    )
    copied_status = mailbox_status(
        db, "tenant_demo", copied.id, can_configure=True, can_send=True
    )
    assert copied_status.configured is False
    source = get_mailbox_status(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert source.configured is True
    copied_sent = get_sent(
        tenant_id="tenant_demo", agent_id=copied.id, db=db, current_user=owner
    )
    assert copied_sent.messages == []
    copied_files = list_folder(db, "tenant_demo", copied.id, "", can_write=True)
    assert copied_files.entries == []
    source_zone = get_entry(db, "tenant_demo", agent_a.id, MAIL_INBOUND_FOLDER)
    assert source_zone is not None


def test_copying_skill_does_not_copy_mailbox(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, agent_b), _transport = mail_db
    _configure(db, owner, agent_a.id, password="source-secret")
    skill = Skill(
        tenant_id="tenant_demo",
        skill_id="skill_quote",
        name="报价SOP",
        version="1.0.0",
        content_json={"confirm_before_send_mail": True},
        status="published",
    )
    db.add(skill)
    db.flush()
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=agent_a.id,
            resource_type="skill",
            resource_id=skill.id,
            status="active",
        )
    )
    db.commit()
    from app.api.agents import _copy_agent_scope_from_source

    _copy_agent_scope_from_source(db, "tenant_demo", agent_a, agent_b)
    status = mailbox_status(db, "tenant_demo", agent_b.id, can_configure=True, can_send=True)
    assert status.configured is False
    row = db.exec(
        select(EmployeeMailbox).where(EmployeeMailbox.agent_id == agent_b.id)
    ).first()
    assert row is None


def test_smtp_auth_failure_is_failed_sent_not_success(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    transport.smtp_error = MailError(
        "MAIL_SMTP_AUTH_FAILED",
        "SMTP 认证失败（主机 smtp.example.com）。请检查用户名和密码。",
        details={"host": "smtp.example.com"},
    )
    with pytest.raises(Exception) as error:
        _compose(db, owner, agent_a.id)
    assert error.value.detail["code"] == "MAIL_SMTP_AUTH_FAILED"
    sent = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    assert sent.messages[0].status == "failed"
    assert "认证失败" in (sent.messages[0].smtp_error or "")


def test_unconfirmed_draft_is_not_delivered(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    drafted = invoke_mail_tool(
        _invoker(db, agent_a.id, latest_user_text="先起草，我确认后再发"),
        "mail_send",
        {"to": ["sales@example.com"], "subject": "草稿", "body": "内容"},
    )
    assert drafted["data"]["draft"] is True
    refused = invoke_mail_tool(
        _invoker(db, agent_a.id, latest_user_text="先看一眼"),
        "mail_send",
        {"draft_id": drafted["data"]["id"]},
    )
    assert refused["success"] is False
    assert refused["error"]["code"] == "MAIL_CONFIRM_REQUIRED"
    assert transport.sent == []


def test_console_and_chat_share_sent_log(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _transport = mail_db
    _configure(db, owner, agent_a.id)
    _compose(db, owner, agent_a.id, subject="控制台发出")
    invoke_mail_tool(
        _invoker(db, agent_a.id),
        "mail_send",
        {"to": ["sales@example.com"], "subject": "对话发出", "body": "内容"},
    )
    sent = get_sent(tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner)
    subjects = {item.subject for item in sent.messages}
    assert subjects == {"控制台发出", "对话发出"}


def test_empty_configured_inbox_is_empty_state(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _transport = mail_db
    _configure(db, owner, agent_a.id)
    listing = get_inbox(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert listing.configured is True
    assert listing.messages == []
    assert listing.empty_reason == "收件箱是空的。"


def test_mail_tools_are_reserved_and_password_stays_out_of_message_read(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _transport = mail_db
    _configure(db, owner, agent_a.id, password="trace-secret")
    result = _compose(db, owner, agent_a.id)
    payload = message_read(
        db.get(EmployeeMailMessage, result.message.id)
    ).model_dump()
    assert "trace-secret" not in str(payload)
    manifest = CapabilityManifestBuilder(db).build("tenant_demo", agent_a.id, None, None)
    names = {item.name for item in manifest.available}
    assert {"mail_list", "mail_read", "mail_draft", "mail_send"} <= names
    assert "mail_send" in RESERVED_HARNESS_CAPABILITY_NAMES


def test_draft_upload_survives_confirm_send(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    drafted = send_or_draft(
        MailComposeRequest(
            tenant_id="tenant_demo",
            to=["sales@example.com"],
            subject="带附件草稿",
            body="请确认附件",
            as_draft=True,
            attachments=[
                MailAttachmentInput(
                    filename="报价.pdf",
                    content_base64=base64.b64encode(b"%PDF-draft").decode(),
                    content_type="application/pdf",
                )
            ],
        ),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert drafted.draft is True
    assert drafted.message.attachments[0].saved is True
    assert (drafted.message.attachments[0].cabinet_path or "").startswith(
        f"{MAIL_OUTBOUND_FOLDER}/"
    )
    confirmed = confirm_draft(
        drafted.message.id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert confirmed.delivered is True
    assert len(transport.sent) == 1
    outbound = list_folder(db, "tenant_demo", agent_a.id, MAIL_OUTBOUND_FOLDER, can_write=True)
    files = [item for item in outbound.entries if item.kind == "file"]
    assert [item.name for item in files] == ["报价.pdf"]


def test_console_can_send_pending_draft(mail_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), transport = mail_db
    _configure(db, owner, agent_a.id)
    drafted = invoke_mail_tool(
        _invoker(db, agent_a.id, confirm_before_send_mail=True),
        "mail_send",
        {"to": ["sales@example.com"], "subject": "待确认", "body": "内容"},
    )
    confirmed = confirm_draft(
        drafted["data"]["id"],
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert confirmed.delivered is True
    assert transport.sent

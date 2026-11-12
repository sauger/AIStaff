from dataclasses import replace

from sqlmodel import Session, select
from test_employee_mail import _configure, _inbox_item
from test_employee_mail import mail_db as mail_db  # noqa: PLC0414

from app.cabinet.paths import storage_path
from app.db.models import EmployeeCabinetEntry, EmployeeMailMessage, EmployeeMailTriageRule
from app.mail.api import get_inbox, get_reply_defaults, read_message
from app.mail.errors import MailError
from app.mail.header_query import ensure_message_body
from app.mail.inbound import set_inbound_skill_runner, set_mailbox_enabled, triage_all_pending
from app.mail.service import get_mailbox, get_message, sync_all_mailboxes
from app.mail.transport import FetchedAttachment


def seed(mail_db):
    db, (owner, other, _admin, employee, other_employee), transport = mail_db
    _configure(db, owner, employee.id)
    item = _inbox_item("701", "普通来信", minutes_ago=1)
    item.attachments = [FetchedAttachment("file.pdf", "application/pdf", b"%PDF-test")]
    transport.inbox = [item]
    return db, owner, other, employee, other_employee, transport


def test_web_list_reply_and_detail_load_only_what_is_needed(mail_db):
    db, owner, _other, employee, _, transport = seed(mail_db)
    listing = get_inbox(tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner)
    message = listing.messages[0]
    assert listing.headers_only and not message.body_loaded and not message.attachments_loaded
    assert message.content_state == "headers_only" and message.triage_state == "pending"
    assert transport.fetch_calls == transport.body_calls == transport.detail_calls == []
    assert db.exec(select(EmployeeCabinetEntry)).all() == []
    defaults = get_reply_defaults(
        message.id, tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner
    )
    assert "正文 701" in defaults["body"]
    assert transport.body_calls == ["701"] and transport.detail_calls == []
    detail = read_message(
        message.id, tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner
    )
    assert detail.body_loaded and detail.attachments_loaded and detail.attachments[0].saved
    assert transport.detail_calls == ["701"]
    assert (
        storage_path("tenant_demo", employee.id, detail.attachments[0].cabinet_path).read_bytes()
        == b"%PDF-test"
    )
    get_inbox(tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner)
    read_message(
        message.id, tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner
    )
    assert transport.body_calls == ["701"] and transport.detail_calls == ["701"]


def test_sync_is_header_only_and_triage_caches_text_without_files(mail_db):
    db, _owner, _, employee, _, transport = seed(mail_db)
    sync_all_mailboxes(db)
    sync_all_mailboxes(db)
    assert len(transport.header_calls) == 2
    assert transport.fetch_calls == transport.body_calls == transport.detail_calls == []
    triage_all_pending(db)
    row = db.exec(
        select(EmployeeMailMessage).where(EmployeeMailMessage.agent_id == employee.id)
    ).one()
    assert row.metadata_json["content_state"] == "body_only"
    assert row.triage_state == "awaiting_owner"
    assert transport.body_calls == ["701"] and transport.detail_calls == []
    assert db.exec(select(EmployeeCabinetEntry)).all() == []
    sync_all_mailboxes(db)
    triage_all_pending(db)
    assert transport.body_calls == ["701"] and transport.detail_calls == []


def test_sender_ignore_rule_needs_neither_text_nor_attachments(mail_db):
    db, _owner, _, employee, _, transport = seed(mail_db)
    db.add(
        EmployeeMailTriageRule(
            tenant_id="tenant_demo",
            agent_id=employee.id,
            from_address=transport.inbox[0].from_address,
            disposition="ignore",
            instruction="忽略这个发件人",
        )
    )
    db.commit()
    sync_all_mailboxes(db)
    triage_all_pending(db)
    row = db.exec(
        select(EmployeeMailMessage).where(EmployeeMailMessage.agent_id == employee.id)
    ).one()
    assert row.triage_disposition == "ignore"
    assert transport.fetch_calls == transport.body_calls == transport.detail_calls == []


def test_body_matching_skill_gets_complete_content_before_it_runs(mail_db):
    from test_employee_inbound_mail import _bind_skill

    db, _owner, _, employee, _, transport = seed(mail_db)
    _bind_skill(
        db,
        employee.id,
        runtime_config_json={"inbound_auto_run": True, "inbound_match_hint": "only-in-body"},
    )
    transport.inbox = [replace(transport.inbox[0], body_text="only-in-body actual message")]
    received = []

    def runner(db, agent, skill, message):
        received.append((message.body_text, message.attachments_json))

    set_inbound_skill_runner(runner)
    try:
        sync_all_mailboxes(db)
        assert received == [] and transport.detail_calls == []
        triage_all_pending(db)
        assert received[0][0] == "only-in-body actual message"
        assert received[0][1][0]["saved"] is True
        assert transport.body_calls == ["701"] and transport.detail_calls == ["701"]
        sync_all_mailboxes(db)
        triage_all_pending(db)
        assert len(received) == 1
    finally:
        set_inbound_skill_runner(None)


def test_sparse_cached_pages_follow_remote_page_not_local_offsets(mail_db):
    db, owner, _, employee, _, transport = seed(mail_db)
    transport.inbox = [_inbox_item(str(i), f"mail {i}", minutes_ago=50 - i) for i in range(1, 46)]
    listing = get_inbox(
        tenant_id="tenant_demo",
        agent_id=employee.id,
        db=db,
        current_user=owner,
        page=3,
        page_size=20,
    )
    assert listing.total == 45 and [m.subject for m in listing.messages] == [
        f"mail {i}" for i in range(5, 0, -1)
    ]
    assert transport.fetch_calls == transport.body_calls == transport.detail_calls == []


def test_one_broken_mailbox_does_not_block_header_sync_of_the_next(mail_db, monkeypatch):
    db, _owner, other, employee, other_employee, transport = seed(mail_db)
    _configure(db, other, other_employee.id)
    second = get_mailbox(db, "tenant_demo", other_employee.id)
    second.email_address = "agent-b@example.com"
    db.add(second)
    db.commit()
    broken_address = get_mailbox(db, "tenant_demo", employee.id).email_address
    real = transport.fetch_headers

    def headers(mailbox, **kwargs):
        if mailbox.email_address == broken_address:
            raise MailError("MAIL_IMAP_FAILED", "temporary failure")
        return real(mailbox, **kwargs)

    monkeypatch.setattr(transport, "fetch_headers", headers)
    sync_all_mailboxes(db)
    assert get_mailbox(db, "tenant_demo", employee.id).last_error == "temporary failure"
    assert get_mailbox(db, "tenant_demo", other_employee.id).last_error is None
    assert db.exec(
        select(EmployeeMailMessage).where(EmployeeMailMessage.agent_id == other_employee.id)
    ).all()
    assert transport.body_calls == transport.detail_calls == []


def test_disabled_mailbox_keeps_cached_text_without_network_reads(mail_db):
    db, owner, _, employee, _, transport = seed(mail_db)
    listing = get_inbox(tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner)
    row = db.get(EmployeeMailMessage, listing.messages[0].id)
    ensure_message_body(db, row)
    set_mailbox_enabled(db, get_mailbox(db, "tenant_demo", employee.id), enabled=False)
    detail = read_message(
        row.id, tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner
    )
    assert detail.body_text == "正文 701" and not detail.attachments_loaded
    assert transport.body_calls == ["701"] and transport.detail_calls == transport.seen_uids == []


def test_body_only_reader_does_not_downgrade_a_concurrent_full_read(mail_db, monkeypatch):
    db, owner, _, employee, _, transport = seed(mail_db)
    row_id = (
        get_inbox(tenant_id="tenant_demo", agent_id=employee.id, db=db, current_user=owner)
        .messages[0]
        .id
    )
    real = transport.fetch_body

    def concurrent(mailbox, uid):
        with Session(db.bind) as other_db:
            get_message(other_db, "tenant_demo", employee.id, row_id)
        return real(mailbox, uid)

    monkeypatch.setattr(transport, "fetch_body", concurrent)
    row = ensure_message_body(db, db.get(EmployeeMailMessage, row_id))
    assert row.metadata_json["content_state"] == "complete" and row.attachments_json[0]["saved"]


def test_polling_thread_survives_one_failed_cycle(mail_db, monkeypatch):
    from app.mail import poller

    db, *_ = mail_db
    syncs, triages = [], []

    def sync(_db):
        syncs.append(1)
        if len(syncs) == 1:
            raise RuntimeError("temporary database failure")
        poller.stop_mail_poller()

    monkeypatch.setattr(poller, "engine", db.bind)
    monkeypatch.setattr(poller, "_stopped", False)
    monkeypatch.setattr(poller, "sync_all_mailboxes", sync)
    monkeypatch.setattr(poller, "triage_all_pending", lambda _db: triages.append(1))
    monkeypatch.setattr(poller._wake, "wait", lambda seconds: False)
    poller.run_poller()
    assert len(syncs) == 2 and len(triages) == 1

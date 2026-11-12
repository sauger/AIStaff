from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.channels import service_intake
from app.channels.session_queue import WAITING_SESSION_KEY, defer_busy_inbound
from app.db.models import AgentEvent, ChannelInboundEvent, HarnessSessionLeaseRecord, utc_now


def setup_queue():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(HarnessSessionLeaseRecord(tenant_id="tenant", session_id="session", lease_owner="other-worker",
                                        lease_expires_at=utc_now() + timedelta(minutes=15)))
        event = ChannelInboundEvent(tenant_id="tenant", binding_id="binding", channel="wecom", event_id="message",
            payload_json={}, target_json={"to_user_id": "person"}, status="processing",
            processor_run_id=service_intake.current_processor_run_id())
        db.add(event)
        db.commit()
        return engine, event.id


def test_busy_session_is_parked_without_repeated_claims_and_recovers_after_release(monkeypatch):
    engine, event_id = setup_queue()
    with Session(engine) as db:
        assert defer_busy_inbound(db, event_id, "session")
        row = db.get(ChannelInboundEvent, event_id)
        assert row.status == "received" and row.processor_run_id is None
        assert row.target_json[WAITING_SESSION_KEY] == "session"
        assert len(db.exec(select(AgentEvent)).all()) == 1
    calls = []
    monkeypatch.setattr(service_intake, "_process_claimed_staged_inbound", lambda *args, **kwargs: calls.append(args) or True)
    for _ in range(50):
        assert service_intake.claim_staged_inbound(event_id, db_engine=engine) is False
    service_intake.run_staged_inbound_daemon(once=True, db_engine=engine)
    assert calls == []
    # The durable queue works across a processor restart, and needs no in-memory timer.
    monkeypatch.setattr(service_intake, "current_processor_run_id", lambda: "restarted-worker")
    with Session(engine) as db:
        db.exec(delete(HarnessSessionLeaseRecord))
        db.commit()
    assert service_intake.claim_staged_inbound(event_id, db_engine=engine) is True
    assert service_intake.claim_staged_inbound(event_id, db_engine=engine) is False
    with Session(engine) as db:
        assert db.get(ChannelInboundEvent, event_id).processor_run_id == "restarted-worker"
        assert len(db.exec(select(AgentEvent)).all()) == 1


def test_queue_is_fifo_and_does_not_block_other_sessions():
    engine, first_id = setup_queue()
    with Session(engine) as db:
        assert defer_busy_inbound(db, first_id, "session")
        later = ChannelInboundEvent(tenant_id="tenant", binding_id="binding", channel="wecom", event_id="later",
            payload_json={}, target_json={WAITING_SESSION_KEY: "session"}, status="received",
            created_at=utc_now() + timedelta(seconds=1))
        other = ChannelInboundEvent(tenant_id="tenant", binding_id="binding", channel="wecom", event_id="other",
            payload_json={}, target_json={WAITING_SESSION_KEY: "other-session"}, status="received")
        db.add(later); db.add(other); db.commit()
        later_id, other_id = later.id, other.id
        db.exec(delete(HarnessSessionLeaseRecord)); db.commit()
    assert service_intake.claim_staged_inbound(later_id, db_engine=engine) is False
    assert service_intake.claim_staged_inbound(other_id, db_engine=engine) is True
    assert service_intake.claim_staged_inbound(first_id, db_engine=engine) is True
    assert service_intake.claim_staged_inbound(later_id, db_engine=engine) is False
    with Session(engine) as db:
        first = db.get(ChannelInboundEvent, first_id); first.status = "done"; db.add(first); db.commit()
    assert service_intake.claim_staged_inbound(later_id, db_engine=engine) is True


def test_race_without_visible_lease_uses_delayed_retry_not_a_hot_loop():
    engine, event_id = setup_queue()
    with Session(engine) as db:
        db.exec(delete(HarnessSessionLeaseRecord)); db.commit()
        assert defer_busy_inbound(db, event_id, "session", force=True)
    assert service_intake.claim_staged_inbound(event_id, db_engine=engine) is False
    with Session(engine) as db:
        row = db.get(ChannelInboundEvent, event_id)
        row.processor_lease_expires_at = utc_now() - timedelta(seconds=1)
        db.add(row); db.commit()
    assert service_intake.claim_staged_inbound(event_id, db_engine=engine) is True

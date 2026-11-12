"""Durable admission checks: wait on session leases without rerunning AgentLoop."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, exists, or_, update
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.db.models import AgentEvent, ChannelInboundEvent, HarnessSessionLeaseRecord, utc_now

WAITING_SESSION_KEY = "_staffdeck_waiting_session_id"


def inbound_ready_condition():
    now = utc_now()
    session_id = ChannelInboundEvent.target_json[WAITING_SESSION_KEY].as_string()
    occupied = exists(select(HarnessSessionLeaseRecord.id).where(
        HarnessSessionLeaseRecord.tenant_id == ChannelInboundEvent.tenant_id,
        HarnessSessionLeaseRecord.session_id == session_id,
        HarnessSessionLeaseRecord.lease_expires_at > now,
    )).correlate(ChannelInboundEvent)
    earlier = aliased(ChannelInboundEvent)
    ahead = exists(select(earlier.id).where(
        earlier.tenant_id == ChannelInboundEvent.tenant_id,
        earlier.target_json[WAITING_SESSION_KEY].as_string() == session_id,
        earlier.status.in_({"received", "processing"}),
        or_(earlier.created_at < ChannelInboundEvent.created_at,
            and_(earlier.created_at == ChannelInboundEvent.created_at,
                 earlier.id < ChannelInboundEvent.id)),
    )).correlate(ChannelInboundEvent)
    return and_(
        or_(ChannelInboundEvent.processor_lease_expires_at.is_(None),
            ChannelInboundEvent.processor_lease_expires_at <= now),
        or_(session_id.is_(None), and_(~occupied, ~ahead)),
    )


def defer_busy_inbound(db, event_id: str, session_id: str, *, force: bool = False, inbound=None) -> bool:
    from app.channels.service_intake import current_processor_run_id

    event = db.get(ChannelInboundEvent, event_id)
    if event is None:
        return False
    now = utc_now()
    busy = db.exec(select(HarnessSessionLeaseRecord.id).where(
        HarnessSessionLeaseRecord.tenant_id == event.tenant_id,
        HarnessSessionLeaseRecord.session_id == session_id,
        HarnessSessionLeaseRecord.lease_expires_at > now,
    )).first()
    ahead = db.exec(select(ChannelInboundEvent.id).where(
        ChannelInboundEvent.tenant_id == event.tenant_id,
        ChannelInboundEvent.id != event.id,
        ChannelInboundEvent.target_json[WAITING_SESSION_KEY].as_string() == session_id,
        ChannelInboundEvent.status.in_({"received", "processing"}),
        or_(ChannelInboundEvent.created_at < event.created_at,
            and_(ChannelInboundEvent.created_at == event.created_at,
                 ChannelInboundEvent.id < event.id)),
    )).first()
    if not force and not busy and not ahead:
        return False
    target = dict(event.target_json or {})
    first_wait = target.get(WAITING_SESSION_KEY) != session_id
    target[WAITING_SESSION_KEY] = session_id
    payload = event.payload_json
    if (event.channel == "wecom" and inbound is not None
            and (not isinstance(payload, dict) or payload.get("schema_version") != 1)):
        from app.channels.service_wecom_inbox import encode_wecom_replay_envelope

        payload = encode_wecom_replay_envelope(inbound, account_scope=inbound.account_scope)
    result = db.exec(update(ChannelInboundEvent).where(
        ChannelInboundEvent.id == event_id,
        ChannelInboundEvent.status == "processing",
        ChannelInboundEvent.processor_run_id == current_processor_run_id(),
    ).values(
        status="received", processor_run_id=None,
        processor_lease_expires_at=(now + timedelta(seconds=2) if force else None),
        target_json=target, payload_json=payload, error="HARNESS_SESSION_QUEUED", updated_at=now,
    ).execution_options(synchronize_session=False))
    if result.rowcount == 1 and first_wait:
        db.add(AgentEvent(
            tenant_id=event.tenant_id, session_id=session_id,
            event_type="channel_inbound_waiting_session",
            payload_json={"client_turn_id": event.event_id, "event_id": event.id,
                          "reason": "session_busy", "queued": True},
        ))
    db.commit()
    db.expire_all()
    return result.rowcount == 1

"""U5: 提交、合规、审计、回调验签与写回。"""

from __future__ import annotations

import base64
import hashlib
import struct
import time

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.wecom_oa as wecom_oa_api
from app.db.models import (
    AgentProfile,
    ChannelIdentity,
    Message,
    Tenant,
    WecomApprovalIntent,
    WecomApprovalStatusEvent,
)
from app.wecom_oa.callback import apply_status_update, verify_callback
from app.wecom_oa.credentials import store_oa_credential
from app.wecom_oa.identity import upsert_temp_binding
from app.wecom_oa.service import preview_approval, save_agent_approval_config, submit_approval


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Client:
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        return self._handler("GET", url, params, None)

    def post(self, url, params=None, json=None):
        return self._handler("POST", url, params, json)


def _encrypt(plaintext: str, aes_key: str, receive_id: str) -> str:
    key = base64.b64decode(aes_key + "=")
    payload = b"0123456789abcdef" + struct.pack("!I", len(plaintext.encode()))
    payload += plaintext.encode() + receive_id.encode()
    padding = 32 - len(payload) % 32
    payload += bytes((padding,)) * padding
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(payload) + encryptor.finalize()).decode()


def _signature(token: str, timestamp: str, nonce: str, ciphertext: str) -> str:
    return hashlib.sha1(
        "".join(sorted((token, timestamp, nonce, ciphertext))).encode()
    ).hexdigest()


def _seed_mode_b(db: Session, *, compliance=False):
    db.add(Tenant(id="t1", name="T"))
    db.add(AgentProfile(id="agent_1", tenant_id="t1", name="Bot"))
    aes_key = base64.b64encode(bytes(range(32))).decode().rstrip("=")
    store_oa_credential(
        db,
        tenant_id="t1",
        corp_id="wwcorp",
        secret="sec",
        callback_token="callback-token",
        callback_aes_key=aes_key,
    )
    upsert_temp_binding(
        db,
        tenant_id="t1",
        corp_scope="wwcorp",
        staffdeck_user_id="user_partner",
        wecom_userid="partner_wx",
        display_name="对方",
    )
    save_agent_approval_config(
        db,
        tenant_id="t1",
        agent_id="agent_1",
        config={
            "template_id": "tpl_b",
            "scenario_key": "default",
            "applicant_mode": "chat_partner",
            "field_mappings": [
                {
                    "control_id": "Text-1",
                    "widget_type": "Text",
                    "slot_key": "reason",
                    "title": "事由",
                }
            ],
            "compliance_enabled": compliance,
            "compliance_policy_ref": "pol_1" if compliance else None,
        },
    )
    return aes_key


def test_mode_b_uses_resolved_userid_and_audit():
    engine = _engine()
    apply_bodies = []

    def handler(method, url, params, json):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        if "applyevent" in url:
            apply_bodies.append(json)
            return _FakeResponse({"errcode": 0, "sp_no": "SPB1"})
        if "getapprovaldetail" in url:
            return _FakeResponse({"errcode": 0, "info": {"sp_status": 1}})
        raise AssertionError(url)

    with Session(engine) as db:
        _seed_mode_b(db)
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_b",
            agent_id="agent_1",
            user_id="user_partner",
            scenario_key="default",
            fields={"reason": "代提"},
        )
        assert preview["summary"]["creator_userid"] == "partner_wx"
        out = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_b",
            agent_id="agent_1",
            user_id="user_partner",
            intent_id=preview["intent_id"],
            confirmed_digest=preview["submission_digest"],
            client_factory=lambda: _Client(handler),
        )
        assert out["ok"] is True
        assert apply_bodies[0]["creator_userid"] == "partner_wx"
        assert out["audit"]["creator_userid"] == "partner_wx"
        assert out["audit"]["user_id"] == "user_partner"
        assert out["audit"]["session_id"] == "sess_b"


def test_compliance_failure_skips_applyevent():
    engine = _engine()
    calls = []

    def handler(method, url, params, json):
        calls.append(url)
        raise AssertionError("must not call wecom")

    with Session(engine) as db:
        _seed_mode_b(db, compliance=True)
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_c",
            agent_id="agent_1",
            user_id="user_partner",
            scenario_key="default",
            fields={"reason": "不合规"},
        )

        def reject_checker(*a, **k):
            return False, "金额超限"

        out = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_c",
            agent_id="agent_1",
            user_id="user_partner",
            intent_id=preview["intent_id"],
            confirmed_digest=preview["submission_digest"],
            client_factory=lambda: _Client(handler),
            compliance_checker=reject_checker,
        )
        assert out["ok"] is False
        assert out["error_code"] == "WECOM_OA_COMPLIANCE_FAILED"
        assert out["status"] == "failed"
        assert calls == []
        intent = db.get(WecomApprovalIntent, preview["intent_id"])
        assert intent.sp_no is None
        msgs = db.exec(select(Message).where(Message.session_id == "sess_c")).all()
        assert any("合规" in m.content for m in msgs)


def test_applyevent_failure_clear_message_not_submitted():
    engine = _engine()

    def handler(method, url, params, json):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        return _FakeResponse({"errcode": 60011, "errmsg": "not in party"})

    with Session(engine) as db:
        _seed_mode_b(db)
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_f",
            agent_id="agent_1",
            user_id="user_partner",
            scenario_key="default",
            fields={"reason": "x"},
        )
        out = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_f",
            agent_id="agent_1",
            user_id="user_partner",
            intent_id=preview["intent_id"],
            confirmed_digest=preview["submission_digest"],
            client_factory=lambda: _Client(handler),
        )
        assert out["ok"] is False
        assert out["status"] == "failed"
        assert "失败" in out["error"]
        intent = db.get(WecomApprovalIntent, preview["intent_id"])
        assert intent.status == "failed"
        assert intent.sp_no is None


def test_callback_verified_writes_session_tampered_does_not():
    engine = _engine()
    aes_key = None
    with Session(engine) as db:
        aes_key = _seed_mode_b(db)
        # 造一条已提交意图
        from app.wecom_oa.intent import create_draft_intent, mark_submitted, mark_submitting, confirm_intent

        intent = create_draft_intent(
            db,
            tenant_id="t1",
            session_id="sess_cb",
            agent_id="agent_1",
            user_id="user_partner",
            corp_id="wwcorp",
            template_id="tpl_b",
            scenario_key="default",
            applicant_mode="chat_partner",
            creator_userid="partner_wx",
            creator_display_name="对方",
            fields={"reason": "x"},
        )
        confirm_intent(
            db,
            intent_id=intent.id,
            tenant_id="t1",
            expected_digest=intent.submission_digest,
        )
        intent = db.get(WecomApprovalIntent, intent.id)
        intent = mark_submitting(db, intent)
        mark_submitted(db, intent, sp_no="SPCB1", audit={})

    token = "callback-token"
    corp_id = "wwcorp"
    xml = (
        "<xml><Event>sys_approval_change</Event>"
        "<ApprovalInfo><SpNo>SPCB1</SpNo><SpStatus>2</SpStatus></ApprovalInfo></xml>"
    )
    ciphertext = _encrypt(xml, aes_key, corp_id)
    timestamp = str(int(time.time()))
    nonce = "nonce"
    sig = _signature(token, timestamp, nonce, ciphertext)

    # mount callback router with patched engine
    import app.api.wecom_oa as api

    monkey_engine = engine
    api.engine = monkey_engine  # type: ignore
    app = FastAPI()
    app.include_router(api.callback_router)
    client = TestClient(app)

    bad = client.post(
        "/api/public/wecom-oa/callback/t1",
        params={
            "msg_signature": "invalid",
            "timestamp": timestamp,
            "nonce": nonce,
        },
        content=f"<xml><Encrypt>{ciphertext}</Encrypt></xml>",
        headers={"Content-Type": "application/xml"},
    )
    assert bad.status_code == 403

    with Session(engine) as db:
        assert (
            db.exec(
                select(WecomApprovalStatusEvent).where(
                    WecomApprovalStatusEvent.sp_no == "SPCB1"
                )
            ).first()
            is None
        )

    ok = client.post(
        "/api/public/wecom-oa/callback/t1",
        params={
            "msg_signature": sig,
            "timestamp": timestamp,
            "nonce": nonce,
        },
        content=f"<xml><Encrypt>{ciphertext}</Encrypt></xml>",
        headers={"Content-Type": "application/xml"},
    )
    assert ok.status_code == 200
    assert ok.text == "success"

    with Session(engine) as db:
        evt = db.exec(
            select(WecomApprovalStatusEvent).where(
                WecomApprovalStatusEvent.sp_no == "SPCB1"
            )
        ).first()
        assert evt is not None
        assert evt.sp_status == "2"
        assert evt.source == "callback"
        msgs = db.exec(select(Message).where(Message.session_id == "sess_cb")).all()
        assert any(
            (m.metadata_json or {}).get("wecom_oa_approval_progress") for m in msgs
        )


def test_unknown_sp_no_does_not_write_session():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.commit()
        event = apply_status_update(
            db,
            sp_no="UNKNOWN",
            sp_status="2",
            change_event="sys_approval_change",
            source="callback",
            tenant_id="t1",
        )
        assert event is None
        assert db.exec(select(Message)).first() is None


def test_query_and_callback_idempotent_same_status():
    engine = _engine()
    with Session(engine) as db:
        _seed_mode_b(db)
        from app.wecom_oa.intent import create_draft_intent, confirm_intent, mark_submitted, mark_submitting

        intent = create_draft_intent(
            db,
            tenant_id="t1",
            session_id="sess_idemp",
            agent_id="agent_1",
            user_id="user_partner",
            corp_id="wwcorp",
            template_id="tpl_b",
            scenario_key="default",
            applicant_mode="chat_partner",
            creator_userid="partner_wx",
            creator_display_name="对方",
            fields={"reason": "x"},
        )
        confirm_intent(
            db,
            intent_id=intent.id,
            tenant_id="t1",
            expected_digest=intent.submission_digest,
        )
        intent = db.get(WecomApprovalIntent, intent.id)
        intent = mark_submitting(db, intent)
        mark_submitted(db, intent, sp_no="SPID", audit={})
        e1 = apply_status_update(
            db,
            sp_no="SPID",
            sp_status="1",
            change_event="same",
            source="query",
            tenant_id="t1",
        )
        e2 = apply_status_update(
            db,
            sp_no="SPID",
            sp_status="1",
            change_event="same",
            source="callback",
            tenant_id="t1",
        )
        assert e1 is not None and e2 is not None
        assert e1.id == e2.id

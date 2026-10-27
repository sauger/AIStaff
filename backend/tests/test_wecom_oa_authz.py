"""Authz regressions: session ACL for chat OA routes; agent manage for approval-config."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.api.wecom_oa as wecom_oa_api
from app.db import get_session
from app.db.models import (
    AgentProfile,
    ChatSession,
    Tenant,
    User,
    WecomApprovalIntent,
)
from app.security.auth import create_access_token
from app.wecom_oa.identity import resolve_wecom_userid
from app.db.models import ChannelIdentity


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _client(engine) -> TestClient:
    app = FastAPI()
    app.include_router(wecom_oa_api.router)
    app.include_router(wecom_oa_api.chat_router)

    def override_session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _seed_session_peer(db: Session):
    db.add(Tenant(id="t1", name="T"))
    owner = User(
        id="user_owner", tenant_id="t1", username="owner", role="member", password_hash="x"
    )
    peer = User(
        id="user_peer", tenant_id="t1", username="peer", role="member", password_hash="x"
    )
    db.add_all([owner, peer])
    db.add(
        ChatSession(
            id="sess_owner",
            tenant_id="t1",
            user_id="user_owner",
            agent_id="agent_1",
            title="owner session",
        )
    )
    db.add(AgentProfile(id="agent_1", tenant_id="t1", name="Bot"))
    intent = WecomApprovalIntent(
        id="woaint_1",
        tenant_id="t1",
        session_id="sess_owner",
        agent_id="agent_1",
        user_id="user_owner",
        status="draft",
        template_id="tpl",
        scenario_key="default",
        applicant_mode="fixed",
        creator_userid="fixed_user",
        payload_json={"fields": {"reason": "x"}, "template_id": "tpl"},
        submission_digest="digest_secret_abc",
        corp_id="wwcorp",
    )
    db.add(intent)
    db.commit()
    db.refresh(owner)
    db.refresh(peer)
    db.expunge(owner)
    db.expunge(peer)
    return owner, peer, intent


def test_peer_cannot_list_session_intents_or_see_digest():
    engine = _engine()
    with Session(engine) as db:
        owner, peer, _intent = _seed_session_peer(db)
    client = _client(engine)

    owner_resp = client.get(
        "/api/chat/wecom-oa/sessions/sess_owner/intents",
        params={"tenant_id": "t1"},
        headers=_auth(owner),
    )
    assert owner_resp.status_code == 200
    assert owner_resp.json()["intents"][0]["submission_digest"] == "digest_secret_abc"

    peer_resp = client.get(
        "/api/chat/wecom-oa/sessions/sess_owner/intents",
        params={"tenant_id": "t1"},
        headers=_auth(peer),
    )
    assert peer_resp.status_code == 404
    body = peer_resp.json()
    # Must not leak digest even in error payloads
    assert "digest_secret_abc" not in peer_resp.text
    assert "submission_digest" not in body


def test_peer_cannot_confirm_or_dismiss_foreign_session_intent():
    engine = _engine()
    with Session(engine) as db:
        _owner, peer, intent = _seed_session_peer(db)
        intent_id = intent.id
        digest = intent.submission_digest
    client = _client(engine)

    confirm = client.post(
        "/api/chat/wecom-oa/confirm",
        params={
            "tenant_id": "t1",
            "session_id": "sess_owner",
            "agent_id": "agent_1",
        },
        json={
            "intent_id": intent_id,
            "confirmed_digest": digest,
            "submit": False,
        },
        headers=_auth(peer),
    )
    assert confirm.status_code == 404

    dismiss = client.post(
        "/api/chat/wecom-oa/dismiss",
        params={"tenant_id": "t1", "intent_id": intent_id},
        headers=_auth(peer),
    )
    assert dismiss.status_code == 404

    with Session(engine) as db:
        row = db.get(WecomApprovalIntent, intent_id)
        assert row is not None
        assert row.status == "draft"


def test_non_manager_cannot_put_approval_config():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        owner = User(
            id="user_owner",
            tenant_id="t1",
            username="owner",
            role="member",
            password_hash="x",
        )
        other = User(
            id="user_other",
            tenant_id="t1",
            username="other",
            role="member",
            password_hash="x",
        )
        db.add_all([owner, other])
        db.add(
            AgentProfile(
                id="agent_owned",
                tenant_id="t1",
                name="Owned",
                metadata_json={
                    "owner_user_id": "user_owner",
                    "owner_username": "owner",
                },
            )
        )
        db.commit()
        db.refresh(owner)
        db.refresh(other)
        db.expunge(owner)
        db.expunge(other)

    client = _client(engine)
    payload = {
        "template_id": "tpl_x",
        "scenario_key": "default",
        "applicant_mode": "fixed",
        "mode_a_userid": "stolen",
        "field_mappings": [
            {
                "control_id": "Text-1",
                "widget_type": "Text",
                "slot_key": "reason",
                "title": "事由",
            }
        ],
        "compliance_enabled": False,
    }
    denied = client.put(
        "/api/enterprise/wecom-oa/agents/agent_owned/approval-config",
        params={"tenant_id": "t1"},
        json=payload,
        headers=_auth(other),
    )
    assert denied.status_code == 403

    ok = client.put(
        "/api/enterprise/wecom-oa/agents/agent_owned/approval-config",
        params={"tenant_id": "t1"},
        json=payload,
        headers=_auth(owner),
    )
    assert ok.status_code == 200
    assert ok.json()["config"]["mode_a_userid"] == "stolen"


def test_corp_scope_prefix_collision_does_not_bind():
    """corp_scope 'ww' must not match ChannelIdentity scope 'wwcorp'."""
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.add(
            ChannelIdentity(
                tenant_id="t1",
                channel="wecom",
                external_account_scope="wwcorp",
                external_user_id="wrong_user",
                staffdeck_user_id="user_1",
            )
        )
        db.commit()
        assert (
            resolve_wecom_userid(
                db, tenant_id="t1", staffdeck_user_id="user_1", corp_scope="ww"
            )
            is None
        )
        hit = resolve_wecom_userid(
            db, tenant_id="t1", staffdeck_user_id="user_1", corp_scope="wwcorp"
        )
        assert hit is not None
        assert hit.wecom_userid == "wrong_user"


def test_corp_scope_allows_delimiter_suffix():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.add(
            ChannelIdentity(
                tenant_id="t1",
                channel="wecom",
                external_account_scope="wwcorp:bot123",
                external_user_id="zhangsan",
                staffdeck_user_id="user_1",
            )
        )
        db.commit()
        hit = resolve_wecom_userid(
            db, tenant_id="t1", staffdeck_user_id="user_1", corp_scope="wwcorp"
        )
        assert hit is not None
        assert hit.wecom_userid == "zhangsan"

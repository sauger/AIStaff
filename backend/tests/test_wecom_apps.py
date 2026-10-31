"""企微自建应用：迁移、CRUD、默认应用、自检与回调。"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import time

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.wecom_apps as wecom_apps_api
import app.api.wecom_oa as wecom_oa_api
from app.db import get_session
from app.db.database import migrate_wecom_apps
from app.db.models import (
    AgentProfile,
    Tenant,
    User,
    WecomApp,
    WecomApprovalIntent,
    WecomOaAppCredential,
)
from app.security.auth import create_access_token
from app.wecom_oa.apps import (
    WecomAppError,
    callback_url_fields,
    check_wecom_app,
    create_wecom_app,
    resolve_default_oa_app,
)
from app.wecom_oa.service import (
    preview_approval,
    save_agent_approval_config,
    submit_approval,
)


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _client(engine) -> TestClient:
    app = FastAPI()
    app.include_router(wecom_apps_api.router)
    app.include_router(wecom_apps_api.callback_router)
    app.include_router(wecom_oa_api.router)
    app.include_router(wecom_oa_api.callback_router)
    wecom_apps_api.engine = engine
    wecom_oa_api.engine = engine

    def override_session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def _users(db: Session) -> tuple[User, User]:
    db.add(Tenant(id="t1", name="T"))
    admin = User(
        id="user_admin",
        tenant_id="t1",
        username="admin",
        role="admin",
        password_hash="x",
    )
    member = User(
        id="user_member",
        tenant_id="t1",
        username="member",
        role="member",
        password_hash="x",
    )
    db.add(admin)
    db.add(member)
    db.commit()
    db.refresh(admin)
    db.refresh(member)
    db.expunge(admin)
    db.expunge(member)
    return admin, member


def _insert_legacy(connection, **overrides) -> None:
    values = {
        "id": "woaapp_legacy",
        "tenant_id": "t1",
        "corp_id": "ww_legacy",
        "agentid": None,
        "secret_enc": "ciphertext-secret",
        "callback_token_enc": "ciphertext-token",
        "callback_aes_key_enc": "ciphertext-aes",
        "status": "active",
        "created_by_user_id": "user_admin",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-02T00:00:00",
    }
    values.update(overrides)
    connection.execute(
        text(
            "INSERT INTO wecom_oa_app_credentials ("
            "id, tenant_id, corp_id, agentid, secret_enc, callback_token_enc, "
            "callback_aes_key_enc, status, created_by_user_id, created_at, updated_at"
            ") VALUES ("
            ":id, :tenant_id, :corp_id, :agentid, :secret_enc, :callback_token_enc, "
            ":callback_aes_key_enc, :status, :created_by_user_id, :created_at, :updated_at"
            ")"
        ),
        values,
    )


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        return self._handler(url, params or {})


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


def test_callback_url_uses_public_base_and_skips_localhost(monkeypatch):
    class _Settings:
        def __init__(self, base: str):
            self.normalized_public_base_url = base

    monkeypatch.setattr(
        "app.wecom_oa.apps.get_settings",
        lambda: _Settings("http://localhost:8000"),
    )
    local = callback_url_fields("tenant_demo", "wapp_x")
    assert local["callback_url_absolute"] is False
    assert local["callback_url"] == "/api/public/wecom-app/callback/tenant_demo/wapp_x"
    assert "localhost" not in local["callback_url"]

    monkeypatch.setattr(
        "app.wecom_oa.apps.get_settings",
        lambda: _Settings("https://aistaffdemo.muli.group"),
    )
    public = callback_url_fields("tenant_demo", "wapp_x")
    assert public["callback_url_absolute"] is True
    assert public["callback_url"] == (
        "https://aistaffdemo.muli.group/api/public/wecom-app/callback/tenant_demo/wapp_x"
    )


def test_migration_copies_ciphertext_and_sets_one_default():
    engine = _engine()
    with engine.begin() as connection:
        _insert_legacy(connection, id="woaapp_old", updated_at="2026-01-01T00:00:00")
        _insert_legacy(
            connection,
            id="woaapp_new",
            corp_id="ww_other",
            secret_enc="ciphertext-newer",
            updated_at="2026-06-01T00:00:00",
        )
        _insert_legacy(
            connection,
            id="woaapp_off",
            corp_id="ww_off",
            status="disabled",
            updated_at="2026-07-01T00:00:00",
        )
        migrate_wecom_apps(connection)
        migrate_wecom_apps(connection)

    with Session(engine) as db:
        rows = db.exec(select(WecomApp).order_by(WecomApp.legacy_oa_credential_id)).all()
        assert len(rows) == 3
        by_legacy = {row.legacy_oa_credential_id: row for row in rows}
        older = by_legacy["woaapp_old"]
        assert older.secret_enc == "ciphertext-secret"
        assert older.callback_token_enc == "ciphertext-token"
        assert older.agentid == ""
        assert older.name == "企业微信应用 ww_legacy"
        assert older.id.startswith("wapp_")
        assert older.is_default is False
        newer = by_legacy["woaapp_new"]
        assert newer.secret_enc == "ciphertext-newer"
        assert newer.is_default is True
        assert by_legacy["woaapp_off"].is_default is False
        legacy = db.exec(select(WecomOaAppCredential)).all()
        assert len(legacy) == 3


def test_migration_tie_breaks_on_legacy_id_and_keeps_existing_default():
    engine = _engine()
    with engine.begin() as connection:
        _insert_legacy(
            connection,
            id="woaapp_b",
            corp_id="ww_b",
            updated_at="2026-03-01T00:00:00",
        )
        _insert_legacy(
            connection,
            id="woaapp_a",
            corp_id="ww_a",
            secret_enc="cipher-a",
            updated_at="2026-03-01T00:00:00",
        )
        migrate_wecom_apps(connection)
    with Session(engine) as db:
        chosen = db.exec(
            select(WecomApp).where(WecomApp.is_default == True)  # noqa: E712
        ).one()
        assert chosen.legacy_oa_credential_id == "woaapp_a"

    with Session(engine) as db:
        manual = WecomApp(
            tenant_id="t2",
            name="手工",
            corp_id="ww_manual",
            agentid="9",
            secret_enc="kept",
            is_default=True,
            status="active",
        )
        db.add(manual)
        db.commit()
    with engine.begin() as connection:
        _insert_legacy(
            connection,
            id="woaapp_t2",
            tenant_id="t2",
            corp_id="ww_t2",
            updated_at="2026-08-01T00:00:00",
        )
        migrate_wecom_apps(connection)
    with Session(engine) as db:
        defaults = db.exec(
            select(WecomApp).where(
                WecomApp.tenant_id == "t2",
                WecomApp.is_default == True,  # noqa: E712
            )
        ).all()
        assert len(defaults) == 1
        assert defaults[0].name == "手工"
        copied = db.exec(
            select(WecomApp).where(WecomApp.legacy_oa_credential_id == "woaapp_t2")
        ).one()
        assert copied.is_default is False


def test_migration_stops_on_duplicate_blank_agentid():
    engine = _engine()
    with Session(engine) as db:
        db.add(
            WecomApp(
                tenant_id="t1",
                name="已有空 AgentId",
                corp_id="ww_legacy",
                agentid="",
                secret_enc="existing",
                status="active",
            )
        )
        db.commit()
    with pytest.raises(RuntimeError, match="唯一约束"), engine.begin() as connection:
        _insert_legacy(connection, id="woaapp_blank", agentid=None)
        migrate_wecom_apps(connection)
    with Session(engine) as db:
        rows = db.exec(select(WecomApp)).all()
        assert len(rows) == 1
        assert rows[0].legacy_oa_credential_id is None


def test_partial_unique_index_allows_only_one_default():
    engine = _engine()
    with Session(engine) as db:
        db.add(
            WecomApp(
                tenant_id="t1",
                name="A",
                corp_id="ww",
                agentid="1",
                secret_enc="enc",
                is_default=False,
            )
        )
        db.add(
            WecomApp(
                tenant_id="t1",
                name="B",
                corp_id="ww",
                agentid="2",
                secret_enc="enc",
                is_default=False,
            )
        )
        db.commit()
    with engine.begin() as connection:
        migrate_wecom_apps(connection)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE wecom_apps SET is_default = 1"))


def test_crud_default_overlap_and_secret_is_not_returned():
    engine = _engine()
    with Session(engine) as db:
        admin, member = _users(db)
    client = _client(engine)
    secret = "plain-corp-secret"
    denied = client.get(
        "/api/enterprise/wecom-apps",
        params={"tenant_id": "t1"},
        headers=_auth(member),
    )
    assert denied.status_code == 403

    empty = client.get(
        "/api/enterprise/wecom-apps",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert empty.status_code == 200
    assert empty.json() == {"apps": []}

    created = client.post(
        "/api/enterprise/wecom-apps",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={
            "name": "审批",
            "corp_id": "ww_a",
            "agentid": "100",
            "secret": secret,
            "callback_token": "callback-token-plain",
        },
    )
    assert created.status_code == 400

    created = client.post(
        "/api/enterprise/wecom-apps",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={
            "name": "审批",
            "corp_id": "ww_a",
            "agentid": "100",
            "secret": secret,
            "callback_token": "callback-token-plain",
            "callback_aes_key": "aes-key-plain",
        },
    )
    assert created.status_code == 200
    body = created.json()["app"]
    assert body["is_default"] is True
    assert body["has_secret"] is True
    assert body["has_callback_token"] is True
    assert secret not in created.text
    assert "callback-token-plain" not in created.text
    assert "aes-key-plain" not in created.text
    assert body["callback_url"].endswith(f"/api/public/wecom-app/callback/t1/{body['id']}")
    app_id = body["id"]

    overlap = client.post(
        "/api/enterprise/wecom-apps",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"name": "重复", "corp_id": "ww_a", "agentid": "100", "secret": "other"},
    )
    assert overlap.status_code == 409

    second = client.post(
        "/api/enterprise/wecom-apps",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"name": "人事", "corp_id": "ww_a", "agentid": "200", "secret": "other-secret"},
    )
    assert second.status_code == 200
    second_id = second.json()["app"]["id"]
    assert second.json()["app"]["is_default"] is False

    renamed = client.patch(
        f"/api/enterprise/wecom-apps/{app_id}",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"name": "审批改名"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["app"]["name"] == "审批改名"
    with Session(engine) as db:
        stored = db.get(WecomApp, app_id)
        assert stored is not None
        assert secret not in stored.secret_enc

    cleared = client.patch(
        f"/api/enterprise/wecom-apps/{app_id}",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"secret": ""},
    )
    assert cleared.status_code == 400

    made_default = client.post(
        f"/api/enterprise/wecom-apps/{second_id}/default",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert made_default.status_code == 200
    assert made_default.json()["app"]["is_default"] is True

    blocked = client.delete(
        f"/api/enterprise/wecom-apps/{second_id}",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert blocked.status_code == 409

    removed = client.delete(
        f"/api/enterprise/wecom-apps/{app_id}",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert removed.status_code == 204
    last = client.delete(
        f"/api/enterprise/wecom-apps/{second_id}",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert last.status_code == 204
    listed = client.get(
        "/api/enterprise/wecom-apps",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert listed.json()["apps"] == []


def test_legacy_credential_api_is_read_only_projection():
    engine = _engine()
    with Session(engine) as db:
        admin, _member = _users(db)
        create_wecom_app(
            db,
            tenant_id="t1",
            name="审批",
            corp_id="ww_a",
            agentid="100",
            secret="secret-value",
        )
    client = _client(engine)
    projected = client.get(
        "/api/enterprise/wecom-oa/credentials",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert projected.status_code == 200
    credential = projected.json()["credential"]
    assert credential["id"].startswith("wapp_")
    assert credential["corp_id"] == "ww_a"
    assert "secret-value" not in projected.text

    rejected = client.put(
        "/api/enterprise/wecom-oa/credentials",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"corp_id": "ww_a", "secret": "new"},
    )
    assert rejected.status_code == 409
    assert "/enterprise/company/wecom-apps" in rejected.json()["detail"]


def test_check_records_result_and_hides_secrets(caplog):
    engine = _engine()
    secret = "super-secret-value"
    token_value = "access-token-value"

    def handler(url, params):
        if "gettoken" in url:
            assert params["corpsecret"] == secret
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": token_value, "expires_in": 7200}
            )
        assert "agent/get" in url
        assert params["access_token"] == token_value
        assert params["agentid"] == "100"
        return _FakeResponse({"errcode": 0, "errmsg": "ok", "name": "审批应用"})

    with Session(engine) as db:
        row = create_wecom_app(
            db,
            tenant_id="t1",
            name="审批",
            corp_id="ww",
            agentid="100",
            secret=secret,
        )
        result = check_wecom_app(db, row, client_factory=lambda: _FakeClient(handler))
        assert result["ok"] is True
        assert result["summary"] == "已连通"
        assert result["agent_name"] == "审批应用"
        assert secret not in json.dumps(result)
        assert token_value not in json.dumps(result)
        stored = db.get(WecomApp, row.id)
        assert stored is not None
        assert stored.last_check_ok is True
        assert stored.last_check_summary == "已连通"

        stored.agentid = ""
        db.add(stored)
        db.commit()
        with pytest.raises(WecomAppError):
            check_wecom_app(
                db,
                stored,
                client_factory=lambda: (_ for _ in ()).throw(AssertionError("no http")),
            )

    def fail_handler(url, params):
        if "gettoken" in url:
            return _FakeResponse({"errcode": 40001, "errmsg": f"invalid {secret}"})
        raise AssertionError(url)

    with Session(engine) as db:
        row = db.exec(select(WecomApp)).one()
        row.agentid = "100"
        db.add(row)
        db.commit()
        failed = check_wecom_app(db, row, client_factory=lambda: _FakeClient(fail_handler))
        assert failed["ok"] is False
        assert secret not in failed["summary"]
        assert secret not in json.dumps(failed)
        assert "40001" in failed["summary"] or failed["errcode"] == 40001

    def boom(url, params):
        raise httpx.HTTPError(f"GET {url}?corpsecret={secret}&access_token={token_value}")

    with Session(engine) as db:
        row = db.exec(select(WecomApp)).one()
        with caplog.at_level("WARNING"):
            broken = check_wecom_app(db, row, client_factory=lambda: _FakeClient(boom))
        assert broken["ok"] is False
        assert broken["summary"] == "企微连接失败"
        assert secret not in caplog.text
        assert token_value not in caplog.text
        assert "corpsecret" not in caplog.text


def test_preview_uses_default_corp_and_submit_rejects_mismatch():
    engine = _engine()

    def handler(method, url, params=None, json=None):
        raise AssertionError("mismatch must not call wecom")

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            return handler("GET", url, params, None)

        def post(self, url, params=None, json=None):
            return handler("POST", url, params, json)

    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.add(AgentProfile(id="agent_1", tenant_id="t1", name="Bot"))
        create_wecom_app(
            db,
            tenant_id="t1",
            name="默认",
            corp_id="ww_default",
            agentid="1",
            secret="sec",
        )
        create_wecom_app(
            db,
            tenant_id="t1",
            name="另一家",
            corp_id="ww_other",
            agentid="2",
            secret="sec",
        )
        save_agent_approval_config(
            db,
            tenant_id="t1",
            agent_id="agent_1",
            config={
                "template_id": "tpl_1",
                "scenario_key": "default",
                "applicant_mode": "fixed",
                "mode_a_userid": "fixed_user",
                "field_mappings": [
                    {
                        "control_id": "Text-1",
                        "widget_type": "Text",
                        "slot_key": "reason",
                        "title": "事由",
                    }
                ],
            },
        )
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="user_1",
            scenario_key="default",
            fields={"reason": "差旅"},
        )
        assert preview["ok"] is True
        intent = db.get(WecomApprovalIntent, preview["intent_id"])
        assert intent is not None
        assert intent.corp_id == "ww_default"
        intent.corp_id = "ww_other"
        db.add(intent)
        db.commit()
        digest = intent.submission_digest
        intent_id = intent.id
        submitted = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="user_1",
            intent_id=intent_id,
            confirmed_digest=digest,
            client_factory=lambda: _Client(),
        )
        assert submitted["ok"] is False
        assert submitted["error_code"] == "WECOM_OA_APP_CORP_MISMATCH"
        missing, code = resolve_default_oa_app(db, tenant_id="missing")
        assert missing is None
        assert code == "WECOM_OA_NO_CREDENTIAL"


def test_callback_paths_use_app_token_and_legacy_default_only(caplog):
    engine = _engine()
    aes_key = base64.b64encode(bytes(range(32))).decode().rstrip("=")
    with Session(engine) as db:
        _users(db)
        default_app = create_wecom_app(
            db,
            tenant_id="t1",
            name="默认",
            corp_id="ww_default",
            agentid="1",
            secret="sec",
            callback_token="token-default",
            callback_aes_key=aes_key,
        )
        other = create_wecom_app(
            db,
            tenant_id="t1",
            name="第二",
            corp_id="ww_other",
            agentid="2",
            secret="sec",
            callback_token="token-other",
            callback_aes_key=aes_key,
        )
        default_id = default_app.id
        other_id = other.id
    client = _client(engine)
    timestamp = str(int(time.time()))
    nonce = "nonce1"
    xml = "<xml><Event>enter_agent</Event></xml>"
    other_cipher = _encrypt(xml, aes_key, "ww_other")
    other_sig = _signature("token-other", timestamp, nonce, other_cipher)
    default_cipher = _encrypt(xml, aes_key, "ww_default")
    default_sig = _signature("token-default", timestamp, nonce, default_cipher)

    missing = client.post(
        "/api/public/wecom-app/callback/t1/wapp_missing",
        params={"msg_signature": other_sig, "timestamp": timestamp, "nonce": nonce},
        content=f"<xml><Encrypt>{other_cipher}</Encrypt></xml>",
    )
    assert missing.status_code == 404

    wrong_tenant = client.post(
        f"/api/public/wecom-app/callback/other-tenant/{other_id}",
        params={"msg_signature": other_sig, "timestamp": timestamp, "nonce": nonce},
        content=f"<xml><Encrypt>{other_cipher}</Encrypt></xml>",
    )
    assert wrong_tenant.status_code == 404

    with caplog.at_level("INFO"):
        accepted = client.post(
            f"/api/public/wecom-app/callback/t1/{other_id}",
            params={"msg_signature": other_sig, "timestamp": timestamp, "nonce": nonce},
            content=f"<xml><Encrypt>{other_cipher}</Encrypt></xml>",
        )
    assert accepted.status_code == 200
    assert accepted.text == "success"
    assert "enter_agent" in caplog.text
    assert other_cipher not in caplog.text
    assert "token-other" not in caplog.text

    legacy_wrong = client.post(
        "/api/public/wecom-oa/callback/t1",
        params={"msg_signature": other_sig, "timestamp": timestamp, "nonce": nonce},
        content=f"<xml><Encrypt>{other_cipher}</Encrypt></xml>",
    )
    assert legacy_wrong.status_code == 403

    with caplog.at_level("INFO"):
        legacy_ok = client.post(
            "/api/public/wecom-oa/callback/t1",
            params={"msg_signature": default_sig, "timestamp": timestamp, "nonce": nonce},
            content=f"<xml><Encrypt>{default_cipher}</Encrypt></xml>",
        )
    assert legacy_ok.status_code == 200
    assert "wecom oa legacy callback" in caplog.text
    assert default_id in caplog.text

    echo = _encrypt("ping", aes_key, "ww_default")
    echo_sig = _signature("token-default", timestamp, nonce, echo)
    verified = client.get(
        f"/api/public/wecom-app/callback/t1/{default_id}",
        params={
            "msg_signature": echo_sig,
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": echo,
        },
    )
    assert verified.status_code == 200
    assert verified.text == "ping"
    with Session(engine) as db:
        row = db.get(WecomApp, default_id)
        assert row is not None
        assert row.last_callback_verified_at is not None
        other_row = db.get(WecomApp, other_id)
        assert other_row is not None
        other_row.status = "disabled"
        db.add(other_row)
        db.commit()
    disabled = client.post(
        f"/api/public/wecom-app/callback/t1/{other_id}",
        params={"msg_signature": other_sig, "timestamp": timestamp, "nonce": nonce},
        content=f"<xml><Encrypt>{other_cipher}</Encrypt></xml>",
    )
    assert disabled.status_code == 404

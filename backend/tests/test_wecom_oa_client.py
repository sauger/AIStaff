"""U1: OA 凭证与 HTTP 客户端（mock HTTP，无外网）。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.channels.crypto import decrypt_channel_secret
from app.db.models import WecomOaAppCredential
from app.wecom_oa.client import WecomOaApiError, WecomOaClient, WecomOaTokenProvider
from app.wecom_oa.credentials import (
    WecomOaCredentialError,
    load_plaintext_secrets,
    store_oa_credential,
)


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
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
        return self._handler("GET", url, params=params, json=None)

    def post(self, url, params=None, json=None):
        return self._handler("POST", url, params=params, json=json)


def test_store_and_load_oa_credential_encrypts_secret():
    engine = _engine()
    with Session(engine) as db:
        row = store_oa_credential(
            db,
            tenant_id="t1",
            corp_id="ww_corp",
            secret="super-secret",
            agentid="1000002",
            callback_token="tok",
            callback_aes_key="aes" * 14,  # length not validated here
            created_by_user_id="user_1",
        )
        assert row.id.startswith("woaapp_")
        assert row.secret_enc
        assert row.secret_enc != "super-secret"
        assert decrypt_channel_secret(row.secret_enc) == "super-secret"
        secrets = load_plaintext_secrets(row)
        assert secrets["secret"] == "super-secret"
        assert secrets["callback_token"] == "tok"
        assert row.corp_id == "ww_corp"


def test_store_oa_credential_rejects_missing_secret():
    engine = _engine()
    with Session(engine) as db:
        with pytest.raises(WecomOaCredentialError):
            store_oa_credential(db, tenant_id="t1", corp_id="ww", secret="")


def test_gettoken_and_applyevent_happy_path():
    calls: list[tuple] = []

    def handler(method, url, params=None, json=None):
        calls.append((method, url, params, json))
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": "tok_1", "expires_in": 7200}
            )
        if "applyevent" in url:
            assert params and params.get("access_token") == "tok_1"
            assert json["creator_userid"] == "zhangsan"
            assert json["use_template_approver"] == 1
            return _FakeResponse({"errcode": 0, "errmsg": "ok", "sp_no": "202610270001"})
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        cred = store_oa_credential(
            db, tenant_id="t1", corp_id="ww", secret="s1", agentid="1"
        )
        client = WecomOaClient(
            cred, client_factory=lambda: _FakeClient(handler)
        )
        result = client.apply_event(
            creator_userid="zhangsan",
            template_id="tpl_1",
            apply_data={"contents": []},
        )
        assert result["sp_no"] == "202610270001"
        assert any("gettoken" in c[1] for c in calls)
        assert any("applyevent" in c[1] for c in calls)


def test_applyevent_custom_approver_when_not_using_template():
    captured: dict = {}

    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        captured["body"] = json
        return _FakeResponse({"errcode": 0, "sp_no": "SP-CUSTOM"})

    engine = _engine()
    with Session(engine) as db:
        cred = store_oa_credential(db, tenant_id="t1", corp_id="ww", secret="s1")
        client = WecomOaClient(cred, client_factory=lambda: _FakeClient(handler))
        result = client.apply_event(
            creator_userid="zhangsan",
            template_id="tpl_1",
            apply_data={"contents": []},
            use_template_approver=0,
            approver=[{"attr": 1, "userid": ["zhangsan"]}],
            notifyer=["lisi"],
            notify_type=1,
        )
        assert result["sp_no"] == "SP-CUSTOM"
        body = captured["body"]
        assert body["use_template_approver"] == 0
        assert body["approver"] == [{"attr": 1, "userid": ["zhangsan"]}]
        assert body["notifyer"] == ["lisi"]
        assert body["notify_type"] == 1


def test_applyevent_omits_approver_fields_when_using_template():
    captured: dict = {}

    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        captured["body"] = json
        return _FakeResponse({"errcode": 0, "sp_no": "SP-TPL"})

    engine = _engine()
    with Session(engine) as db:
        cred = store_oa_credential(db, tenant_id="t1", corp_id="ww", secret="s1")
        client = WecomOaClient(cred, client_factory=lambda: _FakeClient(handler))
        client.apply_event(
            creator_userid="zhangsan",
            template_id="tpl_1",
            apply_data={"contents": []},
            approver=[{"attr": 1, "userid": ["zhangsan"]}],
            notifyer=["lisi"],
            notify_type=1,
        )
        body = captured["body"]
        assert body["use_template_approver"] == 1
        assert "approver" not in body
        assert "notifyer" not in body
        assert "notify_type" not in body


def test_applyevent_custom_mode_requires_approver():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        raise AssertionError("must not call applyevent")

    engine = _engine()
    with Session(engine) as db:
        cred = store_oa_credential(db, tenant_id="t1", corp_id="ww", secret="s1")
        client = WecomOaClient(cred, client_factory=lambda: _FakeClient(handler))
        with pytest.raises(WecomOaApiError, match="approver"):
            client.apply_event(
                creator_userid="zhangsan",
                template_id="tpl_1",
                apply_data={"contents": []},
                use_template_approver=0,
            )


def test_applyevent_errcode_raises_mapped_error():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        return _FakeResponse({"errcode": 60011, "errmsg": "not allow to access"})

    engine = _engine()
    with Session(engine) as db:
        cred = store_oa_credential(db, tenant_id="t1", corp_id="ww", secret="s1")
        client = WecomOaClient(cred, client_factory=lambda: _FakeClient(handler))
        with pytest.raises(WecomOaApiError) as ei:
            client.apply_event(
                creator_userid="u1",
                template_id="tpl",
                apply_data={"contents": []},
            )
        assert ei.value.code == "WECOM_OA_VISIBLE_SCOPE"
        assert ei.value.errcode == 60011


def test_missing_secret_no_fake_success():
    cred = WecomOaAppCredential(
        tenant_id="t1", corp_id="ww", secret_enc=""
    )
    provider = WecomOaTokenProvider(
        client_factory=lambda: (_ for _ in ()).throw(AssertionError("must not call"))
    )
    with pytest.raises((WecomOaApiError, WecomOaCredentialError)):
        provider.get(cred)


def test_token_expired_forces_refresh_then_succeeds():
    tokens_issued = {"n": 0}
    apply_calls = {"n": 0}

    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            tokens_issued["n"] += 1
            return _FakeResponse(
                {
                    "errcode": 0,
                    "access_token": f"tok_{tokens_issued['n']}",
                    "expires_in": 7200,
                }
            )
        if "applyevent" in url:
            apply_calls["n"] += 1
            token = (params or {}).get("access_token")
            if token == "tok_1":
                return _FakeResponse({"errcode": 42001, "errmsg": "expired"})
            assert token == "tok_2"
            return _FakeResponse({"errcode": 0, "sp_no": "SP99"})
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        cred = store_oa_credential(db, tenant_id="t1", corp_id="ww", secret="s1")
        client = WecomOaClient(cred, client_factory=lambda: _FakeClient(handler))
        result = client.apply_event(
            creator_userid="u",
            template_id="tpl",
            apply_data={"contents": []},
        )
        assert result["sp_no"] == "SP99"
        assert tokens_issued["n"] == 2
        assert apply_calls["n"] == 2


def test_getapprovaldetail_happy():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        assert "getapprovaldetail" in url
        assert json == {"sp_no": "SP1"}
        return _FakeResponse(
            {"errcode": 0, "info": {"sp_no": "SP1", "sp_status": 1}}
        )

    engine = _engine()
    with Session(engine) as db:
        cred = store_oa_credential(db, tenant_id="t1", corp_id="ww", secret="s1")
        client = WecomOaClient(cred, client_factory=lambda: _FakeClient(handler))
        data = client.get_approval_detail("SP1")
        assert data["info"]["sp_status"] == 1

"""U2: 身份解析与临时绑定（含 admin 权限）。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.api.wecom_oa as wecom_oa_api
from app.db import get_session
from app.db.models import ChannelIdentity, Tenant, User
from app.security.auth import create_access_token
from app.wecom_oa.identity import (
    DefaultIdentityResolver,
    resolve_wecom_userid,
    upsert_temp_binding,
)


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

    def override_session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_resolve_from_channel_identity_only():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.add(
            ChannelIdentity(
                tenant_id="t1",
                channel="wecom",
                external_account_scope="wwcorp",
                external_user_id="zhangsan",
                staffdeck_user_id="user_1",
                display_name="张三",
            )
        )
        db.commit()
        hit = resolve_wecom_userid(
            db, tenant_id="t1", staffdeck_user_id="user_1", corp_scope="wwcorp"
        )
        assert hit is not None
        assert hit.wecom_userid == "zhangsan"
        assert hit.source == "channel_identity"


def test_resolve_from_temp_when_channel_missing():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        upsert_temp_binding(
            db,
            tenant_id="t1",
            corp_scope="wwcorp",
            staffdeck_user_id="user_1",
            wecom_userid="lisi",
        )
        hit = resolve_wecom_userid(
            db, tenant_id="t1", staffdeck_user_id="user_1", corp_scope="wwcorp"
        )
        assert hit is not None
        assert hit.wecom_userid == "lisi"
        assert hit.source == "temp_binding"


def test_temp_overrides_channel_identity():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.add(
            ChannelIdentity(
                tenant_id="t1",
                channel="wecom",
                external_account_scope="wwcorp",
                external_user_id="from_channel",
                staffdeck_user_id="user_1",
            )
        )
        db.commit()
        upsert_temp_binding(
            db,
            tenant_id="t1",
            corp_scope="wwcorp",
            staffdeck_user_id="user_1",
            wecom_userid="from_temp",
        )
        hit = DefaultIdentityResolver(db).resolve_wecom_userid(
            tenant_id="t1", staffdeck_user_id="user_1", corp_scope="wwcorp"
        )
        assert hit is not None
        assert hit.wecom_userid == "from_temp"
        assert hit.source == "temp_binding"


def test_missing_both_returns_none():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.commit()
        assert (
            resolve_wecom_userid(
                db, tenant_id="t1", staffdeck_user_id="user_x", corp_scope="ww"
            )
            is None
        )


def test_non_admin_cannot_write_temp_binding():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        member = User(
            id="user_m", tenant_id="t1", username="m", role="member", password_hash="x"
        )
        admin = User(
            id="user_a", tenant_id="t1", username="a", role="admin", password_hash="x"
        )
        db.add_all([member, admin])
        db.commit()
        db.refresh(member)
        db.refresh(admin)
        db.expunge(member)
        db.expunge(admin)
    client = _client(engine)
    resp = client.post(
        "/api/enterprise/wecom-oa/temp-identity-bindings",
        params={"tenant_id": "t1"},
        json={
            "corp_scope": "ww",
            "staffdeck_user_id": "user_m",
            "wecom_userid": "wx",
        },
        headers=_auth(member),
    )
    assert resp.status_code == 403

    ok = client.post(
        "/api/enterprise/wecom-oa/temp-identity-bindings",
        params={"tenant_id": "t1"},
        json={
            "corp_scope": "ww",
            "staffdeck_user_id": "user_m",
            "wecom_userid": "wx",
        },
        headers=_auth(admin),
    )
    assert ok.status_code == 200
    assert ok.json()["binding"]["wecom_userid"] == "wx"


def test_resolver_protocol_swappable():
    """AE5: 调用方只依赖接口，可替换后端。"""

    class FakeResolver:
        def resolve_wecom_userid(self, *, tenant_id, staffdeck_user_id, corp_scope):
            from app.wecom_oa.identity import ResolvedWecomIdentity

            return ResolvedWecomIdentity(
                wecom_userid="fake", source="temp_binding", display_name="F"
            )

    r = FakeResolver()
    hit = r.resolve_wecom_userid(
        tenant_id="t", staffdeck_user_id="u", corp_scope="c"
    )
    assert hit.wecom_userid == "fake"

"""默认企微应用的通讯录只读接口。"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.api.wecom_apps as wecom_apps_api
from app.db import get_session
from app.db.models import Tenant, User, WecomApp
from app.security.auth import create_access_token
from app.wecom_oa.apps import create_wecom_app
from app.wecom_oa.directory import (
    WecomDirectoryClient,
    WecomDirectoryError,
    load_department_tree,
    load_department_users,
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


def _factory(handler):
    return lambda: _FakeClient(handler)


def _no_http(url, params):
    raise AssertionError(url)


def _default_app(db: Session) -> WecomApp:
    return create_wecom_app(
        db,
        tenant_id="t1",
        name="审批",
        corp_id="ww2e1322b745c17c56",
        agentid="1000085",
        secret="corp-secret",
    )


def test_directory_lists_department_tree_and_strips_sensitive_user_fields():
    engine = _engine()
    secret = "corp-secret"
    calls: list[tuple[str, dict]] = []

    def handler(url, params):
        calls.append((url, dict(params)))
        if "gettoken" in url:
            assert params["corpid"] == "ww2e1322b745c17c56"
            assert params["corpsecret"] == secret
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": "token-1", "expires_in": 7200}
            )
        if url.endswith("/department/list"):
            assert params["access_token"] == "token-1"
            return _FakeResponse(
                {
                    "errcode": 0,
                    "errmsg": "ok",
                    "department": [
                        {"id": 16, "name": "数字化赋能中心", "parentid": 1, "order": 8},
                        {"id": 1, "name": "公司", "parentid": 0, "order": 1},
                    ],
                }
            )
        raise AssertionError(url)

    with Session(engine) as db:
        _users(db)
        _default_app(db)
        payload = load_department_tree(db, tenant_id="t1", client_factory=_factory(handler))

    assert payload["corp_id"] == "ww2e1322b745c17c56"
    assert [row["id"] for row in payload["departments"]] == [16, 1]
    assert payload["departments"][0]["parent_id"] == 1
    assert secret not in str(payload)
    assert "access_token" not in str(payload)

    def users_handler(url, params):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": "token-2", "expires_in": 7200}
            )
        assert url.endswith("/user/list")
        assert params["department_id"] == "16"
        assert params["fetch_child"] == "0"
        assert params["access_token"] == "token-2"
        return _FakeResponse(
            {
                "errcode": 0,
                "errmsg": "ok",
                "userlist": [
                    {
                        "userid": "lisi",
                        "name": "李四",
                        "email": "",
                        "biz_mail": "lisi@example.com",
                        "mobile": "13800001111",
                        "telephone": "010-1000",
                        "address": "hidden-address",
                        "avatar": "https://example.invalid/a.png",
                        "department": [16, "bad"],
                    },
                    {
                        "userid": "zhangsan",
                        "name": "张三",
                        "email": "zhangsan@example.com",
                        "mobile": "13900002222",
                        "department": [16],
                    },
                    {"userid": "", "name": "无名", "mobile": "13700003333"},
                ],
            }
        )

    with Session(engine) as db:
        users = load_department_users(
            db,
            tenant_id="t1",
            department_id=16,
            client_factory=_factory(users_handler),
        )
    dumped = str(users)
    assert users["department_id"] == 16
    assert [row["userid"] for row in users["users"]] == ["zhangsan", "lisi"]
    assert users["users"][0]["email"] == "zhangsan@example.com"
    assert users["users"][1]["email"] == "lisi@example.com"
    assert users["users"][0]["department_ids"] == [16]
    assert "13800001111" not in dumped
    assert "13900002222" not in dumped
    assert "hidden-address" not in dumped
    assert "mobile" not in dumped
    assert "biz_mail" not in dumped
    assert "avatar" not in dumped


def test_directory_refreshes_expired_token_once():
    engine = _engine()
    tokens: list[str] = []

    def handler(url, params):
        if "gettoken" in url:
            token = f"token-{len(tokens) + 1}"
            tokens.append(token)
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": token, "expires_in": 7200}
            )
        if params["access_token"] == "token-1":
            return _FakeResponse({"errcode": 42001, "errmsg": "access_token expired"})
        return _FakeResponse(
            {
                "errcode": 0,
                "errmsg": "ok",
                "department": [{"id": 16, "name": "数字化赋能中心", "parentid": 1, "order": 1}],
            }
        )

    with Session(engine) as db:
        _default_app(db)
        payload = load_department_tree(db, tenant_id="t1", client_factory=_factory(handler))
    assert payload["departments"][0]["id"] == 16
    assert tokens == ["token-1", "token-2"]


def test_directory_reports_missing_app_token_failure_and_empty_scope():
    engine = _engine()

    with Session(engine) as db, pytest.raises(WecomDirectoryError) as missing:
        load_department_tree(db, tenant_id="t1", client_factory=_factory(_no_http))
    assert missing.value.status_code == 404
    assert "默认企微应用" in str(missing.value)

    def token_fail(url, params):
        if "gettoken" in url:
            return _FakeResponse({"errcode": 40001, "errmsg": "invalid secret corp-secret"})
        raise AssertionError(url)

    with Session(engine) as db:
        _default_app(db)
        with pytest.raises(WecomDirectoryError) as token_error:
            load_department_tree(db, tenant_id="t1", client_factory=_factory(token_fail))
    assert token_error.value.status_code == 502
    assert "access_token" in str(token_error.value)
    assert "40001" in str(token_error.value)
    assert "corp-secret" not in str(token_error.value)

    def empty_scope(url, params):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": "token", "expires_in": 7200}
            )
        return _FakeResponse({"errcode": 0, "errmsg": "ok", "department": []})

    with Session(engine) as db, pytest.raises(WecomDirectoryError) as empty:
        load_department_tree(db, tenant_id="t1", client_factory=_factory(empty_scope))
    assert empty.value.status_code == 409
    assert "可见范围为空" in str(empty.value)

    def forbidden(url, params):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": "token", "expires_in": 7200}
            )
        return _FakeResponse({"errcode": 60011, "errmsg": "no privilege"})

    with Session(engine) as db, pytest.raises(WecomDirectoryError) as scope:
        load_department_users(
            db,
            tenant_id="t1",
            department_id=16,
            client_factory=_factory(forbidden),
        )
    assert scope.value.status_code == 409
    assert "无权读取通讯录" in str(scope.value)

    def broken(url, params):
        raise httpx.ConnectError(
            "GET https://qyapi.weixin.qq.com?access_token=token&corpsecret=corp-secret"
        )

    with Session(engine) as db, pytest.raises(WecomDirectoryError) as http_error:
        load_department_tree(db, tenant_id="t1", client_factory=_factory(broken))
    assert http_error.value.status_code == 502
    assert "corp-secret" not in str(http_error.value)
    assert "token&corpsecret" not in str(http_error.value)


def test_directory_ignores_non_default_app():
    engine = _engine()
    with Session(engine) as db:
        row = _default_app(db)
        row.is_default = False
        db.add(row)
        db.commit()
        with pytest.raises(WecomDirectoryError) as exc:
            load_department_tree(
                db,
                tenant_id="t1",
                client_factory=_factory(_no_http),
            )
    assert exc.value.status_code == 404


def test_directory_api_requires_admin_and_returns_department_users():
    engine = _engine()
    with Session(engine) as db:
        admin, member = _users(db)
        _default_app(db)

    def handler(url, params):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "errmsg": "ok", "access_token": "token-1", "expires_in": 7200}
            )
        if url.endswith("/department/list"):
            return _FakeResponse(
                {
                    "errcode": 0,
                    "department": [
                        {"id": 16, "name": "数字化赋能中心", "parentid": 1, "order": 1},
                    ],
                }
            )
        if url.endswith("/user/list"):
            return _FakeResponse(
                {
                    "errcode": 0,
                    "userlist": [
                        {
                            "userid": "zhangsan",
                            "name": "张三",
                            "email": "zhangsan@example.com",
                            "mobile": "13800001111",
                            "department": [16],
                        }
                    ],
                }
            )
        raise AssertionError(url)

    client = _client(engine)
    original = WecomDirectoryClient.__init__

    def patched_init(self, credential, *, token_provider=None, client_factory=None):
        original(self, credential, token_provider=token_provider, client_factory=_factory(handler))

    wecom_apps_api_client = wecom_apps_api
    assert wecom_apps_api_client is not None
    monkey = pytest.MonkeyPatch()
    monkey.setattr(WecomDirectoryClient, "__init__", patched_init)
    try:
        denied = client.get(
            "/api/enterprise/wecom-apps/directory/departments",
            params={"tenant_id": "t1"},
            headers=_auth(member),
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "仅企业管理员可读取企微通讯录"

        mismatch = client.get(
            "/api/enterprise/wecom-apps/directory/departments",
            params={"tenant_id": "other"},
            headers=_auth(admin),
        )
        assert mismatch.status_code == 403

        departments = client.get(
            "/api/enterprise/wecom-apps/directory/departments",
            params={"tenant_id": "t1"},
            headers=_auth(admin),
        )
        assert departments.status_code == 200
        body = departments.json()
        assert body["departments"] == [
            {"id": 16, "name": "数字化赋能中心", "parent_id": 1, "order": 1}
        ]
        assert "138" not in departments.text

        users = client.get(
            "/api/enterprise/wecom-apps/directory/departments/16/users",
            params={"tenant_id": "t1"},
            headers=_auth(admin),
        )
        assert users.status_code == 200
        assert users.json()["users"][0]["userid"] == "zhangsan"
        assert users.json()["users"][0]["email"] == "zhangsan@example.com"
        assert "13800001111" not in users.text

        missing = client.get(
            "/api/enterprise/wecom-apps/directory/departments",
            params={"tenant_id": "t1"},
        )
        assert missing.status_code == 401
    finally:
        monkey.undo()


def test_directory_api_missing_default_app():
    engine = _engine()
    with Session(engine) as db:
        admin, _member = _users(db)
    client = _client(engine)
    response = client.get(
        "/api/enterprise/wecom-apps/directory/departments",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "未配置可用的默认企微应用"

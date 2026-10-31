"""企微应用可见审批模板：列表 fail-open，分配写入 wecom_oa_approval。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.wecom_apps as wecom_apps_api
from app.db import get_session
from app.db.models import AgentProfile, AgentResourceBinding, Tenant, User
from app.security.auth import create_access_token
from app.wecom_oa.apps import create_wecom_app
from app.wecom_oa.policy import RESOURCE_TYPE
from app.wecom_oa.service import approval_config_for_edit, save_agent_approval_config
from app.wecom_oa.templates import (
    assign_approval_template,
    load_visible_approval_templates,
    lookup_approval_template,
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
    wecom_apps_api.engine = engine

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


def _token_ok():
    return _FakeResponse(
        {"errcode": 0, "errmsg": "ok", "access_token": "tok_1", "expires_in": 7200}
    )


def _app(db: Session, *, status: str = "active"):
    return create_wecom_app(
        db,
        tenant_id="t1",
        name="审批",
        corp_id="ww_demo",
        agentid="100",
        secret="secret-value",
        created_by_user_id="user_admin",
    )


def _agent(db: Session, *, agent_id: str, name: str) -> AgentProfile:
    row = AgentProfile(id=agent_id, tenant_id="t1", name=name, status="active")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_catalog_lists_template_id_and_name_without_scanning_approvals():
    calls: list[str] = []

    def handler(method, url, params=None, json=None):
        calls.append(url)
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            return _FakeResponse(
                {
                    "errcode": 0,
                    "errmsg": "ok",
                    "template_list": [
                        {
                            "template_id": "tpl_leave",
                            "template_names": [
                                {"text": "Leave", "lang": "en"},
                                {"text": "请假", "lang": "zh_CN"},
                            ],
                        }
                    ],
                }
            )
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        payload = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app.id,
            client_factory=lambda: _FakeClient(handler),
            now=1_700_000_000,
        )
    assert payload["error"] is None
    assert payload["templates"] == [
        {
            "template_id": "tpl_leave",
            "name": "请假",
            "control_count": None,
            "source": "catalog",
        }
    ]
    assert not any("getapprovalinfo" in url for url in calls)


def test_missing_catalog_discovers_templates_from_visible_approvals():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            return _FakeResponse({"errcode": 48002, "errmsg": "api forbidden"})
        if "getapprovalinfo" in url:
            assert json["endtime"] - json["starttime"] == 30 * 24 * 3600
            assert json["new_cursor"] == ""
            return _FakeResponse(
                {"errcode": 0, "sp_no_list": ["SP1", "SP2", "SP3"]}
            )
        if "getapprovaldetail" in url:
            sp_no = json["sp_no"]
            details = {
                "SP1": ("tpl_a", "报销"),
                "SP2": ("tpl_a", "报销"),
                "SP3": ("tpl_b", "用印"),
            }
            template_id, name = details[sp_no]
            return _FakeResponse(
                {
                    "errcode": 0,
                    "info": {"sp_no": sp_no, "template_id": template_id, "sp_name": name},
                }
            )
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        payload = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app.id,
            client_factory=lambda: _FakeClient(handler),
            now=1_700_000_000,
        )
    assert payload["error"] is None
    assert [(row["template_id"], row["name"], row["source"]) for row in payload["templates"]] == [
        ("tpl_a", "报销", "approval"),
        ("tpl_b", "用印", "approval"),
    ]
    assert "近 30 天" in (payload["notice"] or "")


def test_permission_error_fail_open_empty_list():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        return _FakeResponse({"errcode": 301055, "errmsg": "no approval permission"})

    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        payload = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app.id,
            client_factory=lambda: _FakeClient(handler),
        )
    assert payload["templates"] == []
    assert "审批" in payload["error"]
    assert "secret-value" not in payload["error"]


def test_lookup_returns_name_and_fail_open_when_missing():
    def ok_handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        assert json == {"template_id": "tpl_ok"}
        return _FakeResponse(
            {
                "errcode": 0,
                "template_names": [{"text": "出差", "lang": "zh_CN"}],
                "template_content": {"controls": [{}, {}]},
            }
        )

    def missing_handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        return _FakeResponse({"errcode": 301025, "errmsg": "template not found"})

    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        found = lookup_approval_template(
            db,
            tenant_id="t1",
            app_id=app.id,
            template_id="tpl_ok",
            client_factory=lambda: _FakeClient(ok_handler),
        )
        missing = lookup_approval_template(
            db,
            tenant_id="t1",
            app_id=app.id,
            template_id="tpl_missing",
            client_factory=lambda: _FakeClient(missing_handler),
        )
    assert found["error"] is None
    assert found["template"]["name"] == "出差"
    assert found["template"]["control_count"] == 2
    assert missing["template"] is None
    assert missing["error"]


def test_assign_keeps_mode_a_userid_and_one_template_per_agent():
    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        _agent(db, agent_id="agent_a", name="小李")
        _agent(db, agent_id="agent_b", name="小王")
        save_agent_approval_config(
            db,
            tenant_id="t1",
            agent_id="agent_a",
            config={
                "template_id": "old_tpl",
                "scenario_key": "default",
                "applicant_mode": "fixed",
                "mode_a_userid": "zhangsan",
                "mode_a_display_name": "张三",
                "field_mappings": [
                    {
                        "control_id": "Text-1",
                        "widget_type": "Text",
                        "slot_key": "reason",
                        "title": "事由",
                        "required": True,
                    }
                ],
                "display_name": "原场景",
            },
        )
        assigned = assign_approval_template(
            db,
            tenant_id="t1",
            app_id=app.id,
            template_id="tpl_leave",
            template_name="请假",
            agent_ids=["agent_a", "agent_b", "agent_a"],
        )
        edited_a = approval_config_for_edit(
            db, tenant_id="t1", agent_id="agent_a", scenario_key="default"
        )
        edited_b = approval_config_for_edit(
            db, tenant_id="t1", agent_id="agent_b", scenario_key="default"
        )
        rows = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == "t1",
                AgentResourceBinding.resource_type == RESOURCE_TYPE,
            )
        ).all()

    assert {item["agent_id"] for item in assigned} == {"agent_a", "agent_b"}
    assert edited_a["template_id"] == "tpl_leave"
    assert edited_a["mode_a_userid"] == "zhangsan"
    assert edited_a["display_name"] == "原场景"
    assert edited_a["field_mappings"][0]["control_id"] == "Text-1"
    assert edited_b["template_id"] == "tpl_leave"
    assert edited_b["applicant_mode"] == "fixed"
    assert edited_b["mode_a_userid"] is None
    assert len(rows) == 2
    assert all(row.resource_id == "default" for row in rows)


def test_templates_api_lists_and_assignment_is_visible_on_agent_config(monkeypatch):
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            return _FakeResponse(
                {
                    "errcode": 0,
                    "template_list": [
                        {
                            "template_id": "tpl_leave",
                            "template_names": [{"text": "请假", "lang": "zh_CN"}],
                        }
                    ],
                }
            )
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        admin, member = _users(db)
        app = _app(db)
        app_id = app.id
        _agent(db, agent_id="agent_a", name="小李")
    client = _client(engine)

    def _load(*args, **kwargs):
        kwargs["client_factory"] = lambda: _FakeClient(handler)
        return load_visible_approval_templates(*args, **kwargs)

    monkeypatch.setattr(wecom_apps_api, "load_visible_approval_templates", _load)
    denied = client.get(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates",
        params={"tenant_id": "t1"},
        headers=_auth(member),
    )
    assert denied.status_code == 403

    listed = client.get(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["templates"][0]["template_id"] == "tpl_leave"
    assert body["templates"][0]["name"] == "请假"
    assert "secret-value" not in listed.text

    saved = client.post(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates/assignments",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"template_id": "tpl_leave", "template_name": "请假", "agent_ids": ["agent_a"]},
    )
    assert saved.status_code == 200
    assert saved.json()["assignments"][0]["template_id"] == "tpl_leave"

    with Session(engine) as db:
        config = approval_config_for_edit(
            db, tenant_id="t1", agent_id="agent_a", scenario_key="default"
        )
    assert config["template_id"] == "tpl_leave"


def test_disabled_app_does_not_call_wecom():
    def handler(method, url, params=None, json=None):
        raise AssertionError("disabled app must not call WeCom")

    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        app.status = "disabled"
        db.add(app)
        db.commit()
        db.refresh(app)
        payload = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app.id,
            client_factory=lambda: _FakeClient(handler),
        )
    assert payload["templates"] == []
    assert "停用" in payload["error"]


def test_assign_unknown_agent_raises():
    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        with pytest.raises(ValueError, match="员工不存在"):
            assign_approval_template(
                db,
                tenant_id="t1",
                app_id=app.id,
                template_id="tpl_leave",
                template_name="请假",
                agent_ids=["missing"],
            )

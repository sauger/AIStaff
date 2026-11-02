"""企微审批模板：不调用模板目录，保存与分配仍可管理。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.wecom_apps as wecom_apps_api
from app.db import get_session
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    Tenant,
    User,
    WecomSavedApprovalTemplate,
)
from app.security.auth import create_access_token
from app.wecom_oa.apps import create_wecom_app
from app.wecom_oa.policy import RESOURCE_TYPE
from app.wecom_oa.service import approval_config_for_edit, save_agent_approval_config
from app.wecom_oa.templates import (
    assign_approval_template,
    delete_saved_approval_template,
    list_template_assignments,
    load_visible_approval_templates,
    lookup_approval_template,
    save_approval_template,
    unassign_approval_template,
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


def test_recent_approvals_discover_templates_without_catalog():
    calls: list[str] = []

    def handler(method, url, params=None, json=None):
        calls.append(url)
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            raise AssertionError(url)
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
    assert not any("gettemplatelist" in url for url in calls)


def test_permission_error_fail_open_empty_list():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            raise AssertionError(url)
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
    assert payload["error"] is None
    assert "审批" in (payload["notice"] or "")
    assert "gettemplatelist" not in (payload["notice"] or "")
    assert "secret-value" not in (payload["notice"] or "")


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
    assert "301025" in missing["error"]
    assert "办公" in missing["error"]
    assert "审批接口" in missing["error"]


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
            raise AssertionError(url)
        if "getapprovalinfo" in url:
            return _FakeResponse({"errcode": 0, "sp_no_list": []})
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        admin, member = _users(db)
        app = _app(db)
        app_id = app.id
        _agent(db, agent_id="agent_a", name="小李")
        db.add(
            WecomSavedApprovalTemplate(
                tenant_id="t1",
                app_id=app_id,
                template_id="tpl_leave",
                name="请假",
            )
        )
        db.commit()
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
    assert body["templates"][0]["source"] == "saved"
    assert "gettemplatelist" not in listed.text
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


class _EmptyBodyResponse:
    status_code = 404

    def json(self):
        raise ValueError("empty body")


def _detail_handler(name: str):
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            raise AssertionError(url)
        if "gettemplatedetail" in url:
            return _FakeResponse(
                {
                    "errcode": 0,
                    "template_names": [{"text": name, "lang": "zh_CN"}],
                    "template_content": {"controls": [{}, {}, {}]},
                }
            )
        if "getapprovalinfo" in url:
            return _FakeResponse({"errcode": 0, "sp_no_list": []})
        raise AssertionError(url)

    return handler


def test_saved_template_survives_reload_and_delete_keeps_assignment():
    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        _agent(db, agent_id="agent_a", name="小李")
        app_id = app.id
        saved = save_approval_template(
            db,
            tenant_id="t1",
            app_id=app_id,
            template_id="tpl_contract",
            client_factory=lambda: _FakeClient(_detail_handler("采购合同审批")),
        )
        assign_approval_template(
            db,
            tenant_id="t1",
            app_id=app_id,
            template_id="tpl_bound",
            template_name="已分配采购",
            agent_ids=["agent_a"],
        )
        listed = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app_id,
            client_factory=lambda: _FakeClient(_detail_handler("采购合同审批")),
        )
        delete_saved_approval_template(
            db, tenant_id="t1", app_id=app_id, template_id="tpl_contract"
        )
        after_delete = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app_id,
            client_factory=lambda: _FakeClient(_detail_handler("采购合同审批")),
        )

    assert saved["error"] is None
    assert saved["template"]["name"] == "采购合同审批"
    assert saved["template"]["source"] == "saved"
    assert saved["template"]["control_count"] == 3
    by_id = {row["template_id"]: row for row in listed["templates"]}
    assert by_id["tpl_contract"]["source"] == "saved"
    assert by_id["tpl_bound"]["name"] == "已分配采购"
    assert by_id["tpl_bound"]["source"] == "assignment"
    assert "tpl_contract" not in {row["template_id"] for row in after_delete["templates"]}
    assert "tpl_bound" in {row["template_id"] for row in after_delete["templates"]}
    assert "gettemplatelist" not in str(listed)


def test_assigned_template_stays_visible_when_discovery_http_404():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            raise AssertionError(url)
        if "getapprovalinfo" in url:
            return _EmptyBodyResponse()
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        _agent(db, agent_id="agent_a", name="小李")
        db.add(
            WecomSavedApprovalTemplate(
                tenant_id="t1",
                app_id=app.id,
                template_id="tpl_saved",
                name="用印",
            )
        )
        db.commit()
        assign_approval_template(
            db,
            tenant_id="t1",
            app_id=app.id,
            template_id="C4hL5jz3SkpeKUAirMToH2UHuZKtzabyPf4uQLgTj",
            template_name="采购合同审批",
            agent_ids=["agent_a"],
        )
        payload = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app.id,
            client_factory=lambda: _FakeClient(handler),
        )
    rendered = str(payload)
    assert payload["error"] is None
    assert "gettemplatelist" not in rendered
    ids = {row["template_id"]: row for row in payload["templates"]}
    assert ids["tpl_saved"]["source"] == "saved"
    assert ids["C4hL5jz3SkpeKUAirMToH2UHuZKtzabyPf4uQLgTj"]["name"] == "采购合同审批"
    assert payload["assignments"][0]["agent_name"] == "小李"


def test_assigned_template_without_name_uses_template_detail():
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            raise AssertionError(url)
        if "getapprovalinfo" in url:
            return _FakeResponse({"errcode": 0, "sp_no_list": []})
        if "gettemplatedetail" in url:
            assert json["template_id"] == "tpl_contract"
            return _FakeResponse(
                {
                    "errcode": 0,
                    "template_names": [{"text": "采购合同审批", "lang": "zh_CN"}],
                }
            )
        raise AssertionError(url)

    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        _agent(db, agent_id="agent_a", name="小李")
        save_agent_approval_config(
            db,
            tenant_id="t1",
            agent_id="agent_a",
            config={
                "template_id": "tpl_contract",
                "scenario_key": "default",
                "applicant_mode": "fixed",
                "mode_a_userid": "zhangsan",
                "field_mappings": [],
            },
        )
        payload = load_visible_approval_templates(
            db,
            tenant_id="t1",
            app_id=app.id,
            client_factory=lambda: _FakeClient(handler),
        )
    assert payload["templates"] == [
        {
            "template_id": "tpl_contract",
            "name": "采购合同审批",
            "control_count": None,
            "source": "assignment",
        }
    ]


def test_unassign_clears_template_and_keeps_applicant():
    engine = _engine()
    with Session(engine) as db:
        _users(db)
        app = _app(db)
        _agent(db, agent_id="agent_a", name="小李")
        save_agent_approval_config(
            db,
            tenant_id="t1",
            agent_id="agent_a",
            config={
                "template_id": "old_tpl",
                "scenario_key": "default",
                "applicant_mode": "fixed",
                "mode_a_userid": "zhangsan",
                "field_mappings": [],
                "display_name": "原场景",
            },
        )
        assign_approval_template(
            db,
            tenant_id="t1",
            app_id=app.id,
            template_id="tpl_leave",
            template_name="请假",
            agent_ids=["agent_a"],
        )
        removed = unassign_approval_template(
            db,
            tenant_id="t1",
            app_id=app.id,
            template_id="tpl_leave",
            agent_ids=["agent_a"],
        )
        edited = approval_config_for_edit(
            db, tenant_id="t1", agent_id="agent_a", scenario_key="default"
        )
        assignments = list_template_assignments(db, tenant_id="t1")
    assert removed[0]["agent_id"] == "agent_a"
    assert edited["template_id"] == ""
    assert edited["mode_a_userid"] == "zhangsan"
    assert edited["display_name"] == "原场景"
    assert assignments == []


def test_save_and_unassign_routes(monkeypatch):
    def handler(method, url, params=None, json=None):
        if "gettoken" in url:
            return _token_ok()
        if "gettemplatelist" in url:
            raise AssertionError(url)
        if "gettemplatedetail" in url:
            return _FakeResponse(
                {
                    "errcode": 0,
                    "template_names": [{"text": "采购合同审批", "lang": "zh_CN"}],
                }
            )
        if "getapprovalinfo" in url:
            return _FakeResponse({"errcode": 0, "sp_no_list": []})
        raise AssertionError(url)

    def _bind(fn):
        def _wrapped(*args, **kwargs):
            kwargs["client_factory"] = lambda: _FakeClient(handler)
            return fn(*args, **kwargs)

        return _wrapped

    engine = _engine()
    with Session(engine) as db:
        admin, member = _users(db)
        app = _app(db)
        app_id = app.id
        _agent(db, agent_id="agent_a", name="小李")
    client = _client(engine)
    monkeypatch.setattr(
        wecom_apps_api, "save_approval_template", _bind(save_approval_template)
    )
    monkeypatch.setattr(
        wecom_apps_api,
        "load_visible_approval_templates",
        _bind(load_visible_approval_templates),
    )
    denied = client.post(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates/saved",
        params={"tenant_id": "t1"},
        headers=_auth(member),
        json={"template_id": "tpl_contract"},
    )
    assert denied.status_code == 403

    saved = client.post(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates/saved",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"template_id": "tpl_contract"},
    )
    assert saved.status_code == 200
    assert saved.json()["template"]["name"] == "采购合同审批"
    assert saved.json()["error"] is None

    listed = client.get(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert listed.status_code == 200
    assert listed.json()["templates"][0]["template_id"] == "tpl_contract"
    assert listed.json()["templates"][0]["source"] == "saved"

    assigned = client.post(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates/assignments",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={
            "template_id": "tpl_contract",
            "template_name": "采购合同审批",
            "agent_ids": ["agent_a"],
        },
    )
    assert assigned.status_code == 200

    removed = client.post(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates/assignments/remove",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
        json={"template_id": "tpl_contract", "agent_ids": ["agent_a"]},
    )
    assert removed.status_code == 200
    assert removed.json()["assignments"][0]["template_id"] == ""

    deleted = client.delete(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates/saved",
        params={"tenant_id": "t1", "template_id": "tpl_contract"},
        headers=_auth(admin),
    )
    assert deleted.status_code == 200
    listed_again = client.get(
        f"/api/enterprise/wecom-apps/{app_id}/approval-templates",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert listed_again.json()["templates"] == []
    assert listed_again.json()["assignments"] == []

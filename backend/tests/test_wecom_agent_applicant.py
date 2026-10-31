"""数字员工的企微审批身份写入 mode_a_userid，并由代发读取。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.api.wecom_oa as wecom_oa_api
from app.db import get_session
from app.db.models import AgentProfile, Tenant, User, WecomApprovalIntent
from app.security.auth import create_access_token
from app.wecom_oa.apps import create_wecom_app
from app.wecom_oa.service import (
    preview_approval,
    save_agent_approval_config,
    set_agent_wecom_applicant,
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
    app.include_router(wecom_oa_api.router)

    def override_session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def _mapping() -> list[dict]:
    return [
        {
            "control_id": "Text-1",
            "widget_type": "Text",
            "slot_key": "reason",
            "title": "事由",
        }
    ]


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _RecordingClient:
    def __init__(self, handler, calls):
        self._handler = handler
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        self.calls.append(("GET", url, params, None))
        return self._handler("GET", url, params, None)

    def post(self, url, params=None, json=None):
        self.calls.append(("POST", url, params, json))
        return self._handler("POST", url, params, json)


def _seed(db: Session) -> None:
    db.add(Tenant(id="t1", name="T"))
    db.add(AgentProfile(id="agent_1", tenant_id="t1", name="Bot"))
    create_wecom_app(
        db,
        tenant_id="t1",
        name="OA",
        corp_id="ww2e1322b745c17c56",
        agentid="1000085",
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
            "mode_a_userid": "admin_userid",
            "field_mappings": _mapping(),
        },
    )


def test_bound_wecom_user_is_the_approval_creator():
    engine = _engine()
    calls: list = []

    def handler(method, url, params, body):
        if "gettoken" in url:
            return _FakeResponse({"errcode": 0, "access_token": "tok", "expires_in": 7200})
        if "applyevent" in url:
            assert body["creator_userid"] == "zhangsan"
            return _FakeResponse({"errcode": 0, "sp_no": "SP100"})
        if "getapprovaldetail" in url:
            return _FakeResponse({"errcode": 0, "info": {"sp_status": 1}})
        raise AssertionError(url)

    with Session(engine) as db:
        _seed(db)
        bound = set_agent_wecom_applicant(
            db,
            tenant_id="t1",
            agent_id="agent_1",
            wecom_userid="zhangsan",
            display_name="张三",
        )
        assert bound["wecom_userid"] == "zhangsan"
        assert bound["display_name"] == "张三"
        assert bound["applicant_mode"] == "fixed"

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
        assert preview["summary"]["creator_userid"] == "zhangsan"
        assert "张三" in preview["summary"]["applicant_summary"]
        intent = db.get(WecomApprovalIntent, preview["intent_id"])
        assert intent is not None
        assert intent.creator_userid == "zhangsan"
        assert intent.creator_display_name == "张三"
        assert intent.identity_source == "mode_a_userid"
        assert "admin_userid" not in (intent.creator_userid or "")

        submitted = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="user_1",
            intent_id=preview["intent_id"],
            confirmed_digest=preview["submission_digest"],
            client_factory=lambda: _RecordingClient(handler, calls),
        )
        assert submitted["ok"] is True
        assert submitted["sp_no"] == "SP100"
        assert any(
            call[3] and call[3].get("creator_userid") == "zhangsan"
            for call in calls
            if call[0] == "POST"
        )

        cleared = set_agent_wecom_applicant(
            db,
            tenant_id="t1",
            agent_id="agent_1",
            wecom_userid=None,
        )
        assert cleared["wecom_userid"] is None
        missing = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_2",
            agent_id="agent_1",
            user_id="user_1",
            scenario_key="default",
            fields={"reason": "差旅"},
        )
        assert missing["ok"] is False
        assert "mode_a_userid" in missing["error"]


def test_binding_keeps_template_and_switches_chat_partner_to_fixed():
    engine = _engine()
    with Session(engine) as db:
        _seed(db)
        save_agent_approval_config(
            db,
            tenant_id="t1",
            agent_id="agent_1",
            config={
                "template_id": "tpl_keep",
                "scenario_key": "default",
                "applicant_mode": "chat_partner",
                "field_mappings": _mapping(),
            },
        )
        set_agent_wecom_applicant(
            db,
            tenant_id="t1",
            agent_id="agent_1",
            wecom_userid="lisi",
            display_name="李四",
        )
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id=None,
            scenario_key="default",
            fields={"reason": "采购"},
        )
        assert preview["ok"] is True
        assert preview["summary"]["creator_userid"] == "lisi"
        assert preview["summary"]["applicant_mode"] == "fixed"


def test_wecom_applicant_api_is_tenant_scoped_and_manageable():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        admin = User(
            id="user_admin",
            tenant_id="t1",
            username="admin",
            role="admin",
            password_hash="x",
        )
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
        db.add(admin)
        db.add(owner)
        db.add(other)
        db.add(
            AgentProfile(
                id="agent_owned",
                tenant_id="t1",
                name="Owned",
                metadata_json={"owner_user_id": "user_owner"},
            )
        )
        db.commit()
        for user in (admin, owner, other):
            db.refresh(user)
            db.expunge(user)

    client = _client(engine)
    body = {"wecom_userid": "zhangsan", "display_name": "张三", "scenario_key": "default"}
    denied = client.put(
        "/api/enterprise/wecom-oa/agents/agent_owned/wecom-applicant",
        params={"tenant_id": "t1"},
        json=body,
        headers=_auth(other),
    )
    assert denied.status_code == 403

    saved = client.put(
        "/api/enterprise/wecom-oa/agents/agent_owned/wecom-applicant",
        params={"tenant_id": "t1"},
        json=body,
        headers=_auth(owner),
    )
    assert saved.status_code == 200
    assert saved.json()["applicant"]["wecom_userid"] == "zhangsan"
    assert saved.json()["applicant"]["display_name"] == "张三"

    viewed = client.get(
        "/api/enterprise/wecom-oa/agents/agent_owned/approval-config",
        params={"tenant_id": "t1", "scenario_key": "default"},
        headers=_auth(admin),
    )
    assert viewed.status_code == 200
    config = viewed.json()["config"]
    assert config["mode_a_userid"] == "zhangsan"
    assert config["mode_a_display_name"] == "张三"
    assert config["applicant_mode"] == "fixed"
    assert config["template_id"] == ""

    cleared = client.delete(
        "/api/enterprise/wecom-oa/agents/agent_owned/wecom-applicant",
        params={"tenant_id": "t1", "scenario_key": "default"},
        headers=_auth(admin),
    )
    assert cleared.status_code == 200
    assert cleared.json()["applicant"]["wecom_userid"] is None
    still = client.get(
        "/api/enterprise/wecom-oa/agents/agent_owned/approval-config",
        params={"tenant_id": "t1"},
        headers=_auth(admin),
    )
    assert still.json()["config"]["mode_a_userid"] is None

"""U4: 意图快照与确认闸（无确认不调用 applyevent）。"""

from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    AgentProfile,
    Message,
    Tenant,
    WecomApprovalIntent,
)
from app.wecom_oa.apps import create_wecom_app
from app.wecom_oa.service import (
    invoke_wecom_oa_approval,
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


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _RecordingClient:
    calls: list

    def __init__(self, handler, calls):
        self._handler = handler
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls.append(("GET", url, params, None))
        return self._handler("GET", url, params, None)

    def post(self, url, params=None, json=None):
        self.calls.append(("POST", url, params, json))
        return self._handler("POST", url, params, json)


def _seed_mode_a(db: Session):
    db.add(Tenant(id="t1", name="T"))
    db.add(AgentProfile(id="agent_1", tenant_id="t1", name="Bot"))
    create_wecom_app(
        db,
        tenant_id="t1",
        name="OA",
        corp_id="ww",
        agentid="1000002",
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


def test_preview_summary_includes_mode_a_applicant():
    engine = _engine()
    with Session(engine) as db:
        _seed_mode_a(db)
        out = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="user_1",
            scenario_key="default",
            fields={"reason": "差旅"},
        )
        assert out["ok"] is True
        assert out["requires_confirmation"] is True
        assert "fixed_user" in out["summary"]["applicant_summary"]
        assert out["summary"]["creator_userid"] == "fixed_user"
        intent = db.get(WecomApprovalIntent, out["intent_id"])
        assert intent is not None
        assert intent.status == "draft"
        assert intent.submission_digest == out["submission_digest"]
        msgs = db.exec(select(Message).where(Message.session_id == "sess_1")).all()
        assert any(
            (m.metadata_json or {}).get("wecom_oa_approval_confirm") for m in msgs
        )


def test_submit_without_confirm_does_not_call_applyevent():
    engine = _engine()
    calls: list = []

    def handler(method, url, params, json):
        raise AssertionError("HTTP must not be called without confirmation")

    with Session(engine) as db:
        _seed_mode_a(db)
        # 直接 submit 无 intent / digest
        out = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="user_1",
            intent_id="",
            confirmed_digest="",
            client_factory=lambda: _RecordingClient(handler, calls),
        )
        assert out["ok"] is False
        assert out["error_code"] == "WECOM_OA_CONFIRMATION_REQUIRED"
        assert calls == []


def test_digest_mismatch_rejects():
    engine = _engine()
    calls: list = []

    def handler(method, url, params, json):
        raise AssertionError("should not call")

    with Session(engine) as db:
        _seed_mode_a(db)
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="u",
            scenario_key="default",
            fields={"reason": "A"},
        )
        out = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="u",
            intent_id=preview["intent_id"],
            confirmed_digest="sha256:deadbeef",
            client_factory=lambda: _RecordingClient(handler, calls),
        )
        assert out["ok"] is False
        assert out["error_code"] == "WECOM_OA_DIGEST_MISMATCH"
        assert calls == []
        intent = db.get(WecomApprovalIntent, preview["intent_id"])
        assert intent.status != "submitted"


def test_confirm_freezes_payload_and_submit_replays(monkeypatch):
    engine = _engine()
    calls: list = []

    def handler(method, url, params, json):
        if "gettoken" in url:
            return _FakeResponse(
                {"errcode": 0, "access_token": "tok", "expires_in": 7200}
            )
        if "applyevent" in url:
            assert json["creator_userid"] == "fixed_user"
            assert json["template_id"] == "tpl_1"
            return _FakeResponse({"errcode": 0, "sp_no": "SP100"})
        if "getapprovaldetail" in url:
            return _FakeResponse({"errcode": 0, "info": {"sp_status": 1}})
        raise AssertionError(url)

    with Session(engine) as db:
        _seed_mode_a(db)
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="u",
            scenario_key="default",
            fields={"reason": "差旅报销"},
        )
        # 篡改 DB 字段应导致 digest 不匹配（不可变）——这里验证确认后正常提交
        out = submit_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="u",
            intent_id=preview["intent_id"],
            confirmed_digest=preview["submission_digest"],
            client_factory=lambda: _RecordingClient(handler, calls),
        )
        assert out["ok"] is True
        assert out["sp_no"] == "SP100"
        assert out["status"] == "submitted"
        assert any("applyevent" in c[1] for c in calls)
        intent = db.get(WecomApprovalIntent, preview["intent_id"])
        assert intent.payload_json["fields"]["reason"] == "差旅报销"


def test_dismiss_path_via_failed_without_submit():
    """F2: 用户拒绝确认 — 无 confirmed/submitted 成功路径。"""
    engine = _engine()
    with Session(engine) as db:
        _seed_mode_a(db)
        preview = preview_approval(
            db,
            tenant_id="t1",
            session_id="sess_1",
            agent_id="agent_1",
            user_id="u",
            scenario_key="default",
            fields={"reason": "x"},
        )
        intent = db.get(WecomApprovalIntent, preview["intent_id"])
        assert intent.status == "draft"
        # 不调用 submit → 无 sp_no
        assert intent.sp_no is None

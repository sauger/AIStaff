import json
from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.channels.service_autoroute as autoroute_module
import app.channels.service_intake as intake_module
import app.core.agent_loop as agent_loop_module
from app.channels.service_autoroute import (
    AUTO_ROUTE_CONFIDENCE_THRESHOLD,
    RouteDecision,
    classify_intent,
    maybe_auto_route,
)
from app.channels.service_intake import process_inbound
from app.channels.service_routing import resolve_current_agent, set_current_agent
from app.db.models import (
    AgentEvent,
    AgentProfile,
    ChannelBinding,
    ChannelBindingAgent,
    ChannelConvState,
    ChannelDelivery,
    ChannelInboundEvent,
    ChatSession,
    ModelConfig,
    Tenant,
    User,
    utc_now,
)


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


class FakeLLMClient:
    calls: list[dict] = []
    script: object = json.dumps({"agent_id": "stay", "confidence": 0.9, "reason": "意图不明"})
    error: Exception | None = None

    def __init__(self, model_config):
        self.model_config = model_config

    def generate_text(self, system_prompt, user_payload, response_format=None):
        type(self).calls.append(
            {"system": system_prompt, "payload": user_payload, "format": response_format}
        )
        if type(self).error:
            raise type(self).error
        return type(self).script


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    FakeLLMClient.calls = []
    FakeLLMClient.script = json.dumps({"agent_id": "stay", "confidence": 0.9, "reason": "意图不明"})
    FakeLLMClient.error = None
    monkeypatch.setattr(autoroute_module, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(
        autoroute_module,
        "model_for_agent",
        lambda db, tenant_id, agent_id, role: ModelConfig(
            tenant_id=tenant_id, name="fake", api_key_encrypted="x", model="fake-model"
        ),
    )
    yield


CANDIDATES = [
    {"agent_id": "agent_xz", "name": "行政", "description": "会议室预订、差旅、行政流程"},
    {"agent_id": "agent_cw", "name": "财务", "description": "报销、发票、付款"},
]


# ---------- 分类器 ----------


def test_classify_hit_switches() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        FakeLLMClient.script = json.dumps(
            {"agent_id": "agent_cw", "confidence": 0.92, "reason": "报销属于财务"}
        )
        decision = classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "差旅费怎么报销？")
        assert decision.switched is True
        assert decision.agent_id == "agent_cw"
        assert decision.target_agent_id == "agent_cw"
        assert decision.confidence == 0.92
        # 描述注入 prompt 载荷
        payload = FakeLLMClient.calls[0]["payload"]
        assert payload["candidates"][0]["description"] == "会议室预订、差旅、行政流程"
        assert FakeLLMClient.calls[0]["format"] == {"type": "json_object"}


def test_classify_stay_variants() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        decision = classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "会议室还有吗")
        assert decision.switched is False
        assert decision.agent_id == "agent_xz"

        FakeLLMClient.script = json.dumps({"agent_id": "agent_xz", "confidence": 0.99, "reason": "行政领域"})
        decision = classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "帮我订会议室")
        assert decision.switched is False


def test_classify_low_confidence_falls_back() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.5, "reason": "不太确定"})
        decision = classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "报销?")
        assert decision.switched is False
        assert decision.agent_id == "agent_xz"
        assert AUTO_ROUTE_CONFIDENCE_THRESHOLD == 0.75


def test_classify_bad_json_and_llm_error_fall_back() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        FakeLLMClient.script = "这不是 JSON"
        assert classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "报销?").switched is False

        FakeLLMClient.script = json.dumps(["not", "a", "dict"])
        assert classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "报销?").switched is False

        FakeLLMClient.script = json.dumps({"agent_id": "agent_nope", "confidence": 0.99, "reason": "幻觉"})
        assert classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "报销?").switched is False

        FakeLLMClient.error = RuntimeError("LLM 超时")
        assert classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "报销?").switched is False


def test_classify_without_model_config_stays(monkeypatch) -> None:
    engine = _test_engine()
    monkeypatch.setattr(autoroute_module, "model_for_agent", lambda db, tenant_id, agent_id, role: None)
    with Session(engine) as db:
        FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.99, "reason": "x"})
        decision = classify_intent(db, "tenant_demo", CANDIDATES, "agent_xz", "报销?")
        assert decision.switched is False
        assert FakeLLMClient.calls == []


# ---------- 粘性保护 ----------


def _seed_binding(engine, *, auto_route=None, single_mount=False) -> str:
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(
            AgentProfile(
                id="agent_xz",
                tenant_id="tenant_demo",
                name="行政",
                description="会议室预订、差旅、行政流程",
                metadata_json={},
            )
        )
        db.add(
            AgentProfile(
                id="agent_cw",
                tenant_id="tenant_demo",
                name="财务",
                description="报销、发票、付款",
                metadata_json={},
            )
        )
        config = {"ilink_bot_id": "bot_1@im.bot"}
        if auto_route is not None:
            config["auto_route"] = auto_route
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_xz",
            channel="wechat",
            status="active",
            config_json=config,
        )
        db.add(binding)
        db.flush()
        db.add(
            ChannelBindingAgent(
                tenant_id="tenant_demo",
                binding_id=binding.id,
                agent_id="agent_xz",
                is_default=True,
                sort_order=0,
            )
        )
        if not single_mount:
            db.add(
                ChannelBindingAgent(
                    tenant_id="tenant_demo",
                    binding_id=binding.id,
                    agent_id="agent_cw",
                    is_default=False,
                    sort_order=1,
                )
            )
        db.commit()
        return binding.id


def _load_binding(engine, binding_id: str) -> ChannelBinding:
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        db.expunge(binding)
        return binding


def test_sticky_rules_skip_classification() -> None:
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    with Session(engine) as db:
        db.add(
            ChannelConvState(
                tenant_id="tenant_demo",
                binding_id=binding_id,
                external_conv_id="wechat_p2p_u1",
                current_agent_id="agent_xz",
            )
        )
        db.add(
            ChatSession(
                id="s_sop",
                tenant_id="tenant_demo",
                agent_id="agent_xz",
                channel="wechat",
                external_conv_id="wechat_p2p_u1",
                channel_binding_id=binding_id,
                status="handoff",
            )
        )
        db.commit()
        binding = db.get(ChannelBinding, binding_id)
        # handoff 进行中:硬跳过
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销吗") is None

        # 手动保护窗内:硬跳过
        session = db.get(ChatSession, "s_sop")
        session.status = "active"
        db.add(session)
        state = db.exec(select(ChannelConvState)).one()
        state.manual_pin_until = utc_now() + timedelta(minutes=5)
        db.add(state)
        db.commit()
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销吗") is None

    assert FakeLLMClient.calls == []


def test_sop_active_uses_higher_threshold() -> None:
    # Product: auto_route_enabled always False → no switch (SOP path dead)
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.92, "reason": "明确财务"})
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销吗") is None
    assert FakeLLMClient.calls == []


def test_no_sop_uses_normal_threshold() -> None:
    # Product: auto_route_enabled always False → no switch
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.8, "reason": "财务问题"})
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销吗") is None
    assert FakeLLMClient.calls == []


def test_parse_command_current_alias() -> None:
    from app.channels.service_routing import parse_command

    assert parse_command("/目前").kind == "current"
    assert parse_command("/当前").kind == "current"


def test_auto_route_disabled_and_single_mount_skip() -> None:
    engine = _test_engine()
    binding_id = _seed_binding(engine, auto_route=False)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销吗") is None

    engine2 = _test_engine()
    binding_id2 = _seed_binding(engine2, single_mount=True)
    with Session(engine2) as db:
        binding = db.get(ChannelBinding, binding_id2)
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销吗") is None

    assert FakeLLMClient.calls == []


def test_auto_route_hit_updates_pointer() -> None:
    # Product: auto_route_enabled always False → no switch
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销怎么走") is None


def test_auto_route_does_not_overwrite_manual_switch_during_classification(monkeypatch) -> None:
    # Product: auto_route_enabled always False → classify never runs
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        resolve_current_agent(db, binding, "wechat_p2p_u1")
        db.commit()
        assert maybe_auto_route(db, binding, "agent_xz", "wechat_p2p_u1", "报销吗") is None
        current, _ = resolve_current_agent(db, binding, "wechat_p2p_u1")
        assert current == "agent_xz"


# ---------- intake 集成 ----------


def _p2p_message(event_id: str, text: str) -> dict:
    return {
        "message_id": event_id,
        "from_user_id": "user_ab12cd34@im.wechat",
        "to_user_id": "bot_1@im.bot",
        "client_id": f"wx-{event_id}",
        "session_id": "user_ab12cd34@im.wechat#bot_1@im.bot",
        "message_type": 1,
        "message_state": 2,
        "context_token": f"ctx_{event_id}",
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


class RecordingAgentLoop:
    calls: list = []

    def __init__(self, db, *, event_sink=None):
        self.db = db

    def handle_turn(self, request):
        type(self).calls.append(request)
        self.db.commit()


@pytest.fixture(autouse=True)
def _fake_agent_loop(monkeypatch):
    RecordingAgentLoop.calls = []
    monkeypatch.setattr(agent_loop_module, "AgentLoop", RecordingAgentLoop)
    monkeypatch.setattr(intake_module, "_send_wechat_typing", lambda *args, **kwargs: None)
    yield


def test_intake_auto_route_switches_with_notice_and_event() -> None:
    # Product: auto_route off → stay on default, no switch notice/event
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    binding = _load_binding(engine, binding_id)
    FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.9, "reason": "财务问题"})

    assert process_inbound(binding, _p2p_message("evt_ar1", "报销怎么走"), db_engine=engine) is True
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_xz"
    assert FakeLLMClient.calls == []

    with Session(engine) as db:
        notices = db.exec(
            select(ChannelDelivery).where(ChannelDelivery.kind == "notice")
        ).all()
        assert notices == []
        events = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "auto_route_decision")
        ).all()
        assert events == []
        chat_session = db.exec(select(ChatSession)).one()
        assert chat_session.agent_id == "agent_xz"


def test_intake_sop_event_carries_effective_threshold() -> None:
    # Product: auto_route off → no classification / decision event
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    with Session(engine) as db:
        db.add(
            ChannelConvState(
                tenant_id="tenant_demo",
                binding_id=binding_id,
                external_conv_id="wechat_p2p_user_ab12cd34@im.wechat",
                current_agent_id="agent_xz",
            )
        )
        db.add(
            ChatSession(
                id="s_sop_intake",
                tenant_id="tenant_demo",
                agent_id="agent_xz",
                channel="wechat",
                external_conv_id="wechat_p2p_user_ab12cd34@im.wechat",
                channel_binding_id=binding_id,
                active_skill_id="meeting_room_sop",
            )
        )
        db.commit()
    binding = _load_binding(engine, binding_id)
    FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.85, "reason": "财务倾向"})

    assert process_inbound(binding, _p2p_message("evt_sop1", "报销怎么走"), db_engine=engine) is True
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_xz"
    assert FakeLLMClient.calls == []
    with Session(engine) as db:
        events = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "auto_route_decision")
        ).all()
        assert events == []


def test_intake_auto_route_stay_no_notice() -> None:
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    binding = _load_binding(engine, binding_id)

    assert process_inbound(binding, _p2p_message("evt_ar2", "会议室还有吗"), db_engine=engine) is True
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_xz"
    with Session(engine) as db:
        notices = db.exec(
            select(ChannelDelivery).where(ChannelDelivery.kind == "notice")
        ).all()
        assert notices == []
        events = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "auto_route_decision")
        ).all()
        assert events == []


def test_intake_new_conversation_initial_dispatch() -> None:
    # Product: auto_route off → first message stays on default owner
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    binding = _load_binding(engine, binding_id)
    FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.95, "reason": "首条即财务"})

    assert process_inbound(binding, _p2p_message("evt_ar3", "我要报销"), db_engine=engine) is True
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_xz"
    assert FakeLLMClient.calls == []


def test_manual_switch_writes_pin_window_and_blocks_auto_route() -> None:
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    binding = _load_binding(engine, binding_id)
    FakeLLMClient.script = json.dumps({"agent_id": "agent_xz", "confidence": 0.99, "reason": "行政问题"})

    # 手动切换到财务:写 10 分钟保护窗(手动路由仍可用)
    assert process_inbound(binding, _p2p_message("evt_ar4", "/切换 财务"), db_engine=engine) is False
    with Session(engine) as db:
        state = db.exec(select(ChannelConvState)).one()
        assert state.current_agent_id == "agent_cw"
        assert state.manual_pin_until is not None
        # SQLite may strip tz; compare naive walls
        pin = state.manual_pin_until.replace(tzinfo=None)
        now = utc_now().replace(tzinfo=None)
        assert pin > now + timedelta(minutes=9)

    # 保护窗内 / 窗外:产品路径永不智能分发,LLM 不被调用,粘性停在财务
    assert process_inbound(binding, _p2p_message("evt_ar5", "帮我订会议室"), db_engine=engine) is True
    assert FakeLLMClient.calls == []
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_cw"

    with Session(engine) as db:
        state = db.exec(select(ChannelConvState)).one()
        state.manual_pin_until = utc_now() - timedelta(minutes=1)
        db.add(state)
        db.commit()
    assert process_inbound(binding, _p2p_message("evt_ar6", "帮我订会议室"), db_engine=engine) is True
    assert FakeLLMClient.calls == []
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_cw"


def test_intake_auto_route_disabled_uses_default() -> None:
    engine = _test_engine()
    binding_id = _seed_binding(engine, auto_route=False)
    binding = _load_binding(engine, binding_id)
    FakeLLMClient.script = json.dumps({"agent_id": "agent_cw", "confidence": 0.99, "reason": "财务"})

    assert process_inbound(binding, _p2p_message("evt_ar7", "我要报销"), db_engine=engine) is True
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_xz"
    assert FakeLLMClient.calls == []
    with Session(engine) as db:
        events = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "auto_route_decision")
        ).all()
        assert events == []


def test_intake_classification_failure_still_runs_turn() -> None:
    # Product: auto_route off → no classify, turn still runs on default
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    binding = _load_binding(engine, binding_id)
    FakeLLMClient.error = RuntimeError("LLM 超时")

    assert process_inbound(binding, _p2p_message("evt_ar8", "报销怎么走"), db_engine=engine) is True
    assert RecordingAgentLoop.calls[-1].agent_id == "agent_xz"
    assert FakeLLMClient.calls == []
    with Session(engine) as db:
        event = db.exec(select(ChannelInboundEvent)).one()
        assert event.status == "done"
        route_events = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "auto_route_decision")
        ).all()
        assert route_events == []


def test_intake_bad_json_error_in_decision_event() -> None:
    # Product: auto_route off → no classify / decision event
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    binding = _load_binding(engine, binding_id)
    FakeLLMClient.script = "这不是 JSON"

    assert process_inbound(binding, _p2p_message("evt_ar9", "报销怎么走"), db_engine=engine) is True
    assert FakeLLMClient.calls == []
    with Session(engine) as db:
        route_events = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "auto_route_decision")
        ).all()
        assert route_events == []


def test_normal_decision_event_error_empty() -> None:
    # Product: auto_route off → no decision event
    engine = _test_engine()
    binding_id = _seed_binding(engine)
    binding = _load_binding(engine, binding_id)

    assert process_inbound(binding, _p2p_message("evt_ar10", "会议室还有吗"), db_engine=engine) is True
    with Session(engine) as db:
        route_events = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "auto_route_decision")
        ).all()
        assert route_events == []


# ---------- PUT auto_route 读写 ----------


def _make_api_client(engine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.channels as channels_api
    from app.db import get_session

    app = FastAPI()
    app.include_router(channels_api.router)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _seed_api_users(engine) -> dict[str, User]:
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        owner = User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x")
        db.add(owner)
        db.add(
            AgentProfile(
                id="agent_xz",
                tenant_id="tenant_demo",
                name="行政",
                metadata_json={"owner_user_id": owner.id},
            )
        )
        db.commit()
        db.refresh(owner)
        db.expunge(owner)
        return {"owner": owner}


def _auth(user: User) -> dict[str, str]:
    from app.security.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_put_auto_route_read_write() -> None:
    engine = _test_engine()
    users = _seed_api_users(engine)
    with Session(engine) as db:
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_xz",
            channel="wechat",
            status="active",
            created_by_user_id="user_owner",
        )
        db.add(binding)
        db.commit()
        binding_id = binding.id

    client = _make_api_client(engine)
    # 读路径恒 False(智能分发已下线)
    listed = client.get(
        "/api/enterprise/channels?tenant_id=tenant_demo&agent_id=agent_xz",
        headers=_auth(users["owner"]),
    )
    assert listed.json()[0]["auto_route"] is False

    # 显式传 True 也被强制写成 False
    updated = client.put(
        f"/api/enterprise/channels/{binding_id}?tenant_id=tenant_demo",
        json={"agents": [{"agent_id": "agent_xz"}], "auto_route": True},
        headers=_auth(users["owner"]),
    )
    assert updated.status_code == 200
    assert updated.json()["auto_route"] is False
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert binding.config_json["auto_route"] is False

    # 不传 auto_route:读路径仍 False
    updated = client.put(
        f"/api/enterprise/channels/{binding_id}?tenant_id=tenant_demo",
        json={"agents": [{"agent_id": "agent_xz"}]},
        headers=_auth(users["owner"]),
    )
    assert updated.json()["auto_route"] is False

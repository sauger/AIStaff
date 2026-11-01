from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import chat as chat_api
from app.core.agent_loop import AgentLoop
from app.core.context_projection import compact_conversation_context
from app.core.conversation_context import build_conversation_context
from app.core.conversation_projection import ConversationProjection
from app.core.task_request_compiler import TaskRequirement, dump_task_requirement
from app.db.models import AgentProfile, ChatSession, Tenant, User
from app.honcho.service import (
    dual_write_message,
    inject_honcho_representation,
    schedule_honcho_message,
)
from app.llm.stage_protocol import render_stage_user_message, speaker_prompt_text, stage_payload
from app.session.session_schema import ChatTurnRequest, ChatTurnResponse, SessionPublic, TurnSpeaker
from app.session.turn_speaker import (
    current_speaker_for_requirement,
    portrait_user_id,
    resolve_known_user_speaker,
    resolve_web_speaker,
    resolve_wecom_bot_speaker,
    stamp_conversation_speaker,
)
from tests.test_honcho_service import FakeHoncho, _request, _seed, _settings


def test_web_speaker_uses_login_user_and_not_a_client_id() -> None:
    speaker = resolve_web_speaker(
        User(id="user_login", tenant_id="t", username="a", password_hash="x")
    )
    assert speaker.status == "resolved"
    assert speaker.user_id == "user_login"
    assert speaker.source == "web_login"
    missing = resolve_web_speaker(User(id="", tenant_id="t", username="a", password_hash="x"))
    assert missing.reason == "missing_login"


def test_wecom_private_speaker_uses_user_id_not_wecom_userid() -> None:
    speaker = resolve_wecom_bot_speaker(
        user_id="user_abc",
        is_group=False,
        external_user_id="zhangsan",
    )
    assert speaker.status == "resolved"
    assert speaker.user_id == "user_abc"
    assert speaker.source == "channel_identity"
    assert "zhangsan" not in speaker.model_dump().values()


def test_wecom_group_and_external_id_are_not_a_person() -> None:
    group = resolve_wecom_bot_speaker(
        user_id="user_group",
        is_group=True,
        external_user_id="zhangsan",
    )
    assert group.status == "unresolved"
    assert group.reason == "group_chat"
    assert group.user_id is None

    collided = resolve_wecom_bot_speaker(
        user_id="zhangsan",
        is_group=False,
        external_user_id="zhangsan",
    )
    assert collided.reason == "external_id_is_not_user"
    missing = resolve_wecom_bot_speaker(user_id=None, is_group=False, external_user_id="")
    assert missing.reason == "missing_channel_user"


def test_portrait_user_id_follows_speaker_and_refuses_external_id() -> None:
    resolved = _request(
        user_id="zhangsan",
        speaker=TurnSpeaker(
            status="resolved",
            user_id="user_login",
            channel="web",
            source="web_login",
        ),
    )
    assert portrait_user_id(resolved) == "user_login"

    unresolved = _request(
        user_id="user_group",
        speaker=TurnSpeaker(
            status="unresolved",
            channel="wecom",
            source="unresolved",
            reason="group_chat",
        ),
    )
    assert portrait_user_id(unresolved) == ""

    collided = _request(user_id="zhangsan", channel_external_user_id="zhangsan")
    assert portrait_user_id(collided) == ""
    assert portrait_user_id(_request()) == "user_abc"


def test_unresolved_speaker_does_not_read_or_write_honcho() -> None:
    db = _seed_db()
    session = _seed(db, honcho_enabled=True)
    client = FakeHoncho()
    queued: list[dict] = []
    request = _request(
        speaker=TurnSpeaker(
            status="unresolved",
            channel="wecom",
            source="unresolved",
            reason="group_chat",
        )
    )

    schedule_honcho_message(
        db,
        request,
        session,
        role="user",
        content="你认识我吗",
        settings=_settings(),
        enqueue=queued.append,
    )
    memories = inject_honcho_representation(
        db,
        request,
        session,
        [{"kind": "fact", "content": "本地事实"}],
        settings=_settings(),
        client=client,
    )

    assert queued == []
    assert client.calls == []
    assert memories == [{"kind": "fact", "content": "本地事实"}]
    db.close()


def test_dual_write_refuses_channel_userid_as_peer() -> None:
    client = FakeHoncho()
    dual_write_message(
        {
            "tenant_id": "tenant_demo",
            "user_id": "zhangsan",
            "agent_id": "agent_xyz",
            "session_id": "session_1",
            "role": "user",
            "content": "你好",
            "channel": "wecom",
            "external_user_id": "zhangsan",
        },
        settings=_settings(),
        client=client,
    )
    assert client.calls == []


def test_speaker_is_stamped_into_model_context_without_external_id() -> None:
    speaker = resolve_wecom_bot_speaker(
        user_id="user_abc",
        is_group=False,
        external_user_id="zhangsan",
    )
    context = stamp_conversation_speaker(
        {"messages": [{"role": "user", "content": "你认识我吗"}], "metadata": {}},
        speaker,
    )
    assert context["metadata"]["speaker"]["user_id"] == "user_abc"
    assert "zhangsan" not in str(context["metadata"]["speaker"])

    payload = stage_payload(
        phase="Response Generator",
        user_message="你认识我吗",
        conversation_context=context,
        memory_context=[{"content": "长期用户画像\n偏好上午沟通"}],
        instructions="回答用户。",
        stage_data={},
        output_contract="纯文本",
    )
    rendered = render_stage_user_message(payload)
    assert "当前对话用户：\nuser_id=user_abc\nchannel=wecom" in rendered
    assert "不要编造姓名或部门" in rendered
    assert "zhangsan" not in rendered
    assert speaker_prompt_text({"status": "unresolved", "reason": "group_chat"}) == "未确定"


def test_compaction_keeps_speaker() -> None:
    context = {
        "messages": [
            {"role": "user", "content": "甲" * 800},
            {"role": "assistant", "content": "乙" * 800},
            {"role": "user", "content": "丙" * 800},
        ],
        "metadata": {
            "token_budget": 200,
            "estimated_tokens": 900,
            "speaker": {"status": "resolved", "user_id": "user_abc", "channel": "web"},
        },
    }
    compacted = compact_conversation_context(context, token_budget=200)
    assert compacted["metadata"]["speaker"]["user_id"] == "user_abc"


def test_user_message_metadata_records_resolved_speaker() -> None:
    request = ChatTurnRequest(
        tenant_id="tenant_demo",
        message="你好",
        speaker=TurnSpeaker(
            status="resolved",
            user_id="user_abc",
            channel="wecom",
            source="channel_identity",
        ),
    )
    assert ConversationProjection.user_message_metadata(request)["speaker_user_id"] == "user_abc"


def test_requirement_speaker_drops_directory_and_external_id() -> None:
    speaker = TurnSpeaker(
        status="resolved",
        user_id="user_abc",
        channel="wecom",
        source="channel_identity",
    )
    payload = current_speaker_for_requirement(speaker)
    assert payload == {
        "status": "resolved",
        "user_id": "user_abc",
        "channel": "wecom",
        "source": "channel_identity",
    }
    requirement = TaskRequirement(
        task_frame_id="task-1",
        kind="conversation",
        goal="回答",
        current_speaker=payload,
    )
    dumped = dump_task_requirement(requirement)
    assert dumped["current_speaker"]["user_id"] == "user_abc"
    assert "external_user_id" not in dumped["current_speaker"]
    assert "current_speaker" not in dump_task_requirement(
        TaskRequirement(task_frame_id="task-2", kind="conversation", goal="回答")
    )


def test_conversation_context_metadata_carries_the_resolved_speaker() -> None:
    speaker = resolve_web_speaker(
        User(id="user_login", tenant_id="t", username="a", password_hash="x"),
        channel="web",
    )
    context = stamp_conversation_speaker(
        build_conversation_context(
            [{"role": "user", "content": "你认识我吗", "id": "m1"}]
        ),
        speaker,
    )
    assert context["metadata"]["speaker"] == {
        "status": "resolved",
        "user_id": "user_login",
        "channel": "web",
        "source": "web_login",
    }


def test_rebuilt_wecom_request_fails_closed_without_speaker() -> None:
    original = _request(
        speaker=TurnSpeaker(
            status="resolved",
            user_id="user_abc",
            channel="wecom",
            source="channel_identity",
        )
    )
    rebuilt = ChatTurnRequest(**original.model_dump())
    assert rebuilt.speaker is None
    assert rebuilt.channel_external_user_id is None
    assert portrait_user_id(rebuilt) == ""

    db = _seed_db()
    session = _seed(db, honcho_enabled=True)
    queued: list[dict] = []
    schedule_honcho_message(
        db,
        rebuilt,
        session,
        role="user",
        content="你好",
        settings=_settings(),
        enqueue=queued.append,
    )
    assert queued == []
    db.close()


def test_web_dialog_turn_overwrites_client_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        login = User(
            id="user_login",
            tenant_id="tenant_demo",
            username="login",
            password_hash="x",
        )
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(login)
        db.add(AgentProfile(id="agent_1", tenant_id="tenant_demo", name="助理", is_overall=False))
        db.add(
            ChatSession(
                id="session_web",
                tenant_id="tenant_demo",
                user_id="user_login",
                agent_id="agent_1",
            )
        )
        db.commit()
        seen: dict[str, ChatTurnRequest] = {}

        def fake_handle_turn(self, request: ChatTurnRequest) -> ChatTurnResponse:
            seen["request"] = request
            return ChatTurnResponse(
                reply="认识。",
                session_id=request.session_id or "session_web",
                session_state=SessionPublic(
                    session_id=request.session_id or "session_web",
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                ),
            )

        monkeypatch.setattr(AgentLoop, "handle_turn", fake_handle_turn)
        monkeypatch.setattr(
            chat_api,
            "_schedule_session_title_summary",
            lambda *args, **kwargs: None,
        )
        chat_api.chat_turn(
            ChatTurnRequest(
                tenant_id="tenant_demo",
                session_id="session_web",
                user_id="zhangsan",
                message="你认识我吗",
                channel="web",
                channel_external_user_id="zhangsan",
                speaker=TurnSpeaker(
                    status="resolved",
                    user_id="zhangsan",
                    channel="wecom",
                    source="channel_identity",
                ),
            ),
            login,
            db,
        )

        request = seen["request"]
        assert request.user_id == "user_login"
        assert request.speaker is not None
        assert request.speaker.status == "resolved"
        assert request.speaker.user_id == "user_login"
        assert request.speaker.source == "web_login"
        assert request.channel_external_user_id is None
        assert request.channel_is_group is False
        assert request.channel_external_account_scope is None
        assert request.channel_target_json is None
        assert portrait_user_id(request) == "user_login"
        session = db.get(ChatSession, "session_web")
        assert session is not None
        assert session.user_id == "user_login"


def test_web_turn_forged_external_id_still_uses_login_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        login = User(
            id="user_login",
            tenant_id="tenant_demo",
            username="login",
            password_hash="x",
        )
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(login)
        db.add(AgentProfile(id="agent_1", tenant_id="tenant_demo", name="助理", is_overall=False))
        db.add(
            ChatSession(
                id="session_web",
                tenant_id="tenant_demo",
                user_id="user_login",
                agent_id="agent_1",
            )
        )
        db.commit()
        seen: dict[str, ChatTurnRequest] = {}

        def fake_handle_turn(self, request: ChatTurnRequest) -> ChatTurnResponse:
            seen["request"] = request
            return ChatTurnResponse(
                reply="认识。",
                session_id=request.session_id or "session_web",
                session_state=SessionPublic(
                    session_id=request.session_id or "session_web",
                    tenant_id=request.tenant_id,
                ),
            )

        monkeypatch.setattr(AgentLoop, "handle_turn", fake_handle_turn)
        monkeypatch.setattr(
            chat_api,
            "_schedule_session_title_summary",
            lambda *args, **kwargs: None,
        )
        chat_api.chat_turn(
            ChatTurnRequest(
                tenant_id="tenant_demo",
                session_id="session_web",
                user_id="user_login",
                message="你认识我吗",
                channel="web",
                channel_external_user_id="user_login",
                channel_external_account_scope="corp_a",
                channel_target_json={"to_user_id": "user_login"},
                channel_is_group=True,
            ),
            login,
            db,
        )

    request = seen["request"]
    assert request.user_id == "user_login"
    assert request.speaker is not None
    assert request.speaker.user_id == "user_login"
    assert request.channel_external_user_id is None
    assert request.channel_is_group is False
    assert portrait_user_id(request) == "user_login"

    honcho_db = _seed_db()
    session = _seed(honcho_db, honcho_enabled=True)
    queued: list[dict] = []
    schedule_honcho_message(
        honcho_db,
        request.model_copy(
            update={"agent_id": session.agent_id, "session_id": session.id}
        ),
        session,
        role="user",
        content="你认识我吗",
        settings=_settings(),
        enqueue=queued.append,
    )
    assert queued[0]["user_id"] == "user_login"
    assert queued[0]["external_user_id"] is None
    honcho_db.close()


def test_known_user_speaker_is_the_portrait_peer() -> None:
    speaker = resolve_known_user_speaker("user_demo", channel="human_handoff_resume")
    request = ChatTurnRequest(
        tenant_id="tenant_demo",
        user_id="user_other",
        message="继续",
        channel="human_handoff_resume",
        channel_external_user_id="zhangsan",
        speaker=speaker,
    )
    assert speaker.source == "session_user"
    assert portrait_user_id(request) == "user_demo"


def test_web_dialog_without_login_id_is_rejected() -> None:
    anonymous = User(id="", tenant_id="tenant_demo", username="anon", password_hash="x")
    with pytest.raises(HTTPException) as exc_info:
        chat_api._bind_web_dialog_speaker(
            ChatTurnRequest(tenant_id="tenant_demo", message="你好", user_id="zhangsan"),
            anonymous,
        )
    assert exc_info.value.status_code == 401


def _seed_db():
    from tests.test_honcho_service import _db

    return _db()

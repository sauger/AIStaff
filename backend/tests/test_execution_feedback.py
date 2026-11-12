import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.chat import _event_trace_line, _relay_event_payload
from app.channels.adapters.wecom import WeComStreamReply
from app.core.execution_feedback import execution_feedback
from app.db.models import AgentEvent
from app.observability.event_log import EventLog


@pytest.mark.parametrize(
    "event,payload,text",
    [
        ("user_message_received", {}, "已收到"),
        ("harness_action_started", {"iteration": 2}, "下一步"),
        ("harness_action_created", {"action": "tool", "tool_name": "mail_list"}, "邮件头"),
        ("harness_action_created", {"action": "tool", "tool_name": "mail_read"}, "选中的邮件"),
        ("harness_action_created", {"action": "finish"}, "整理"),
        ("harness_tool_completed", {"success": False}, "未成功"),
        ("harness_no_progress_stopped", {}, "停止"),
        ("harness_step_timeout", {}, "停止"),
        ("task_frame_finished", {"status": "awaiting_user"}, "暂停"),
    ],
)
def test_feedback_uses_stage_without_exposing_inputs(event, payload, text):
    payload.update(
        arguments={"password": "secret"}, result={"body": "private-mail"}, error="private-error"
    )
    feedback = execution_feedback(event, payload)
    assert text in feedback["text"]
    assert all(value not in str(feedback) for value in ["secret", "private-mail", "private-error"])
    assert execution_feedback("execution_progress", feedback) is None


def test_live_and_durable_feedback_reaches_both_channels_before_tool_finishes():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    emitted = []
    with Session(engine) as db:
        log = EventLog(db, event_sink=lambda event, payload: emitted.append((event, payload)))
        log.bind_turn("turn_1", "client_1")
        log.record(
            "t",
            "s",
            "harness_action_created",
            {"action": "tool", "tool_name": "mail_list", "task_frame_id": "frame"},
        )
        db.commit()
        progress = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "execution_progress")
        ).one()
        assert progress.payload_json["turn_id"] == "turn_1"
        assert progress.payload_json["client_turn_id"] == "client_1"
        assert [event for event, _ in emitted] == ["harness_action_created", "execution_progress"]
        assert _relay_event_payload(progress)[0] == "execution_progress"
        assert _event_trace_line(progress, {})["text"] == progress.payload_json["text"]
        reply = WeComStreamReply.__new__(WeComStreamReply)
        text = reply._event_message("execution_progress", progress.payload_json)
        assert "邮件头" in text
        assert not db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "harness_tool_completed")
        ).all()


def test_internal_turn_visibility_is_preserved():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        log = EventLog(db)
        log.record("t", "s", "user_message_received", {"message_visibility": "internal"})
        db.commit()
        progress = db.exec(
            select(AgentEvent).where(AgentEvent.event_type == "execution_progress")
        ).one()
        assert progress.payload_json["message_visibility"] == "internal"


def test_wecom_sends_live_progress_before_final_answer():
    from test_channel_wecom import _FakeStreamClient, _run_loop_in_thread, _wait_for

    from app.db.models import ChannelBinding

    client = _FakeStreamClient()
    loop, thread = _run_loop_in_thread()
    reply = WeComStreamReply(
        ChannelBinding(id="binding-feedback", tenant_id="t", agent_id="a", channel="wecom"),
        {"headers": {"req_id": "feedback-test"}, "body": {"msgid": "feedback-test"}},
        (client, loop),
    )
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            events = EventLog(db, event_sink=reply.on_event)
            events.record(
                "t", "s", "harness_action_created", {"action": "tool", "tool_name": "mail_list"}
            )
            assert _wait_for(lambda: any("邮件头" in item[1]["content"] for item in client.sent))
            assert not any(item[1].get("stream_id", "").endswith(":answer") for item in client.sent)
            reply.on_delta("处理完成")
            assert reply.finish()
            assert client.sent[-1][1]["content"] == "处理完成"
    finally:
        reply.abort()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)

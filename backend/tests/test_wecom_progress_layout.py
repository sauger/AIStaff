import unicodedata

from test_channel_wecom import _FakeStreamClient, _run_loop_in_thread, _wait_for

from app.channels.adapters.wecom import WeComStreamReply
from app.db.models import ChannelBinding


def test_hourglass_stays_in_one_row_through_intent_answer_and_finish():
    client = _FakeStreamClient()
    loop, thread = _run_loop_in_thread()
    reply = WeComStreamReply(
        ChannelBinding(id="stable", tenant_id="t", agent_id="a", channel="wecom"),
        {"headers": {"req_id": "stable"}, "body": {"msgid": "one"}},
        (client, loop),
    )

    def progress():
        return [body for _, body in client.sent if body["stream_id"].endswith(":progress")]

    try:
        assert _wait_for(lambda: any("正在思考" in p["content"] for p in progress()))
        reply.on_event("router_decision_created", {"user_intent": "查找邮件"})
        assert len(reply._progress) == 1
        assert _wait_for(lambda: any("已判断意图" in p["content"] for p in progress()))
        reply.on_event(
            "execution_progress", {"text": "正在检索邮件标题和邮件头…", "state": "running"}
        )
        assert _wait_for(
            lambda: len({p["content"][0] for p in progress() if "正在检索" in p["content"]}) == 2
        )
        reply.on_delta("最终结果")
        assert _wait_for(lambda: any("正在生成回复" in p["content"] for p in progress()))
        assert reply.finish()
        assert progress()[-1]["content"] == "✅ 本轮处理结束"
        assert progress()[-1]["finish"] is True
        assert all(p["content"] and "\n" not in p["content"] for p in progress())
        assert len({p["stream_id"] for p in progress()}) == 1
        assert client.sent[-1][1]["content"] == "最终结果"
    finally:
        reply.abort()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)


def test_status_bounds_long_names_and_newlines_without_mutating_result():
    reply = WeComStreamReply.__new__(WeComStreamReply)
    original = "⏳ 正在执行\n\n" + "特别长的流程名称" * 20
    reply._progress = [("status", original)]
    text = reply._render_progress_unlocked()
    assert "\n" not in text and text.endswith("…")
    assert sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in text) <= 32
    assert reply._progress == [("status", original)]

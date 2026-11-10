import time
from types import SimpleNamespace

from app.core.harness_v2_engine import (
    _apply_turn_budget_exhausted,
    _earliest_deadline,
    _turn_budget_seconds,
)
from app.core.task_request_compiler import TaskExecutionResult


def _result(**overrides):
    values = {
        "task_frame_id": "tf_budget",
        "status": "action_budget",
        "reply_fragment": "已查到账单明细，正在汇总。",
        "action_count": 3,
    }
    values.update(overrides)
    return TaskExecutionResult(**values)


def test_expired_budget_fails_without_dropping_partial_answer():
    result = _result()
    trace_events: list[tuple[str, dict]] = []

    updated = _apply_turn_budget_exhausted(
        result,
        time.monotonic() - 1,
        120.0,
        trace=lambda name, payload: trace_events.append((name, payload)),
    )

    assert updated.status == "failed"
    assert updated.error is not None
    assert updated.error["code"] == "TURN_BUDGET_EXCEEDED"
    assert updated.error["budget_seconds"] == 120
    assert updated.reply_fragment == "已查到账单明细，正在汇总。"
    assert updated.action_count == 3
    assert trace_events[0][0] == "harness_turn_budget_exhausted"
    assert trace_events[0][1]["previous_status"] == "action_budget"


def test_expired_budget_supplies_a_reply_when_model_produced_none():
    updated = _apply_turn_budget_exhausted(
        _result(status="failed", reply_fragment=""),
        time.monotonic() - 1,
        120.0,
    )

    assert updated.status == "failed"
    assert updated.reply_fragment.startswith("本轮执行超过 120 秒预算")


def test_budget_still_running_leaves_result_untouched():
    result = _result()

    assert _apply_turn_budget_exhausted(result, time.monotonic() + 60, 120.0) is result


def test_completed_and_awaiting_user_results_are_never_rewritten():
    for status in ("completed", "awaiting_user", "handoff", "waiting_external_task"):
        result = _result(status=status, reply_fragment="done")
        assert _apply_turn_budget_exhausted(result, time.monotonic() - 1, 120.0) is result


def test_react_mode_without_budget_keeps_behaviour():
    result = _result()

    assert _apply_turn_budget_exhausted(result, None, None) is result


def test_earliest_deadline_prefers_the_tighter_limit():
    assert _earliest_deadline(None, 30.0, 10.0) == 10.0
    assert _earliest_deadline(None, None) is None
    assert _earliest_deadline(12.0) == 12.0


def test_turn_budget_reads_settings(monkeypatch):
    monkeypatch.setattr(
        "app.core.harness_v2_engine.get_settings",
        lambda: SimpleNamespace(agent_turn_budget_seconds=45.0),
    )
    assert _turn_budget_seconds() == 45.0

    monkeypatch.setattr(
        "app.core.harness_v2_engine.get_settings",
        lambda: SimpleNamespace(agent_turn_budget_seconds=0),
    )
    assert _turn_budget_seconds() is None

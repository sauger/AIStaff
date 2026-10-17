from app.core.task_request_compiler import TaskExecutionResult
from app.core.user_visible_reply import ensure_user_visible_reply

BUDGET = "当前任务已达到本轮自动执行上限，需要下一轮继续。"


def _result(**kwargs) -> TaskExecutionResult:
    payload = {"task_frame_id": "tf_test", "status": "completed"}
    payload.update(kwargs)
    return TaskExecutionResult(**payload)


def test_empty_reply_is_replaced_with_human_language() -> None:
    text = ensure_user_visible_reply(_result(reply_fragment=""))
    assert text.strip()
    assert not text.lstrip().startswith("{")
    assert "没有" in text or "未" in text or "结论" in text


def test_json_reply_is_rewritten_even_when_it_matches_structured_result() -> None:
    payload = {"ok": False, "exit_code": 1, "stderr": "boom"}
    text = ensure_user_visible_reply(
        _result(reply_fragment='{"ok":false,"exit_code":1}', structured_result=payload)
    )
    assert not text.lstrip().startswith("{")
    assert "上限" not in text or "自动执行" in text


def test_action_budget_keeps_limit_sentence_and_appends_artifact_path() -> None:
    text = ensure_user_visible_reply(
        _result(
            status="action_budget",
            reply_fragment=BUDGET,
            artifacts=[{
                "path": "消费账单/迅傲测试/2026-10/迅傲测试_2026-10_平台消费账单.json",
                "name": "迅傲测试_2026-10_平台消费账单.json",
            }],
        )
    )
    assert BUDGET in text
    assert "消费账单" in text
    assert "迅傲测试" in text


def test_action_budget_mentions_zero_when_summary_says_empty() -> None:
    text = ensure_user_visible_reply(
        _result(
            status="action_budget",
            reply_fragment=BUDGET,
            task_summary="本月 0 条明细",
        )
    )
    assert BUDGET in text
    assert "0" in text


def test_plain_chinese_reply_is_kept() -> None:
    text = ensure_user_visible_reply(
        _result(reply_fragment="迅傲测试 10 月合计 108,855.67 元。")
    )
    assert text == "迅傲测试 10 月合计 108,855.67 元。"

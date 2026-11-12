from copy import deepcopy

import pytest

from app.core.harness_v2_engine import _single_task_reply
from app.core.task_request_compiler import TaskExecutionResult


def completed():
    return TaskExecutionResult(
        task_frame_id="mail",
        status="completed",
        reply_fragment="账单，附件 invoice.xlsx 已保存到邮件附件/收件。",
        structured_result={"saved": True},
        capability_results=[
            {
                "tool_name": "mail_read",
                "success": True,
                "error": None,
                "data": {
                    "subject": "账单",
                    "attachment_verification_version": 1,
                    "all_attachments_verified": True,
                    "attachments": [
                        {
                            "filename": "invoice.xlsx",
                            "saved": True,
                            "verified": True,
                            "sha256": "abc",
                            "cabinet_path": "邮件附件/收件/invoice.xlsx",
                        }
                    ],
                },
            }
        ],
    )


def test_verified_single_mail_completion_reuses_the_finish_reply():
    result = completed()
    assert _single_task_reply([result]) == result.reply_fragment
    assert _single_task_reply([result, result]) is None


@pytest.mark.parametrize(
    "case",
    [
        "missing_filename",
        "missing_subject",
        "failed",
        "unchecked",
        "empty",
        "truncated",
        "other_tool",
        "json",
        "unfinished",
        "partial",
    ],
)
def test_synthesis_is_preserved_for_unsafe_or_incomplete_replies(case):
    result = deepcopy(completed())
    tool = result.capability_results[0]
    if case == "missing_filename":
        result.reply_fragment = "账单已保存。"
    elif case == "missing_subject":
        result.reply_fragment = "invoice.xlsx 已保存。"
    elif case == "failed":
        tool["success"] = False
    elif case == "unchecked":
        tool["data"]["attachments"][0]["verified"] = False
    elif case == "empty":
        tool["data_status"] = "empty"
    elif case == "truncated":
        tool["truncated"] = True
    elif case == "other_tool":
        tool["tool_name"] = "http_tool"
    elif case == "json":
        result.reply_fragment = '{"saved":true}'
    elif case == "unfinished":
        result.status = "awaiting_user"
    elif case == "partial":
        tool["data"]["errors"] = [{"message_id": "missing"}]
    assert _single_task_reply([result]) is None

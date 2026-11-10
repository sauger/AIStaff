from __future__ import annotations

import json

from app.core.result_summary import summarize_capability_result


def test_summary_keeps_status_counts_ids_and_amounts():
    result = {
        "tool_name": "tokenop.get_organization_account",
        "success": True,
        "data": {
            "total_amount": 1234.56,
            "currency": "CNY",
            "items": [
                {"id": f"org_{index}", "name": f"org {index}", "status": "active"}
                for index in range(30)
            ],
        },
    }

    summary = summarize_capability_result(result, max_chars=2000)

    assert summary["tool_name"] == "tokenop.get_organization_account"
    assert summary["success"] is True
    assert summary["data"]["total_amount"] == 1234.56
    items = summary["data"]["items"]
    assert items["count"] == 30
    assert [item["id"] for item in items["first"]] == [
        f"org_{index}" for index in range(20)
    ]
    assert summary["history_receipt"]["omitted_chars"] > 0


def test_summary_trims_long_strings_and_nested_objects():
    result = {
        "tool_name": "read_file",
        "success": True,
        "data": {
            "note": "x" * 500,
            "meta": {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6, "g": 7, "h": 8},
        },
    }

    summary = summarize_capability_result(result, max_chars=600)

    assert summary["data"]["note"].endswith("…")
    assert len(summary["data"]["note"]) <= 121
    assert summary["data"]["meta"]["count"] == 8


def test_summary_reports_errors_without_payload():
    result = {
        "tool_name": "exec_command",
        "success": False,
        "error": {
            "code": "TOOL_NOT_AVAILABLE",
            "message": "not authorized",
            "stack": "traceback " * 100,
        },
    }

    summary = summarize_capability_result(result)

    assert summary["error"] == {
        "code": "TOOL_NOT_AVAILABLE",
        "message": "not authorized",
    }


def test_summary_is_bounded_by_max_chars():
    result = {
        "tool_name": "knowledge_search",
        "success": True,
        "data": {
            f"field_{index}": "y" * 120 for index in range(12)
        },
    }

    summary = summarize_capability_result(result, max_chars=200)

    assert len(json.dumps(summary, ensure_ascii=False)) <= 200
    assert summary["history_receipt"]["omitted_chars"] > 0


def test_summary_of_empty_result_is_stable():
    summary = summarize_capability_result({"tool_name": "write_file", "success": True})

    assert summary["tool_name"] == "write_file"
    assert summary["success"] is True
    assert "data" not in summary
    assert summary["history_receipt"]["sha256"]

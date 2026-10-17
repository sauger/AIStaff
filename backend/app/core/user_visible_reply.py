from __future__ import annotations

import json

from app.core.task_request_compiler import TaskExecutionResult

ACTION_BUDGET_SENTENCE = "当前任务已达到本轮自动执行上限，需要下一轮继续。"
EMPTY_FALLBACK = "这一轮没有形成给您看的结论。请再说一次要查的企业、账期，或看文件柜里是否已有账单。"


def ensure_user_visible_reply(result: TaskExecutionResult) -> str:
    raw = str(result.reply_fragment or "").strip()
    if result.status == "action_budget":
        return _budget_reply(result, raw)
    if _looks_like_internal_payload(raw):
        return _from_work_done(result) or EMPTY_FALLBACK
    if raw:
        return raw
    return _from_work_done(result) or EMPTY_FALLBACK


def sanitize_assistant_reply(reply: str, *, status: str = "completed") -> str:
    return ensure_user_visible_reply(
        TaskExecutionResult(
            task_frame_id="finalize",
            status=status,
            reply_fragment=reply,
        )
    )


def _looks_like_internal_payload(text: str) -> bool:
    if not text:
        return False
    if text.startswith("{"):
        return True
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, (dict, list))


def _budget_reply(result: TaskExecutionResult, raw: str) -> str:
    head = raw if ACTION_BUDGET_SENTENCE in raw else ACTION_BUDGET_SENTENCE
    extra = _from_work_done(result)
    if extra and extra not in head:
        return f"{head}\n\n已经做成：{extra}"
    return head


def _from_work_done(result: TaskExecutionResult) -> str:
    parts: list[str] = []
    summary = str(result.task_summary or "").strip()
    if summary and summary != "Harness 达到 action budget。":
        parts.append(summary)
    for artifact in result.artifacts or []:
        if not isinstance(artifact, dict):
            continue
        path = str(artifact.get("path") or artifact.get("name") or "").strip()
        if path:
            parts.append(path)
    for item in result.capability_results or []:
        if not isinstance(item, dict) or not item.get("success"):
            continue
        name = str(item.get("tool_name") or "").strip()
        if name and name not in {"exec_command", "run_skill_script"}:
            parts.append(f"已调用 {name}")
    # keep first few; do not dump tool JSON
    unique = list(dict.fromkeys(parts))[:4]
    return "；".join(unique)

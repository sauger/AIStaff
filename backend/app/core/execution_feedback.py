"""Deterministic user-facing execution status; never includes tool inputs or results."""

from __future__ import annotations

from typing import Any

_TOOL_STAGES = {
    "mail_list": ("searching_mail", "正在检索邮件标题和邮件头…", "邮件检索完成，正在核对结果…"),
    "mail_read": ("reading_mail", "正在读取选中的邮件正文和附件…", "邮件读取完成，正在整理结果…"),
    "mail_draft": ("drafting_mail", "正在准备邮件草稿…", "邮件草稿已准备，正在整理结果…"),
    "mail_send": ("sending_mail", "正在发送邮件…", "邮件发送完成，正在整理结果…"),
    "knowledge_search": ("searching_knowledge", "正在检索业务资料…", "资料检索完成，正在核对结果…"),
    "cabinet_find": ("finding_files", "正在查找文件柜中的文件…", "文件查找完成，正在核对结果…"),
    "cabinet_list": ("finding_files", "正在查看文件柜…", "文件柜查询完成，正在核对结果…"),
    "cabinet_read": ("reading_files", "正在读取文件…", "文件读取完成，正在整理结果…"),
    "cabinet_save": ("saving_files", "正在保存文件到员工文件柜…", "文件保存完成，正在整理结果…"),
    "capability_search": (
        "preparing_tools",
        "正在查找可用的处理能力…",
        "已找到处理能力，正在继续…",
    ),
    "capability_describe": ("preparing_tools", "正在准备处理能力…", "处理能力已准备，正在继续…"),
}


def execution_feedback(event_type: str, payload: dict[str, Any]) -> dict[str, str] | None:
    stage, text, state = "", "", "running"
    if event_type == "user_message_received":
        stage, text = "planning", "已收到，正在理解需求…"
    elif event_type == "task_frame_started":
        stage, text = "executing", "正在执行任务…"
    elif event_type == "harness_action_started":
        stage, text = "planning_action", "正在确定下一步操作…"
    elif event_type == "harness_action_created":
        if payload.get("action") == "finish":
            stage, text = "summarizing", "正在整理处理结果…"
        elif payload.get("action") == "tool":
            stage, text, _done = _TOOL_STAGES.get(
                str(payload.get("tool_name") or ""),
                ("using_tool", "正在执行工具操作…", "操作完成，正在继续…"),
            )
    elif event_type == "harness_tool_completed":
        if payload.get("success") is True:
            stage, _start, text = _TOOL_STAGES.get(
                str(payload.get("tool_name") or ""), ("using_tool", "", "操作完成，正在继续…")
            )
        else:
            stage, text = "handling_error", "这一步未成功，正在判断能否继续…"
    elif event_type in {
        "harness_no_progress_stopped",
        "harness_step_timeout",
        "harness_action_failed",
    }:
        stage, text, state = "stopped", "处理遇到问题，已停止继续执行，正在整理说明…", "failed"
    elif event_type == "task_frame_finished":
        status = str(payload.get("status") or "")
        if status in {"failed", "blocked", "cancelled"}:
            stage, text, state = "stopped", "任务未完成，正在整理原因和已完成的部分…", "failed"
        elif status in {"awaiting_user", "handoff", "waiting_external_task", "action_budget"}:
            stage, text = "waiting", "当前步骤已暂停，正在整理后续安排…"
        else:
            stage, text = "summarizing", "正在整理处理结果…"
    if not stage:
        return None
    return {"stage": stage, "text": text, "state": state}

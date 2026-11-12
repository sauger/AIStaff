"""Per-invocation progress accounting; never carries failures into a new user turn."""
from __future__ import annotations

import hashlib
import json
from typing import Any

DISCOVERY_TOOLS = {"capability_search", "capability_describe"}
READ_TOOLS = {"mail_list", "mail_read", "read_file", "file_info", "glob", "grep"}
PERMANENT_ERRORS = {
    "TOOL_NOT_AVAILABLE", "CAPABILITY_NOT_AVAILABLE", "INVALID_ARGUMENTS",
    "PERMISSION_DENIED", "UNSUPPORTED_INTERNAL_CAPABILITY",
}


def action_signature(name: str, arguments: dict) -> str:
    if name == "mail_list":
        arguments = {"folder": "inbox", "page": 1, "page_size": 20, **arguments}
    return fingerprint([name, arguments])


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, default=str,
    ).encode()).hexdigest()


class ExecutionProgress:
    max_stalled_actions = 4
    max_attempts = 3

    def __init__(self) -> None:
        self.stalled_actions = 0
        self.failures: dict[str, int] = {}
        self.permanent: set[str] = set()
        self.read_actions: set[str] = set()
        self.observations: set[str] = set()
        self.discovered: set[str] = set()
        self.last_reason = ""

    @property
    def exhausted(self) -> bool:
        return self.stalled_actions >= self.max_stalled_actions

    def snapshot(self) -> dict[str, Any]:
        return {
            "consecutive_actions_without_progress": self.stalled_actions,
            "stop_after": self.max_stalled_actions,
            "last_reason": self.last_reason,
            "instruction": (
                "工具成功不等于任务推进。不要重复已失败路径或相同读取；"
                "只有新证据、新资源或有效状态变化才算进展。没有可行替代路径时结束任务，"
                "说明实际障碍，不要索要无法解除障碍的信息。"
            ),
        }

    def is_read(self, name: str, descriptor: Any) -> bool:
        metadata = getattr(descriptor, "metadata", {}) or {}
        # Polling tools may intentionally re-observe changing external state.
        if (name == "external_task_status" or metadata.get("repeatable")
                or metadata.get("progress_policy") == "poll"):
            return False
        return name in READ_TOOLS or metadata.get("side_effect") == "read"

    def before(self, name: str, arguments: dict, descriptor: Any) -> dict | None:
        signature = action_signature(name, arguments)
        code = ""
        message = ""
        schema = getattr(descriptor, "input_schema", {}) or {}
        unknown = set(arguments) - set(schema.get("properties", {}))
        if schema.get("additionalProperties") is False and unknown:
            return {"success": False, "error": {
                "code": "INVALID_ARGUMENTS",
                "message": "工具不支持参数：" + "、".join(sorted(unknown)),
                "retryable": False,
            }}
        if signature in self.permanent:
            code = "NON_RETRYABLE_ACTION_REPEATED"
            message = "相同调用已明确不可重试，未再次执行；请换可行路径或结束任务。"
        elif self.failures.get(signature, 0) >= self.max_attempts:
            code = "TOOL_RETRY_EXHAUSTED"
            message = "相同调用已失败三次，未再次执行；请换可行路径或结束任务。"
        elif self.is_read(name, descriptor) and signature in self.read_actions:
            code = "REPEATED_READ_NO_PROGRESS"
            message = "相同读取已成功执行，未重复访问；请使用已有结果或改变查询范围。"
        if not code:
            return None
        return {"success": False, "error": {
            "code": code, "message": message, "retryable": False,
        }}

    def observe(self, name: str, arguments: dict, descriptor: Any, result: dict) -> None:
        signature = action_signature(name, arguments)
        progress = False
        if not result.get("success"):
            error = result.get("error")
            error = error if isinstance(error, dict) else {}
            code = str(error.get("code") or "TOOL_FAILED")
            # A duplicate read is a cached observation, not a permanent tool failure.
            # A later successful write must allow that resource to be read again.
            if code != "REPEATED_READ_NO_PROGRESS":
                self.failures[signature] = self.failures.get(signature, 0) + 1
                if error.get("retryable") is False or code in PERMANENT_ERRORS:
                    self.permanent.add(signature)
            self.last_reason = code
        elif name in DISCOVERY_TOOLS:
            data = result.get("data")
            data = data if isinstance(data, dict) else {}
            matches = data.get("matches", data.get("activated", [])) or []
            ids = {
                fingerprint(item.get("capability_id") or item.get("name") or item)
                if isinstance(item, dict) else fingerprint(item)
                for item in matches
            }
            progress = bool(ids - self.discovered)
            self.discovered.update(ids)
            self.last_reason = "能力发现没有新增可用能力" if not progress else ""
        elif self.is_read(name, descriptor):
            self.read_actions.add(signature)
            observation = fingerprint([name, result.get("data")])
            progress = observation not in self.observations
            self.observations.add(observation)
            self.last_reason = "读取结果没有新增信息" if not progress else ""
        else:
            progress = True
            metadata = getattr(descriptor, "metadata", {}) or {}
            if metadata.get("side_effect") == "write":
                self.read_actions.clear()
                self.observations.clear()
        self.stalled_actions = 0 if progress else self.stalled_actions + 1

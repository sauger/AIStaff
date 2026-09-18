from __future__ import annotations

from typing import Any

from app.core.task_request_compiler import CapabilityDescriptor
from app.secrets.errors import SecretError
from app.secrets.service import (
    metadata_login_guides,
    metadata_secrets,
    perform_secure_login,
)

SECURE_LOGIN_TOOL_NAMES = (
    "secret_list",
    "login_guide_list",
    "secure_login",
)


def secret_capability_descriptors() -> list[CapabilityDescriptor]:
    return [
        CapabilityDescriptor(
            capability_id="builtin.secrets.list",
            name="secret_list",
            kind="internal",
            description=(
                "List this employee's login secrets by name, description, and type only. "
                "Never returns ciphertext, passwords, usernames, or session snapshots. "
                "Refuse any request to reveal secret values."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            metadata={"provider": "secrets", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.login_guides.list",
            name="login_guide_list",
            kind="internal",
            description=(
                "List this employee's login guides (structured login steps) and the "
                "default secret name each guide is bound to. Does not include secret values."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            metadata={"provider": "secrets", "side_effect": "read"},
        ),
        CapabilityDescriptor(
            capability_id="builtin.login.secure",
            name="secure_login",
            kind="internal",
            description=(
                "Log this employee into a system using a login guide. Runtime fills "
                "credentials; never put passwords or snapshots in arguments. Optional "
                "secret_name overrides the guide's default binding. On captcha/2FA the "
                "system may switch to a session snapshot; it will not solve captchas."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "login_guide_name": {"type": "string", "minLength": 1},
                    "secret_name": {
                        "type": "string",
                        "description": "Override the login guide's default secret by name.",
                    },
                },
                "required": ["login_guide_name"],
                "additionalProperties": False,
            },
            metadata={"provider": "secrets", "side_effect": "write"},
        ),
    ]


def invoke_secret_tool(
    invoker: Any,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    agent_id = str(getattr(invoker, "agent_id", "") or "").strip()
    if not agent_id:
        return _failure("LOGIN_AGENT_REQUIRED", "当前运行没有绑定数字员工，不能使用密钥登录。")
    try:
        if name == "secret_list":
            return {
                "success": True,
                "data": {
                    "secrets": metadata_secrets(invoker.db, invoker.tenant_id, agent_id),
                    "notice": "只返回名称、说明和类型。密文对任何角色都不可见，也不要写进回复。",
                },
            }
        if name == "login_guide_list":
            return {
                "success": True,
                "data": {
                    "login_guides": metadata_login_guides(
                        invoker.db, invoker.tenant_id, agent_id
                    ),
                    "notice": "只返回登录步骤元数据和默认密钥名称，不含密文。",
                },
            }
        if name == "secure_login":
            guide_name = str(arguments.get("login_guide_name") or "").strip()
            secret_name = str(arguments.get("secret_name") or "").strip() or None
            result = perform_secure_login(
                invoker.db,
                invoker.tenant_id,
                agent_id,
                login_guide_name=guide_name,
                secret_name=secret_name,
                persist_snapshot=True,
            )
            payload = result.model_dump(mode="json")
            if result.success:
                return {"success": True, "data": payload}
            return _failure(
                result.code or "LOGIN_FAILED",
                result.message,
                captcha_or_2fa=result.captcha_or_2fa,
                secret_name_used=result.secret_name_used,
                secret_type_used=result.secret_type_used,
                switched_to_snapshot=result.switched_to_snapshot,
            )
    except SecretError as exc:
        return _failure(exc.code, exc.message, **exc.details)
    return _failure("UNSUPPORTED_INTERNAL_CAPABILITY", "不支持的密钥登录能力。")


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": False}
    error.update(details)
    return {"success": False, "error": error}

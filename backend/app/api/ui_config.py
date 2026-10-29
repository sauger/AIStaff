from __future__ import annotations

import os
import re
import signal
import sys
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session

from app import paths
from app.config import get_settings
from app.db import get_session
from app.db.models import UIConfig, User, utc_now
from app.harness.sandbox import diagnostics, windows_install_command
from app.honcho.runtime import apply_honcho_ui_config, display_honcho_base_url
from app.honcho.sidecar_env import (
    SIDECAR_PATH_UNSET_MESSAGE,
    SIDECAR_RESTART_MESSAGE,
    SIDECAR_SAVED_ONLY_MESSAGE,
    HonchoSidecarApplyError,
    apply_sidecar_env_file,
    build_honcho_model_env,
)
from app.security.auth import get_current_user, require_current_tenant
from app.security.encryption import decrypt_secret, encrypt_secret
from app.security.permissions import ensure_tenant_admin
from app.security.tenant import ensure_tenant

enterprise_router = APIRouter(
    prefix="/api/enterprise/ui-config",
    tags=["enterprise:ui-config"],
    dependencies=[Depends(get_current_user)],
)
chat_router = APIRouter(prefix="/api/chat/ui-config", tags=["chat:ui-config"])


class UIConfigRead(BaseModel):
    tenant_id: str
    show_thinking_trace: bool
    show_skill_trace: bool
    show_tool_trace: bool
    reflection_max_rounds: int
    agent_loop_max_actions: int
    context_token_budget: int
    context_compaction_trigger_ratio: float
    context_recent_round_limit: int
    context_long_summary_token_budget: int
    context_medium_summary_token_budget: int
    context_allowed_roles: list[Literal["user", "assistant"]]
    context_long_summary_prefix: str
    context_medium_summary_prefix: str
    sandbox_enabled: bool = False
    harness_storage_path: str = ""
    effective_harness_storage_path: str = ""
    restart_scheduled: bool = False
    sandbox_network_mode: Literal["all", "allowlist", "deny"] = "all"
    sandbox_allowed_domains: list[str] = Field(default_factory=list)
    sandbox_backend: str | None = None
    sandbox_setup_required: bool = False
    sandbox_setup_instructions: str | None = None
    sandbox_status: str = "unavailable"
    sandbox_status_code: str | None = None
    sandbox_status_message: str | None = None
    sandbox_status_remediation: str | None = None
    honcho_enabled: bool = False
    honcho_base_url: str = ""
    honcho_timeout_ms: int = 200
    honcho_deriver_model: str = ""
    honcho_deriver_base_url: str = ""
    honcho_deriver_api_key_set: bool = False
    honcho_embedding_model: str = ""
    honcho_embedding_base_url: str = ""
    honcho_embedding_api_key_set: bool = False
    honcho_embedding_dimensions: int = 1536
    honcho_sidecar_apply_available: bool = False
    honcho_sidecar_applied: bool = False
    honcho_sidecar_apply_message: str = ""
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class HonchoSidecarApplyRequest(BaseModel):
    tenant_id: str


class HonchoSidecarApplyRead(BaseModel):
    applied: bool
    message: str


class UIConfigUpdateRequest(BaseModel):
    tenant_id: str
    show_thinking_trace: bool = True
    show_skill_trace: bool = True
    show_tool_trace: bool = True
    reflection_max_rounds: int = Field(default=1, ge=0, le=5)
    agent_loop_max_actions: int = Field(default=32, ge=1, le=100)
    context_token_budget: int = Field(default=32_000, ge=512, le=262_144)
    context_compaction_trigger_ratio: float = Field(default=0.70, ge=0.10, le=0.95)
    context_recent_round_limit: int = Field(default=6, ge=1, le=50)
    context_long_summary_token_budget: int = Field(default=4_000, ge=128, le=32_768)
    context_medium_summary_token_budget: int = Field(default=4_000, ge=128, le=32_768)
    context_allowed_roles: list[Literal["user", "assistant"]] = Field(
        default_factory=lambda: ["user", "assistant"],
        min_length=1,
        max_length=2,
    )
    context_long_summary_prefix: str = Field(
        default="历史的信息可以被总结为：",
        min_length=1,
        max_length=200,
    )
    context_medium_summary_prefix: str = Field(
        default="近期的历史信息总结为：",
        min_length=1,
        max_length=200,
    )
    sandbox_enabled: bool = False
    harness_storage_path: str = Field(default="", max_length=1024)
    sandbox_network_mode: Literal["all", "allowlist", "deny"] = "all"
    sandbox_allowed_domains: list[str] = Field(default_factory=list, max_length=200)
    # None leaves the saved override unchanged. A value, including false or "",
    # becomes the tenant override and wins over HONCHO_* env.
    honcho_enabled: bool | None = None
    honcho_base_url: str | None = Field(default=None, max_length=500)
    honcho_timeout_ms: int | None = Field(default=None, ge=50, le=10_000)
    # None leaves the saved value unchanged. An empty API key also leaves the
    # stored secret unchanged. Model ids and URLs, including "", are saved.
    honcho_deriver_model: str | None = Field(default=None, max_length=200)
    honcho_deriver_base_url: str | None = Field(default=None, max_length=500)
    honcho_deriver_api_key: str | None = Field(default=None, max_length=4000)
    honcho_embedding_model: str | None = Field(default=None, max_length=200)
    honcho_embedding_base_url: str | None = Field(default=None, max_length=500)
    honcho_embedding_api_key: str | None = Field(default=None, max_length=4000)
    honcho_embedding_dimensions: int | None = Field(default=None, ge=1, le=8192)

    @model_validator(mode="after")
    def validate_context_budgets(self) -> UIConfigUpdateRequest:
        summary_budget = (
            self.context_long_summary_token_budget
            + self.context_medium_summary_token_budget
        )
        if summary_budget > self.context_token_budget:
            raise ValueError("长期与近期摘要预算之和不能超过上下文预算")
        self.context_allowed_roles = list(dict.fromkeys(self.context_allowed_roles))
        self.context_long_summary_prefix = self.context_long_summary_prefix.strip()
        self.context_medium_summary_prefix = self.context_medium_summary_prefix.strip()
        if not self.context_long_summary_prefix or not self.context_medium_summary_prefix:
            raise ValueError("摘要前缀不能为空")
        return self


def ui_config_read(
    row: UIConfig,
    *,
    restart_scheduled: bool = False,
    honcho_sidecar_applied: bool = False,
    honcho_sidecar_apply_message: str = "",
) -> UIConfigRead:
    effective_honcho = apply_honcho_ui_config(get_settings(), row)
    report = diagnostics() if row.sandbox_enabled else None
    windows_setup_required = (
        row.sandbox_enabled
        and sys.platform == "win32"
        and report is not None
        and report.code == "SANDBOX_WINDOWS_SETUP_REQUIRED"
    )
    return UIConfigRead(
        tenant_id=row.tenant_id,
        show_thinking_trace=row.show_thinking_trace,
        show_skill_trace=row.show_skill_trace,
        show_tool_trace=row.show_tool_trace,
        reflection_max_rounds=row.reflection_max_rounds,
        agent_loop_max_actions=row.agent_loop_max_actions,
        context_token_budget=row.context_token_budget,
        context_compaction_trigger_ratio=row.context_compaction_trigger_ratio,
        context_recent_round_limit=row.context_recent_round_limit,
        context_long_summary_token_budget=row.context_long_summary_token_budget,
        context_medium_summary_token_budget=row.context_medium_summary_token_budget,
        context_allowed_roles=[
            role
            for role in ("user", "assistant")
            if role in set(row.context_allowed_roles or [])
        ]
        or ["user", "assistant"],
        context_long_summary_prefix=(
            str(row.context_long_summary_prefix or "").strip()
            or "历史的信息可以被总结为："
        ),
        context_medium_summary_prefix=(
            str(row.context_medium_summary_prefix or "").strip()
            or "近期的历史信息总结为："
        ),
        sandbox_enabled=bool(row.sandbox_enabled),
        harness_storage_path=str(row.harness_storage_path or ""),
        effective_harness_storage_path=_effective_storage_path(row),
        restart_scheduled=restart_scheduled,
        sandbox_network_mode=(
            row.sandbox_network_mode
            if row.sandbox_network_mode in {"all", "allowlist", "deny"}
            else "deny"
        ),
        sandbox_allowed_domains=[
            str(item).strip()
            for item in (row.sandbox_allowed_domains or [])
            if str(item).strip()
        ],
        sandbox_backend=report.backend if report is not None else "disabled",
        sandbox_setup_required=windows_setup_required,
        sandbox_setup_instructions=(
            "StaffDeck 服务运行在 Windows 主机上，首次启用安全执行环境需要一次管理员确认。\n"
            "请在这台 Windows 电脑上打开 PowerShell 或 CMD（右键并选择‘以管理员身份运行’），执行：\n"
            f"{windows_install_command()}\n"
            "确认 UAC 后等待初始化完成，然后重启 StaffDeck 服务。"
            if windows_setup_required
            else (
                f"沙盒状态：{report.message}\n{report.remediation}"
                if report is not None and report.status != "ready"
                else None
            )
        ),
        sandbox_status=report.status if report is not None else "disabled",
        sandbox_status_code=report.code if report is not None else None,
        sandbox_status_message=(
            report.message if report is not None else "沙盒已由管理员关闭。"
        ),
        sandbox_status_remediation=report.remediation if report is not None else None,
        honcho_enabled=effective_honcho.honcho_enabled,
        honcho_base_url=display_honcho_base_url(effective_honcho.honcho_base_url),
        honcho_timeout_ms=effective_honcho.honcho_timeout_ms,
        honcho_deriver_model=str(row.honcho_deriver_model or "").strip(),
        honcho_deriver_base_url=display_honcho_base_url(str(row.honcho_deriver_base_url or "")),
        honcho_deriver_api_key_set=bool(row.honcho_deriver_api_key_encrypted),
        honcho_embedding_model=str(row.honcho_embedding_model or "").strip(),
        honcho_embedding_base_url=display_honcho_base_url(str(row.honcho_embedding_base_url or "")),
        honcho_embedding_api_key_set=bool(row.honcho_embedding_api_key_encrypted),
        honcho_embedding_dimensions=(
            1536
            if row.honcho_embedding_dimensions is None
            else int(row.honcho_embedding_dimensions)
        ),
        honcho_sidecar_apply_available=bool(get_settings().honcho_sidecar_env_file.strip()),
        honcho_sidecar_applied=honcho_sidecar_applied,
        honcho_sidecar_apply_message=honcho_sidecar_apply_message,
        updated_at=row.updated_at.isoformat(),
    )


def get_or_create_ui_config(db: Session, tenant_id: str) -> UIConfig:
    ensure_tenant(db, tenant_id)
    row = db.get(UIConfig, tenant_id)
    if not row:
        row = UIConfig(tenant_id=tenant_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@enterprise_router.get("", response_model=UIConfigRead, dependencies=[Depends(require_current_tenant)])
def get_enterprise_ui_config(
    tenant_id: str = Query(...), db: Session = Depends(get_session)
) -> UIConfigRead:
    return ui_config_read(get_or_create_ui_config(db, tenant_id))


@enterprise_router.put("", response_model=UIConfigRead)
def update_enterprise_ui_config(
    request: UIConfigUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UIConfigRead:
    ensure_tenant_admin(request.tenant_id, current_user)
    row = get_or_create_ui_config(db, request.tenant_id)
    sandbox_changed = bool(row.sandbox_enabled) != request.sandbox_enabled
    storage_path = _validate_storage_path(request.harness_storage_path)
    row.show_thinking_trace = request.show_thinking_trace
    row.show_skill_trace = request.show_skill_trace
    row.show_tool_trace = request.show_tool_trace
    row.reflection_max_rounds = request.reflection_max_rounds
    row.agent_loop_max_actions = request.agent_loop_max_actions
    row.context_token_budget = request.context_token_budget
    row.context_compaction_trigger_ratio = request.context_compaction_trigger_ratio
    row.context_recent_round_limit = request.context_recent_round_limit
    row.context_long_summary_token_budget = request.context_long_summary_token_budget
    row.context_medium_summary_token_budget = request.context_medium_summary_token_budget
    row.context_allowed_roles = request.context_allowed_roles
    row.context_long_summary_prefix = request.context_long_summary_prefix
    row.context_medium_summary_prefix = request.context_medium_summary_prefix
    row.sandbox_enabled = request.sandbox_enabled
    row.harness_storage_path = storage_path
    row.sandbox_network_mode = request.sandbox_network_mode
    row.sandbox_allowed_domains = request.sandbox_allowed_domains
    if request.honcho_enabled is not None:
        row.honcho_enabled = request.honcho_enabled
    if request.honcho_base_url is not None:
        row.honcho_base_url = _validate_honcho_base_url(request.honcho_base_url)
    if request.honcho_timeout_ms is not None:
        row.honcho_timeout_ms = request.honcho_timeout_ms
    models_touched = _assign_honcho_model_settings(row, request)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    if sandbox_changed:
        _schedule_application_restart()
    applied, apply_message = _sync_honcho_sidecar(row, touched=models_touched)
    return ui_config_read(
        row,
        restart_scheduled=sandbox_changed,
        honcho_sidecar_applied=applied,
        honcho_sidecar_apply_message=apply_message,
    )


@enterprise_router.post("/honcho-sidecar", response_model=HonchoSidecarApplyRead)
def apply_enterprise_honcho_sidecar(
    request: HonchoSidecarApplyRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> HonchoSidecarApplyRead:
    ensure_tenant_admin(request.tenant_id, current_user)
    row = get_or_create_ui_config(db, request.tenant_id)
    path = get_settings().honcho_sidecar_env_file.strip()
    if not path:
        raise HTTPException(status_code=409, detail=SIDECAR_PATH_UNSET_MESSAGE)
    try:
        _changed, message = _write_honcho_sidecar(row, path)
    except HonchoSidecarApplyError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return HonchoSidecarApplyRead(applied=True, message=message)


@chat_router.get("", response_model=UIConfigRead)
def get_chat_ui_config(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UIConfigRead:
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    return ui_config_read(get_or_create_ui_config(db, tenant_id))


_HONCHO_URL_LABEL = "Honcho 服务地址"
_MODEL_ID_ERROR = "模型 ID 只能包含字母、数字和 . _ : / + -"
_MODEL_ID = re.compile(r"^[A-Za-z0-9_.:/+-]{1,200}$")


def _validate_honcho_base_url(value: str, *, label: str = _HONCHO_URL_LABEL) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    malformed_scheme = lowered.startswith(("http:/", "https:/")) and not lowered.startswith(
        ("http://", "https://")
    )
    if "\\" in normalized or malformed_scheme:
        raise HTTPException(status_code=422, detail=f"{label}必须是 http 或 https URL")
    try:
        parsed = urlparse(normalized)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label}必须是 http 或 https URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail=f"{label}必须是 http 或 https URL")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail=f"{label}不能包含账号或密码")
    if parsed.query or parsed.fragment:
        raise HTTPException(status_code=422, detail=f"{label}不能包含查询参数或片段")
    host = parsed.hostname
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"


def _validate_honcho_model_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _MODEL_ID.fullmatch(text) is None:
        raise HTTPException(status_code=422, detail=_MODEL_ID_ERROR)
    return text


def _incoming_honcho_secret(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if any(char in text for char in "\r\n\x00"):
        raise HTTPException(status_code=422, detail="API 密钥不能包含换行")
    return text


def _assign_honcho_model_settings(row: UIConfig, request: UIConfigUpdateRequest) -> bool:
    touched = False
    if request.honcho_deriver_model is not None:
        row.honcho_deriver_model = _validate_honcho_model_id(request.honcho_deriver_model)
        touched = True
    if request.honcho_deriver_base_url is not None:
        row.honcho_deriver_base_url = _validate_honcho_base_url(
            request.honcho_deriver_base_url,
            label="对话抽取接口地址",
        )
        touched = True
    deriver_secret = _incoming_honcho_secret(request.honcho_deriver_api_key)
    if deriver_secret is not None:
        row.honcho_deriver_api_key_encrypted = encrypt_secret(deriver_secret)
        touched = True
    elif request.honcho_deriver_api_key is not None:
        touched = True
    if request.honcho_embedding_model is not None:
        row.honcho_embedding_model = _validate_honcho_model_id(request.honcho_embedding_model)
        touched = True
    if request.honcho_embedding_base_url is not None:
        row.honcho_embedding_base_url = _validate_honcho_base_url(
            request.honcho_embedding_base_url,
            label="向量嵌入接口地址",
        )
        touched = True
    embedding_secret = _incoming_honcho_secret(request.honcho_embedding_api_key)
    if embedding_secret is not None:
        row.honcho_embedding_api_key_encrypted = encrypt_secret(embedding_secret)
        touched = True
    elif request.honcho_embedding_api_key is not None:
        touched = True
    if request.honcho_embedding_dimensions is not None:
        row.honcho_embedding_dimensions = request.honcho_embedding_dimensions
        touched = True
    return touched


def _decrypt_honcho_secret(value: str | None) -> str:
    if not value:
        return ""
    try:
        return decrypt_secret(value)
    except ValueError as exc:
        raise HonchoSidecarApplyError(
            "无法解密已保存的 Honcho 模型密钥，请重新填写后再应用。"
        ) from exc


def _write_honcho_sidecar(row: UIConfig, path: str) -> tuple[bool, str]:
    updates = build_honcho_model_env(
        deriver_model=str(row.honcho_deriver_model or ""),
        deriver_base_url=str(row.honcho_deriver_base_url or ""),
        deriver_api_key=_decrypt_honcho_secret(row.honcho_deriver_api_key_encrypted),
        embedding_model=str(row.honcho_embedding_model or ""),
        embedding_base_url=str(row.honcho_embedding_base_url or ""),
        embedding_api_key=_decrypt_honcho_secret(row.honcho_embedding_api_key_encrypted),
        embedding_dimensions=row.honcho_embedding_dimensions,
    )
    changed = apply_sidecar_env_file(path, updates)
    if not changed:
        return False, (
            "Honcho 环境文件已与当前设置一致。请确认 api 与 deriver 已按这份配置重启过。"
        )
    return True, SIDECAR_RESTART_MESSAGE


def _sync_honcho_sidecar(row: UIConfig, *, touched: bool) -> tuple[bool, str]:
    if not touched:
        return False, ""
    if not str(row.honcho_deriver_model or "").strip() and not str(
        row.honcho_embedding_model or ""
    ).strip():
        return False, ""
    path = get_settings().honcho_sidecar_env_file.strip()
    if not path:
        return False, SIDECAR_SAVED_ONLY_MESSAGE
    try:
        changed, message = _write_honcho_sidecar(row, path)
    except HonchoSidecarApplyError as exc:
        return False, exc.message
    if not changed:
        return False, ""
    return True, message


def _validate_storage_path(value: str) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    candidate = Path(normalized).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=422, detail="文件存储目录必须是绝对路径")
    resolved = candidate.resolve(strict=False)
    if resolved == Path(resolved.anchor):
        raise HTTPException(status_code=422, detail="文件存储目录不能是文件系统根目录")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"无法创建文件存储目录：{exc}") from exc
    if not resolved.is_dir() or not os.access(resolved, os.W_OK | os.X_OK):
        raise HTTPException(status_code=422, detail="文件存储目录不可写")
    return str(resolved)


def _effective_storage_path(row: UIConfig) -> str:
    if not row.sandbox_enabled and row.harness_storage_path:
        return str(Path(row.harness_storage_path).expanduser().resolve())
    try:
        return str((paths.user_data_dir() / "harness_workspaces").resolve())
    except OSError:
        # Diagnostics and API reads must remain side-effect safe even when the
        # configured home directory is not writable (for example unit tests).
        return ""


_restart_lock = threading.Lock()
_restart_scheduled = False


def _schedule_application_restart(delay_seconds: float = 1.0) -> None:
    """Exit after the response is flushed; the service supervisor restarts StaffDeck."""

    global _restart_scheduled
    with _restart_lock:
        if _restart_scheduled:
            return
        _restart_scheduled = True

    def restart() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    timer = threading.Timer(delay_seconds, restart)
    timer.daemon = True
    timer.start()

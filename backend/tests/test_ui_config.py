from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import ui_config as ui_config_module
from app.api.ui_config import (
    UIConfigUpdateRequest,
    get_or_create_ui_config,
    ui_config_read,
    update_enterprise_ui_config,
)
from app.config import Settings
from app.core.agent_loop import AgentLoop
from app.db.models import Tenant, UIConfig, User
from app.harness.sandbox import SandboxDiagnostics


def test_runtime_settings_action_limit_matches_backend_contract() -> None:
    request = UIConfigUpdateRequest(tenant_id="tenant_demo")
    row = UIConfig(tenant_id="tenant_demo")

    assert request.agent_loop_max_actions == 32
    assert row.agent_loop_max_actions == 32
    assert request.context_token_budget == row.context_token_budget == 32_000
    assert request.context_compaction_trigger_ratio == 0.70
    assert request.context_recent_round_limit == 6
    assert request.context_allowed_roles == ["user", "assistant"]
    with pytest.raises(ValidationError):
        UIConfigUpdateRequest(tenant_id="tenant_demo", agent_loop_max_actions=101)


def test_context_runtime_settings_validate_related_limits() -> None:
    with pytest.raises(ValidationError, match="不能超过上下文预算"):
        UIConfigUpdateRequest(
            tenant_id="tenant_demo",
            context_token_budget=1_000,
            context_long_summary_token_budget=600,
            context_medium_summary_token_budget=600,
        )
    with pytest.raises(ValidationError):
        UIConfigUpdateRequest(
            tenant_id="tenant_demo",
            context_allowed_roles=[],
        )


def test_agent_loop_reads_tenant_context_runtime_settings() -> None:
    class FakeDatabase:
        def get(self, _model: object, _tenant_id: str) -> UIConfig:
            return UIConfig(
                tenant_id="tenant_demo",
                context_token_budget=48_000,
                context_compaction_trigger_ratio=0.55,
                context_recent_round_limit=9,
                context_long_summary_token_budget=3_000,
                context_medium_summary_token_budget=2_000,
                context_allowed_roles=["assistant"],
                context_long_summary_prefix="长期：",
                context_medium_summary_prefix="近期：",
            )

    loop = object.__new__(AgentLoop)
    loop.db = FakeDatabase()

    settings = loop._get_conversation_context_settings("tenant_demo")

    assert settings.token_budget == 48_000
    assert settings.compaction_trigger_ratio == 0.55
    assert settings.recent_round_limit == 9
    assert settings.allowed_roles == frozenset({"assistant"})
    assert settings.long_summary_prefix == "长期："
    assert settings.medium_summary_prefix == "近期："


def test_agent_loop_honors_runtime_settings_action_limit() -> None:
    class FakeDatabase:
        def get(self, _model: object, _tenant_id: str) -> UIConfig:
            return UIConfig(tenant_id="tenant_demo", agent_loop_max_actions=100)

    loop = object.__new__(AgentLoop)
    loop.db = FakeDatabase()

    assert loop._get_agent_loop_max_actions("tenant_demo") == 100


def test_ui_config_read_fails_closed_for_unknown_network_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ui_config_module,
        "diagnostics",
        lambda: SandboxDiagnostics(
            status="ready",
            code=None,
            message="沙盒可用（srt）。",
            backend="srt",
        ),
    )
    row = UIConfig(
        tenant_id="tenant_demo",
        sandbox_enabled=True,
        sandbox_network_mode="legacy",
        sandbox_allowed_domains=[" api.example.com ", "", "*.example.org"],
    )

    result = ui_config_read(row)

    assert result.sandbox_network_mode == "deny"
    assert result.sandbox_allowed_domains == ["api.example.com", "*.example.org"]
    assert result.sandbox_status == "ready"
    assert result.sandbox_backend == "srt"


def test_windows_setup_prompt_is_based_on_backend_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_config_module.sys, "platform", "win32")
    monkeypatch.setattr(
        ui_config_module,
        "diagnostics",
        lambda: SandboxDiagnostics(
            status="unavailable",
            code="SANDBOX_WINDOWS_SETUP_REQUIRED",
            message="Windows sandbox setup is required.",
            remediation="Run the installer as administrator.",
            backend="srt",
        ),
    )
    monkeypatch.setattr(
        ui_config_module,
        "windows_install_command",
        lambda: "node srt-cli.js windows-install",
    )

    result = ui_config_read(UIConfig(tenant_id="tenant_demo", sandbox_enabled=True))

    assert result.sandbox_setup_required is True
    assert "PowerShell 或 CMD" in (result.sandbox_setup_instructions or "")
    assert "node srt-cli.js windows-install" in (result.sandbox_setup_instructions or "")


def test_sandbox_is_disabled_by_default_without_running_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ui_config_module,
        "diagnostics",
        lambda: pytest.fail("disabled sandbox must not be probed"),
    )

    result = ui_config_read(UIConfig(tenant_id="tenant_demo"))

    assert result.sandbox_enabled is False
    assert result.sandbox_status == "disabled"
    assert result.sandbox_backend == "disabled"


def test_sandbox_toggle_schedules_application_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    scheduled: list[bool] = []
    monkeypatch.setattr(ui_config_module, "_schedule_application_restart", lambda: scheduled.append(True))
    monkeypatch.setattr(
        ui_config_module,
        "diagnostics",
        lambda: SandboxDiagnostics(status="ready", code=None, message="ok", backend="srt"),
    )
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        admin = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            password_hash="unused",
            role="admin",
        )
        result = update_enterprise_ui_config(
            UIConfigUpdateRequest(tenant_id="tenant_demo", sandbox_enabled=True),
            db,
            admin,
        )

    assert result.restart_scheduled is True
    assert scheduled == [True]


def test_context_runtime_settings_persist_without_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    scheduled: list[bool] = []
    monkeypatch.setattr(
        ui_config_module,
        "_schedule_application_restart",
        lambda: scheduled.append(True),
    )
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        admin = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            password_hash="unused",
            role="admin",
        )
        result = update_enterprise_ui_config(
            UIConfigUpdateRequest(
                tenant_id="tenant_demo",
                context_token_budget=48_000,
                context_compaction_trigger_ratio=0.65,
                context_recent_round_limit=8,
                context_long_summary_token_budget=5_000,
                context_medium_summary_token_budget=3_000,
                context_allowed_roles=["user"],
                context_long_summary_prefix="长期记忆：",
                context_medium_summary_prefix="近期记忆：",
            ),
            db,
            admin,
        )

    assert result.context_token_budget == 48_000
    assert result.context_compaction_trigger_ratio == 0.65
    assert result.context_recent_round_limit == 8
    assert result.context_allowed_roles == ["user"]
    assert result.context_long_summary_prefix == "长期记忆："
    assert result.context_medium_summary_prefix == "近期记忆："
    assert result.restart_scheduled is False
    assert scheduled == []


def _honcho_env(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "honcho_enabled": False,
        "honcho_base_url": "http://honcho.env",
        "honcho_api_key": "env-secret",
        "honcho_timeout_ms": 200,
        "honcho_workspace_prefix": "tenant:",
    }
    payload.update(overrides)
    return Settings(**payload)


def test_honcho_runtime_settings_fall_back_to_env_until_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ui_config_module, "get_settings", lambda: _honcho_env(honcho_enabled=True))
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        admin = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            password_hash="unused",
            role="admin",
        )
        initial = ui_config_read(get_or_create_ui_config(db, "tenant_demo"))
        saved = update_enterprise_ui_config(
            UIConfigUpdateRequest(
                tenant_id="tenant_demo",
                honcho_enabled=False,
                honcho_base_url="http://honcho.ui/v3/",
                honcho_timeout_ms=800,
            ),
            db,
            admin,
        )
        monkeypatch.setattr(
            ui_config_module,
            "get_settings",
            lambda: _honcho_env(honcho_enabled=True, honcho_base_url="http://honcho.other"),
        )
        reread = ui_config_read(db.get(UIConfig, "tenant_demo"))

    assert initial.honcho_enabled is True
    assert initial.honcho_base_url == "http://honcho.env"
    assert initial.honcho_timeout_ms == 200
    assert "honcho_api_key" not in initial.model_dump()
    assert saved.honcho_enabled is False
    assert saved.honcho_base_url == "http://honcho.ui/v3"
    assert saved.honcho_timeout_ms == 800
    assert saved.restart_scheduled is False
    assert reread.honcho_enabled is False
    assert reread.honcho_base_url == "http://honcho.ui/v3"
    assert reread.honcho_timeout_ms == 800


def test_honcho_runtime_update_leaves_override_when_fields_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ui_config_module, "get_settings", lambda: _honcho_env())
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        admin = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            password_hash="unused",
            role="admin",
        )
        update_enterprise_ui_config(
            UIConfigUpdateRequest(
                tenant_id="tenant_demo",
                honcho_enabled=True,
                honcho_base_url="http://honcho.ui",
                honcho_timeout_ms=500,
            ),
            db,
            admin,
        )
        updated = update_enterprise_ui_config(
            UIConfigUpdateRequest(tenant_id="tenant_demo", agent_loop_max_actions=40),
            db,
            admin,
        )

    assert updated.agent_loop_max_actions == 40
    assert updated.honcho_enabled is True
    assert updated.honcho_base_url == "http://honcho.ui"
    assert updated.honcho_timeout_ms == 500


def test_honcho_runtime_settings_reject_bad_url_and_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ui_config_module, "get_settings", lambda: _honcho_env())
    with pytest.raises(ValidationError):
        UIConfigUpdateRequest(tenant_id="tenant_demo", honcho_timeout_ms=10)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        admin = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            password_hash="unused",
            role="admin",
        )
        member = User(
            id="user_member",
            tenant_id="tenant_demo",
            username="member",
            password_hash="unused",
            role="member",
        )
        with pytest.raises(HTTPException) as url_error:
            update_enterprise_ui_config(
                UIConfigUpdateRequest(
                    tenant_id="tenant_demo",
                    honcho_enabled=True,
                    honcho_base_url="file:///tmp/honcho",
                ),
                db,
                admin,
            )
        with pytest.raises(HTTPException) as auth_error:
            update_enterprise_ui_config(
                UIConfigUpdateRequest(tenant_id="tenant_demo", honcho_enabled=True),
                db,
                member,
            )

    assert url_error.value.status_code == 422
    assert auth_error.value.status_code == 403


@pytest.mark.parametrize(
    "base_url",
    [
        "http://user:secret@honcho.ui",
        "http://honcho.ui/api?x=1",
        "http://＠evil.com",
        "http:/127.0.0.1:8000",
        "http://honcho.ui:99999",
    ],
)
def test_honcho_base_url_rejects_credentials_query_and_malformed_hosts(
    base_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ui_config_module, "get_settings", lambda: _honcho_env())
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        admin = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            password_hash="unused",
            role="admin",
        )
        with pytest.raises(HTTPException) as error:
            update_enterprise_ui_config(
                UIConfigUpdateRequest(
                    tenant_id="tenant_demo",
                    honcho_enabled=True,
                    honcho_base_url=base_url,
                    honcho_timeout_ms=200,
                ),
                db,
                admin,
            )

    assert error.value.status_code == 422


def test_honcho_read_strips_env_userinfo_and_clamps_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        ui_config_module,
        "get_settings",
        lambda: _honcho_env(
            honcho_base_url="http://user:secret@honcho.env:8000/v1",
            honcho_timeout_ms=20,
        ),
    )
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.commit()
        read = ui_config_read(get_or_create_ui_config(db, "tenant_demo"))

    assert read.honcho_base_url == "http://honcho.env:8000/v1"
    assert "secret" not in read.honcho_base_url
    assert read.honcho_timeout_ms == 50

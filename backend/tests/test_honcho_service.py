from __future__ import annotations

import logging

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings
from app.db.models import AgentProfile, ChatSession, Tenant, UIConfig
from app.honcho.client import HonchoRequestError, HonchoTimeout
from app.honcho.service import (
    inject_honcho_representation,
    schedule_honcho_message,
)
from app.session.session_schema import ChatTurnRequest


class FakeHoncho:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.representation = "喜欢上午沟通"
        self.error: Exception | None = None

    def ensure_workspace(self, workspace_id: str, metadata: dict | None = None) -> None:
        self.calls.append(("workspace", workspace_id, metadata))

    def ensure_peer(self, workspace_id: str, peer_id: str, metadata: dict | None = None) -> None:
        self.calls.append(("peer", workspace_id, peer_id, metadata))

    def ensure_session(
        self,
        workspace_id: str,
        session_id: str,
        peer_ids: list[str],
        metadata: dict | None = None,
    ) -> None:
        self.calls.append(("session", workspace_id, session_id, peer_ids, metadata))

    def add_message(
        self,
        workspace_id: str,
        session_id: str,
        *,
        peer_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        self.calls.append(("message", workspace_id, session_id, peer_id, content, metadata))

    def fetch_representation(self, workspace_id: str, peer_id: str, *, timeout_seconds: float) -> str:
        self.calls.append(("representation", workspace_id, peer_id, timeout_seconds))
        if self.error:
            raise self.error
        return self.representation


def _db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session, *, honcho_enabled: bool | None) -> ChatSession:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    metadata: dict = {"owner_user_id": "user_owner"}
    if honcho_enabled is not None:
        metadata["honcho_enabled"] = honcho_enabled
    db.add(
        AgentProfile(
            id="agent_xyz",
            tenant_id="tenant_demo",
            name="员工",
            metadata_json=metadata,
        )
    )
    session = ChatSession(
        id="session_1",
        tenant_id="tenant_demo",
        user_id="user_abc",
        agent_id="agent_xyz",
    )
    db.add(session)
    db.commit()
    return session


def _request(**overrides) -> ChatTurnRequest:
    payload = {
        "tenant_id": "tenant_demo",
        "user_id": "user_abc",
        "agent_id": "agent_xyz",
        "session_id": "session_1",
        "message": "你好",
        "channel": "wecom",
        "channel_external_user_id": "zhangsan",
        "channel_external_account_scope": "corp_a",
    }
    payload.update(overrides)
    return ChatTurnRequest(**payload)


def _settings(**overrides) -> Settings:
    payload = {
        "honcho_enabled": True,
        "honcho_base_url": "http://honcho.test",
        "honcho_api_key": "",
        "honcho_timeout_ms": 200,
        "honcho_workspace_prefix": "tenant:",
    }
    payload.update(overrides)
    return Settings(**payload)


def test_master_switch_off_makes_no_honcho_calls() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    client = FakeHoncho()
    queued: list[dict] = []

    schedule_honcho_message(
        db,
        _request(),
        session,
        role="user",
        content="你好",
        message_id="msg_user",
        settings=_settings(honcho_enabled=False),
        enqueue=queued.append,
    )
    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        [{"kind": "fact", "content": "本地事实"}],
        settings=_settings(honcho_enabled=False),
        client=client,
    )

    assert queued == []
    assert client.calls == []
    assert memories == [{"kind": "fact", "content": "本地事实"}]
    db.close()


def test_employee_switch_off_makes_no_honcho_calls() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=False)
    client = FakeHoncho()
    queued: list[dict] = []

    schedule_honcho_message(
        db,
        _request(),
        session,
        role="user",
        content="你好",
        settings=_settings(),
        enqueue=queued.append,
    )
    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        [{"kind": "fact", "content": "本地事实"}],
        settings=_settings(),
        client=client,
    )

    assert queued == []
    assert client.calls == []
    assert memories == [{"kind": "fact", "content": "本地事实"}]
    db.close()


def test_missing_employee_flag_makes_no_honcho_calls() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=None)
    client = FakeHoncho()

    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        [],
        settings=_settings(),
        client=client,
    )

    assert client.calls == []
    assert memories == []
    db.close()


def test_group_chat_skips_write_and_inject() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    client = FakeHoncho()
    queued: list[dict] = []
    request = _request(channel_is_group=True)

    schedule_honcho_message(
        db,
        request,
        session,
        role="user",
        content="群里说",
        settings=_settings(),
        enqueue=queued.append,
    )
    memories = inject_honcho_representation(
        db,
        request,
        session,
        [{"kind": "fact", "content": "本地事实"}],
        settings=_settings(),
        client=client,
    )

    assert queued == []
    assert client.calls == []
    assert memories == [{"kind": "fact", "content": "本地事实"}]
    db.close()


def test_enabled_private_chat_queues_user_and_agent_peers() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    queued: list[dict] = []
    request = _request()

    schedule_honcho_message(
        db,
        request,
        session,
        role="user",
        content="你好",
        message_id="msg_user",
        settings=_settings(),
        enqueue=queued.append,
    )
    schedule_honcho_message(
        db,
        request,
        session,
        role="assistant",
        content="您好",
        message_id="msg_assistant",
        settings=_settings(),
        enqueue=queued.append,
    )

    assert [item["role"] for item in queued] == ["user", "assistant"]
    assert queued[0]["user_id"] == "user_abc"
    assert queued[0]["external_user_id"] == "zhangsan"
    assert queued[0]["content"] == "你好"
    assert queued[1]["content"] == "您好"
    db.close()


def test_dual_write_job_sends_messages_with_mapped_peers_only(caplog: pytest.LogCaptureFixture) -> None:
    from app.honcho.jobs import run_honcho_dual_write_job

    client = FakeHoncho()
    payload = {
        "tenant_id": "tenant_demo",
        "user_id": "user_abc",
        "agent_id": "agent_xyz",
        "session_id": "session_1",
        "role": "user",
        "content": "你好",
        "message_id": "msg_user",
        "channel": "wecom",
        "account_scope": "corp_a",
        "external_user_id": "zhangsan",
        "channel_is_group": False,
    }

    with caplog.at_level(logging.WARNING):
        run_honcho_dual_write_job(payload, settings=_settings(), client=client)

    message_calls = [call for call in client.calls if call[0] == "message"]
    assert len(message_calls) == 1
    assert message_calls[0][3] == "user_user_abc"
    assert "zhangsan" not in message_calls[0][3]
    assert message_calls[0][5]["external_user_id"] == "zhangsan"
    assert message_calls[0][5]["channel"] == "wecom"
    peer_calls = [call for call in client.calls if call[0] == "peer"]
    assert [call[2] for call in peer_calls] == ["user_user_abc", "agent_agent_xyz"]
    assert peer_calls[0][3] == {"staff_peer": "user:user_abc"}
    assert "external_user_id" not in peer_calls[0][3]
    assert "channel" not in peer_calls[0][3]
    assert client.calls[0][1] == "tenant_tenant_demo"


def test_assistant_dual_write_uses_agent_peer() -> None:
    from app.honcho.jobs import run_honcho_dual_write_job

    client = FakeHoncho()
    run_honcho_dual_write_job(
        {
            "tenant_id": "tenant_demo",
            "user_id": "user_abc",
            "agent_id": "agent_xyz",
            "session_id": "session_1",
            "role": "assistant",
            "content": "您好",
            "channel": "web",
            "channel_is_group": False,
        },
        settings=_settings(),
        client=client,
    )

    message = next(call for call in client.calls if call[0] == "message")
    assert message[3] == "agent_agent_xyz"


def test_inject_appends_labeled_portrait_and_keeps_local_memory() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    client = FakeHoncho()
    local = [{"kind": "fact", "content": "本地事实"}]

    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        local,
        settings=_settings(honcho_timeout_ms=200),
        client=client,
    )

    assert local == [{"kind": "fact", "content": "本地事实"}]
    assert memories[0] == local[0]
    assert memories[1]["kind"] == "honcho_representation"
    assert str(memories[1]["content"]).startswith("长期用户画像")
    assert "喜欢上午沟通" in str(memories[1]["content"])
    assert client.calls[-1] == ("representation", "tenant_tenant_demo", "user_user_abc", 0.2)
    db.close()


@pytest.mark.parametrize(
    "error",
    [HonchoTimeout("slow"), HonchoRequestError(503, "down")],
)
def test_representation_failure_leaves_memory_unchanged(error: Exception) -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    client = FakeHoncho()
    client.error = error
    local = [{"kind": "fact", "content": "本地事实"}]

    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        local,
        settings=_settings(),
        client=client,
    )

    assert memories == local
    db.close()


def test_dual_write_failure_is_logged_and_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    from app.honcho.jobs import run_honcho_dual_write_job

    client = FakeHoncho()
    client.error = HonchoRequestError(500, "write failed")

    def add_message(*_args, **_kwargs):
        raise client.error

    client.add_message = add_message  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING):
        run_honcho_dual_write_job(
            {
                "tenant_id": "tenant_demo",
                "user_id": "user_abc",
                "agent_id": "agent_xyz",
                "session_id": "session_1",
                "role": "user",
                "content": "你好",
                "channel": "web",
                "channel_is_group": False,
            },
            settings=_settings(),
            client=client,
        )

    assert any("honcho" in record.message.lower() for record in caplog.records)


def test_job_does_not_call_honcho_when_master_switch_is_off() -> None:
    from app.honcho.jobs import run_honcho_dual_write_job

    client = FakeHoncho()
    run_honcho_dual_write_job(
        {
            "tenant_id": "tenant_demo",
            "user_id": "user_abc",
            "agent_id": "agent_xyz",
            "session_id": "session_1",
            "role": "user",
            "content": "你好",
            "channel": "web",
            "channel_is_group": False,
        },
        settings=_settings(honcho_enabled=False),
        client=client,
    )

    assert client.calls == []


def test_job_skips_group_payload_even_if_enqueued() -> None:
    from app.honcho.jobs import run_honcho_dual_write_job

    client = FakeHoncho()
    run_honcho_dual_write_job(
        {
            "tenant_id": "tenant_demo",
            "user_id": "user_abc",
            "agent_id": "agent_xyz",
            "session_id": "session_1",
            "role": "user",
            "content": "群消息",
            "channel": "wecom",
            "channel_is_group": True,
        },
        settings=_settings(),
        client=client,
    )

    assert client.calls == []


def test_different_users_are_not_merged_by_shared_external_id() -> None:
    from app.honcho.jobs import run_honcho_dual_write_job

    client = FakeHoncho()
    for user_id in ("user_wecom", "user_feishu"):
        run_honcho_dual_write_job(
            {
                "tenant_id": "tenant_demo",
                "user_id": user_id,
                "agent_id": "agent_xyz",
                "session_id": f"session_{user_id}",
                "role": "user",
                "content": "你好",
                "channel": "wecom" if user_id == "user_wecom" else "feishu",
                "external_user_id": "same-person",
                "channel_is_group": False,
            },
            settings=_settings(),
            client=client,
        )

    peers = [call[2] for call in client.calls if call[0] == "peer" and str(call[2]).startswith("user_")]
    assert peers == ["user_user_wecom", "user_user_feishu"]


def _save_honcho_runtime(
    db: Session,
    *,
    enabled: bool | None,
    base_url: str | None = None,
    timeout_ms: int | None = None,
) -> None:
    db.add(
        UIConfig(
            tenant_id="tenant_demo",
            honcho_enabled=enabled,
            honcho_base_url=base_url,
            honcho_timeout_ms=timeout_ms,
        )
    )
    db.commit()


def test_saved_runtime_master_switch_off_blocks_honcho_despite_env() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    _save_honcho_runtime(db, enabled=False, base_url="http://honcho.ui", timeout_ms=400)
    client = FakeHoncho()
    queued: list[dict] = []

    schedule_honcho_message(
        db,
        _request(),
        session,
        role="user",
        content="你好",
        settings=_settings(honcho_enabled=True),
        enqueue=queued.append,
    )
    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        [{"kind": "fact", "content": "本地事实"}],
        settings=_settings(honcho_enabled=True),
        client=client,
    )

    assert queued == []
    assert client.calls == []
    assert memories == [{"kind": "fact", "content": "本地事实"}]
    db.close()


def test_saved_runtime_settings_override_disabled_env() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    _save_honcho_runtime(db, enabled=True, base_url="http://honcho.ui", timeout_ms=400)
    client = FakeHoncho()
    queued: list[dict] = []
    env = _settings(honcho_enabled=False, honcho_base_url="", honcho_timeout_ms=200)

    schedule_honcho_message(
        db,
        _request(),
        session,
        role="user",
        content="你好",
        settings=env,
        enqueue=queued.append,
    )
    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        [],
        settings=env,
        client=client,
    )

    assert queued and queued[0]["content"] == "你好"
    assert client.calls[-1] == ("representation", "tenant_tenant_demo", "user_user_abc", 0.4)
    assert memories
    db.close()


def test_blank_saved_base_url_overrides_env_and_skips_calls() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    _save_honcho_runtime(db, enabled=True, base_url="", timeout_ms=200)
    client = FakeHoncho()
    queued: list[dict] = []

    schedule_honcho_message(
        db,
        _request(),
        session,
        role="user",
        content="你好",
        settings=_settings(),
        enqueue=queued.append,
    )
    inject_honcho_representation(
        db,
        _request(),
        session,
        [],
        settings=_settings(),
        client=client,
    )

    assert queued == []
    assert client.calls == []
    db.close()


def test_unsaved_runtime_row_keeps_env_workspace_prefix() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    _save_honcho_runtime(db, enabled=None)
    client = FakeHoncho()

    inject_honcho_representation(
        db,
        _request(),
        session,
        [],
        settings=_settings(honcho_workspace_prefix="org:"),
        client=client,
    )

    assert client.calls[-1][1] == "org_tenant_demo"
    db.close()


def test_saved_override_keeps_env_api_key_and_workspace_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    _save_honcho_runtime(db, enabled=True, base_url="http://honcho.ui", timeout_ms=400)
    seen: dict[str, str] = {}
    client = FakeHoncho()

    def fake_client(active: Settings) -> FakeHoncho:
        seen["api_key"] = active.honcho_api_key
        seen["base_url"] = active.honcho_base_url
        return client

    monkeypatch.setattr("app.honcho.service.build_honcho_client", fake_client)
    memories = inject_honcho_representation(
        db,
        _request(),
        session,
        [],
        settings=_settings(
            honcho_enabled=False,
            honcho_base_url="http://honcho.env",
            honcho_api_key="env-secret",
            honcho_workspace_prefix="org:",
        ),
    )

    assert seen == {"api_key": "env-secret", "base_url": "http://honcho.ui"}
    assert client.calls[-1][1] == "org_tenant_demo"
    assert memories
    db.close()


def test_corrupt_honcho_timeout_does_not_fail_the_turn() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    row = UIConfig(
        tenant_id="tenant_demo",
        honcho_enabled=True,
        honcho_base_url="http://honcho.ui",
        honcho_timeout_ms=200,
    )
    object.__setattr__(row, "honcho_timeout_ms", "nope")

    class _Lookup:
        def get(self, model: object, key: object) -> object:
            if model is UIConfig:
                return row
            return db.get(model, key)

    queued: list[dict] = []
    schedule_honcho_message(
        _Lookup(),
        _request(),
        session,
        role="user",
        content="你好",
        settings=_settings(),
        enqueue=queued.append,
    )
    memories = inject_honcho_representation(
        _Lookup(),
        _request(),
        session,
        [{"kind": "fact", "content": "本地事实"}],
        settings=_settings(),
        client=FakeHoncho(),
    )

    assert queued == []
    assert memories == [{"kind": "fact", "content": "本地事实"}]
    db.close()


def test_dual_write_job_honors_saved_master_switch() -> None:
    from app.honcho.jobs import run_honcho_dual_write_job

    db = _db()
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.commit()
    _save_honcho_runtime(db, enabled=False, base_url="http://honcho.ui", timeout_ms=200)
    client = FakeHoncho()

    run_honcho_dual_write_job(
        {
            "tenant_id": "tenant_demo",
            "user_id": "user_abc",
            "agent_id": "agent_xyz",
            "session_id": "session_1",
            "role": "user",
            "content": "你好",
            "channel": "web",
            "channel_is_group": False,
        },
        settings=_settings(honcho_enabled=True),
        client=client,
        db=db,
    )

    assert client.calls == []
    db.close()


def test_dual_write_job_without_explicit_settings_reads_saved_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.honcho import jobs as jobs_module

    db = _db()
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.commit()
    _save_honcho_runtime(db, enabled=False, base_url="http://honcho.ui", timeout_ms=200)
    client = FakeHoncho()

    class _BoundSession:
        def __enter__(self) -> Session:
            return db

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(jobs_module, "get_settings", lambda: _settings(honcho_enabled=True))
    monkeypatch.setattr("sqlmodel.Session", lambda _engine: _BoundSession())

    jobs_module.run_honcho_dual_write_job(
        {
            "tenant_id": "tenant_demo",
            "user_id": "user_abc",
            "agent_id": "agent_xyz",
            "session_id": "session_1",
            "role": "user",
            "content": "你好",
            "channel": "web",
            "channel_is_group": False,
        },
        client=client,
    )

    assert client.calls == []
    db.close()


def test_internal_messages_are_not_scheduled() -> None:
    db = _db()
    session = _seed(db, honcho_enabled=True)
    queued: list[dict] = []

    schedule_honcho_message(
        db,
        _request(message_visibility="internal"),
        session,
        role="user",
        content="内部重试",
        settings=_settings(),
        enqueue=queued.append,
    )

    assert queued == []
    db.close()

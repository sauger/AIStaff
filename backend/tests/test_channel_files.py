from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.channel_artifacts import download_channel_artifact
from app.channels.adapters.feishu import FeishuAdapter
from app.channels.adapters.wechat_kf import WeChatKfAdapter
from app.channels.artifact_links import (
    artifact_link_expires_at,
    build_channel_artifact_url,
    new_artifact_share_token,
)
from app.channels.service_outbox import (
    _send_artifact_link,
    stage_channel_delivery,
)
from app.core.harness_session_cleanup import harness_task_workspace_path
from app.db.models import (
    ChannelBinding,
    ChannelDelivery,
    ChatSession,
    Message,
    Tenant,
    User,
    utc_now,
)


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_session(db: Session) -> ChatSession:
    db.add(Tenant(id="tenant_file", name="Files"))
    db.add(
        User(
            id="user_file",
            tenant_id="tenant_file",
            username="owner",
            password_hash="x",
        )
    )
    db.add(
        ChannelBinding(
            id="chan_file",
            tenant_id="tenant_file",
            agent_id="agent_file",
            channel="feishu",
            status="active",
            config_json={"app_id": "cli_file"},
            external_account_key="feishu:app:7:cli_file",
            identity_scope_key="tenant_file",
            created_by_user_id="user_file",
        )
    )
    session = ChatSession(
        id="session_file",
        tenant_id="tenant_file",
        agent_id="agent_file",
        user_id="user_file",
        channel="feishu",
        external_conv_id="feishu_p2p_ou_user",
        channel_binding_id="chan_file",
        channel_account_key="feishu:app:7:cli_file",
        channel_target_json={
            "message_id": "om_file",
            "receive_id_type": "open_id",
            "receive_id": "ou_user",
        },
    )
    db.add(session)
    db.commit()
    return session


def test_stage_channel_delivery_stages_workspace_file() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        session = _seed_session(db)
        content = b"%PDF-1.4 test"
        message = Message(
            id="msg_file",
            tenant_id="tenant_file",
            session_id=session.id,
            role="assistant",
            content="报告已生成。",
            metadata_json={
                "harness_artifacts": [
                    {
                        "type": "workspace_file",
                        "task_frame_id": "task_file",
                        "path": "out/report.pdf",
                        "display_name": "report.pdf",
                        "content_type": "application/pdf",
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ]
            },
        )
        db.add(message)
        db.commit()

        stage_channel_delivery(db, session, message)
        db.commit()
        stage_channel_delivery(db, session, message)
        db.commit()

        deliveries = db.exec(
            select(ChannelDelivery).order_by(ChannelDelivery.kind)
        ).all()
        assert [row.kind for row in deliveries] == ["file", "reply"]
        file_delivery = next(row for row in deliveries if row.kind == "file")
        assert file_delivery.text == "已生成文件：report.pdf"
        assert file_delivery.artifact_json["task_frame_id"] == "task_file"
        assert file_delivery.artifact_json["share_token"]
        assert file_delivery.artifact_json["reply_streamed"] is False


def test_stage_channel_delivery_can_skip_streamed_reply() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        session = _seed_session(db)
        content = b"%PDF-1.4 stream"
        message = Message(
            id="msg_stream",
            tenant_id="tenant_file",
            session_id=session.id,
            role="assistant",
            content="流式回复已经发出。",
            metadata_json={
                "harness_artifacts": [
                    {
                        "type": "workspace_file",
                        "task_frame_id": "task_stream",
                        "path": "out/stream.pdf",
                        "display_name": "stream.pdf",
                        "content_type": "application/pdf",
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ]
            },
        )
        db.add(message)
        db.commit()

        stage_channel_delivery(db, session, message, include_reply=False)
        db.commit()

        deliveries = db.exec(select(ChannelDelivery)).all()
        assert [row.kind for row in deliveries] == ["file"]
        assert deliveries[0].artifact_json["display_name"] == "stream.pdf"
        assert deliveries[0].artifact_json["reply_streamed"] is True


def test_signed_artifact_link_and_download(tmp_path: Path, monkeypatch) -> None:
    engine = _test_engine()
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "app.channels.artifact_links.get_settings",
        lambda: SimpleNamespace(normalized_public_base_url="https://staff.example"),
    )
    with Session(engine) as db:
        session = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=session.tenant_id,
            session_id=session.id,
            task_frame_id="task_file",
            db=db,
        )
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "report.pdf").write_bytes(b"%PDF-1.4 test")
        artifact = {
            "task_frame_id": "task_file",
            "path": "report.pdf",
            "display_name": "report.pdf",
            "content_type": "application/pdf",
            "size": len(b"%PDF-1.4 test"),
            "sha256": hashlib.sha256(b"%PDF-1.4 test").hexdigest(),
            "share_token": new_artifact_share_token(),
            "share_expires_at": artifact_link_expires_at(),
        }
        delivery = ChannelDelivery(
            id="chdlv_file",
            tenant_id=session.tenant_id,
            binding_id="chan_file",
            session_id=session.id,
            kind="file",
            text="report",
            artifact_json=artifact,
            status="pending",
            idempotency_key="file:test",
        )
        db.add(delivery)
        db.commit()

        url = build_channel_artifact_url(delivery.id, artifact)
        assert url and url.startswith("https://staff.example/api/channel-artifacts/")

        response = download_channel_artifact(delivery.id, db, artifact["share_token"])

        async def _collect():
            return b"".join([chunk async for chunk in response.body_iterator])

        assert asyncio.run(_collect()) == b"%PDF-1.4 test"
        assert response.headers["content-type"].startswith("application/pdf")
        assert "report.pdf" in response.headers["content-disposition"]


def test_expired_artifact_link_is_rejected() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        session = _seed_session(db)
        delivery = ChannelDelivery(
            id="chdlv_expired",
            tenant_id=session.tenant_id,
            binding_id="chan_file",
            session_id=session.id,
            kind="file",
            text="report",
            artifact_json={
                "task_frame_id": "task_file",
                "path": "report.pdf",
                "share_token": new_artifact_share_token(),
                "share_expires_at": int((utc_now() - timedelta(minutes=1)).timestamp()),
            },
            status="pending",
            idempotency_key="file:expired",
        )
        db.add(delivery)
        db.commit()
        with pytest.raises(HTTPException) as exc_info:
            download_channel_artifact(delivery.id, db, delivery.artifact_json["share_token"])
        assert exc_info.value.status_code == 410


def test_native_feishu_and_wechat_kf_send_file(monkeypatch) -> None:
    feishu = FeishuAdapter()
    monkeypatch.setattr(feishu, "_upload_media", lambda *args, **kwargs: {"file_key": "fk"})
    monkeypatch.setattr(
        feishu, "_post", lambda *args, **kwargs: {"data": {"message_id": "om_sent"}}
    )
    assert (
        feishu.send_file(
            SimpleNamespace(),
            {"message_id": "om_in"},
            filename="report.pdf",
            content=b"%PDF",
            content_type="application/pdf",
            idempotency_key="key",
        )
        == "om_sent"
    )

    kf = WeChatKfAdapter()
    monkeypatch.setattr(kf, "_upload_media", lambda *args, **kwargs: "media-1")
    sent: list[dict] = []
    monkeypatch.setattr(kf, "_post", lambda binding, path, body: sent.append(body))
    kf.send_file(
        SimpleNamespace(),
        {"to_user_id": "wx_user", "open_kfid": "kf_1"},
        filename="report.pdf",
        content=b"%PDF",
        content_type="application/pdf",
        idempotency_key="key",
    )
    assert sent[0]["msgtype"] == "file"
    assert sent[0]["file"]["media_id"] == "media-1"


def test_wechat_ilink_send_file_protocol(monkeypatch) -> None:
    from app.channels.adapters.wechat import WeChatClient

    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, payload=None, headers=None, status_code=200) -> None:
            self._payload = payload or {}
            self.headers = headers or {}
            self.status_code = status_code
            self.content = b"{}"

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("http error")

        def json(self):
            return self._payload

    class FakeHttp:
        def post(self, url, **kwargs):
            calls.append({"url": url, "kwargs": kwargs})
            if url.endswith("/ilink/bot/getuploadurl"):
                return FakeResponse({"ret": 0, "upload_full_url": "https://cdn.example/upload"})
            if url == "https://cdn.example/upload":
                return FakeResponse(headers={"x-encrypted-param": "encrypted-param"})
            return FakeResponse({})

    client = WeChatClient("https://ilink.example", "token")
    monkeypatch.setattr(client, "_client", FakeHttp())
    client.send_file(
        "wx_user",
        "ctx",
        filename="report.pdf",
        content=b"%PDF-1.4 test",
        content_type="application/pdf",
        client_id="cid",
    )
    send = next(call for call in calls if call["url"].endswith("/sendmessage"))
    item = send["kwargs"]["json"]["msg"]["item_list"][0]
    assert item["type"] == 4
    assert item["file_item"]["file_name"] == "report.pdf"
    assert item["file_item"]["media"]["encrypt_query_param"] == "encrypted-param"


def test_dingtalk_send_file_uses_robot_api() -> None:
    from app.channels.adapters.dingtalk import DingTalkAdapter
    from app.channels.crypto import encrypt_channel_secret

    calls: list[dict] = []

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def __init__(self, payload=None) -> None:
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeHttp:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, **kwargs):
            calls.append({"url": url, "kwargs": kwargs})
            if "media/upload" in url:
                return FakeResponse({"errcode": 0, "media_id": "media-1"})
            return FakeResponse({"errcode": 0, "processQueryKey": "q"})

    class FakeTokens:
        def get(self, binding, **_kwargs):
            return "token"

    adapter = DingTalkAdapter(client_factory=FakeHttp)
    adapter._tokens = FakeTokens()
    binding = ChannelBinding(
        id="chan_ding",
        tenant_id="tenant_file",
        agent_id="agent_file",
        channel="dingtalk",
        status="active",
        config_json={"client_id": "robot-code"},
        credentials_enc=encrypt_channel_secret("secret"),
    )
    adapter.send_file(
        binding,
        {"conversation_id": "cid-group"},
        filename="report.xlsx",
        content=b"xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        idempotency_key="key",
    )
    send = next(call for call in calls if "groupMessages/send" in call["url"])
    assert send["kwargs"]["json"]["msgKey"] == "sampleFile"
    assert send["kwargs"]["json"]["openConversationId"] == "cid-group"
    assert "media-1" in send["kwargs"]["json"]["msgParam"]


def test_wecom_upload_uses_ws_media_commands() -> None:
    from app.channels.adapters.wecom import _upload_wecom_file

    class FakeWsManager:
        def __init__(self) -> None:
            self.commands: list[str] = []

        async def send_reply(self, req_id, body, cmd):
            self.commands.append(cmd)
            if cmd == "aibot_upload_media_init":
                return {"errcode": 0, "body": {"upload_id": "upload-1"}}
            if cmd == "aibot_upload_media_finish":
                return {"errcode": 0, "body": {"media_id": "media-1"}}
            return {"errcode": 0}

    class FakeClient:
        def __init__(self) -> None:
            self._ws_manager = FakeWsManager()

    client = FakeClient()
    media_id = asyncio.run(
        _upload_wecom_file(
            client,
            filename="report.pdf",
            content=b"%PDF-1.4 test",
            media_type="file",
        )
    )
    assert media_id == "media-1"
    assert client._ws_manager.commands[0] == "aibot_upload_media_init"
    assert client._ws_manager.commands[-1] == "aibot_upload_media_finish"


def test_artifact_link_fallback_contains_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.channels.artifact_links.get_settings",
        lambda: SimpleNamespace(normalized_public_base_url="https://staff.example"),
    )

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def send(self, binding, target, text, *, idempotency_key=None):
            self.calls.append({"text": text, "key": idempotency_key})

    artifact = {
        "task_frame_id": "task_file",
        "path": "report.pdf",
        "display_name": "report.pdf",
        "size": 1024,
        "share_token": new_artifact_share_token(),
        "share_expires_at": artifact_link_expires_at(),
    }
    delivery = ChannelDelivery(
        id="chdlv_link",
        tenant_id="tenant_file",
        binding_id="chan_file",
        session_id="session_file",
        kind="file",
        text="已生成文件：report.pdf",
        artifact_json=artifact,
        status="pending",
        idempotency_key="file:link",
    )
    recorder = Recorder()
    _send_artifact_link(recorder, SimpleNamespace(), {"to_user_id": "u"}, delivery, artifact)
    assert "https://staff.example/api/channel-artifacts/" in recorder.calls[0]["text"]


def _file_delivery(db: Session, *, delivery_id: str, channel: str) -> ChannelDelivery:
    binding = ChannelBinding(
        id=f"chan_{delivery_id}",
        tenant_id="tenant_file",
        agent_id="agent_file",
        channel=channel,
        status="active",
        external_account_key=f"{channel}:acc:{delivery_id}",
        config_json={},
    )
    db.add(binding)
    session = ChatSession(
        id=f"session_{delivery_id}",
        tenant_id="tenant_file",
        agent_id="agent_file",
        user_id="user_file",
        channel=channel,
        external_conv_id=f"{channel}_p2p_u",
        channel_binding_id=binding.id,
        channel_account_key=binding.external_account_key,
        channel_target_json={
            "message_id": "om_1",
            "receive_id": "ou_1",
            "receive_id_type": "open_id",
        },
    )
    db.add(session)
    delivery = ChannelDelivery(
        id=delivery_id,
        tenant_id="tenant_file",
        binding_id=binding.id,
        session_id=session.id,
        message_id=f"msg_{delivery_id}",
        kind="file",
        text="已生成文件：report.pdf",
        target_json=dict(session.channel_target_json),
        artifact_json={
            "task_frame_id": "task_file",
            "path": "report.pdf",
            "display_name": "report.pdf",
            "content_type": "application/pdf",
            "size": len(b"%PDF-1.4 test"),
            "sha256": hashlib.sha256(b"%PDF-1.4 test").hexdigest(),
            "share_token": new_artifact_share_token(),
            "share_expires_at": artifact_link_expires_at(),
            "reply_streamed": True,
        },
        status="sending",
        idempotency_key=f"file:{delivery_id}",
    )
    db.add(delivery)
    db.commit()
    return delivery


def test_delivery_sends_native_file(tmp_path: Path, monkeypatch) -> None:
    from app.channels.adapters.base import register_channel_adapter
    from app.channels.service_outbox import _deliver_one_locked

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_session(db)
        delivery = _file_delivery(db, delivery_id="native", channel="fake_native")
        workspace = harness_task_workspace_path(
            tenant_id=delivery.tenant_id,
            session_id=delivery.session_id,
            task_frame_id="task_file",
            db=db,
        )
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "report.pdf").write_bytes(b"%PDF-1.4 test")

        class NativeAdapter:
            file_max_bytes = 25 * 1024 * 1024

            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.send_calls: list[str] = []

            def send_file(self, binding, target, **kwargs):
                self.calls.append(kwargs)
                return "om_file"

            def send(self, binding, target, text, *, idempotency_key=None):
                self.send_calls.append(text)
                return None

        adapter = NativeAdapter()
        register_channel_adapter("fake_native", adapter)
        _deliver_one_locked(db, delivery)
        assert [call["filename"] for call in adapter.calls] == ["report.pdf"]
        assert delivery.status == "delivered"
        assert delivery.message_id == "msg_native"
        assert delivery.remote_message_id == "om_file"
        assert adapter.send_calls == ["已生成文件：report.pdf"]


def test_delivery_falls_back_to_link(tmp_path: Path, monkeypatch) -> None:
    from app.channels.adapters.base import register_channel_adapter
    from app.channels.service_outbox import _deliver_one_locked

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "app.channels.artifact_links.get_settings",
        lambda: SimpleNamespace(normalized_public_base_url="https://staff.example"),
    )
    engine = _test_engine()
    with Session(engine) as db:
        _seed_session(db)
        delivery = _file_delivery(db, delivery_id="link", channel="fake_link")
        workspace = harness_task_workspace_path(
            tenant_id=delivery.tenant_id,
            session_id=delivery.session_id,
            task_frame_id="task_file",
            db=db,
        )
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "report.pdf").write_bytes(b"%PDF-1.4 test")

        class TextOnlyAdapter:
            def __init__(self) -> None:
                self.texts: list[str] = []

            def send(self, binding, target, text, *, idempotency_key=None):
                self.texts.append(text)

        adapter = TextOnlyAdapter()
        register_channel_adapter("fake_link", adapter)
        _deliver_one_locked(db, delivery)
        assert delivery.status == "delivered"
        assert "https://staff.example/api/channel-artifacts/" in adapter.texts[0]


def test_oversized_artifact_stages_error_notice() -> None:
    engine = _test_engine()
    with Session(engine) as db:
        session = _seed_session(db)
        message = Message(
            id="msg_oversized",
            tenant_id="tenant_file",
            session_id=session.id,
            role="assistant",
            content="文件已生成。",
            metadata_json={
                "harness_artifacts": [
                    {
                        "type": "workspace_file",
                        "task_frame_id": "task_big",
                        "path": "out/big.zip",
                        "display_name": "big.zip",
                        "content_type": "application/zip",
                        "size": 26 * 1024 * 1024,
                    }
                ]
            },
        )
        db.add(message)
        db.commit()
        stage_channel_delivery(db, session, message)
        db.commit()
        deliveries = db.exec(select(ChannelDelivery)).all()
        notices = [row for row in deliveries if row.kind == "error_notice"]
        assert len(notices) == 1
        assert "超过 25MB" in notices[0].text


def test_artifact_link_requires_explicit_public_base(monkeypatch) -> None:
    from app.channels.artifact_links import build_channel_artifact_url
    from app.channels.service_outbox import _send_artifact_link

    monkeypatch.setattr(
        "app.channels.artifact_links.get_settings",
        lambda: SimpleNamespace(normalized_public_base_url=""),
    )
    artifact = {
        "path": "report.pdf",
        "display_name": "report.pdf",
        "share_token": new_artifact_share_token(),
        "share_expires_at": artifact_link_expires_at(),
    }
    assert build_channel_artifact_url("chdlv_x", artifact) is None
    delivery = ChannelDelivery(
        id="chdlv_x",
        tenant_id="tenant_file",
        binding_id="chan_file",
        session_id="session_file",
        kind="file",
        text="已生成文件",
        artifact_json=artifact,
        status="pending",
        idempotency_key="file:no-public-base",
    )
    with pytest.raises(RuntimeError) as exc_info:
        _send_artifact_link(SimpleNamespace(), SimpleNamespace(), {}, delivery, artifact)
    assert exc_info.value.retryable is False


def test_wechat_file_size_error_is_permanent() -> None:
    from app.channels.adapters.wechat import WeChatAdapter, WeChatPermanentError

    adapter = WeChatAdapter(client_factory=lambda binding: None)
    with pytest.raises(WeChatPermanentError) as exc_info:
        adapter.send_file(
            SimpleNamespace(),
            {"to_user_id": "u", "context_token": "ctx"},
            filename="big.pdf",
            content=b"x" * (25 * 1024 * 1024 + 1),
            content_type="application/pdf",
        )
    assert exc_info.value.retryable is False


def test_public_base_url_rejects_loopback_by_default() -> None:
    from app.config import Settings

    assert (
        Settings(_env_file=None, public_base_url="", tool_base_url="http://localhost:5173")
        .normalized_public_base_url
        == ""
    )
    assert (
        Settings(
            _env_file=None,
            public_base_url="http://localhost:5173",
            allow_local_artifact_links=False,
        ).normalized_public_base_url
        == ""
    )
    assert (
        Settings(
            _env_file=None,
            public_base_url="http://localhost:5173",
            allow_local_artifact_links=True,
        ).normalized_public_base_url
        == "http://localhost:5173"
    )
    assert (
        Settings(
            _env_file=None, public_base_url="https://staff.example/"
        ).normalized_public_base_url
        == "https://staff.example"
    )


def test_permanent_native_error_falls_back_to_link(tmp_path: Path, monkeypatch) -> None:
    from app.channels.adapters.base import register_channel_adapter
    from app.channels.adapters.wechat import WeChatPermanentError
    from app.channels.service_outbox import _deliver_one_locked

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "app.channels.artifact_links.get_settings",
        lambda: SimpleNamespace(normalized_public_base_url="https://staff.example"),
    )
    engine = _test_engine()
    with Session(engine) as db:
        _seed_session(db)
        delivery = _file_delivery(db, delivery_id="permanent", channel="fake_perm")
        workspace = harness_task_workspace_path(
            tenant_id=delivery.tenant_id,
            session_id=delivery.session_id,
            task_frame_id="task_file",
            db=db,
        )
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "report.pdf").write_bytes(b"%PDF-1.4 test")

        class PermanentAdapter:
            file_max_bytes = 25 * 1024 * 1024

            def __init__(self) -> None:
                self.texts: list[str] = []

            def send_file(self, *args, **kwargs):
                raise WeChatPermanentError(-1, "unsupported file")

            def send(self, binding, target, text, *, idempotency_key=None):
                self.texts.append(text)
                return None

        adapter = PermanentAdapter()
        register_channel_adapter("fake_perm", adapter)
        _deliver_one_locked(db, delivery)
        assert delivery.status == "delivered"
        assert "https://staff.example/api/channel-artifacts/" in adapter.texts[0]

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    AgentProfile,
    ChannelIdentity,
    ChatSession,
    KnowledgeBase,
    KnowledgeBaseVersion,
    ChannelBinding,
    ChannelDelivery,
    KnowledgeDocument,
    KnowledgeIngestJob,
    Message,
    KnowledgeUpdateProposal,
    Tenant,
    User,
    utc_now,
)
from app.knowledge.proposals import (
    KnowledgeProposalError,
    confirm_knowledge_update_proposal,
    create_knowledge_update_proposal,
)
from app.session.attachment_store import stage_chat_attachment
from app.session.session_schema import ChatAttachmentRead


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session, *, owner_user_id: str = "user_owner") -> ChatSession:
    db.add(Tenant(id="tenant_kp", name="Knowledge"))
    db.add(
        User(
            id=owner_user_id,
            tenant_id="tenant_kp",
            username=owner_user_id,
            password_hash="x",
            role="member",
        )
    )
    db.add(
        AgentProfile(
            id="agent_kp",
            tenant_id="tenant_kp",
            name="知识员工",
            metadata_json={"owner_user_id": owner_user_id},
        )
    )
    db.add(
        KnowledgeBase(
            id="kb_kp",
            tenant_id="tenant_kp",
            name="财务制度",
            status="active",
        )
    )
    db.add(
        KnowledgeBaseVersion(
            id="kbver_kp",
            tenant_id="tenant_kp",
            knowledge_base_id="kb_kp",
            version="1.0.0",
            name="财务制度",
            status="active",
        )
    )
    session = ChatSession(
        id="session_kp",
        tenant_id="tenant_kp",
        agent_id="agent_kp",
        user_id=owner_user_id,
        channel="web",
    )
    db.add(session)
    db.commit()
    return session


def _attachment(
    *,
    data: bytes,
    filename: str = "制度.txt",
    size: int | None = None,
    user_id: str = "user_owner",
) -> ChatAttachmentRead:
    attachment = ChatAttachmentRead(
        id=f"att_{hashlib.sha256(data).hexdigest()[:12]}",
        filename=filename,
        content_type="text/plain",
        size=len(data) if size is None else size,
        kind="text",
    )
    return stage_chat_attachment(
        attachment,
        data,
        tenant_id="tenant_kp",
        user_id=user_id,
    )


def _visible(monkeypatch) -> None:
    import app.knowledge.proposals as proposals

    monkeypatch.setattr(
        proposals,
        "visible_knowledge_base_versions",
        lambda *args, **kwargs: {"kb_kp": type("V", (), {"id": "kbver_kp"})()},
    )


def _set_open_channels(monkeypatch, channels: set[str]) -> None:
    from types import SimpleNamespace

    import app.knowledge.proposals as proposals

    monkeypatch.setattr(
        proposals,
        "get_settings",
        lambda: SimpleNamespace(knowledge_open_write_channel_set=channels),
    )


def test_create_proposal_without_candidates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="报销制度正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_new",
            requested_action="create",
        )
        assert proposal.status == "pending"
        assert proposal.proposed_action == "create"
        assert proposal.target_knowledge_base_id == "kb_kp"
        assert proposal.candidates_json == []
        from app.time_utils import as_utc

        assert as_utc(proposal.expires_at) > utc_now() + timedelta(minutes=29)
        assert db.exec(select(KnowledgeUpdateProposal)).all() == [proposal]
        assert db.exec(select(KnowledgeIngestJob)).all() == []


def test_create_proposal_lists_similar_documents(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        db.add(
            KnowledgeDocument(
                id="kdoc_existing",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="制度.txt",
                file_type="txt",
                title="制度",
                status="ready",
                metadata_json={"raw_text_excerpt": "旧版报销制度正文"},
            )
        )
        db.commit()
        attachment = _attachment(data="新版报销制度正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_similar",
            requested_action="replace",
        )
        assert proposal.proposed_action == "undecided"
        assert proposal.candidates_json[0]["document_id"] == "kdoc_existing"
        assert "filename" in proposal.candidates_json[0]["match_reasons"]


def test_duplicate_hash_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        data = "完全相同的内容".encode("utf-8")
        attachment = _attachment(data=data)
        db.add(
            KnowledgeDocument(
                id="kdoc_dup",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="其他.txt",
                file_type="txt",
                title="其他",
                status="ready",
                source_sha256=hashlib.sha256(data).hexdigest(),
            )
        )
        db.commit()
        with pytest.raises(KnowledgeProposalError) as exc_info:
            create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id="turn_dup",
                requested_action="create",
            )
        assert exc_info.value.code == "DUPLICATE_DOCUMENT"


def test_unsupported_doc_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data=b"old doc", filename="制度.doc")
        with pytest.raises(KnowledgeProposalError) as exc_info:
            create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id="turn_doc",
                requested_action="create",
            )
        assert exc_info.value.code == "UNSUPPORTED_FORMAT"


def test_web_attachment_over_12mb_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data=b"small", size=13 * 1024 * 1024)
        with pytest.raises(KnowledgeProposalError) as exc_info:
            create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id="turn_big",
                requested_action="create",
            )
        assert exc_info.value.code == "ATTACHMENT_TOO_LARGE"


def test_same_source_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="幂等内容".encode("utf-8"))
        first = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_same",
            requested_action="create",
        )
        second = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_same",
            requested_action="create",
        )
        assert first.id == second.id
        assert len(db.exec(select(KnowledgeUpdateProposal)).all()) == 1


def test_proposal_rejects_non_owner(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db, owner_user_id="user_owner")
        session.user_id = "user_other"
        db.add(
            User(
                id="user_other",
                tenant_id="tenant_kp",
                username="user_other",
                password_hash="x",
                role="member",
            )
        )
        db.add(session)
        db.commit()
        attachment = _attachment(data="无权限内容".encode("utf-8"), user_id="user_other")
        with pytest.raises(KnowledgeProposalError) as exc_info:
            create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id="turn_denied",
                requested_action="create",
            )
        assert exc_info.value.code == "PERMISSION_DENIED"


def test_proposal_requires_knowledge_base_when_multiple(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    import app.knowledge.proposals as proposals

    monkeypatch.setattr(
        proposals,
        "visible_knowledge_base_versions",
        lambda *args, **kwargs: {
            "kb_kp": type("V", (), {"id": "kbver_kp"})(),
            "kb_kp2": type("V", (), {"id": "kbver_kp2"})(),
        },
    )
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="多库内容".encode("utf-8"))
        with pytest.raises(KnowledgeProposalError) as exc_info:
            create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id="turn_multi",
                requested_action="create",
            )
        assert exc_info.value.code == "KNOWLEDGE_BASE_REQUIRED"
        assert len(exc_info.value.details["knowledge_bases"]) == 2


def test_body_similarity_candidate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        body = "这是一份完整的财务报销制度正文，包含审批流程、单据要求和时限说明。"
        db.add(
            KnowledgeDocument(
                id="kdoc_body",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="旧文件.txt",
                file_type="txt",
                title="旧标题",
                status="ready",
                metadata_json={"raw_text_excerpt": body},
            )
        )
        db.commit()
        attachment = _attachment(data=body.encode("utf-8"), filename="新文件.txt")
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_body",
            requested_action="replace",
        )
        assert proposal.candidates_json
        assert "content" in proposal.candidates_json[0]["match_reasons"]


def test_channel_sender_requires_bound_identity(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    _set_open_channels(monkeypatch, set())
    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        db.add(session)
        db.commit()
        attachment = _attachment(data="渠道内容".encode("utf-8"))
        with pytest.raises(KnowledgeProposalError) as exc_info:
            create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id="turn_channel",
                requested_action="create",
                external_user_id="external-unknown",
                external_account_scope="scope-1",
            )
        assert exc_info.value.code == "CHANNEL_IDENTITY_NOT_BOUND"


def test_channel_bound_identity_can_propose(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    _set_open_channels(monkeypatch, set())
    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        db.add(session)
        db.add(
            ChannelIdentity(
                id="chident_kp",
                tenant_id="tenant_kp",
                channel="wecom",
                external_account_scope="scope-1",
                external_user_id="external-owner",
                staffdeck_user_id="user_owner",
            )
        )
        db.commit()
        attachment = _attachment(data="渠道内容".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_channel_ok",
            requested_action="create",
            external_user_id="external-owner",
            external_account_scope="scope-1",
        )
        assert proposal.status == "pending"


def test_confirm_creates_existing_ingest_job(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    import app.knowledge.proposals as proposals

    enqueued: list[tuple] = []
    monkeypatch.setattr(
        proposals,
        "knowledge_version_for_upload",
        lambda *args, **kwargs: type("V", (), {"id": "kbver_new"})(),
    )
    monkeypatch.setattr(
        proposals,
        "enqueue_async_job",
        lambda name, func, *args, **kwargs: enqueued.append((name, args)),
    )
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="确认后新增正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_confirm",
            requested_action="create",
        )
        job = confirm_knowledge_update_proposal(
            db,
            proposal,
            action="create",
            confirmed_by_user_id="user_owner",
        )
        assert job.id
        assert job.knowledge_base_id == "kb_kp"
        assert job.knowledge_base_version_id == "kbver_new"
        assert proposal.status == "executing"
        assert proposal.job_id == job.id
        assert enqueued[0][0] == "knowledge_ingest"


def test_confirm_replace_creates_replace_job(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    import app.knowledge.proposals as proposals

    enqueued: list[tuple] = []
    monkeypatch.setattr(
        proposals,
        "knowledge_version_for_upload",
        lambda *args, **kwargs: type("V", (), {"id": "kbver_kp"})(),
    )
    monkeypatch.setattr(
        proposals,
        "enqueue_async_job",
        lambda name, func, *args, **kwargs: enqueued.append((name, args)),
    )
    with Session(_engine()) as db:
        session = _seed(db)
        db.add(
            KnowledgeDocument(
                id="kdoc_replace",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="制度.txt",
                file_type="txt",
                title="制度",
                status="ready",
                metadata_json={"raw_text_excerpt": "旧版正文"},
            )
        )
        db.commit()
        attachment = _attachment(data="新版正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_replace_ok",
            requested_action="replace",
        )
        target = proposal.candidates_json[0]
        job = confirm_knowledge_update_proposal(
            db,
            proposal,
            action="replace",
            confirmed_by_user_id="user_owner",
            target_document_id=target["document_id"],
            expected_updated_at=target["expected_updated_at"],
        )
        assert job.operation == "replace"
        assert job.target_document_id == "kdoc_replace"
        assert proposal.status == "executing"
        assert enqueued[0][0] == "knowledge_ingest"


def test_confirm_replace_rejects_stale_document(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        db.add(
            KnowledgeDocument(
                id="kdoc_stale",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="制度.txt",
                file_type="txt",
                title="制度",
                status="ready",
            )
        )
        db.commit()
        attachment = _attachment(data="新正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_stale",
            requested_action="replace",
        )
        target = proposal.candidates_json[0]
        row = db.get(KnowledgeDocument, "kdoc_stale")
        row.updated_at = utc_now()
        db.add(row)
        db.commit()
        with pytest.raises(KnowledgeProposalError) as exc_info:
            confirm_knowledge_update_proposal(
                db,
                proposal,
                action="replace",
                confirmed_by_user_id="user_owner",
                target_document_id=target["document_id"],
                expected_updated_at=target["expected_updated_at"],
            )
        assert exc_info.value.code == "DOCUMENT_CONFLICT"


def test_run_replace_ingest_job_updates_target_without_new_document(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    from app.knowledge.service import IngestPayload, KnowledgeService

    with Session(_engine()) as db:
        _seed(db)
        target = KnowledgeDocument(
            id="kdoc_run_replace",
            tenant_id="tenant_kp",
            knowledge_base_id="kb_kp",
            knowledge_base_version_id="kbver_kp",
            filename="制度.txt",
            file_type="txt",
            title="制度",
            status="ready",
        )
        db.add(target)
        db.commit()
        content = "# 新制度\n报销需要审批。".encode("utf-8")
        service = KnowledgeService(db)
        job = service.create_ingest_job(
            IngestPayload(
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="制度.txt",
                content_base64=__import__("base64").b64encode(content).decode("ascii"),
                operation="replace",
                target_document_id="kdoc_run_replace",
                source_sha256=hashlib.sha256(content).hexdigest(),
            )
        )
        service._run_ingest_job(job.id)  # noqa: SLF001
        job = db.get(KnowledgeIngestJob, job.id)
        target = db.get(KnowledgeDocument, "kdoc_run_replace")
        assert job.status == "succeeded"
        assert job.document_id == "kdoc_run_replace"
        assert target is not None and "新制度" in (target.metadata_json or {}).get("raw_text", "")
        assert len(db.exec(select(KnowledgeDocument)).all()) == 1


def test_proposal_has_short_confirmation_code(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="确认码正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_code",
            requested_action="create",
        )
        assert proposal.confirmation_code
        assert len(proposal.confirmation_code) == 6
        assert proposal.confirmation_code == proposal.confirmation_code.upper()


def test_channel_result_creates_outbox_notice(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    from app.knowledge.proposals import notify_knowledge_proposal_result

    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="渠道结果正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_result",
            requested_action="create",
        )
        proposal.channel = "wecom"
        proposal.binding_id = "chan_wecom"
        proposal.target_json = {"to_user_id": "u1", "context_token": "ctx"}
        db.add(proposal)
        db.commit()
        job = KnowledgeIngestJob(
            id="kjob_result",
            tenant_id="tenant_kp",
            knowledge_base_id="kb_kp",
            knowledge_base_version_id="kbver_kp",
            filename="制度.txt",
            status="succeeded",
            proposal_id=proposal.id,
        )
        db.add(job)
        db.commit()
        notify_knowledge_proposal_result(db, job)
        delivery = db.exec(select(ChannelDelivery)).first()
        assert delivery is not None
        assert delivery.kind == "notice"
        assert "完成" in delivery.text
        assert db.get(KnowledgeUpdateProposal, proposal.id).status == "succeeded"


def test_channel_confirmation_cancel_marks_proposal_cancelled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    from types import SimpleNamespace

    from app.channels.service_intake import _try_handle_knowledge_proposal_confirmation

    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        binding = ChannelBinding(
            id="chan_kp",
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            channel="wecom",
            status="active",
            external_account_key="wecom:test",
        )
        db.add(binding)
        db.add(session)
        db.commit()
        attachment = _attachment(data="取消正文".encode("utf-8"))
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_cancel",
            requested_action="create",
        )
        inbound = SimpleNamespace(
            text=f"取消 {proposal.confirmation_code}",
            external_conv_id="wecom_conv",
            from_user_id="external-user",
            is_group=False,
            event_id="event_cancel",
        )
        handled = _try_handle_knowledge_proposal_confirmation(
            db,
            binding,
            inbound,
            user=SimpleNamespace(id="user_owner"),
            chat_session=session,
            target={"to_user_id": "external-user", "context_token": "ctx"},
            scope="scope",
        )
        assert handled is True
        assert db.get(KnowledgeUpdateProposal, proposal.id).status == "cancelled"
        assert db.exec(select(ChannelDelivery)).first() is not None


def test_proposal_attachment_falls_back_to_previous_user_message(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from app.core.harness_capability_invoker import HarnessCapabilityInvoker

    engine = _engine()
    with Session(engine) as db:
        db.add(
            Message(
                id="msg_prev",
                tenant_id="tenant_kp",
                session_id="session_kp",
                role="user",
                content="上传了制度",
                metadata_json={
                    "attachments": [
                        {
                            "id": "att_prev",
                            "filename": "制度.txt",
                            "content_type": "text/plain",
                            "size": 10,
                            "kind": "text",
                            "sha256": "a" * 64,
                        }
                    ]
                },
            )
        )
        db.commit()
        invoker = object.__new__(HarnessCapabilityInvoker)
        invoker.db = db
        invoker.tenant_id = "tenant_kp"
        invoker.session = SimpleNamespace(id="session_kp")
        invoker.attachments = []
        attachment, candidates = invoker._resolve_proposal_attachment("")
        assert attachment is not None
        assert attachment.id == "att_prev"
        assert candidates == []


def test_internal_dispatch_routes_knowledge_proposal() -> None:
    from app.core.harness_capability_invoker import HarnessCapabilityInvoker

    invoker = object.__new__(HarnessCapabilityInvoker)
    invoker._knowledge_ingest_proposal = lambda arguments: {"success": True, "data": arguments}
    result = invoker._invoke_internal(
        "knowledge_ingest_proposal", {"attachment_id": "att_dispatch"}
    )
    assert result["success"] is True
    assert result["data"]["attachment_id"] == "att_dispatch"


def test_open_write_channels_allow_unbound_private_senders(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    _set_open_channels(
        monkeypatch, {"wecom", "feishu", "dingtalk", "wechat_kf", "wechat"}
    )
    for channel in ("wecom", "feishu", "dingtalk", "wechat_kf", "wechat"):
        with Session(_engine()) as db:
            session = _seed(db)
            session.channel = channel
            db.add(session)
            db.commit()
            attachment = _attachment(data=f"{channel} 正文".encode("utf-8"))
            proposal = create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id=f"turn_open_{channel}",
                requested_action="create",
                external_user_id=f"external-{channel}",
                external_account_scope=f"scope-{channel}",
            )
            assert proposal.status == "pending"
            assert proposal.metadata_json["authorization_mode"] == "channel_open_phase1"


def test_open_write_rejects_group_chat(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    _set_open_channels(monkeypatch, {"wecom"})
    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        db.add(session)
        db.commit()
        attachment = _attachment(data="群聊正文".encode("utf-8"))
        with pytest.raises(KnowledgeProposalError) as exc_info:
            create_knowledge_update_proposal(
                db,
                chat_session=session,
                attachment=attachment,
                source_message_id="turn_group",
                requested_action="create",
                external_user_id="external-group-user",
                external_account_scope="scope-group",
                is_group=True,
            )
        assert exc_info.value.code == "CHANNEL_IDENTITY_NOT_BOUND"


def test_confirmation_parser_accepts_trailing_words_and_bare_code() -> None:
    from app.knowledge.confirmation import parse_knowledge_confirmation

    created = parse_knowledge_confirmation("新增 880bf6 进行确认")
    assert created is not None
    assert created.kind == "create"
    assert created.code == "880BF6"
    explicit = parse_knowledge_confirmation("确认新增 880BF6")
    assert explicit is not None and explicit.kind == "create"
    replaced = parse_knowledge_confirmation("覆盖 1 880BF6")
    assert replaced is not None and replaced.kind == "replace" and replaced.index == 1
    bare = parse_knowledge_confirmation("880bf6")
    assert bare is not None and bare.kind == "bare" and bare.code == "880BF6"


def test_confirmation_parser_does_not_treat_negation_as_write() -> None:
    from app.knowledge.confirmation import parse_knowledge_confirmation

    assert parse_knowledge_confirmation("不要新增 880BF6").kind == "negative"
    assert parse_knowledge_confirmation("取消新增 880BF6").kind == "cancel"
    assert parse_knowledge_confirmation("先不要覆盖 1 880BF6").kind == "negative"
    assert parse_knowledge_confirmation("取消 880BF6").kind == "cancel"
    assert parse_knowledge_confirmation("好的 880BF6") is None
    assert parse_knowledge_confirmation("请确认一下差旅标准") is None


def test_pending_selection_uses_index_or_exact_name() -> None:
    from app.knowledge.confirmation import CANCEL_SELECTION, select_pending_knowledge_base

    targets = [
        {"id": "kb_short", "name": "财务"},
        {"id": "kb_long", "name": "财务-报销政策手册"},
    ]
    assert select_pending_knowledge_base("2024年怎么报销", targets) is None
    assert select_pending_knowledge_base("第2个", targets) is None
    assert select_pending_knowledge_base("2", targets)["id"] == "kb_long"
    assert select_pending_knowledge_base("2. 财务-报销政策手册", targets)["id"] == "kb_long"
    assert select_pending_knowledge_base("财务-报销政策手册", targets)["id"] == "kb_long"
    assert select_pending_knowledge_base("财务", targets)["id"] == "kb_short"
    assert select_pending_knowledge_base("取消", targets) is CANCEL_SELECTION


def test_pending_knowledge_selection_creates_proposal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    _set_open_channels(monkeypatch, {"wecom"})
    from types import SimpleNamespace

    from app.channels.service_intake import _try_handle_pending_knowledge_selection

    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        attachment = _attachment(data="待选知识库正文".encode("utf-8"))
        session.context_state_json = {
            "knowledge_pending": {
                "attachment": attachment.model_dump(mode="json"),
                "requested_action": "create",
                "targets": [{"id": "kb_kp", "name": "财务制度"}],
            }
        }
        binding = ChannelBinding(
            id="chan_pending",
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            channel="wecom",
            status="active",
            external_account_key="wecom:pending",
        )
        db.add(session)
        db.add(binding)
        db.commit()
        inbound = SimpleNamespace(
            text="1. 财务制度",
            external_conv_id="conv_pending",
            from_user_id="external-pending",
            is_group=False,
            event_id="event_pending",
        )
        handled = _try_handle_pending_knowledge_selection(
            db,
            binding,
            inbound,
            chat_session=session,
            target={"to_user_id": "external-pending"},
            scope="scope",
        )
        assert handled is True
        proposal = db.exec(select(KnowledgeUpdateProposal)).first()
        assert proposal is not None
        assert proposal.target_knowledge_base_id == "kb_kp"
        assert "knowledge_pending" not in (session.context_state_json or {})


def test_capability_search_exposes_knowledge_proposal() -> None:
    from app.core.capability_discovery import search_capability_descriptors
    from app.core.task_request_compiler import CapabilityDescriptor

    descriptor = CapabilityDescriptor(
        capability_id="builtin.knowledge.proposal",
        name="knowledge_ingest_proposal",
        kind="internal",
        description="Create a pending proposal to add an attachment to knowledge.",
    )
    matches = search_capability_descriptors([descriptor], "加入知识库")
    assert [item.name for item in matches] == ["knowledge_ingest_proposal"]


def test_negative_confirmation_does_not_create_job(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    from types import SimpleNamespace

    from app.channels.service_intake import _try_handle_knowledge_proposal_confirmation

    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        binding = ChannelBinding(
            id="chan_neg",
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            channel="wecom",
            status="active",
            external_account_key="wecom:neg",
        )
        db.add(binding)
        db.add(session)
        db.commit()
        attachment = _attachment(data="否定确认正文".encode())
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_negative",
            requested_action="create",
        )
        inbound = SimpleNamespace(
            text=f"不要新增 {proposal.confirmation_code}",
            external_conv_id="wecom_conv",
            from_user_id="external-user",
            is_group=False,
            event_id="event_negative",
        )
        handled = _try_handle_knowledge_proposal_confirmation(
            db,
            binding,
            inbound,
            user=SimpleNamespace(id="user_owner"),
            chat_session=session,
            target={"to_user_id": "external-user", "context_token": "ctx"},
            scope="scope",
        )
        assert handled is True
        saved = db.get(KnowledgeUpdateProposal, proposal.id)
        assert saved.status == "pending"
        assert saved.job_id is None
        notice = db.exec(select(ChannelDelivery)).first()
        assert notice is not None
        assert "没有写入" in notice.text


def test_cancel_create_phrase_cancels_instead_of_creating(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    from types import SimpleNamespace

    from app.channels.service_intake import _try_handle_knowledge_proposal_confirmation

    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        binding = ChannelBinding(
            id="chan_cancel_create",
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            channel="wecom",
            status="active",
            external_account_key="wecom:cancel-create",
        )
        db.add(binding)
        db.add(session)
        db.commit()
        attachment = _attachment(data="取消新增正文".encode())
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_cancel_create",
            requested_action="create",
        )
        inbound = SimpleNamespace(
            text=f"取消新增 {proposal.confirmation_code}",
            external_conv_id="wecom_conv",
            from_user_id="external-user",
            is_group=False,
            event_id="event_cancel_create",
        )
        _try_handle_knowledge_proposal_confirmation(
            db,
            binding,
            inbound,
            user=SimpleNamespace(id="user_owner"),
            chat_session=session,
            target={"to_user_id": "external-user", "context_token": "ctx"},
            scope="scope",
        )
        saved = db.get(KnowledgeUpdateProposal, proposal.id)
        assert saved.status == "cancelled"
        assert saved.job_id is None


def test_bare_code_with_candidates_does_not_create(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    from types import SimpleNamespace

    from app.channels.service_intake import _try_handle_knowledge_proposal_confirmation

    with Session(_engine()) as db:
        session = _seed(db)
        session.channel = "wecom"
        db.add(
            KnowledgeDocument(
                id="kdoc_bare",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="制度.txt",
                file_type="txt",
                title="制度",
                status="ready",
                metadata_json={"raw_text_excerpt": "旧版报销制度"},
            )
        )
        binding = ChannelBinding(
            id="chan_bare",
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            channel="wecom",
            status="active",
            external_account_key="wecom:bare",
        )
        db.add(binding)
        db.add(session)
        db.commit()
        attachment = _attachment(data="新版报销制度需要审批".encode())
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_bare_candidates",
            requested_action="undecided",
        )
        assert proposal.candidates_json
        inbound = SimpleNamespace(
            text=proposal.confirmation_code,
            external_conv_id="wecom_conv",
            from_user_id="external-user",
            is_group=False,
            event_id="event_bare",
        )
        _try_handle_knowledge_proposal_confirmation(
            db,
            binding,
            inbound,
            user=SimpleNamespace(id="user_owner"),
            chat_session=session,
            target={"to_user_id": "external-user", "context_token": "ctx"},
            scope="scope",
        )
        saved = db.get(KnowledgeUpdateProposal, proposal.id)
        assert saved.status == "pending"
        assert saved.job_id is None
        notice = db.exec(select(ChannelDelivery)).first()
        assert notice is not None
        assert f"覆盖 1 {proposal.confirmation_code}" in notice.text


def test_expired_pending_proposal_allows_new_upload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="过期后重新上传".encode())
        first = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_expired_1",
            requested_action="create",
        )
        first.expires_at = utc_now() - timedelta(minutes=1)
        db.add(first)
        db.commit()
        second = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_expired_2",
            requested_action="create",
        )
        assert second.id != first.id
        assert second.status == "pending"
        assert db.get(KnowledgeUpdateProposal, first.id).status == "expired"


def test_web_pending_selection_creates_proposal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    from app.knowledge.web_turn import prepare_web_knowledge_reply

    with Session(_engine()) as db:
        session = _seed(db)
        attachment = _attachment(data="网页选择知识库".encode())
        session.context_state_json = {
            "knowledge_pending": {
                "attachment": attachment.model_dump(mode="json"),
                "requested_action": "create",
                "targets": [{"id": "kb_kp", "name": "财务制度"}],
                "created_at": utc_now().isoformat(),
            }
        }
        db.add(session)
        db.commit()
        reply = prepare_web_knowledge_reply(
            db,
            session,
            text="1",
            user_id="user_owner",
            event_id="web_turn_1",
        )
        assert reply is not None
        assert "确认码" in reply
        proposal = db.exec(select(KnowledgeUpdateProposal)).first()
        assert proposal is not None
        assert proposal.target_knowledge_base_id == "kb_kp"
        assert "knowledge_pending" not in (session.context_state_json or {})
        ignored = prepare_web_knowledge_reply(
            db,
            session,
            text="2024年怎么报销",
            user_id="user_owner",
            event_id="web_turn_2",
        )
        assert ignored is None


def test_confirm_marks_executing_before_enqueue(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    import app.knowledge.proposals as proposals

    seen: list[str] = []
    engine = _engine()

    def _enqueue(*args, **kwargs):
        with Session(engine) as check_db:
            row = check_db.exec(select(KnowledgeUpdateProposal)).first()
            seen.append(row.status if row else "")

    monkeypatch.setattr(
        proposals,
        "knowledge_version_for_upload",
        lambda *args, **kwargs: type("V", (), {"id": "kbver_new"})(),
    )
    monkeypatch.setattr(proposals, "enqueue_async_job", _enqueue)
    with Session(engine) as db:
        session = _seed(db)
        attachment = _attachment(data="先提交执行中".encode())
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_exec_order",
            requested_action="create",
        )
        confirm_knowledge_update_proposal(
            db,
            proposal,
            action="create",
            confirmed_by_user_id="user_owner",
        )
    assert seen == ["executing"]


def test_terminal_proposal_is_not_overwritten_by_later_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    from app.knowledge.proposals import notify_knowledge_proposal_result

    with Session(_engine()) as db:
        session = _seed(db)
        proposal = KnowledgeUpdateProposal(
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            session_id=session.id,
            source_message_id="turn_terminal",
            attachment_id="att_terminal",
            source_sha256="a" * 64,
            filename="制度.txt",
            size=4,
            target_knowledge_base_id="kb_kp",
            confirmation_code="ABC123",
            status="succeeded",
            expires_at=utc_now() + timedelta(minutes=30),
        )
        db.add(proposal)
        db.commit()
        job = KnowledgeIngestJob(
            tenant_id="tenant_kp",
            knowledge_base_id="kb_kp",
            filename="制度.txt",
            status="failed",
            proposal_id=proposal.id,
            error="late",
        )
        db.add(job)
        db.commit()
        notify_knowledge_proposal_result(db, job, error="late")
        assert db.get(KnowledgeUpdateProposal, proposal.id).status == "succeeded"


def test_clone_keeps_source_sha256(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    from app.agents.branching import clone_knowledge_version_assets

    with Session(_engine()) as db:
        _seed(db)
        db.add(
            KnowledgeBaseVersion(
                id="kbver_next",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                version="1.0.0-branch.agent_kp.1",
                name="财务制度",
                status="active",
            )
        )
        db.add(
            KnowledgeDocument(
                id="kdoc_sha",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="制度.txt",
                file_type="txt",
                title="制度",
                status="ready",
                source_sha256="abc123",
            )
        )
        db.commit()
        clone_knowledge_version_assets(db, "tenant_kp", "kb_kp", "kbver_kp", "kbver_next")
        db.commit()
        cloned = db.exec(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_version_id == "kbver_next"
            )
        ).one()
        assert cloned.source_sha256 == "abc123"
        assert cloned.id != "kdoc_sha"


def test_replace_cancel_restores_previous_head_and_keeps_document(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    from app.db.models import AgentKnowledgeBranch
    from app.knowledge.service import KnowledgeService

    with Session(_engine()) as db:
        _seed(db)
        db.add(
            KnowledgeBaseVersion(
                id="kbver_new",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                version="1.0.0-branch.agent_kp.1",
                name="财务制度",
                status="active",
            )
        )
        db.add(
            AgentKnowledgeBranch(
                tenant_id="tenant_kp",
                agent_id="agent_kp",
                knowledge_base_id="kb_kp",
                base_version="1.0.0",
                head_version="1.0.0-branch.agent_kp.1",
                status="active",
            )
        )
        db.add(
            KnowledgeDocument(
                id="kdoc_keep",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_new",
                filename="制度.txt",
                file_type="txt",
                title="制度",
                status="ready",
            )
        )
        proposal = KnowledgeUpdateProposal(
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            session_id="session_kp",
            channel="wecom",
            binding_id="chan_kp",
            source_message_id="turn_cancel_replace",
            attachment_id="att_keep",
            source_sha256="b" * 64,
            filename="制度.txt",
            size=4,
            target_knowledge_base_id="kb_kp",
            target_json={"to_user_id": "u1", "context_token": "ctx"},
            confirmation_code="DEF456",
            status="executing",
            expires_at=utc_now() + timedelta(minutes=30),
        )
        db.add(proposal)
        db.commit()
        service = KnowledgeService(db)
        job = service.create_ingest_job(
            __import__("app.knowledge.service", fromlist=["IngestPayload"]).IngestPayload(
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_new",
                filename="制度.txt",
                content_base64="YQ==",
                operation="replace",
                target_document_id="kdoc_keep",
                proposal_id=proposal.id,
                metadata={
                    "agent_id": "agent_kp",
                    "previous_knowledge_base_version_id": "kbver_kp",
                },
            )
        )
        job.document_id = "kdoc_keep"
        job.status = "running"
        db.add(job)
        db.commit()
        service._finalize_cancelled_job(job, "入库任务已取消")
        branch = db.exec(select(AgentKnowledgeBranch)).one()
        assert branch.head_version == "1.0.0"
        assert db.get(KnowledgeDocument, "kdoc_keep") is not None
        assert db.get(KnowledgeUpdateProposal, proposal.id).status == "cancelled"


def test_channel_negative_reply_then_replace_updates_document(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    _visible(monkeypatch)
    from types import SimpleNamespace

    import app.knowledge.proposals as proposals
    from app.channels.service_intake import _try_handle_knowledge_proposal_confirmation
    from app.db.models import AgentKnowledgeBranch
    from app.knowledge.service import KnowledgeService

    monkeypatch.setattr(proposals, "enqueue_async_job", lambda *args, **kwargs: None)
    engine = _engine()
    with Session(engine) as db:
        session = _seed(db)
        session.channel = "wecom"
        binding = ChannelBinding(
            id="chan_replace_flow",
            tenant_id="tenant_kp",
            agent_id="agent_kp",
            channel="wecom",
            status="active",
            external_account_key="wecom:replace-flow",
        )
        db.add(binding)
        db.add(
            KnowledgeDocument(
                id="kdoc_live",
                tenant_id="tenant_kp",
                knowledge_base_id="kb_kp",
                knowledge_base_version_id="kbver_kp",
                filename="制度.txt",
                file_type="txt",
                title="制度",
                status="ready",
                source_sha256="old-hash",
                metadata_json={"raw_text": "旧的差旅标准：只能坐绿皮火车。"},
            )
        )
        db.add(session)
        db.commit()
        attachment = _attachment(data="新的差旅标准：可以坐高铁二等座。".encode())
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=session,
            attachment=attachment,
            source_message_id="turn_replace_flow",
            requested_action="create",
        )
        assert proposal.candidates_json
        refused = SimpleNamespace(
            text=f"不要新增 {proposal.confirmation_code}",
            external_conv_id="wecom_conv",
            from_user_id="external-user",
            is_group=False,
            event_id="event_refuse_replace",
        )
        assert _try_handle_knowledge_proposal_confirmation(
            db,
            binding,
            refused,
            user=SimpleNamespace(id="user_owner"),
            chat_session=session,
            target={"to_user_id": "external-user", "context_token": "ctx"},
            scope="scope",
        )
        refused_row = db.get(KnowledgeUpdateProposal, proposal.id)
        assert refused_row.status == "pending"
        assert refused_row.job_id is None
        replaced = SimpleNamespace(
            text=f"覆盖 1 {proposal.confirmation_code}",
            external_conv_id="wecom_conv",
            from_user_id="external-user",
            is_group=False,
            event_id="event_do_replace",
        )
        assert _try_handle_knowledge_proposal_confirmation(
            db,
            binding,
            replaced,
            user=SimpleNamespace(id="user_owner"),
            chat_session=session,
            target={"to_user_id": "external-user", "context_token": "ctx"},
            scope="scope",
        )
        executing = db.get(KnowledgeUpdateProposal, proposal.id)
        assert executing.job_id
        KnowledgeService(db)._run_ingest_job(executing.job_id)
        finished = db.get(KnowledgeUpdateProposal, proposal.id)
        assert finished.status == "succeeded"
        branch = db.exec(select(AgentKnowledgeBranch)).one()
        head = db.exec(
            select(KnowledgeBaseVersion).where(
                KnowledgeBaseVersion.version == branch.head_version
            )
        ).one()
        current = db.exec(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_version_id == head.id,
                KnowledgeDocument.filename == "制度.txt",
            )
        ).one()
        assert current.status == "ready"
        assert "高铁二等座" in str((current.metadata_json or {}).get("raw_text") or "")
        assert db.get(KnowledgeDocument, "kdoc_live").metadata_json["raw_text"].startswith("旧的差旅标准")

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
        assert proposal.confirmation_code.isupper()


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

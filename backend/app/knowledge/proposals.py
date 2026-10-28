from __future__ import annotations

import base64
import difflib
import hashlib
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.agents.branching import (
    knowledge_version_for_upload,
    visible_knowledge_base_versions,
)
from app.channels.media import MAX_CHANNEL_MEDIA_BYTES
from app.async_jobs import enqueue_async_job
from app.db.models import (
    ChannelDelivery,
    ChannelIdentity,
    ChatSession,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeIngestJob,
    KnowledgeUpdateProposal,
    User,
    utc_now,
)
from app.knowledge.parser import KnowledgeParseError, extract_text
from app.security.permissions import ensure_agent_scope_manager
from app.knowledge.service import IngestPayload, KnowledgeService
from app.session.attachment_store import read_staged_chat_attachment
from app.session.session_schema import ChatAttachmentRead
from app.time_utils import as_utc


MAX_CHAT_ATTACHMENT_BYTES = 12 * 1024 * 1024
MAX_KNOWLEDGE_CANDIDATES = 3
SIMILARITY_THRESHOLD = 0.85
PROPOSAL_TTL_MINUTES = 30
SUPPORTED_KNOWLEDGE_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


class KnowledgeProposalError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable


def _new_confirmation_code(db: Session, tenant_id: str) -> str:
    for _ in range(20):
        code = secrets.token_hex(3).upper()
        exists = db.exec(
            select(KnowledgeUpdateProposal.id).where(
                KnowledgeUpdateProposal.tenant_id == tenant_id,
                KnowledgeUpdateProposal.confirmation_code == code,
            )
        ).first()
        if not exists:
            return code
    raise KnowledgeProposalError("CONFIRMATION_CODE_FAILED", "无法生成确认码，请重试。", retryable=True)


def _normalize_action(value: str | None) -> str:
    action = str(value or "undecided").strip().lower()
    if action not in {"create", "replace", "undecided"}:
        raise KnowledgeProposalError(
            "INVALID_ACTION", "知识库操作只能是 create、replace 或 undecided。"
        )
    return action


def _validate_attachment(attachment: ChatAttachmentRead) -> str:
    filename = str(attachment.filename or "").strip()
    suffix = Path(filename).suffix.lower()
    if not filename or not suffix:
        raise KnowledgeProposalError(
            "UNSUPPORTED_FORMAT", "知识库只接受带扩展名的 pdf/docx/txt/md/html 文件。"
        )
    if suffix == ".doc":
        raise KnowledgeProposalError(
            "UNSUPPORTED_FORMAT", "暂不支持旧版 .doc，请转换为 .docx 后上传。"
        )
    if attachment.kind == "image" or suffix in _IMAGE_SUFFIXES:
        raise KnowledgeProposalError(
            "UNSUPPORTED_FORMAT", "图片不能作为知识库文档，请上传 pdf/docx/txt/md/html。"
        )
    if suffix not in SUPPORTED_KNOWLEDGE_SUFFIXES:
        raise KnowledgeProposalError(
            "UNSUPPORTED_FORMAT",
            "暂不支持该文件格式，请上传 pdf/docx/txt/md/html。",
            details={"suffix": suffix},
        )
    return suffix


def _resolve_actor_user_id(
    db: Session,
    chat_session: ChatSession,
    *,
    external_user_id: str | None,
    external_account_scope: str | None,
) -> str:
    if not external_user_id:
        return str(chat_session.user_id or "").strip()
    channel = str(chat_session.channel or "").strip()
    if not channel:
        return str(chat_session.user_id or "").strip()
    identity = db.exec(
        select(ChannelIdentity).where(
            ChannelIdentity.tenant_id == chat_session.tenant_id,
            ChannelIdentity.channel == channel,
            ChannelIdentity.external_account_scope
            == str(external_account_scope or ""),
            ChannelIdentity.external_user_id == external_user_id,
        )
    ).first()
    if identity is None:
        raise KnowledgeProposalError(
            "CHANNEL_IDENTITY_NOT_BOUND",
            "当前渠道账号还没有绑定 StaffDeck 用户，不能更新知识库。",
        )
    return str(identity.staffdeck_user_id or "").strip()


def _ensure_writable_agent(
    db: Session,
    chat_session: ChatSession,
    *,
    external_user_id: str | None,
    external_account_scope: str | None,
) -> tuple[str, str]:
    user_id = _resolve_actor_user_id(
        db,
        chat_session,
        external_user_id=external_user_id,
        external_account_scope=external_account_scope,
    )
    if not user_id:
        raise KnowledgeProposalError("AUTH_REQUIRED", "当前会话没有可用的 StaffDeck 用户。")
    user = db.get(User, user_id)
    if user is None or user.tenant_id != chat_session.tenant_id:
        raise KnowledgeProposalError("AUTH_REQUIRED", "当前用户不存在或不属于该租户。")
    agent_id = str(chat_session.agent_id or "").strip()
    if not agent_id:
        raise KnowledgeProposalError("AGENT_REQUIRED", "当前会话没有关联数字员工。")
    try:
        agent = ensure_agent_scope_manager(db, chat_session.tenant_id, agent_id, user)
    except HTTPException as exc:
        raise KnowledgeProposalError(
            "PERMISSION_DENIED",
            "你当前没有更新该员工知识库的权限。",
            details={"status_code": exc.status_code},
        ) from exc
    if agent is None or agent.is_overall:
        raise KnowledgeProposalError(
            "OPEN_GALLERY_WRITE_UNSUPPORTED",
            "聊天只允许更新员工私有知识库；开放画廊请使用网页知识库后台。",
        )
    return agent_id, user_id


def _resolve_target_knowledge_base(
    db: Session,
    *,
    tenant_id: str,
    agent_id: str,
    requested_knowledge_base_id: str | None,
) -> tuple[str, str]:
    visible = visible_knowledge_base_versions(db, tenant_id, agent_id)
    if not visible:
        raise KnowledgeProposalError(
            "NO_WRITABLE_KNOWLEDGE_BASE",
            "当前员工没有可见的知识库，请联系管理员配置后再试。",
        )
    requested = str(requested_knowledge_base_id or "").strip()
    if requested:
        version = visible.get(requested)
        if version is None:
            raise KnowledgeProposalError(
                "KNOWLEDGE_BASE_NOT_VISIBLE",
                "指定知识库不在当前员工的知识范围内。",
            )
        return requested, str(version.id)
    if len(visible) == 1:
        knowledge_base_id = next(iter(visible))
        return knowledge_base_id, str(visible[knowledge_base_id].id)
    options = []
    for kb_id in visible:
        kb = db.get(KnowledgeBase, kb_id)
        options.append({"id": kb_id, "name": kb.name if kb else kb_id})
    raise KnowledgeProposalError(
        "KNOWLEDGE_BASE_REQUIRED",
        "当前员工绑定了多个知识库，请先指定要更新哪一个。",
        details={"knowledge_bases": options},
    )


def _document_text(row: KnowledgeDocument) -> str:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    raw = str(metadata.get("raw_text") or "").strip()
    if raw:
        return raw
    excerpt = str(metadata.get("raw_text_excerpt") or "").strip()
    if excerpt:
        return excerpt
    card = metadata.get("document_card")
    if isinstance(card, dict):
        parts = [
            str(card.get("title") or ""),
            str(card.get("summary") or ""),
        ]
        outline = card.get("outline")
        if isinstance(outline, list):
            parts.extend(
                str(item.get("title") or "")
                for item in outline
                if isinstance(item, dict)
            )
        return "\n".join(part for part in parts if part.strip())
    return ""


def _normalized(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _candidate_documents(
    db: Session,
    *,
    tenant_id: str,
    knowledge_base_id: str,
    knowledge_base_version_id: str,
    filename: str,
    title: str,
    new_text: str,
    source_sha256: str,
) -> list[dict[str, Any]]:
    rows = db.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.knowledge_base_version_id == knowledge_base_version_id,
            KnowledgeDocument.status != "archived",
        )
    ).all()
    exact = [row for row in rows if str(row.source_sha256 or "") == source_sha256]
    if exact:
        row = exact[0]
        raise KnowledgeProposalError(
            "DUPLICATE_DOCUMENT",
            "这份文件已经存在于目标知识库中，本次不会重复新增。",
            details={
                "document": {
                    "id": row.id,
                    "title": row.title or row.filename,
                    "filename": row.filename,
                    "updated_at": row.updated_at.isoformat(),
                }
            },
        )
    target_filename = _normalized(Path(filename).name)
    target_title = _normalized(title)
    new_text_sample = _normalized(new_text)[:4000]
    candidates: list[dict[str, Any]] = []
    for row in rows:
        row_filename = _normalized(Path(row.filename).name)
        row_title = _normalized(str(row.title or ""))
        reasons: list[str] = []
        score = 0.0
        if target_filename and row_filename == target_filename:
            reasons.append("filename")
            score = max(score, 1.0)
        if target_title and row_title == target_title:
            reasons.append("title")
            score = max(score, 1.0)
        row_text = _normalized(_document_text(row))[:4000]
        if new_text_sample and row_text:
            ratio = difflib.SequenceMatcher(
                None, new_text_sample, row_text, autojunk=False
            ).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                reasons.append("content")
                score = max(score, ratio)
        if not reasons:
            continue
        candidates.append(
            {
                "document_id": row.id,
                "title": row.title or row.filename,
                "filename": row.filename,
                "knowledge_base_id": row.knowledge_base_id,
                "knowledge_base_version_id": row.knowledge_base_version_id,
                "updated_at": _iso_datetime(row.updated_at),
                "expected_updated_at": _iso_datetime(row.updated_at),
                "match_reasons": reasons,
                "score": round(score, 4),
            }
        )
    candidates.sort(key=lambda item: (-float(item["score"]), item["updated_at"]))
    return candidates[:MAX_KNOWLEDGE_CANDIDATES]


def create_knowledge_update_proposal(
    db: Session,
    *,
    chat_session: ChatSession,
    attachment: ChatAttachmentRead,
    source_message_id: str,
    requested_action: str | None = None,
    target_knowledge_base_id: str | None = None,
    external_user_id: str | None = None,
    external_account_scope: str | None = None,
    binding_id: str | None = None,
    target_json: dict[str, Any] | None = None,
) -> KnowledgeUpdateProposal:
    """Create a durable proposal only; never ingest or overwrite here."""

    action = _normalize_action(requested_action)
    source_message_id = str(source_message_id or "").strip()
    if not source_message_id:
        raise KnowledgeProposalError("SOURCE_REQUIRED", "缺少来源消息 ID。")
    suffix = _validate_attachment(attachment)
    channel = str(chat_session.channel or "web").strip() or "web"
    max_bytes = (
        MAX_CHAT_ATTACHMENT_BYTES if channel == "web" else MAX_CHANNEL_MEDIA_BYTES
    )
    declared_size = int(attachment.size or 0)
    if declared_size > max_bytes:
        raise KnowledgeProposalError(
            "ATTACHMENT_TOO_LARGE",
            f"文件超过 {max_bytes // (1024 * 1024)}MB 的上限。",
            details={"size": declared_size, "max_bytes": max_bytes},
        )
    data = read_staged_chat_attachment(
        attachment,
        tenant_id=chat_session.tenant_id,
        user_id=str(chat_session.user_id or ""),
    )
    if data is None:
        raise KnowledgeProposalError(
            "ATTACHMENT_MISSING", "附件已过期或无法读取，请重新上传。"
        )
    if len(data) > max_bytes:
        raise KnowledgeProposalError(
            "ATTACHMENT_TOO_LARGE",
            f"文件超过 {max_bytes // (1024 * 1024)}MB 的上限。",
            details={"size": len(data), "max_bytes": max_bytes},
        )
    source_sha256 = hashlib.sha256(data).hexdigest()
    if attachment.sha256 and attachment.sha256.lower() != source_sha256:
        raise KnowledgeProposalError("ATTACHMENT_CHANGED", "附件内容已变化，请重新上传。")
    try:
        text, file_type = extract_text(attachment.filename, data)
    except KnowledgeParseError as exc:
        raise KnowledgeProposalError("PARSE_FAILED", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface a stable user-facing parse error.
        raise KnowledgeProposalError(
            "PARSE_FAILED", f"文件解析失败：{type(exc).__name__}"
        ) from exc
    if not text.strip():
        raise KnowledgeProposalError(
            "EMPTY_DOCUMENT", "文件没有可提取的文本内容，不能加入知识库。"
        )

    agent_id, user_id = _ensure_writable_agent(
        db,
        chat_session,
        external_user_id=external_user_id,
        external_account_scope=external_account_scope,
    )
    knowledge_base_id, version_id = _resolve_target_knowledge_base(
        db,
        tenant_id=chat_session.tenant_id,
        agent_id=agent_id,
        requested_knowledge_base_id=target_knowledge_base_id,
    )

    existing = db.exec(
        select(KnowledgeUpdateProposal).where(
            KnowledgeUpdateProposal.tenant_id == chat_session.tenant_id,
            KnowledgeUpdateProposal.target_knowledge_base_id == knowledge_base_id,
            KnowledgeUpdateProposal.source_sha256 == source_sha256,
            KnowledgeUpdateProposal.source_message_id == source_message_id,
        )
    ).first()
    if existing is not None:
        return existing

    existing_same_file = db.exec(
        select(KnowledgeUpdateProposal).where(
            KnowledgeUpdateProposal.tenant_id == chat_session.tenant_id,
            KnowledgeUpdateProposal.session_id == chat_session.id,
            KnowledgeUpdateProposal.target_knowledge_base_id == knowledge_base_id,
            KnowledgeUpdateProposal.source_sha256 == source_sha256,
            KnowledgeUpdateProposal.status.in_({"pending", "executing"}),
        )
    ).first()
    if existing_same_file is not None:
        return existing_same_file

    candidates = _candidate_documents(
        db,
        tenant_id=chat_session.tenant_id,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_version_id=version_id,
        filename=attachment.filename,
        title=str(Path(attachment.filename).stem or attachment.filename),
        new_text=text,
        source_sha256=source_sha256,
    )
    if candidates:
        proposed_action = "undecided"
    elif action == "replace":
        raise KnowledgeProposalError(
            "NO_MATCHING_DOCUMENT", "没有找到可覆盖的文档，请选择作为新文档加入。"
        )
    elif action == "create":
        proposed_action = "create"
    else:
        proposed_action = "undecided"

    proposal = KnowledgeUpdateProposal(
        tenant_id=chat_session.tenant_id,
        agent_id=agent_id,
        session_id=chat_session.id,
        binding_id=binding_id,
        channel=channel,
        external_user_id=external_user_id,
        external_account_scope=external_account_scope,
        source_message_id=source_message_id,
        attachment_id=attachment.id,
        source_sha256=source_sha256,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size=len(data),
        confirmation_code=_new_confirmation_code(db, chat_session.tenant_id),
        target_knowledge_base_id=knowledge_base_id,
        target_knowledge_base_version_id=version_id,
        target_json=dict(target_json or {}),
        proposed_action=proposed_action,
        candidates_json=candidates,
        metadata_json={
            "file_type": file_type,
            "suffix": suffix,
            "extracted_chars": len(text),
            "acting_user_id": user_id,
        },
        status="pending",
        expires_at=utc_now() + timedelta(minutes=PROPOSAL_TTL_MINUTES),
    )
    db.add(proposal)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.exec(
            select(KnowledgeUpdateProposal).where(
                KnowledgeUpdateProposal.tenant_id == chat_session.tenant_id,
                KnowledgeUpdateProposal.target_knowledge_base_id == knowledge_base_id,
                KnowledgeUpdateProposal.source_sha256 == source_sha256,
                KnowledgeUpdateProposal.source_message_id == source_message_id,
            )
        ).first()
        if existing is not None:
            return existing
        raise
    db.refresh(proposal)
    return proposal


__all__ = [
    "KnowledgeProposalError",
    "MAX_CHAT_ATTACHMENT_BYTES",
    "MAX_KNOWLEDGE_CANDIDATES",
    "PROPOSAL_TTL_MINUTES",
    "SIMILARITY_THRESHOLD",
    "SUPPORTED_KNOWLEDGE_SUFFIXES",
    "create_knowledge_update_proposal",
]


def _document_for_version(
    db: Session,
    source: KnowledgeDocument,
    version_id: str,
) -> KnowledgeDocument:
    if source.knowledge_base_version_id == version_id:
        return source
    rows = db.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == source.tenant_id,
            KnowledgeDocument.knowledge_base_id == source.knowledge_base_id,
            KnowledgeDocument.knowledge_base_version_id == version_id,
            KnowledgeDocument.filename == source.filename,
            KnowledgeDocument.file_type == source.file_type,
        )
    ).all()
    if not rows:
        raise KnowledgeProposalError(
            "KNOWLEDGE_VERSION_COPY_NOT_FOUND", "目标文档的版本副本不存在。"
        )
    exact = [
        row
        for row in rows
        if row.title == source.title and row.created_at == source.created_at
    ]
    return exact[0] if exact else rows[0]


def _iso_datetime(value: Any) -> str:
    parsed = as_utc(value) if value is not None else None
    return parsed.isoformat() if parsed else ""


def confirm_knowledge_update_proposal(
    db: Session,
    proposal: KnowledgeUpdateProposal,
    *,
    action: str,
    confirmed_by_user_id: str,
    expected_updated_at: str | None = None,
    target_document_id: str | None = None,
) -> KnowledgeIngestJob:
    """Confirm a pending proposal and create the existing ingest job."""

    normalized_action = _normalize_action(action)
    if normalized_action == "undecided":
        raise KnowledgeProposalError("INVALID_ACTION", "请选择新增或覆盖。")
    if proposal.status in {"executing", "succeeded"} and proposal.job_id:
        job = db.get(KnowledgeIngestJob, proposal.job_id)
        if job is not None:
            return job
    if proposal.status != "pending":
        raise KnowledgeProposalError("PROPOSAL_NOT_PENDING", "这个提案已经处理过或已失效。")
    if as_utc(proposal.expires_at) <= utc_now():
        proposal.status = "expired"
        proposal.updated_at = utc_now()
        db.add(proposal)
        db.commit()
        raise KnowledgeProposalError("PROPOSAL_EXPIRED", "这次确认已过期，请重新上传文件发起更新。")
    acting_user_id = str(
        (proposal.metadata_json or {}).get("acting_user_id") or ""
    ).strip()
    if acting_user_id and acting_user_id != confirmed_by_user_id:
        raise KnowledgeProposalError(
            "PERMISSION_DENIED", "只有发起该提案的用户可以确认。"
        )
    target: KnowledgeDocument | None = None
    if normalized_action == "replace":
        requested_target = str(target_document_id or "").strip()
        if not requested_target:
            raise KnowledgeProposalError("TARGET_REQUIRED", "请选择要覆盖的文档。")
        target = db.get(KnowledgeDocument, requested_target)
        if (
            target is None
            or target.tenant_id != proposal.tenant_id
            or target.knowledge_base_id != proposal.target_knowledge_base_id
        ):
            raise KnowledgeProposalError("TARGET_NOT_FOUND", "要覆盖的文档不存在。")
        candidate = next(
            (
                item
                for item in proposal.candidates_json or []
                if str(item.get("document_id") or "") == requested_target
            ),
            None,
        )
        if candidate is None:
            raise KnowledgeProposalError(
                "TARGET_NOT_IN_PROPOSAL", "该文档不在本次提案的候选列表中。"
            )
        expected = str(
            expected_updated_at
            or candidate.get("expected_updated_at")
            or ""
        ).strip()
        if not expected:
            raise KnowledgeProposalError(
                "EXPECTED_VERSION_REQUIRED", "覆盖缺少文档版本信息，请重新发起提案。"
            )
        if _iso_datetime(target.updated_at) != expected:
            raise KnowledgeProposalError(
                "DOCUMENT_CONFLICT", "文档刚被其他人修改，本次未覆盖，请重新发起。"
            )
    elif target_document_id:
        raise KnowledgeProposalError(
            "INVALID_TARGET", "新增提案不能指定覆盖文档。"
        )
    attachment = ChatAttachmentRead(
        id=proposal.attachment_id,
        filename=proposal.filename,
        content_type=proposal.content_type or "application/octet-stream",
        size=proposal.size,
        kind="binary",
        sha256=proposal.source_sha256,
    )
    data = read_staged_chat_attachment(
        attachment,
        tenant_id=proposal.tenant_id,
        user_id=acting_user_id or confirmed_by_user_id,
    )
    if data is None:
        raise KnowledgeProposalError(
            "ATTACHMENT_MISSING", "附件已过期或无法读取，请重新上传。"
        )
    if hashlib.sha256(data).hexdigest() != proposal.source_sha256:
        raise KnowledgeProposalError("ATTACHMENT_CHANGED", "附件内容已变化，请重新上传。")
    try:
        version = knowledge_version_for_upload(
            db,
            proposal.tenant_id,
            proposal.target_knowledge_base_id,
            proposal.agent_id,
            metadata_json={
                "source": "chat",
                "proposal_id": proposal.id,
                "session_id": proposal.session_id,
            },
        )
    except HTTPException as exc:
        raise KnowledgeProposalError(
            "KNOWLEDGE_BASE_NOT_WRITABLE", "目标知识库当前不可写。"
        ) from exc
    target_version_id = version.id
    job_target_document_id: str | None = None
    expected_value: str | None = None
    if normalized_action == "replace":
        assert target is not None
        expected_value = str(
            expected_updated_at
            or next(
                (
                    item.get("expected_updated_at")
                    for item in proposal.candidates_json or []
                    if str(item.get("document_id") or "") == target.id
                ),
                "",
            )
            or ""
        ).strip()
        source_target_id = target.id
        target = _document_for_version(db, target, version.id)
        target_version_id = str(target.knowledge_base_version_id or version.id)
        job_target_document_id = target.id
    service = KnowledgeService(db)
    job = service.create_ingest_job(
        IngestPayload(
            tenant_id=proposal.tenant_id,
            knowledge_base_id=proposal.target_knowledge_base_id,
            knowledge_base_version_id=target_version_id,
            operation=normalized_action,
            target_document_id=job_target_document_id,
            expected_updated_at=expected_value,
            source_sha256=proposal.source_sha256,
            source_message_id=proposal.source_message_id,
            proposal_id=proposal.id,
            filename=proposal.filename,
            content_base64=base64.b64encode(data).decode("ascii"),
            title=Path(proposal.filename).stem or proposal.filename,
            metadata={
                "source": "chat",
                "proposal_id": proposal.id,
                "source_message_id": proposal.source_message_id,
                "source_sha256": proposal.source_sha256,
                "session_id": proposal.session_id,
                "channel": proposal.channel,
                "user_id": confirmed_by_user_id,
                "agent_id": proposal.agent_id,
                "source_document_id": (
                    source_target_id if normalized_action == "replace" else None
                ),
                "expected_updated_at": expected_value,
            },
        )
    )
    enqueue_async_job(
        "knowledge_ingest",
        service.run_ingest_job,
        job.id,
        metadata={
            "tenant_id": proposal.tenant_id,
            "knowledge_base_id": proposal.target_knowledge_base_id,
            "proposal_id": proposal.id,
        },
    )
    proposal.status = "executing"
    proposal.job_id = job.id
    proposal.proposed_action = normalized_action
    proposal.target_document_id = target.id if target is not None else None
    proposal.target_knowledge_base_version_id = target_version_id
    proposal.confirmed_at = utc_now()
    proposal.confirmed_by_user_id = confirmed_by_user_id
    proposal.expected_updated_at = expected_value or proposal.expected_updated_at
    proposal.updated_at = utc_now()
    db.add(proposal)
    db.commit()
    return job


def notify_knowledge_proposal_result(
    db: Session,
    job: KnowledgeIngestJob,
    *,
    error: str | None = None,
) -> None:
    """Update proposal state and enqueue a channel result notice when applicable."""

    proposal_id = str(job.proposal_id or "").strip()
    if not proposal_id:
        return
    proposal = db.get(KnowledgeUpdateProposal, proposal_id)
    if proposal is None:
        return
    succeeded = not error and job.status == "succeeded"
    proposal.status = "succeeded" if succeeded else "failed"
    proposal.updated_at = utc_now()
    db.add(proposal)
    if proposal.channel != "web" and proposal.binding_id and proposal.target_json:
        name = proposal.filename
        text = (
            f"知识库更新完成：《{name}》已生效，可以直接检索。"
            if succeeded
            else f"知识库更新失败：{name}。{error or '请查看知识库任务。'}"
        )
        key = f"knowledge-proposal-result:{proposal.id}"
        existing = db.exec(
            select(ChannelDelivery).where(ChannelDelivery.idempotency_key == key)
        ).first()
        if existing is None:
            db.add(
                ChannelDelivery(
                    tenant_id=proposal.tenant_id,
                    binding_id=proposal.binding_id,
                    session_id=proposal.session_id,
                    message_id=None,
                    target_json=dict(proposal.target_json),
                    kind="notice",
                    text=text,
                    status="pending",
                    next_attempt_at=utc_now(),
                    idempotency_key=key,
                )
            )
    db.commit()

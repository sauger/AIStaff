from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlmodel import Session, select

from app.db.models import ChatSession, KnowledgeUpdateProposal, utc_now
from app.knowledge.confirmation import (
    CANCEL_SELECTION,
    confirmation_hint,
    is_knowledge_confirm_intent,
    parse_knowledge_confirmation,
    pending_created_at,
    select_pending_knowledge_base,
)
from app.knowledge.proposals import (
    KnowledgeProposalError,
    confirm_knowledge_update_proposal,
    create_knowledge_update_proposal,
)
from app.session.session_schema import ChatAttachmentRead
from app.time_utils import as_utc

_PENDING_TTL = timedelta(minutes=30)


def prepare_web_knowledge_reply(
    db: Session,
    chat_session: ChatSession,
    *,
    text: str,
    user_id: str,
    event_id: str,
) -> str | None:
    """Create or confirm a proposal before the web turn reaches the model."""

    pending_reply = _prepare_pending_selection(
        db,
        chat_session,
        text=text,
        event_id=event_id,
    )
    if pending_reply is not None:
        return pending_reply
    return _prepare_confirmation(db, chat_session, text=text, user_id=user_id)


def _prepare_pending_selection(
    db: Session,
    chat_session: ChatSession,
    *,
    text: str,
    event_id: str,
) -> str | None:
    state = dict(getattr(chat_session, "context_state_json", None) or {})
    pending = state.get("knowledge_pending")
    if not isinstance(pending, dict):
        return None
    if _pending_expired(pending):
        _clear_pending(db, chat_session, state)
        return None
    targets = [item for item in (pending.get("targets") or []) if isinstance(item, dict)]
    if not targets:
        return None
    selected = select_pending_knowledge_base(text, targets)
    if selected is None:
        return None
    if selected is CANCEL_SELECTION:
        _clear_pending(db, chat_session, state)
        return "已取消这次知识库操作。"
    try:
        attachment = ChatAttachmentRead.model_validate(pending.get("attachment"))
    except ValidationError:
        _clear_pending(db, chat_session, state)
        return "附件信息已失效，请重新上传文件。"
    try:
        proposal = create_knowledge_update_proposal(
            db,
            chat_session=chat_session,
            attachment=attachment,
            source_message_id=f"{event_id}:{attachment.id}",
            requested_action=str(pending.get("requested_action") or "undecided"),
            target_knowledge_base_id=str(selected.get("id") or ""),
        )
    except KnowledgeProposalError as exc:
        _clear_pending(db, chat_session, state)
        return str(exc)
    _clear_pending(db, chat_session, state)
    target_name = str(selected.get("name") or selected.get("id") or "")
    hint = confirmation_hint(str(proposal.confirmation_code or ""), proposal.candidates_json)
    return (
        f"知识库提案已创建。\n目标知识库：{target_name}\n"
        f"确认码：{proposal.confirmation_code}\n请回复：{hint}"
    )


def _prepare_confirmation(
    db: Session,
    chat_session: ChatSession,
    *,
    text: str,
    user_id: str,
) -> str | None:
    command = parse_knowledge_confirmation(text)
    if command is None:
        if not is_knowledge_confirm_intent(text):
            return None
        pending = _latest_pending(db, chat_session)
        if pending is None:
            return None
        hint = confirmation_hint(str(pending.confirmation_code or ""), pending.candidates_json)
        return f"要确认这次知识库操作，请回复：{hint}"
    proposal = db.exec(
        select(KnowledgeUpdateProposal).where(
            KnowledgeUpdateProposal.tenant_id == chat_session.tenant_id,
            KnowledgeUpdateProposal.session_id == chat_session.id,
            KnowledgeUpdateProposal.confirmation_code == command.code,
        )
    ).first()
    if proposal is None:
        return "没有找到这个知识库操作，可能已过期，请重新发送文件并说明要加入知识库。"
    if command.kind == "negative":
        return f"这次没有写入知识库。要取消请回复：取消 {proposal.confirmation_code}"
    if command.kind == "cancel":
        if proposal.status == "pending":
            proposal.status = "cancelled"
            proposal.updated_at = utc_now()
            db.add(proposal)
            db.commit()
        return "已取消这次知识库操作。"
    action, target_document_id, expected_updated_at, clarify = _action_for_command(proposal, command)
    if clarify:
        return clarify
    try:
        confirm_knowledge_update_proposal(
            db,
            proposal,
            action=action,
            confirmed_by_user_id=user_id,
            target_document_id=target_document_id,
            expected_updated_at=expected_updated_at,
        )
    except KnowledgeProposalError as exc:
        return str(exc)
    return "已确认知识库操作，正在处理；完成后会在这里通知你。"


def _action_for_command(
    proposal: KnowledgeUpdateProposal,
    command: Any,
) -> tuple[str, str | None, str | None, str | None]:
    candidates = list(proposal.candidates_json or [])
    code = str(proposal.confirmation_code or "")
    if command.kind == "bare":
        if candidates:
            return "", None, None, (
                f"请回复：{confirmation_hint(code, candidates)}。"
                f"如果要另存为新文档，请回复：新增 {code}"
            )
        return "create", None, None, None
    if command.kind == "create":
        return "create", None, None, None
    index = command.index
    if index is None:
        if len(candidates) == 1:
            index = 1
        else:
            return "", None, None, f"请回复：覆盖 1 {code}"
    position = index - 1
    if position < 0 or position >= len(candidates):
        return "", None, None, "候选序号无效，请按提示重新回复。"
    candidate = candidates[position]
    return (
        "replace",
        str(candidate.get("document_id") or "").strip(),
        str(candidate.get("expected_updated_at") or "").strip(),
        None,
    )


def _latest_pending(db: Session, chat_session: ChatSession) -> KnowledgeUpdateProposal | None:
    rows = db.exec(
        select(KnowledgeUpdateProposal).where(
            KnowledgeUpdateProposal.tenant_id == chat_session.tenant_id,
            KnowledgeUpdateProposal.session_id == chat_session.id,
            KnowledgeUpdateProposal.status == "pending",
        )
    ).all()
    now = utc_now()
    fresh = [row for row in rows if as_utc(row.expires_at) > now]
    if not fresh:
        return None
    return max(fresh, key=lambda row: row.created_at)


def _pending_expired(pending: dict[str, Any]) -> bool:
    created = pending_created_at(pending.get("created_at"))
    if created is None:
        return False
    return created + _PENDING_TTL <= utc_now()


def _clear_pending(db: Session, chat_session: ChatSession, state: dict[str, Any]) -> None:
    state.pop("knowledge_pending", None)
    chat_session.context_state_json = state
    db.add(chat_session)
    db.commit()

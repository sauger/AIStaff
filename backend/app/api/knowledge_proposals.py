from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import ChatSession, KnowledgeBase, KnowledgeUpdateProposal, User, utc_now
from app.knowledge.proposals import (
    KnowledgeProposalError,
    confirm_knowledge_update_proposal,
)
from app.security.auth import get_current_user
from app.time_utils import as_utc

router = APIRouter(
    prefix="/api/chat/sessions/{session_id}/knowledge-proposals",
    tags=["chat:knowledge-proposals"],
)


class KnowledgeProposalConfirmRequest(BaseModel):
    action: Literal["create", "replace"]
    target_document_id: str | None = None
    expected_updated_at: str | None = None


class KnowledgeProposalRead(BaseModel):
    id: str
    confirmation_code: str | None = None
    status: str
    proposed_action: str
    filename: str
    content_type: str | None = None
    size: int
    target_knowledge_base_id: str
    target_knowledge_base_name: str
    candidates: list[dict] = Field(default_factory=list)
    expires_at: str
    created_at: str
    job_id: str | None = None


def _owned_session(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    current_user: User,
) -> ChatSession:
    if current_user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    session = db.get(ChatSession, session_id)
    if (
        session is None
        or session.tenant_id != tenant_id
        or session.user_id != current_user.id
    ):
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


def _proposal_read(db: Session, proposal: KnowledgeUpdateProposal) -> KnowledgeProposalRead:
    knowledge_base = db.get(KnowledgeBase, proposal.target_knowledge_base_id)
    return KnowledgeProposalRead(
        id=proposal.id,
        confirmation_code=proposal.confirmation_code,
        status=proposal.status,
        proposed_action=proposal.proposed_action,
        filename=proposal.filename,
        content_type=proposal.content_type,
        size=proposal.size,
        target_knowledge_base_id=proposal.target_knowledge_base_id,
        target_knowledge_base_name=(
            knowledge_base.name if knowledge_base else proposal.target_knowledge_base_id
        ),
        candidates=list(proposal.candidates_json or []),
        expires_at=proposal.expires_at.isoformat(),
        created_at=proposal.created_at.isoformat(),
        job_id=proposal.job_id,
    )


@router.get("", response_model=list[KnowledgeProposalRead])
def list_knowledge_proposals(
    session_id: str,
    tenant_id: Annotated[str, Query(...)],
    db: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[KnowledgeProposalRead]:
    _owned_session(
        db, tenant_id=tenant_id, session_id=session_id, current_user=current_user
    )
    rows = db.exec(
        select(KnowledgeUpdateProposal)
        .where(
            KnowledgeUpdateProposal.tenant_id == tenant_id,
            KnowledgeUpdateProposal.session_id == session_id,
            KnowledgeUpdateProposal.status == "pending",
        )
        .order_by(KnowledgeUpdateProposal.created_at)
    ).all()
    now = utc_now()
    return [
        _proposal_read(db, row)
        for row in rows
        if as_utc(row.expires_at) > now
    ]


@router.get("/{proposal_id}", response_model=KnowledgeProposalRead)
def get_knowledge_proposal(
    session_id: str,
    proposal_id: str,
    tenant_id: Annotated[str, Query(...)],
    db: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeProposalRead:
    _owned_session(
        db, tenant_id=tenant_id, session_id=session_id, current_user=current_user
    )
    proposal = db.get(KnowledgeUpdateProposal, proposal_id)
    if (
        proposal is None
        or proposal.tenant_id != tenant_id
        or proposal.session_id != session_id
    ):
        raise HTTPException(status_code=404, detail="Knowledge proposal not found")
    return _proposal_read(db, proposal)


@router.post("/{proposal_id}/confirm")
def confirm_knowledge_proposal(
    session_id: str,
    proposal_id: str,
    request: KnowledgeProposalConfirmRequest,
    tenant_id: Annotated[str, Query(...)],
    db: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    _owned_session(
        db, tenant_id=tenant_id, session_id=session_id, current_user=current_user
    )
    proposal = db.get(KnowledgeUpdateProposal, proposal_id)
    if (
        proposal is None
        or proposal.tenant_id != tenant_id
        or proposal.session_id != session_id
    ):
        raise HTTPException(status_code=404, detail="Knowledge proposal not found")
    try:
        job = confirm_knowledge_update_proposal(
            db,
            proposal,
            action=request.action,
            confirmed_by_user_id=current_user.id,
            target_document_id=request.target_document_id,
            expected_updated_at=request.expected_updated_at,
        )
    except KnowledgeProposalError as exc:
        status_code = 409 if exc.code in {
            "PROPOSAL_EXPIRED",
            "PROPOSAL_NOT_PENDING",
            "DOCUMENT_CONFLICT",
        } else 422
        if exc.code in {"PERMISSION_DENIED", "CHANNEL_IDENTITY_NOT_BOUND"}:
            status_code = 403
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc), "details": exc.details},
        ) from exc
    return {
        "proposal_id": proposal.id,
        "job_id": job.id,
        "status": job.status,
        "stage": job.stage,
    }

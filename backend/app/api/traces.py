from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.api.sessions import get_session_detail
from app.db import get_session
from app.db.models import AgentEvent, ChatSession, Message, User
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.tenant import ensure_tenant

router = APIRouter(
    prefix="/api/enterprise/traces",
    tags=["enterprise:traces"],
    dependencies=[Depends(get_current_user)],
)


@router.get("")
def list_traces(
    tenant_id: str = Query(...),
    agent_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    statement = select(ChatSession).where(
        ChatSession.tenant_id == tenant_id,
        ChatSession.user_id == current_user.id,
    )
    if agent_id:
        statement = statement.where(ChatSession.agent_id == agent_id)
    sessions = db.exec(statement.order_by(ChatSession.updated_at.desc())).all()
    traces: list[dict] = []
    for chat_session in sessions:
        events = db.exec(
            select(AgentEvent)
            .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == chat_session.id)
            .order_by(AgentEvent.created_at.desc())
        ).all()
        messages = db.exec(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.session_id == chat_session.id)
            .order_by(Message.created_at.desc())
        ).all()
        last_decision = next(
            (event.payload_json for event in events if event.event_type == "router_decision_created"),
            None,
        )
        tool_calls = len([event for event in events if event.event_type == "tool_call_finished"])
        traces.append(
            {
                "session_id": chat_session.id,
                "user_id": chat_session.user_id,
                "agent_id": chat_session.agent_id,
                "title": chat_session.title,
                "active_skill_id": chat_session.active_skill_id,
                "active_step_id": chat_session.active_step_id,
                "last_decision": last_decision,
                "last_message": messages[0].content if messages else None,
                "last_message_time": messages[0].created_at.isoformat() if messages else None,
                "tool_call_count": tool_calls,
                "status": chat_session.status,
                "updated_at": chat_session.updated_at.isoformat(),
            }
        )
    return traces


@router.get("/{session_id}")
def get_trace(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    return get_session_detail(
        session_id=session_id,
        tenant_id=tenant_id,
        current_user=current_user,
        db=db,
    )

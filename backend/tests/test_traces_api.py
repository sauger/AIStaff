from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.traces import list_traces
from app.db.models import AgentEvent, ChatSession, Message, Tenant, User


def test_list_traces_can_scope_to_one_agent() -> None:
    with _test_session() as db:
        user = _seed(db)
        _seed_session(db, session_id="session_screen", agent_id="agent_screen", user_id=user.id)
        _seed_session(db, session_id="session_other", agent_id="agent_other", user_id=user.id)

        scoped = list_traces(tenant_id="tenant_demo", agent_id="agent_screen", current_user=user, db=db)
        assert [row["session_id"] for row in scoped] == ["session_screen"]
        assert scoped[0]["agent_id"] == "agent_screen"
        assert scoped[0]["last_message"] == "帮我做一张经营大屏"

        unscoped = list_traces(tenant_id="tenant_demo", current_user=user, db=db)
        assert {row["session_id"] for row in unscoped} == {"session_screen", "session_other"}


def test_list_traces_reports_last_router_decision_and_tool_calls() -> None:
    with _test_session() as db:
        user = _seed(db)
        chat_session = _seed_session(
            db,
            session_id="session_tools",
            agent_id="agent_screen",
            user_id=user.id,
        )
        db.add(
            AgentEvent(
                id="event_decision",
                tenant_id="tenant_demo",
                session_id=chat_session.id,
                event_type="router_decision_created",
                payload_json={"target_skill_id": "screen.change", "target_step_id": "bind"},
            )
        )
        for index in range(2):
            db.add(
                AgentEvent(
                    id=f"event_tool_{index}",
                    tenant_id="tenant_demo",
                    session_id=chat_session.id,
                    event_type="tool_call_finished",
                    payload_json={"tool_name": "describe_dataset"},
                )
            )
        db.commit()

        rows = list_traces(tenant_id="tenant_demo", agent_id="agent_screen", current_user=user, db=db)
        assert rows[0]["tool_call_count"] == 2
        assert rows[0]["last_decision"]["target_skill_id"] == "screen.change"


def _seed(db: Session) -> User:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    user = User(id="user_owner", tenant_id="tenant_demo", username="owner", password_hash="x")
    db.add(user)
    db.commit()
    return user


def _seed_session(db: Session, *, session_id: str, agent_id: str, user_id: str) -> ChatSession:
    chat_session = ChatSession(
        id=session_id,
        tenant_id="tenant_demo",
        user_id=user_id,
        agent_id=agent_id,
        status="active",
        updated_at=datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC),
    )
    db.add(chat_session)
    db.add(
        Message(
            id=f"msg_{session_id}",
            tenant_id="tenant_demo",
            session_id=session_id,
            role="user",
            content="帮我做一张经营大屏",
            created_at=datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC) - timedelta(minutes=1),
        )
    )
    db.commit()
    return chat_session


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)

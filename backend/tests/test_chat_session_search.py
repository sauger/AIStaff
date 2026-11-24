from datetime import UTC, datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.chat import search_chat_sessions
from app.db.models import ChatSession, Message, Tenant, User


def test_search_matches_title_summary_and_visible_message_content() -> None:
    with _session() as db:
        user = _seed_user(db)
        _add_session(db, "title-hit", user.id, "agent-a", title="Weekly Report")
        _add_session(db, "summary-hit", user.id, "agent-a", summary="预算已经对齐")
        _add_session(db, "content-hit", user.id, "agent-a", title="普通会话")
        db.add(Message(
            id="msg-visible",
            tenant_id="tenant_demo",
            session_id="content-hit",
            role="assistant",
            content="报名系统的截止日期是周五",
        ))
        _add_session(db, "internal-only", user.id, "agent-a", title="内部会话")
        db.add(Message(
            id="msg-internal",
            tenant_id="tenant_demo",
            session_id="internal-only",
            role="user",
            content="系统提示里提到了报名系统",
            metadata_json={"message_visibility": "internal"},
        ))
        _add_session(db, "other-agent", user.id, "agent-b", title="Weekly Report for someone else")
        db.commit()

        title_page = search_chat_sessions(
            "tenant_demo", "weekly", "agent-a", None, 10, 0, user, db,
        )
        assert [item.id for item in title_page.items] == ["title-hit"]

        content_page = search_chat_sessions(
            "tenant_demo", "报名系统", "agent-a", None, 10, 0, user, db,
        )
        assert [item.id for item in content_page.items] == ["content-hit"]

        summary_page = search_chat_sessions(
            "tenant_demo", "预算", "agent-a", None, 10, 0, user, db,
        )
        assert [item.id for item in summary_page.items] == ["summary-hit"]


def test_search_pages_within_the_requested_agent() -> None:
    with _session() as db:
        user = _seed_user(db)
        for index in range(11):
            _add_session(
                db,
                f"page-{index}",
                user.id,
                "agent-a",
                title=f"分页条目 {index:02d}",
                updated_at=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=index),
            )
        _add_session(db, "other-agent", user.id, "agent-b", title="分页条目 other")
        db.commit()

        page = search_chat_sessions(
            "tenant_demo", "分页条目", "agent-a", None, 10, 10, user, db,
        )
        assert page.total == 11
        assert [item.id for item in page.items] == ["page-0"]


def test_search_keeps_personal_history_separate_from_team_sessions() -> None:
    with _session() as db:
        user = _seed_user(db)
        _add_session(db, "personal", user.id, "agent-a", title="共享词 个人")
        _add_session(
            db,
            "team",
            user.id,
            "agent-a",
            title="共享词 TL 对话",
            team_id="team-1",
        )
        db.commit()

        personal = search_chat_sessions(
            "tenant_demo", "共享词", "agent-a", None, 10, 0, user, db,
        )
        team = search_chat_sessions(
            "tenant_demo", "共享词", None, "team-1", 10, 0, user, db,
        )
        assert [item.id for item in personal.items] == ["personal"]
        assert [item.id for item in team.items] == ["team"]


def _seed_user(db: Session) -> User:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    user = User(id="user_demo", tenant_id="tenant_demo", username="demo", password_hash="x")
    db.add(user)
    db.commit()
    return user


def _add_session(
    db: Session,
    session_id: str,
    user_id: str,
    agent_id: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    team_id: str | None = None,
    updated_at: datetime | None = None,
) -> None:
    db.add(ChatSession(
        id=session_id,
        tenant_id="tenant_demo",
        user_id=user_id,
        agent_id=agent_id,
        title=title,
        summary=summary,
        team_id=team_id,
        updated_at=updated_at or datetime(2026, 8, 1, tzinfo=UTC),
    ))


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)

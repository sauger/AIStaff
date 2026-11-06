from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.chat import _harness_event_trace_line, read_dashboard_view
from app.dashboard.chart_policy import normalize_spec
from app.dashboard.service import create_view
from app.dashboard.spec import RenderInput
from app.db.models import AgentEvent, ChatSession, Tenant, User


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _spec():
    return normalize_spec(
        RenderInput.model_validate(
            {
                "title": "经营看板",
                "charts": [
                    {
                        "id": "trend",
                        "intent": "trend",
                        "encoding": {"x": "month", "y": "sales"},
                        "data": {
                            "columns": [
                                {"name": "month", "type": "string"},
                                {"name": "sales", "type": "number"},
                            ],
                            "rows": [["2026-07", 1], ["2026-08", 2], ["2026-09", 3]],
                        },
                    }
                ],
            }
        )
    )[0]


@pytest.fixture()
def db() -> Session:
    engine = _engine()
    with Session(engine) as session:
        session.add(Tenant(id="tenant_demo", name="Demo"))
        session.add(
            User(
                id="user_1",
                tenant_id="tenant_demo",
                username="owner",
                display_name="Owner",
                password_hash="x",
            )
        )
        session.add(
            User(
                id="user_2",
                tenant_id="tenant_demo",
                username="other",
                display_name="Other",
                password_hash="x",
            )
        )
        session.add(
            ChatSession(id="session_1", tenant_id="tenant_demo", user_id="user_1", agent_id="agent_1")
        )
        session.commit()
        yield session


def _view(db: Session, ttl: timedelta = timedelta(days=1)):
    return create_view(
        db,
        tenant_id="tenant_demo",
        session_id="session_1",
        spec=_spec(),
        agent_id="agent_1",
        user_id="user_1",
        ttl=ttl,
    )[0]


def test_owner_can_read_view(db: Session) -> None:
    view = _view(db)
    user = db.get(User, "user_1")
    payload = read_dashboard_view(view.id, tenant_id="tenant_demo", current_user=user, db=db)
    assert payload["id"] == view.id
    assert payload["spec"]["title"] == "经营看板"
    assert payload["spec"]["charts"][0]["type"] == "line"


def test_other_member_of_the_session_team_cannot_read(db: Session) -> None:
    view = _view(db)
    stranger = db.get(User, "user_2")
    with pytest.raises(HTTPException) as excinfo:
        read_dashboard_view(view.id, tenant_id="tenant_demo", current_user=stranger, db=db)
    assert excinfo.value.status_code == 404


def test_cross_tenant_read_is_not_found(db: Session) -> None:
    view = _view(db)
    user = db.get(User, "user_1")
    with pytest.raises(HTTPException) as excinfo:
        read_dashboard_view(view.id, tenant_id="tenant_other", current_user=user, db=db)
    assert excinfo.value.status_code in {403, 404}


def test_missing_view_is_not_found(db: Session) -> None:
    user = db.get(User, "user_1")
    with pytest.raises(HTTPException) as excinfo:
        read_dashboard_view("dashview_missing", tenant_id="tenant_demo", current_user=user, db=db)
    assert excinfo.value.status_code == 404


def test_expired_view_returns_410(db: Session) -> None:
    view = _view(db, ttl=timedelta(seconds=-5))
    user = db.get(User, "user_1")
    with pytest.raises(HTTPException) as excinfo:
        read_dashboard_view(view.id, tenant_id="tenant_demo", current_user=user, db=db)
    assert excinfo.value.status_code == 410


def test_trace_line_carries_only_view_reference(db: Session) -> None:
    event = AgentEvent(
        id="evt_1",
        tenant_id="tenant_demo",
        session_id="session_1",
        event_type="harness_dashboard_view",
        payload_json={
            "view_id": "dashview_abc",
            "title": "经营看板",
            "kpi_count": 2,
            "chart_count": 1,
            "tool_name": "render_dashboard",
        },
    )
    line = _harness_event_trace_line(event)
    assert line is not None
    assert line["dashboard"]["view_id"] == "dashview_abc"
    assert line["dashboard"]["chart_count"] == 1
    assert "2 个指标" in line["detail"]
    assert "rows" not in str(line)


def test_trace_line_without_view_id_is_skipped() -> None:
    event = AgentEvent(
        id="evt_2",
        tenant_id="tenant_demo",
        session_id="session_1",
        event_type="harness_dashboard_view",
        payload_json={"title": "x"},
    )
    assert _harness_event_trace_line(event) is None


def test_stored_v1_spec_is_returned_without_revalidation(db: Session) -> None:
    from app.db.models import DashboardView, utc_now

    legacy = _spec().model_dump(mode="json")
    # a phase-1 row the v2 model would refuse: proves the API never re-validates
    legacy["charts"][0]["legacy_hint"] = "kept verbatim"
    now = utc_now()
    db.add(
        DashboardView(
            id="dashview_legacy",
            tenant_id="tenant_demo",
            session_id="session_1",
            user_id="user_1",
            title="经营看板",
            spec_version=1,
            spec_json=legacy,
            spec_hash="legacy",
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
    )
    db.commit()
    user = db.get(User, "user_1")
    payload = read_dashboard_view("dashview_legacy", tenant_id="tenant_demo", current_user=user, db=db)
    assert payload["spec"] == legacy
    assert payload["spec"]["spec_version"] == 1
    assert "layout" not in payload["spec"]


def test_view_endpoint_returns_a_share_url_from_the_request_host(db: Session) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.chat import router as chat_router
    from app.db import get_session
    from app.security.auth import get_current_user

    view = _view(db)
    user = db.get(User, "user_1")
    app = FastAPI()
    app.include_router(chat_router)
    app.dependency_overrides[get_current_user] = lambda: user

    def override_session():
        yield db

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        response = client.get(f"/api/chat/dashboard-views/{view.id}?tenant_id=tenant_demo")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == view.id
    assert body["share_scope"] == "local"
    assert body["share_url"].startswith(f"http://testserver/s/{view.id}.")
    assert body["spec"]["spec_version"] == 1

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.public_dashboard import public_router
from app.dashboard.chart_policy import normalize_spec
from app.dashboard.service import create_view
from app.dashboard.share import share_token
from app.dashboard.spec import RenderInput
from app.db import get_session
from app.db.models import ChatSession, DashboardView, Tenant


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
                "title": "分享看板",
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
def client_and_db():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(ChatSession(id="session_1", tenant_id="tenant_demo", user_id="user_1"))
        db.commit()
        app = FastAPI()
        app.include_router(public_router)

        def override_session():
            yield db

        app.dependency_overrides[get_session] = override_session
        with TestClient(app) as client:
            yield client, db


def _view(db: Session, ttl: timedelta = timedelta(days=1)) -> DashboardView:
    view, _ = create_view(
        db,
        tenant_id="tenant_demo",
        session_id="session_1",
        spec=_spec(),
        user_id="user_1",
        ttl=ttl,
    )
    return view


def test_public_endpoint_returns_only_title_spec_and_expiry(client_and_db) -> None:
    client, db = client_and_db
    view = _view(db)
    response = client.get(f"/api/public/dashboards/{share_token(view.id)}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "分享看板"
    assert body["spec"]["title"] == "分享看板"
    assert set(body) == {"title", "spec", "expires_at"}
    assert "tenant" not in str(body).lower()
    assert "session_1" not in str(body)


def test_public_headers_are_set_on_success_and_errors(client_and_db) -> None:
    client, db = client_and_db
    view = _view(db)
    for response in (
        client.get(f"/api/public/dashboards/{share_token(view.id)}"),
        client.get("/api/public/dashboards/bad-token"),
    ):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "noindex" in response.headers["x-robots-tag"]


def test_bad_token_is_404_and_does_not_leak(client_and_db) -> None:
    client, _ = client_and_db
    response = client.get("/api/public/dashboards/not-a-token")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_missing_view_is_404(client_and_db) -> None:
    client, _ = client_and_db
    response = client.get(f"/api/public/dashboards/{share_token('dashview_missing')}")
    assert response.status_code == 404


def test_expired_view_is_410_without_title(client_and_db) -> None:
    client, db = client_and_db
    view = _view(db, ttl=timedelta(seconds=-5))
    response = client.get(f"/api/public/dashboards/{share_token(view.id)}")
    assert response.status_code == 410
    assert response.json() == {"detail": "Gone"}
    assert "分享看板" not in response.text

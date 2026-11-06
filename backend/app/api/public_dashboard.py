"""Public read-only dashboard endpoint (plan §3.7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.dashboard.service import is_expired
from app.dashboard.share import parse_share_token
from app.db import get_session
from app.db.models import DashboardView

public_router = APIRouter(prefix="/api/public", tags=["public-dashboard"])

_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code, headers=_HEADERS)


@public_router.get("/dashboards/{token}")
def read_public_dashboard(
    token: str,
    db: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    """Render one shared board without a login; the token is the capability."""

    view_id = parse_share_token(token)
    if not view_id:
        return _error(404, "Not found")
    view = db.get(DashboardView, view_id)
    if view is None:
        return _error(404, "Not found")
    if is_expired(view):
        return _error(410, "Gone")
    return JSONResponse(
        {
            "title": view.title,
            "spec": dict(view.spec_json or {}),
            "expires_at": view.expires_at.isoformat(),
        },
        headers=_HEADERS,
    )

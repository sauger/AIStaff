"""Persistence for rendered dashboards.

A view is an immutable snapshot of one render: the spec is materialised, so
reading it back needs no further query.  Views are session-scoped, deduplicated
by content hash and expire after ``DEFAULT_VIEW_TTL``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from app.dashboard.spec import RenderSpec
from app.db.models import DashboardView, new_id, utc_now

DEFAULT_VIEW_TTL = timedelta(days=7)
SWEEP_BATCH = 100


def spec_digest(spec: RenderSpec) -> str:
    payload = spec.model_dump(mode="json")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sweep_expired(db: Session, tenant_id: str, *, limit: int = SWEEP_BATCH) -> int:
    """Delete expired views for one tenant.  Best effort, never raises."""

    now = utc_now()
    rows = db.exec(
        select(DashboardView)
        .where(DashboardView.tenant_id == tenant_id, DashboardView.expires_at < now)
        .limit(limit)
    ).all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


def find_reusable(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    digest: str,
) -> DashboardView | None:
    now = utc_now()
    return db.exec(
        select(DashboardView)
        .where(
            DashboardView.tenant_id == tenant_id,
            DashboardView.session_id == session_id,
            DashboardView.spec_hash == digest,
            DashboardView.expires_at > now,
        )
        .order_by(col(DashboardView.created_at).desc())
    ).first()


def create_view(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    spec: RenderSpec,
    agent_id: str | None = None,
    user_id: str | None = None,
    ttl: timedelta = DEFAULT_VIEW_TTL,
) -> tuple[DashboardView, bool]:
    """Create (or reuse) a view.  Returns ``(view, reused)``."""

    digest = spec_digest(spec)
    existing = find_reusable(db, tenant_id=tenant_id, session_id=session_id, digest=digest)
    if existing is not None:
        return existing, True

    sweep_expired(db, tenant_id)
    now = utc_now()
    view = DashboardView(
        id=new_id("dashview"),
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        title=spec.title,
        spec_version=spec.spec_version,
        spec_json=spec.model_dump(mode="json"),
        spec_hash=digest,
        created_at=now,
        expires_at=now + ttl,
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    return view, False


def get_view(db: Session, *, tenant_id: str, view_id: str) -> DashboardView | None:
    row = db.get(DashboardView, view_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


def is_expired(view: DashboardView, *, now: datetime | None = None) -> bool:
    """SQLite hands datetimes back naive; normalise before comparing."""

    moment = now or utc_now()
    expires = view.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return expires < moment


def read_spec(view: DashboardView) -> dict[str, Any]:
    payload = view.spec_json if isinstance(view.spec_json, dict) else {}
    return payload

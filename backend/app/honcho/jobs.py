"""Async Honcho dual-write. Failures stay in the job and never fail the turn."""

from __future__ import annotations

import logging
from typing import Any

from app.async_jobs import AsyncJob, enqueue_async_job
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def enqueue_honcho_dual_write(payload: dict[str, Any]) -> AsyncJob:
    return enqueue_async_job(
        "honcho.dual_write",
        run_honcho_dual_write_job,
        payload,
        metadata={
            "tenant_id": payload.get("tenant_id"),
            "session_id": payload.get("session_id"),
            "user_id": payload.get("user_id"),
        },
    )


def run_honcho_dual_write_job(
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    client: Any | None = None,
    db: Any | None = None,
) -> None:
    # Callers that pass settings without a session are tests and in-process
    # overrides. Production jobs omit both and read the saved tenant row.
    explicit_settings = settings is not None
    settings = settings or get_settings()
    if db is not None or not explicit_settings:
        settings = _settings_for_job(payload, settings, db)
    if not settings.honcho_enabled or payload.get("channel_is_group"):
        return
    if client is None and not settings.honcho_base_url.strip():
        logger.warning("honcho dual-write skipped because HONCHO_BASE_URL is empty")
        return
    try:
        from app.honcho.service import dual_write_message

        dual_write_message(payload, settings=settings, client=client)
    except Exception:
        logger.warning("honcho dual-write failed", exc_info=True)


def _settings_for_job(payload: dict[str, Any], settings: Settings, db: Any | None) -> Settings:
    from app.db.models import UIConfig
    from app.honcho.runtime import apply_honcho_ui_config

    tenant_id = str(payload.get("tenant_id") or "").strip()
    try:
        if db is not None:
            row = db.get(UIConfig, tenant_id) if tenant_id else None
        elif not tenant_id:
            return apply_honcho_ui_config(settings, None)
        else:
            from sqlmodel import Session

            from app.db.database import engine

            with Session(engine) as session:
                row = session.get(UIConfig, tenant_id)
        return apply_honcho_ui_config(settings, row if isinstance(row, UIConfig) else None)
    except Exception:
        logger.warning("honcho runtime settings lookup failed", exc_info=True)
        return settings.model_copy(update={"honcho_enabled": False})

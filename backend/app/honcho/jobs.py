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
) -> None:
    settings = settings or get_settings()
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

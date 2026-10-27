from __future__ import annotations

import secrets
import time
from typing import Any

from app.config import get_settings

ARTIFACT_LINK_TTL_SECONDS = 24 * 60 * 60


def new_artifact_share_token() -> str:
    return secrets.token_urlsafe(24)


def artifact_link_expires_at() -> int:
    return int(time.time()) + ARTIFACT_LINK_TTL_SECONDS


def build_channel_artifact_url(delivery_id: str, artifact: dict[str, Any]) -> str | None:
    """Return an absolute bearer download URL for a channel file delivery."""

    token = str(artifact.get("share_token") or "").strip()
    if not token:
        return None
    base = get_settings().normalized_public_base_url
    if not base:
        return None
    return f"{base}/api/channel-artifacts/{delivery_id}?token={token}"


def artifact_link_is_expired(artifact: dict[str, Any]) -> bool:
    try:
        expires_at = int(artifact.get("share_expires_at") or 0)
    except (TypeError, ValueError):
        return True
    return expires_at <= int(time.time())

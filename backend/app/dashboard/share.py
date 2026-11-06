"""Read-only share links for stored dashboards (plan §3.7).

The token is derived from the view id, so no row and no migration is needed:
``<view_id>.<base64url(HMAC-SHA256(APP_SECRET, "dashboard-view:" + view_id))>``.
The link lives exactly as long as the view (7 days by default) and cannot be
revoked individually — the plan accepts that trade-off.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from fastapi import Request

from app.config import get_settings

logger = logging.getLogger(__name__)
DEFAULT_APP_SECRET = "change-me-in-development"
_MSG = "dashboard-view:"

_warned_default_secret = False


def _mac(view_id: str) -> str:
    digest = hmac.new(
        get_settings().app_secret.encode("utf-8"),
        f"{_MSG}{view_id}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def share_token(view_id: str) -> str:
    """Stable, opaque token for one dashboard view."""

    return f"{view_id}.{_mac(view_id)}"


def parse_share_token(token: str) -> str | None:
    """Return the view id only when the signature is intact."""

    value = (token or "").strip()
    if not value or value.count(".") != 1:
        return None
    view_id, mac = value.rsplit(".", 1)
    if not view_id or not mac:
        return None
    if not hmac.compare_digest(mac, _mac(view_id)):
        return None
    return view_id


def build_share_url(view_id: str, request: Request | None = None) -> tuple[str | None, str]:
    """Absolute URL plus a ``public``/``local`` scope for the UI hint."""

    token = share_token(view_id)
    base = get_settings().normalized_public_base_url
    scope = "local"
    if base:
        scope = "public"
    elif request is not None:
        base = str(request.base_url).rstrip("/")
    if not base:
        return None, scope
    return f"{base}/s/{token}", scope


def warn_default_app_secret() -> None:
    """One startup warning; never blocks a local demo."""

    global _warned_default_secret
    if _warned_default_secret:
        return
    _warned_default_secret = True
    if get_settings().app_secret == DEFAULT_APP_SECRET:
        logger.warning(
            "APP_SECRET is still the development default; dashboard share links are forgeable. "
            "Set a strong APP_SECRET before exposing the service."
        )

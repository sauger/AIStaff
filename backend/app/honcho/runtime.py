"""Resolve the Honcho settings a turn or job should actually use.

Precedence for ``honcho_enabled``, ``honcho_base_url``, and ``honcho_timeout_ms``:

1. ``ui_configs`` when that column is not NULL (saved from 运行设置).
2. Process ``Settings``, normally the ``HONCHO_*`` environment, until an admin saves.

``HONCHO_API_KEY`` and ``HONCHO_WORKSPACE_PREFIX`` stay on the process settings.
The runtime-settings page does not store secrets, and the prefix is the Honcho
identity namespace. Timeouts are clamped to the same 50–10000 ms range the
settings form accepts, so an out-of-range env value can still be saved.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.config import Settings
from app.db.models import UIConfig

HONCHO_TIMEOUT_MIN_MS = 50
HONCHO_TIMEOUT_MAX_MS = 10_000


def apply_honcho_ui_config(settings: Settings, row: UIConfig | None) -> Settings:
    updates: dict[str, Any] = {}
    if row is not None:
        if row.honcho_enabled is not None:
            updates["honcho_enabled"] = bool(row.honcho_enabled)
        if row.honcho_base_url is not None:
            updates["honcho_base_url"] = str(row.honcho_base_url).strip()
        if row.honcho_timeout_ms is not None:
            updates["honcho_timeout_ms"] = row.honcho_timeout_ms
    timeout_source = updates.get("honcho_timeout_ms", settings.honcho_timeout_ms)
    bounded_timeout = _bounded_timeout_ms(timeout_source)
    if bounded_timeout != settings.honcho_timeout_ms or "honcho_timeout_ms" in updates:
        updates["honcho_timeout_ms"] = bounded_timeout
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def display_honcho_base_url(url: str) -> str:
    """Return a base URL safe to show to any tenant member.

    Env bootstrap values are not passed through the write validator, so a
    credential in ``HONCHO_BASE_URL`` must not be copied into the settings API.
    """

    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if not parsed.username and not parsed.password:
        return raw.rstrip("/")
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host:
        return ""
    netloc = f"[{host}]" if ":" in host else host
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"


def _bounded_timeout_ms(value: object) -> int:
    timeout = int(value)  # type: ignore[arg-type]
    return min(HONCHO_TIMEOUT_MAX_MS, max(HONCHO_TIMEOUT_MIN_MS, timeout))

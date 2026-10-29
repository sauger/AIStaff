"""Honcho sidecar gates, representation inject, and dual-write."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.config import Settings, get_settings
from app.db.models import AgentProfile, UIConfig
from app.honcho.client import HonchoClient
from app.honcho.ids import HonchoIdError, build_honcho_identity
from app.honcho.runtime import apply_honcho_ui_config
from app.session.session_schema import ChatTurnRequest

logger = logging.getLogger(__name__)

REPRESENTATION_LABEL = "长期用户画像"


def build_honcho_client(settings: Settings) -> HonchoClient:
    from app.honcho.client import WRITE_TIMEOUT_SECONDS

    return HonchoClient(
        base_url=settings.honcho_base_url.strip(),
        api_key=settings.honcho_api_key.strip(),
        timeout_seconds=WRITE_TIMEOUT_SECONDS,
    )


def schedule_honcho_message(
    db: Any,
    request: ChatTurnRequest,
    session: Any,
    *,
    role: str,
    content: str,
    message_id: str | None = None,
    settings: Settings | None = None,
    enqueue: Callable[[dict[str, Any]], Any] | None = None,
) -> None:
    try:
        settings = _effective_settings(db, request.tenant_id, settings or get_settings())
        if request.message_visibility != "visible":
            return
        user_id = str(request.user_id or "").strip()
        agent_id = str(getattr(session, "agent_id", "") or "").strip()
        session_id = str(getattr(session, "id", "") or "").strip()
        if not user_id or not agent_id or not session_id:
            return
        metadata = _agent_metadata(db, request.tenant_id, agent_id)
        if not _turn_enabled(settings, metadata, channel_is_group=request.channel_is_group):
            return
        text = content.strip()
        if not text or role not in {"user", "assistant"}:
            return
        if not settings.honcho_base_url.strip():
            logger.warning("honcho schedule skipped because HONCHO_BASE_URL is empty")
            return
        payload = {
            "tenant_id": request.tenant_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "role": role,
            "content": text,
            "message_id": message_id,
            "channel": request.channel,
            "account_scope": request.channel_external_account_scope,
            "external_user_id": request.channel_external_user_id,
            "channel_is_group": False,
        }
        if enqueue is None:
            from app.honcho.jobs import enqueue_honcho_dual_write

            enqueue = enqueue_honcho_dual_write
        enqueue(payload)
    except Exception:
        logger.warning("honcho schedule failed", exc_info=True)


def inject_honcho_representation(
    db: Any,
    request: ChatTurnRequest,
    session: Any,
    memory_context: list[dict[str, object]] | None,
    *,
    settings: Settings | None = None,
    client: HonchoClient | None = None,
) -> list[dict[str, object]]:
    memories = list(memory_context or [])
    try:
        settings = _effective_settings(db, request.tenant_id, settings or get_settings())
        user_id = str(request.user_id or "").strip()
        agent_id = str(getattr(session, "agent_id", "") or "").strip()
        session_id = str(getattr(session, "id", "") or "").strip()
        if not user_id or not agent_id or not session_id:
            return memories
        metadata = _agent_metadata(db, request.tenant_id, agent_id)
        if not _turn_enabled(settings, metadata, channel_is_group=request.channel_is_group):
            return memories
        if not settings.honcho_base_url.strip():
            if client is None:
                logger.warning("honcho representation skipped because HONCHO_BASE_URL is empty")
            return memories
        identity = build_honcho_identity(
            tenant_id=request.tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_prefix=settings.honcho_workspace_prefix,
            channel=request.channel,
            account_scope=request.channel_external_account_scope,
            external_user_id=request.channel_external_user_id,
        )
        honcho = client or build_honcho_client(settings)
        text = honcho.fetch_representation(
            identity.workspace_id,
            identity.user_peer_id,
            timeout_seconds=max(settings.honcho_timeout_ms, 1) / 1000,
        )
    except Exception:
        logger.warning("honcho representation skipped", exc_info=True)
        return memories
    cleaned = str(text or "").strip()
    if not cleaned:
        return memories
    return [*memories, representation_memory(cleaned)]


def dual_write_message(
    payload: dict[str, Any],
    *,
    settings: Settings,
    client: HonchoClient | None = None,
) -> None:
    if not settings.honcho_enabled or payload.get("channel_is_group"):
        return
    content = str(payload.get("content") or "").strip()
    role = str(payload.get("role") or "")
    if role not in {"user", "assistant"} or not content:
        return
    try:
        identity = build_honcho_identity(
            tenant_id=str(payload.get("tenant_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            agent_id=str(payload.get("agent_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            workspace_prefix=settings.honcho_workspace_prefix,
            channel=_optional_str(payload.get("channel")),
            account_scope=_optional_str(payload.get("account_scope")),
            external_user_id=_optional_str(payload.get("external_user_id")),
        )
    except HonchoIdError:
        logger.warning("honcho dual-write skipped because the identity is not a stable peer")
        return
    honcho = client or build_honcho_client(settings)
    honcho.ensure_workspace(
        identity.workspace_id,
        {
            "staff_workspace": identity.workspace_logical,
            "tenant_id": str(payload.get("tenant_id") or ""),
        },
    )
    # Peer get-or-create replaces metadata wholesale. Send only the stable
    # staff_peer so a later channel does not wipe another channel's fields.
    # Channel external ids stay on the message.
    honcho.ensure_peer(
        identity.workspace_id,
        identity.user_peer_id,
        {"staff_peer": identity.user_peer_logical},
    )
    honcho.ensure_peer(
        identity.workspace_id,
        identity.agent_peer_id,
        {"staff_peer": identity.agent_peer_logical},
    )
    honcho.ensure_session(
        identity.workspace_id,
        identity.session_id,
        [identity.user_peer_id, identity.agent_peer_id],
        identity.session_metadata,
    )
    peer_id = identity.user_peer_id if role == "user" else identity.agent_peer_id
    message_metadata = {"role": role}
    message_id = str(payload.get("message_id") or "").strip()
    if message_id:
        message_metadata["staff_message_id"] = message_id
    for key in ("channel", "account_scope", "external_user_id"):
        if key in identity.session_metadata:
            message_metadata[key] = identity.session_metadata[key]
    honcho.add_message(
        identity.workspace_id,
        identity.session_id,
        peer_id=peer_id,
        content=content,
        metadata=message_metadata,
    )


def representation_memory(text: str) -> dict[str, object]:
    body = text.strip()
    content = body if body.startswith(REPRESENTATION_LABEL) else f"{REPRESENTATION_LABEL}\n{body}"
    return {
        "kind": "honcho_representation",
        "content": content,
        "metadata": {"source": "honcho", "label": REPRESENTATION_LABEL},
    }


def _effective_settings(db: Any, tenant_id: str, settings: Settings) -> Settings:
    try:
        row = db.get(UIConfig, tenant_id) if tenant_id else None
        return apply_honcho_ui_config(settings, row if isinstance(row, UIConfig) else None)
    except Exception:
        # A bad saved value must not fail the turn or fall open into a call.
        logger.warning("honcho runtime settings lookup failed", exc_info=True)
        return settings.model_copy(update={"honcho_enabled": False})


def _turn_enabled(
    settings: Settings,
    metadata: dict[str, Any],
    *,
    channel_is_group: bool,
) -> bool:
    return (
        bool(settings.honcho_enabled)
        and metadata.get("honcho_enabled") is True
        and not channel_is_group
    )


def _agent_metadata(db: Any, tenant_id: str, agent_id: str) -> dict[str, Any]:
    row = db.get(AgentProfile, agent_id)
    if row is None or row.tenant_id != tenant_id:
        return {}
    metadata = row.metadata_json or {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None

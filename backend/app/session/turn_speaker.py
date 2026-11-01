"""Resolve who is speaking, then hang that User.id on the turn context.

Web dialogs use the logged-in user. WeCom bot private chats use the
ChannelIdentity mapping (``resolve_or_provision_user``). Group chats stay
unresolved: the session may still belong to a group bucket, but that bucket
is not a person and must not become a Honcho peer.
"""

from __future__ import annotations

from app.session.session_schema import ChatTurnRequest, TurnSpeaker

_SPEAKER_KEYS = ("status", "user_id", "channel", "source", "reason")


def stable_portrait_user_id(user_id: str | None, external_user_id: str | None = None) -> str:
    """Return a StaffDeck User.id, or empty when the only candidate is a channel id."""

    resolved = str(user_id or "").strip()
    external = str(external_user_id or "").strip()
    if not resolved or (external and resolved == external):
        return ""
    return resolved


def resolve_web_speaker(user: object, *, channel: str = "web") -> TurnSpeaker:
    dialog_channel = str(channel or "web").strip() or "web"
    user_id = str(getattr(user, "id", "") or "").strip()
    if not user_id:
        return TurnSpeaker(
            status="unresolved",
            channel=dialog_channel,
            source="unresolved",
            reason="missing_login",
        )
    return TurnSpeaker(
        status="resolved",
        user_id=user_id,
        channel=dialog_channel,
        source="web_login",
    )


def resolve_wecom_bot_speaker(
    *,
    user_id: str | None,
    is_group: bool,
    external_user_id: str | None,
) -> TurnSpeaker:
    if is_group:
        return TurnSpeaker(
            status="unresolved",
            channel="wecom",
            source="unresolved",
            reason="group_chat",
        )
    external = str(external_user_id or "").strip()
    raw_user_id = str(user_id or "").strip()
    if not external or not raw_user_id:
        return TurnSpeaker(
            status="unresolved",
            channel="wecom",
            source="unresolved",
            reason="missing_channel_user",
        )
    if raw_user_id == external:
        return TurnSpeaker(
            status="unresolved",
            channel="wecom",
            source="unresolved",
            reason="external_id_is_not_user",
        )
    return TurnSpeaker(
        status="resolved",
        user_id=raw_user_id,
        channel="wecom",
        source="channel_identity",
    )


def portrait_user_id(request: ChatTurnRequest) -> str:
    """Honcho peer user. A present speaker wins; an external id never does."""

    external = request.channel_external_user_id
    speaker = request.speaker
    if speaker is not None:
        if speaker.status != "resolved":
            return ""
        return stable_portrait_user_id(speaker.user_id, external)
    return stable_portrait_user_id(request.user_id, external)


def speaker_context_payload(speaker: TurnSpeaker | None) -> dict[str, str]:
    if speaker is None:
        return {}
    payload: dict[str, str] = {
        "status": speaker.status,
        "channel": speaker.channel,
        "source": speaker.source,
    }
    if speaker.status == "resolved":
        user_id = str(speaker.user_id or "").strip()
        if user_id:
            payload["user_id"] = user_id
    if speaker.reason:
        payload["reason"] = speaker.reason
    return payload


def speaker_message_metadata(speaker: TurnSpeaker | None) -> dict[str, str]:
    payload = speaker_context_payload(speaker)
    if not payload:
        return {}
    metadata = {
        "speaker_status": payload["status"],
        "speaker_source": payload["source"],
    }
    if payload.get("user_id"):
        metadata["speaker_user_id"] = payload["user_id"]
    if payload.get("reason"):
        metadata["speaker_reason"] = payload["reason"]
    return metadata


def stamp_conversation_speaker(
    context: dict[str, object],
    speaker: TurnSpeaker | None,
) -> dict[str, object]:
    payload = speaker_context_payload(speaker)
    if not payload:
        return context
    metadata = context.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        context["metadata"] = metadata
    metadata["speaker"] = payload
    return context


def current_speaker_for_requirement(speaker: TurnSpeaker | None) -> dict[str, str] | None:
    payload = speaker_context_payload(speaker)
    if not payload:
        return None
    cleaned = {
        key: payload[key]
        for key in _SPEAKER_KEYS
        if payload.get(key)
    }
    if cleaned.get("status") != "resolved":
        cleaned.pop("user_id", None)
    return cleaned or None

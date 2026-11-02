"""Resolve who is speaking, then hang that User.id on the turn context.

Web dialogs use the logged-in user. WeCom bot private chats use the
ChannelIdentity mapping (``resolve_or_provision_user``). Group chats stay
unresolved: the session may still belong to a group bucket, but that bucket
is not a person and must not become a Honcho peer.

Enterprise roster names are not part of identity resolution.
``attach_speaker_roster`` prefers ``contacts.display_name`` when this turn
already has a contact, then fills department from the linked enterprise
person. Sessions without a contact still fall back to EnterprisePerson by
User.id.
"""

from __future__ import annotations

from sqlmodel import Session

from app.org.speaker_roster import lookup_speaker_roster
from app.session.session_schema import ChatTurnRequest, TurnSpeaker
from app.text import single_line

_SPEAKER_KEYS = (
    "status",
    "user_id",
    "contact_id",
    "channel",
    "source",
    "reason",
    "roster_status",
    "name",
    "department",
    "preferred_name",
)
_ROSTER_KEYS = ("user_id", "roster_status", "name", "department")
_UNRESOLVED_HIDDEN_KEYS = (*_ROSTER_KEYS, "preferred_name")


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


def resolve_known_user_speaker(user_id: str | None, *, channel: str) -> TurnSpeaker:
    """Stamp a User.id the server already chose. Not a channel external id."""

    dialog_channel = str(channel or "").strip() or "internal"
    resolved = str(user_id or "").strip()
    if not resolved:
        return TurnSpeaker(
            status="unresolved",
            channel=dialog_channel,
            source="unresolved",
            reason="missing_session_user",
        )
    return TurnSpeaker(
        status="resolved",
        user_id=resolved,
        channel=dialog_channel,
        source="session_user",
    )


def portrait_contact_id(request: ChatTurnRequest, session: object | None = None) -> str:
    """Contact that should own this turn's memory and Honcho user peer.

    A resolved speaker contact wins. Otherwise use the session contact, except
    on inbound mail: that session contact is the sender, not the mailbox owner.
    Group chats and unresolved speakers do not get a contact peer.
    """

    if request.channel == "wecom" and request.speaker is None:
        return ""
    speaker = request.speaker
    if speaker is not None:
        if speaker.status != "resolved":
            return ""
        contact_id = str(speaker.contact_id or "").strip()
        if contact_id:
            return contact_id
        if request.channel == "mail_inbound":
            return ""
    elif request.channel == "mail_inbound":
        return ""
    if session is None or getattr(session, "channel", None) == "mail_inbound":
        return ""
    speaker_user = ""
    if speaker is not None and speaker.status == "resolved":
        speaker_user = str(speaker.user_id or "").strip()
    if not speaker_user:
        speaker_user = str(request.user_id or "").strip()
    session_user = str(getattr(session, "user_id", "") or "").strip()
    if not speaker_user or speaker_user != session_user:
        return ""
    return str(getattr(session, "contact_id", "") or "").strip()


def portrait_user_id(request: ChatTurnRequest) -> str:
    """Honcho peer user. A present speaker wins; an external id never does.

    WeCom without ``speaker`` fails closed. ``speaker`` is exclude=True, so
    ``ChatTurnRequest(**request.model_dump())`` drops it and must not fall
    back to a raw or group-bucket id.

    TODO: 飞书/钉钉入站还没有 stamp speaker，缺省时仍只靠 user_id 与 external
    id 不相等。那两个入口不在这一层，不要把相等检查当成它们的身份层。
    """

    external = request.channel_external_user_id
    speaker = request.speaker
    if request.channel == "wecom" and speaker is None:
        return ""
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
    if speaker.reason:
        payload["reason"] = speaker.reason
    # 群聊和其他未解析说话人不能带花名册，即使调用方误填了姓名。
    if speaker.status != "resolved":
        return payload
    user_id = str(speaker.user_id or "").strip()
    if user_id:
        payload["user_id"] = user_id
    contact_id = str(speaker.contact_id or "").strip()
    if contact_id:
        payload["contact_id"] = contact_id
    name = single_line(speaker.roster_name)
    department = single_line(speaker.roster_department)
    if speaker.roster_status == "matched" and name:
        payload["roster_status"] = "matched"
        payload["name"] = name
        if department:
            payload["department"] = department
    elif speaker.roster_status in {"matched", "unmatched"}:
        payload["roster_status"] = "unmatched"
    preferred_name = single_line(speaker.preferred_name)
    if preferred_name:
        payload["preferred_name"] = preferred_name
    return payload


def attach_speaker_roster(db: Session, request: ChatTurnRequest) -> ChatTurnRequest:
    """已解析说话人时，把花名册姓名挂到本轮 speaker。

    只查 speaker.user_id。群聊的 request.user_id 可能是群桶账号，不能拿来查。
    """

    speaker = request.speaker
    if speaker is None or speaker.status != "resolved":
        return request
    user_id = str(speaker.user_id or "").strip()
    if not user_id and not str(speaker.contact_id or "").strip():
        return request
    name, department = _speaker_name_and_department(
        db,
        tenant_id=request.tenant_id,
        user_id=user_id,
        contact_id=str(speaker.contact_id or "").strip(),
    )
    if not name:
        return request.model_copy(
            update={
                "speaker": speaker.model_copy(
                    update={
                        "roster_status": "unmatched",
                        "roster_name": None,
                        "roster_department": None,
                    }
                )
            }
        )
    return request.model_copy(
        update={
            "speaker": speaker.model_copy(
                update={
                    "roster_status": "matched",
                    "roster_name": name,
                    "roster_department": department or None,
                }
            )
        }
    )


def attach_speaker_preferred_name(
    request: ChatTurnRequest,
    memory_context: list[dict[str, object]] | None,
) -> ChatTurnRequest:
    """已解析说话人有 preferred_name 记忆时，挂上日常称呼。

    取值规则与槽位灌入 ``user_name`` 相同：只认 profile/preference/fact 上的
    ``preferred_name``，同一键保留最新一条。不改花名册或联系人展示名。
    """

    speaker = request.speaker
    if speaker is None or speaker.status != "resolved":
        return request
    from app.core.slot_hydration_policy import SlotHydrationPolicy

    remembered = SlotHydrationPolicy.memory_values_by_key(list(memory_context or [])).get(
        "preferred_name"
    )
    preferred_name = single_line(remembered)
    if not preferred_name:
        return request
    return request.model_copy(
        update={"speaker": speaker.model_copy(update={"preferred_name": preferred_name})}
    )


def apply_preferred_name(
    request: ChatTurnRequest,
    execution_request: ChatTurnRequest,
    memory_context: list[dict[str, object]] | None,
) -> tuple[ChatTurnRequest, ChatTurnRequest]:
    """把日常称呼挂到本轮请求，并同步到执行快照。

    执行快照在召回记忆之前就复制过。只改原请求的 speaker，TaskRequirement
    仍只看花名册 name。
    """

    updated = attach_speaker_preferred_name(request, memory_context)
    if updated.speaker is execution_request.speaker:
        return updated, execution_request
    return updated, execution_request.model_copy(update={"speaker": updated.speaker})


def _speaker_name_and_department(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    contact_id: str,
) -> tuple[str, str]:
    """联系人展示名优先。花名册姓名在没有联系人名、或联系人名仍是占位时补上。"""

    from app.contacts.identity import is_placeholder_display_name
    from app.db.models import PersonContact
    from app.org.speaker_roster import department_for_person

    contact = db.get(PersonContact, contact_id) if contact_id else None
    if contact is not None and (
        contact.tenant_id != tenant_id or contact.merged_into_id or contact.status != "active"
    ):
        contact = None
    if contact is None and user_id:
        contact = _contact_for_user(db, tenant_id, user_id)
    roster_name = ""
    roster_department = ""
    if user_id:
        roster = lookup_speaker_roster(db, tenant_id=tenant_id, user_id=user_id)
        if roster.status == "matched":
            roster_name = roster.name
            roster_department = roster.department
    contact_name = single_line(contact.display_name) if contact is not None else ""
    department = ""
    if contact is not None and contact.enterprise_person_id:
        department = department_for_person(db, tenant_id, contact.enterprise_person_id)
    if not department:
        department = roster_department
    if contact_name and not is_placeholder_display_name(contact_name):
        return contact_name, department
    if roster_name:
        return roster_name, department
    if contact_name:
        return contact_name, department
    return "", ""


def _contact_for_user(db: Session, tenant_id: str, user_id: str):
    from sqlalchemy import or_
    from sqlmodel import select

    from app.db.models import PersonContact

    matches = list(
        db.exec(
            select(PersonContact).where(
                PersonContact.tenant_id == tenant_id,
                PersonContact.merged_into_id.is_(None),
                PersonContact.status == "active",
                or_(
                    PersonContact.web_user_id == user_id,
                    PersonContact.channel_user_id == user_id,
                ),
            )
        ).all()
    )
    orgs = [row for row in matches if row.kind == "org"]
    if len(orgs) == 1:
        return orgs[0]
    return matches[0] if len(matches) == 1 else None


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
        for key in _UNRESOLVED_HIDDEN_KEYS:
            cleaned.pop(key, None)
    return cleaned or None

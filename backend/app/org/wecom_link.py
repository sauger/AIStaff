"""把企业人员的企微 userid 连到已有的会话账号。

花名册只认 ``EnterprisePerson.primary_user_id ==`` 说话人 ``User.id``
（见 speaker_roster）。这里不在读取时用 userid 猜姓名，也不改
``ChannelIdentity.staffdeck_user_id``。绑定码仍然是把渠道身份改挂到
网页登录账号的路径。

写入规则：
- 主账号为空，或已经就是该说话人：才把 ``primary_user_id`` 设为说话人。
  影子账号可以。已经绑过网页账号、且 ChannelIdentity 也指向该网页账号时，保持不动。
- 已经有另一个主账号（例如网页 admin）：只记下 userid，不改挂，并返回提示。
  操作者要让私聊认人，需先清空主账号再保存。
- 清除 userid 时，已有的主账号保留，避免花名册掉线。
- 还没有 ChannelIdentity：只记下 userid。之后对方第一次私聊时，
  若人员还没有主账号，再补上影子用户。
- 同一 userid 在不同企业/应用范围指向不同用户：拒绝，不猜。
- 不改 ``ChannelIdentity.staffdeck_user_id``。绑定码仍负责改挂渠道身份。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import ChannelIdentity, EnterprisePerson, User, utc_now
from app.org.errors import OrgError
from app.text import single_line

logger = logging.getLogger(__name__)

_WECOM_USERID_LIMIT = 128
_PLACEHOLDER_PREFIXES = ("企业微信用户 ", "企业微信群聊 ")
KEEP_WEB_PRIMARY_NOTICE = (
    "未改挂花名册主账号，因已有网页账号关联。"
    "清空关联登录账号后再保存，才会改挂到企微会话账号。"
)


@dataclass(frozen=True)
class WecomPersonLink:
    wecom_userid: str | None = None
    # 本租户里这个 userid 唯一对应的说话人。没有身份、或范围冲突时为空。
    speaker_user_id: str | None = None


def resolve_wecom_person_link(
    db: Session,
    *,
    tenant_id: str,
    wecom_userid: str | None,
    exclude_person_id: str | None = None,
) -> WecomPersonLink:
    userid = _normalize_wecom_userid(wecom_userid)
    if userid is None:
        return WecomPersonLink()
    _require_userid_free(db, tenant_id, userid, exclude_person_id)
    speaker = _find_speaker_user(db, tenant_id, userid)
    if speaker is None:
        return WecomPersonLink(wecom_userid=userid)
    return WecomPersonLink(wecom_userid=userid, speaker_user_id=speaker.id)


def choose_roster_primary(
    *,
    existing_primary_user_id: str | None,
    requested_primary_user_id: str | None,
    existing_wecom_userid: str | None,
    speaker_user_id: str | None,
    stored_wecom_userid: str | None,
) -> tuple[str | None, str | None]:
    """决定花名册主账号，以及要不要提示「未改挂」。

    返回 ``(primary_user_id, notice)``。notice 只在保住了另一个网页账号时出现。
    """

    clearing_wecom = bool(existing_wecom_userid) and not stored_wecom_userid
    if clearing_wecom and existing_primary_user_id:
        if (
            requested_primary_user_id
            and requested_primary_user_id != existing_primary_user_id
        ):
            return requested_primary_user_id, None
        return existing_primary_user_id, None
    if speaker_user_id and requested_primary_user_id in {None, speaker_user_id}:
        return speaker_user_id, None
    if speaker_user_id and requested_primary_user_id not in {None, speaker_user_id}:
        return requested_primary_user_id, KEEP_WEB_PRIMARY_NOTICE
    return requested_primary_user_id, None


def claim_speaker_if_selected(
    db: Session,
    *,
    tenant_id: str,
    person_name: str,
    speaker_user_id: str | None,
    primary_user_id: str | None,
    wecom_userid: str | None,
    exclude_person_id: str | None = None,
) -> None:
    """主账号确实改为该说话人时，占用检查并替换占位显示名。"""

    if not speaker_user_id or primary_user_id != speaker_user_id or not wecom_userid:
        return
    _require_speaker_free(db, tenant_id, speaker_user_id, exclude_person_id)
    speaker = db.get(User, speaker_user_id)
    if speaker is None or speaker.tenant_id != tenant_id:
        return
    _adopt_display_name(db, tenant_id, speaker, person_name, wecom_userid)


def attach_pending_wecom_person(
    db: Session,
    *,
    tenant_id: str,
    wecom_userid: str,
    user: User,
) -> None:
    """会话账号刚出现时，补上还没有主账号的企业人员。

    不覆盖已经写上的主账号。改挂只在保存时、且主账号为空或已是该说话人时发生。
    影子用户若已属于另一名人员，跳过，避免入站把花名册抢过来。
    """

    userid = str(wecom_userid or "").strip()
    if not userid or userid.startswith("group:"):
        return
    if user is None or str(user.tenant_id or "") != str(tenant_id or ""):
        return
    person = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant_id,
            EnterprisePerson.wecom_userid == userid,
            EnterprisePerson.status == "active",
        )
    ).first()
    if person is None or person.primary_user_id:
        return
    # 多个企业/应用范围指向不同用户时不猜。保存接口会 409；入站不能把回合打失败。
    speakers = _wecom_speakers(db, tenant_id, userid)
    if [item.id for item in speakers] != [user.id]:
        return
    holder = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant_id,
            EnterprisePerson.primary_user_id == user.id,
        )
    ).first()
    if holder is not None and holder.id != person.id:
        return
    # 先开保存点，让刚懒建的用户落在外层事务里。冲突只回滚这次人员关联。
    nested = db.begin_nested()
    try:
        person.primary_user_id = user.id
        person.updated_at = utc_now()
        db.add(person)
        _adopt_display_name(db, tenant_id, user, person.name, userid)
        db.flush()
    except IntegrityError:
        nested.rollback()
        db.expire(person)
        db.expire(user)
        logger.warning(
            "wecom person link skipped after unique conflict person=%s",
            person.id,
        )
        return
    nested.commit()


def _normalize_wecom_userid(value: str | None) -> str | None:
    userid = str(value or "").strip()
    if not userid:
        return None
    if userid.startswith("group:"):
        raise OrgError(
            "ORG_WECOM_USERID_INVALID",
            "群聊身份不能关联到企业人员。",
            status_code=400,
        )
    if len(userid) > _WECOM_USERID_LIMIT:
        raise OrgError("ORG_TEXT_TOO_LONG", "字段超出长度限制。", status_code=400)
    return userid


def _require_userid_free(
    db: Session, tenant_id: str, userid: str, exclude_person_id: str | None
) -> None:
    existing = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant_id,
            EnterprisePerson.wecom_userid == userid,
        )
    ).first()
    if existing is not None and existing.id != exclude_person_id:
        raise OrgError(
            "ORG_WECOM_USERID_TAKEN",
            "该企微 userid 已关联其他企业人员。",
            status_code=409,
        )


def _find_speaker_user(db: Session, tenant_id: str, userid: str) -> User | None:
    speakers = _wecom_speakers(db, tenant_id, userid)
    if len(speakers) > 1:
        raise OrgError(
            "ORG_WECOM_USER_AMBIGUOUS",
            "该企微 userid 在不同企业范围内对应了不同用户，请先核对渠道绑定后再关联。",
            status_code=409,
        )
    return speakers[0] if speakers else None


def _wecom_speakers(db: Session, tenant_id: str, userid: str) -> list[User]:
    rows = db.exec(
        select(ChannelIdentity).where(
            ChannelIdentity.tenant_id == tenant_id,
            ChannelIdentity.channel == "wecom",
            ChannelIdentity.external_user_id == userid,
        )
    ).all()
    speakers: list[User] = []
    seen: set[str] = set()
    for row in rows:
        external_id = str(row.external_user_id or "")
        if external_id.startswith("group:"):
            continue
        user = db.get(User, row.staffdeck_user_id)
        if user is None or user.tenant_id != tenant_id or not user.id or user.id in seen:
            continue
        seen.add(user.id)
        speakers.append(user)
    return speakers


def _require_speaker_free(
    db: Session, tenant_id: str, user_id: str, exclude_person_id: str | None
) -> None:
    existing = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant_id,
            EnterprisePerson.primary_user_id == user_id,
        )
    ).first()
    if existing is not None and existing.id != exclude_person_id:
        raise OrgError(
            "ORG_USER_ALREADY_LINKED",
            "该账号已关联其他企业人员。",
            status_code=409,
        )


def _adopt_display_name(
    db: Session,
    tenant_id: str,
    user: User,
    person_name: str,
    userid: str,
) -> None:
    if user.source != "wecom":
        return
    name = single_line(person_name)
    if not name or not _is_placeholder(user.display_name, user.username):
        return
    user.display_name = name
    user.updated_at = utc_now()
    db.add(user)
    identities = db.exec(
        select(ChannelIdentity).where(
            ChannelIdentity.tenant_id == tenant_id,
            ChannelIdentity.channel == "wecom",
            ChannelIdentity.external_user_id == userid,
            ChannelIdentity.staffdeck_user_id == user.id,
        )
    ).all()
    for identity in identities:
        if _is_placeholder(identity.display_name, user.username):
            identity.display_name = name
            identity.updated_at = utc_now()
            db.add(identity)


def _is_placeholder(display_name: str | None, username: str) -> bool:
    text = str(display_name or "").strip()
    if not text or text == str(username or "").strip():
        return True
    return text.startswith(_PLACEHOLDER_PREFIXES)

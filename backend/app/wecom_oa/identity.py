"""身份解析：ChannelIdentity(wecom) + 临时绑定表；调用方不直耦存储。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session, select

from app.db.models import ChannelIdentity, WecomTempIdentityBinding, utc_now


def _corp_scope_matches(scope: str, corp_scope: str) -> bool:
    """Exact match, or corp_scope + delimiter suffix (e.g. corp_id:bot_id).

    Bare startswith is unsafe: corp_scope "ww" must not bind scope "wwcorp".
    """
    if not scope or not corp_scope:
        return False
    if scope == corp_scope:
        return True
    return scope.startswith(corp_scope + ":")


@dataclass(frozen=True)
class ResolvedWecomIdentity:
    wecom_userid: str
    source: str  # channel_identity | temp_binding
    display_name: str | None = None


class IdentityResolver(Protocol):
    def resolve_wecom_userid(
        self,
        *,
        tenant_id: str,
        staffdeck_user_id: str,
        corp_scope: str,
    ) -> ResolvedWecomIdentity | None: ...


class DefaultIdentityResolver:
    """先查 ChannelIdentity(wecom)，再用临时表覆盖/补齐。"""

    def __init__(self, db: Session):
        self.db = db

    def resolve_wecom_userid(
        self,
        *,
        tenant_id: str,
        staffdeck_user_id: str,
        corp_scope: str,
    ) -> ResolvedWecomIdentity | None:
        staffdeck_user_id = str(staffdeck_user_id or "").strip()
        corp_scope = str(corp_scope or "").strip()
        if not staffdeck_user_id or not corp_scope:
            return None

        # KTD4：临时表覆盖/补齐 — 先查临时表，命中则跳过 ChannelIdentity
        temp_hit = self._from_temp_binding(
            tenant_id=tenant_id,
            staffdeck_user_id=staffdeck_user_id,
            corp_scope=corp_scope,
        )
        if temp_hit is not None:
            return temp_hit
        return self._from_channel_identity(
            tenant_id=tenant_id,
            staffdeck_user_id=staffdeck_user_id,
            corp_scope=corp_scope,
        )

    def _from_channel_identity(
        self,
        *,
        tenant_id: str,
        staffdeck_user_id: str,
        corp_scope: str,
    ) -> ResolvedWecomIdentity | None:
        rows = self.db.exec(
            select(ChannelIdentity).where(
                ChannelIdentity.tenant_id == tenant_id,
                ChannelIdentity.channel == "wecom",
                ChannelIdentity.staffdeck_user_id == staffdeck_user_id,
            )
        ).all()
        for row in rows:
            scope = str(row.external_account_scope or "").strip()
            if _corp_scope_matches(scope, corp_scope):
                userid = str(row.external_user_id or "").strip()
                if userid:
                    return ResolvedWecomIdentity(
                        wecom_userid=userid,
                        source="channel_identity",
                        display_name=row.display_name,
                    )
        return None

    def _from_temp_binding(
        self,
        *,
        tenant_id: str,
        staffdeck_user_id: str,
        corp_scope: str,
    ) -> ResolvedWecomIdentity | None:
        row = self.db.exec(
            select(WecomTempIdentityBinding).where(
                WecomTempIdentityBinding.tenant_id == tenant_id,
                WecomTempIdentityBinding.corp_scope == corp_scope,
                WecomTempIdentityBinding.staffdeck_user_id == staffdeck_user_id,
            )
        ).first()
        if row is None:
            return None
        userid = str(row.wecom_userid or "").strip()
        if not userid:
            return None
        return ResolvedWecomIdentity(
            wecom_userid=userid,
            source="temp_binding",
            display_name=row.display_name,
        )


def resolve_wecom_userid(
    db: Session,
    *,
    tenant_id: str,
    staffdeck_user_id: str,
    corp_scope: str,
) -> ResolvedWecomIdentity | None:
    return DefaultIdentityResolver(db).resolve_wecom_userid(
        tenant_id=tenant_id,
        staffdeck_user_id=staffdeck_user_id,
        corp_scope=corp_scope,
    )


def list_temp_bindings(
    db: Session, *, tenant_id: str, corp_scope: str | None = None
) -> list[WecomTempIdentityBinding]:
    stmt = select(WecomTempIdentityBinding).where(
        WecomTempIdentityBinding.tenant_id == tenant_id
    )
    if corp_scope:
        stmt = stmt.where(WecomTempIdentityBinding.corp_scope == corp_scope)
    return list(db.exec(stmt).all())


def upsert_temp_binding(
    db: Session,
    *,
    tenant_id: str,
    corp_scope: str,
    staffdeck_user_id: str,
    wecom_userid: str,
    display_name: str | None = None,
    actor_user_id: str | None = None,
) -> WecomTempIdentityBinding:
    corp_scope = str(corp_scope or "").strip()
    staffdeck_user_id = str(staffdeck_user_id or "").strip()
    wecom_userid = str(wecom_userid or "").strip()
    if not corp_scope or not staffdeck_user_id or not wecom_userid:
        raise ValueError("corp_scope、staffdeck_user_id、wecom_userid 均为必填")
    row = db.exec(
        select(WecomTempIdentityBinding).where(
            WecomTempIdentityBinding.tenant_id == tenant_id,
            WecomTempIdentityBinding.corp_scope == corp_scope,
            WecomTempIdentityBinding.staffdeck_user_id == staffdeck_user_id,
        )
    ).first()
    if row is None:
        row = WecomTempIdentityBinding(
            tenant_id=tenant_id,
            corp_scope=corp_scope,
            staffdeck_user_id=staffdeck_user_id,
            wecom_userid=wecom_userid,
            display_name=display_name,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        db.add(row)
    else:
        row.wecom_userid = wecom_userid
        row.display_name = display_name
        row.updated_by_user_id = actor_user_id
        row.updated_at = utc_now()
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_temp_binding(
    db: Session,
    *,
    tenant_id: str,
    binding_id: str,
) -> bool:
    row = db.get(WecomTempIdentityBinding, binding_id)
    if row is None or row.tenant_id != tenant_id:
        return False
    db.delete(row)
    db.commit()
    return True

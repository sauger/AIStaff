"""租户级企微自建应用：CRUD、默认应用、连通性自检。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.channels.adapters.wecom import WECOM_API_BASE
from app.channels.crypto import encrypt_channel_secret
from app.config import get_settings
from app.db.models import WecomApp, utc_now
from app.wecom_oa.credentials import (
    WecomOaCredentialError,
    credential_public_view,
    load_plaintext_secrets,
)

logger = logging.getLogger(__name__)

_SECRET_QUERY = re.compile(r"(?i)(corpsecret|access_token)=([^&\s]+)")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class WecomAppError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def list_wecom_apps(db: Session, *, tenant_id: str) -> list[WecomApp]:
    return list(
        db.exec(
            select(WecomApp)
            .where(WecomApp.tenant_id == tenant_id)
            .order_by(WecomApp.created_at, WecomApp.id)
        ).all()
    )


def get_wecom_app(db: Session, *, tenant_id: str, app_id: str) -> WecomApp | None:
    row = db.get(WecomApp, app_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


def get_default_wecom_app(db: Session, *, tenant_id: str) -> WecomApp | None:
    return db.exec(
        select(WecomApp).where(
            WecomApp.tenant_id == tenant_id,
            WecomApp.is_default == True,  # noqa: E712
            WecomApp.status == "active",
        )
    ).first()


def resolve_default_oa_app(
    db: Session,
    *,
    tenant_id: str,
    intent_corp_id: str | None = None,
) -> tuple[WecomApp | None, str | None]:
    """OA 代发只使用默认应用。corp 不一致时不扫描其他行。"""
    app = get_default_wecom_app(db, tenant_id=tenant_id)
    if app is None:
        return None, "WECOM_OA_NO_CREDENTIAL"
    expected = str(intent_corp_id or "").strip()
    if expected and expected != app.corp_id:
        return None, "WECOM_OA_APP_CORP_MISMATCH"
    return app, None


def get_active_callback_app(db: Session, *, tenant_id: str, app_id: str) -> WecomApp | None:
    row = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if row is None or row.status != "active":
        return None
    return row


def create_wecom_app(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    corp_id: str,
    agentid: str,
    secret: str,
    callback_token: str | None = None,
    callback_aes_key: str | None = None,
    created_by_user_id: str | None = None,
) -> WecomApp:
    name = str(name or "").strip()
    corp_id = str(corp_id or "").strip()
    agentid = str(agentid or "").strip()
    secret = str(secret or "").strip()
    if not name:
        raise WecomAppError("请填写应用名称")
    if not corp_id:
        raise WecomAppError("请填写企业 ID")
    if not agentid:
        raise WecomAppError("请填写 AgentId")
    if not secret:
        raise WecomAppError("请填写 Secret")
    token_enc, aes_enc = _encode_callback_pair(callback_token, callback_aes_key)
    has_default = db.exec(
        select(WecomApp.id).where(
            WecomApp.tenant_id == tenant_id,
            WecomApp.is_default == True,  # noqa: E712
        )
    ).first()
    row = WecomApp(
        tenant_id=tenant_id,
        name=name,
        corp_id=corp_id,
        agentid=agentid,
        secret_enc=encrypt_channel_secret(secret),
        callback_token_enc=token_enc,
        callback_aes_key_enc=aes_enc,
        is_default=has_default is None,
        status="active",
        created_by_user_id=created_by_user_id,
    )
    db.add(row)
    _commit_unique(db, row)
    return row


def update_wecom_app(
    db: Session,
    row: WecomApp,
    *,
    fields: set[str],
    name: str | None = None,
    corp_id: str | None = None,
    agentid: str | None = None,
    status: str | None = None,
    secret: str | None = None,
    callback_token: str | None = None,
    callback_aes_key: str | None = None,
) -> WecomApp:
    if "name" in fields:
        cleaned = str(name or "").strip()
        if not cleaned:
            raise WecomAppError("请填写应用名称")
        row.name = cleaned
    if "corp_id" in fields:
        cleaned = str(corp_id or "").strip()
        if not cleaned:
            raise WecomAppError("请填写企业 ID")
        row.corp_id = cleaned
    if "agentid" in fields:
        cleaned = str(agentid or "").strip()
        if not cleaned:
            raise WecomAppError("请填写 AgentId")
        row.agentid = cleaned
    if "status" in fields:
        cleaned = str(status or "").strip()
        if cleaned not in {"active", "disabled"}:
            raise WecomAppError("状态只能是 active 或 disabled")
        row.status = cleaned
        if cleaned == "disabled":
            row.is_default = False
    if "secret" in fields:
        cleaned = str(secret or "").strip()
        if not cleaned:
            raise WecomAppError("不允许清空 Secret")
        row.secret_enc = encrypt_channel_secret(cleaned)
    if "callback_token" in fields or "callback_aes_key" in fields:
        if "callback_token" not in fields or "callback_aes_key" not in fields:
            raise WecomAppError("回调 Token 与 EncodingAESKey 必须成对提交")
        token_enc, aes_enc = _encode_callback_pair(callback_token, callback_aes_key)
        row.callback_token_enc = token_enc
        row.callback_aes_key_enc = aes_enc
    row.updated_at = utc_now()
    db.add(row)
    _commit_unique(db, row)
    return row


def delete_wecom_app(db: Session, row: WecomApp) -> None:
    if row.is_default:
        other = db.exec(
            select(WecomApp.id).where(
                WecomApp.tenant_id == row.tenant_id,
                WecomApp.id != row.id,
            )
        ).first()
        if other is not None:
            raise WecomAppError(
                "请先把另一个应用设为默认，再删除当前默认应用",
                status_code=409,
            )
    db.delete(row)
    db.commit()


def set_default_wecom_app(db: Session, row: WecomApp) -> WecomApp:
    if row.status != "active":
        raise WecomAppError("停用的应用不能设为默认", status_code=409)
    others = db.exec(
        select(WecomApp).where(
            WecomApp.tenant_id == row.tenant_id,
            WecomApp.is_default == True,  # noqa: E712
            WecomApp.id != row.id,
        )
    ).all()
    for other in others:
        other.is_default = False
        other.updated_at = utc_now()
        db.add(other)
    db.flush()
    row.is_default = True
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def check_wecom_app(
    db: Session,
    row: WecomApp,
    *,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> dict[str, Any]:
    if row.status != "active":
        raise WecomAppError("停用的应用不能自检", status_code=409)
    if not str(row.agentid or "").strip():
        raise WecomAppError("请先填写 AgentId")
    try:
        secrets = load_plaintext_secrets(row)
    except WecomOaCredentialError:
        logger.error(
            "wecom app secret decrypt failed tenant_id=%s app_id=%s",
            row.tenant_id,
            row.id,
        )
        raise WecomAppError("企微应用密钥无法解密，请检查服务端密钥配置", status_code=500) from None
    secret = secrets["secret"]
    factory = client_factory or (lambda: httpx.Client(timeout=15.0))
    ok = False
    errcode: int | None = None
    summary = "企微连接失败"
    agent_name: str | None = None
    try:
        with factory() as client:
            token_payload = _wecom_get(
                client,
                f"{WECOM_API_BASE}/gettoken",
                {"corpid": row.corp_id, "corpsecret": secret},
            )
            errcode = _errcode(token_payload)
            if errcode != 0:
                summary = _summary_from_wecom(token_payload, secret)
            else:
                access_token = str(token_payload.get("access_token") or "").strip()
                if not access_token:
                    summary = "企微未返回访问令牌"
                    errcode = errcode or -1
                else:
                    agent_payload = _wecom_get(
                        client,
                        f"{WECOM_API_BASE}/agent/get",
                        {"access_token": access_token, "agentid": row.agentid},
                    )
                    errcode = _errcode(agent_payload)
                    if errcode != 0:
                        summary = _summary_from_wecom(agent_payload, secret, access_token)
                    else:
                        ok = True
                        summary = "已连通"
                        agent_name = str(agent_payload.get("name") or "").strip() or None
    except Exception:
        logger.warning(
            "wecom app check failed tenant_id=%s app_id=%s corp_id=%s agentid=%s",
            row.tenant_id,
            row.id,
            row.corp_id,
            row.agentid,
        )
        ok = False
        summary = "企微连接失败"
        errcode = None
    checked_at = utc_now()
    row.last_check_at = checked_at
    row.last_check_ok = ok
    row.last_check_summary = summary
    row.updated_at = checked_at
    db.add(row)
    db.commit()
    db.refresh(row)
    result: dict[str, Any] = {
        "ok": ok,
        "checked_at": checked_at.isoformat(),
        "errcode": errcode,
        "summary": summary,
    }
    if ok and agent_name:
        result["agent_name"] = agent_name
    return result


def app_public_view(row: WecomApp) -> dict[str, Any]:
    view = credential_public_view(row)
    view["agentid"] = row.agentid
    view.update(
        {
            "name": row.name,
            "is_default": bool(row.is_default),
            "last_check_at": _iso(row.last_check_at),
            "last_check_ok": row.last_check_ok,
            "last_check_summary": row.last_check_summary,
            "last_callback_verified_at": _iso(row.last_callback_verified_at),
        }
    )
    view.update(callback_url_fields(row.tenant_id, row.id))
    return view


def callback_url_fields(tenant_id: str, app_id: str) -> dict[str, Any]:
    path = f"/api/public/wecom-app/callback/{tenant_id}/{app_id}"
    base = public_callback_base()
    if base:
        return {
            "callback_path": path,
            "callback_url": f"{base}{path}",
            "callback_url_absolute": True,
        }
    return {
        "callback_path": path,
        "callback_url": path,
        "callback_url_absolute": False,
        "callback_url_hint": "请先配置公网基地址，再把回调地址贴进企微后台",
    }


def public_callback_base() -> str:
    base = get_settings().normalized_public_base_url
    if not base:
        return ""
    host = (urlparse(base).hostname or "").strip().lower()
    if host in _LOOPBACK_HOSTS:
        return ""
    return base


def mark_callback_verified(db: Session, row: WecomApp) -> None:
    row.last_callback_verified_at = utc_now()
    row.updated_at = row.last_callback_verified_at
    db.add(row)
    db.commit()


def _encode_callback_pair(
    callback_token: str | None,
    callback_aes_key: str | None,
) -> tuple[str | None, str | None]:
    token = str(callback_token or "").strip()
    aes = str(callback_aes_key or "").strip()
    if not token and not aes:
        return None, None
    if not token or not aes:
        raise WecomAppError("回调 Token 与 EncodingAESKey 必须成对填写")
    return encrypt_channel_secret(token), encrypt_channel_secret(aes)


def _commit_unique(db: Session, row: WecomApp) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise WecomAppError(
            "同一企业下该 AgentId 已存在",
            status_code=409,
        ) from None
    db.refresh(row)


def _wecom_get(client: httpx.Client, url: str, params: dict[str, str]) -> dict[str, Any]:
    try:
        response = client.get(url, params=params)
        data = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        raise WecomAppError("企微连接失败") from None
    if not isinstance(data, dict):
        raise WecomAppError("企微返回无效")
    return data


def _errcode(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("errcode", -1))
    except (TypeError, ValueError):
        return -1


def _summary_from_wecom(payload: dict[str, Any], *secrets: str) -> str:
    errmsg = str(payload.get("errmsg") or "").strip()
    errcode = _errcode(payload)
    text = errmsg or f"errcode={errcode}"
    return _redact(text, *secrets) or f"errcode={errcode}"


def _redact(text: str, *secrets: str) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "")
    cleaned = _SECRET_QUERY.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = cleaned.replace("https://qyapi.weixin.qq.com", "")
    return cleaned.strip()[:180]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

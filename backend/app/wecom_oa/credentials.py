"""OA 自建应用凭证存取（加密，与 bot ChannelBinding 隔离）。"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from app.channels.crypto import decrypt_channel_secret, encrypt_channel_secret
from app.db.models import WecomOaAppCredential, utc_now


class WecomOaCredentialError(RuntimeError):
    """凭证缺失或解密失败。"""


def encrypt_secret(plaintext: str) -> str:
    return encrypt_channel_secret(plaintext)


def decrypt_secret(token: str) -> str:
    return decrypt_channel_secret(token)


def store_oa_credential(
    db: Session,
    *,
    tenant_id: str,
    corp_id: str,
    secret: str,
    agentid: str | None = None,
    callback_token: str | None = None,
    callback_aes_key: str | None = None,
    created_by_user_id: str | None = None,
    existing: WecomOaAppCredential | None = None,
) -> WecomOaAppCredential:
    corp_id = str(corp_id or "").strip()
    secret = str(secret or "").strip()
    if not corp_id or not secret:
        raise WecomOaCredentialError("OA 应用缺少 corp_id 或 secret")
    row = existing
    if row is None:
        row = db.exec(
            select(WecomOaAppCredential).where(
                WecomOaAppCredential.tenant_id == tenant_id,
                WecomOaAppCredential.corp_id == corp_id,
            )
        ).first()
    if row is None:
        row = WecomOaAppCredential(
            tenant_id=tenant_id,
            corp_id=corp_id,
            secret_enc=encrypt_secret(secret),
        )
        db.add(row)
    else:
        row.secret_enc = encrypt_secret(secret)
        row.updated_at = utc_now()
    row.agentid = (agentid or "").strip() or None
    if callback_token is not None:
        token = str(callback_token).strip()
        row.callback_token_enc = encrypt_secret(token) if token else None
    if callback_aes_key is not None:
        aes = str(callback_aes_key).strip()
        row.callback_aes_key_enc = encrypt_secret(aes) if aes else None
    if created_by_user_id and not row.created_by_user_id:
        row.created_by_user_id = created_by_user_id
    row.status = "active"
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_oa_credential(
    db: Session, *, tenant_id: str, corp_id: str | None = None
) -> WecomOaAppCredential | None:
    stmt = select(WecomOaAppCredential).where(
        WecomOaAppCredential.tenant_id == tenant_id,
        WecomOaAppCredential.status == "active",
    )
    if corp_id:
        stmt = stmt.where(WecomOaAppCredential.corp_id == corp_id)
    return db.exec(stmt).first()


def load_plaintext_secrets(row: WecomOaAppCredential) -> dict[str, str]:
    if not row.secret_enc:
        raise WecomOaCredentialError("OA 应用 secret 缺失")
    out: dict[str, str] = {
        "corp_id": row.corp_id,
        "secret": decrypt_secret(row.secret_enc),
    }
    if row.agentid:
        out["agentid"] = row.agentid
    if row.callback_token_enc:
        out["callback_token"] = decrypt_secret(row.callback_token_enc)
    if row.callback_aes_key_enc:
        out["callback_aes_key"] = decrypt_secret(row.callback_aes_key_enc)
    return out


def credential_public_view(row: WecomOaAppCredential) -> dict[str, Any]:
    """对外返回不含明文密钥的视图。"""
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "corp_id": row.corp_id,
        "agentid": row.agentid,
        "status": row.status,
        "has_secret": bool(row.secret_enc),
        "has_callback_token": bool(row.callback_token_enc),
        "has_callback_aes_key": bool(row.callback_aes_key_enc),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def dump_callback_bundle(row: WecomOaAppCredential) -> dict[str, str]:
    """回调验签所需的 token / aes_key / corp_id。"""
    secrets = load_plaintext_secrets(row)
    token = secrets.get("callback_token") or ""
    aes_key = secrets.get("callback_aes_key") or ""
    if not token or not aes_key:
        raise WecomOaCredentialError("OA 回调 Token 或 EncodingAESKey 未配置")
    return {
        "corp_id": row.corp_id,
        "token": token,
        "aes_key": aes_key,
    }

"""按应用验签解密回调。审批变更走现有 apply_status_update，其他事件只记事件名。"""

from __future__ import annotations

import json
import logging

from defusedxml import ElementTree as ET
from sqlmodel import Session

from app.db.models import WecomApp
from app.wecom_oa.apps import mark_callback_verified
from app.wecom_oa.callback import (
    WecomOaCallbackError,
    apply_status_update,
    decrypt_callback_message,
    parse_approval_change_xml,
    verify_callback,
)
from app.wecom_oa.credentials import WecomOaCredentialError, dump_callback_bundle

logger = logging.getLogger(__name__)


def verify_app_callback_url(
    db: Session,
    app: WecomApp,
    *,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> str:
    bundle = dump_callback_bundle(app)
    verify_callback(bundle["token"], msg_signature, timestamp, nonce, echostr)
    plain = decrypt_callback_message(echostr, bundle["aes_key"], bundle["corp_id"])
    mark_callback_verified(db, app)
    return plain


def accept_app_callback_event(
    db: Session,
    app: WecomApp,
    *,
    body: bytes,
    msg_signature: str,
    timestamp: str,
    nonce: str,
) -> None:
    ciphertext = extract_encrypt_payload(body)
    if not all((msg_signature, timestamp, nonce, ciphertext)):
        raise WecomOaCallbackError("缺少回调参数", code="WECOM_OA_CALLBACK_PARAMS")
    bundle = dump_callback_bundle(app)
    verify_callback(bundle["token"], msg_signature, timestamp, nonce, ciphertext)
    plain = decrypt_callback_message(ciphertext, bundle["aes_key"], bundle["corp_id"])
    parsed = parse_approval_change_xml(plain)
    sp_no = str(parsed.get("sp_no") or "").strip()
    if sp_no:
        apply_status_update(
            db,
            sp_no=sp_no,
            sp_status=str(parsed.get("sp_status") or ""),
            change_event=str(parsed.get("event") or "sys_approval_change"),
            source="callback",
            payload=parsed,
            tenant_id=app.tenant_id,
        )
        return
    event_name = str(parsed.get("event") or "")[:64]
    logger.info(
        "wecom app callback event tenant_id=%s app_id=%s event=%s",
        app.tenant_id,
        app.id,
        event_name,
    )


def extract_encrypt_payload(body: bytes) -> str:
    try:
        text = body.decode("utf-8")
        if "<Encrypt>" in text:
            root = ET.fromstring(text)
            node = root.find("Encrypt")
            return (node.text or "") if node is not None else ""
        payload = json.loads(text or "{}")
        return str(payload.get("Encrypt") or payload.get("encrypt") or "")
    except (UnicodeDecodeError, ET.ParseError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise WecomOaCallbackError("回调体无效", code="WECOM_OA_BAD_BODY") from exc


def callback_http_status(exc: Exception) -> int:
    if isinstance(exc, WecomOaCredentialError):
        return 403
    if isinstance(exc, WecomOaCallbackError) and exc.code == "WECOM_OA_BAD_BODY":
        return 400
    if isinstance(exc, WecomOaCallbackError) and exc.code == "WECOM_OA_CALLBACK_PARAMS":
        return 400
    return 403

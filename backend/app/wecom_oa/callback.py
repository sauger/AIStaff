"""企微审批回调验签与 sys_approval_change 处理。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import struct
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from defusedxml import ElementTree as ET
from sqlmodel import Session

from app.db.models import Message, WecomApprovalStatusEvent, utc_now
from app.wecom_oa.intent import get_intent_by_sp_no

logger = logging.getLogger(__name__)

CALLBACK_TIMESTAMP_MAX_AGE_SECONDS = 5 * 60


class WecomOaCallbackError(RuntimeError):
    def __init__(self, message: str, *, code: str = "WECOM_OA_CALLBACK_ERROR"):
        super().__init__(message)
        self.code = code


def callback_signature(token: str, timestamp: str, nonce: str, ciphertext: str) -> str:
    values = sorted((token, timestamp, nonce, ciphertext))
    return hashlib.sha1("".join(values).encode("utf-8")).hexdigest()


def verify_callback(
    token: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    ciphertext: str,
) -> None:
    try:
        callback_timestamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise WecomOaCallbackError("回调时间戳无效", code="WECOM_OA_BAD_TIMESTAMP") from exc
    age = time.time() - callback_timestamp
    if abs(age) > CALLBACK_TIMESTAMP_MAX_AGE_SECONDS:
        raise WecomOaCallbackError("回调时间戳过期或时钟偏差过大", code="WECOM_OA_TIMESTAMP_SKEW")
    expected = callback_signature(token, timestamp, nonce, ciphertext)
    if not hmac.compare_digest(expected, msg_signature):
        raise WecomOaCallbackError("回调签名无效", code="WECOM_OA_BAD_SIGNATURE")


def decrypt_callback_message(ciphertext: str, aes_key: str, corp_id: str) -> str:
    try:
        key = base64.urlsafe_b64decode(aes_key + "=")
        if len(key) != 32:
            raise ValueError("invalid AES key")
        encrypted = base64.b64decode(ciphertext, validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        padding = padded[-1]
        if not 1 <= padding <= 32 or padded[-padding:] != bytes((padding,)) * padding:
            raise ValueError("invalid PKCS#7 padding")
        payload = padded[:-padding]
        content_length = struct.unpack("!I", payload[16:20])[0]
        content_end = 20 + content_length
        content = payload[20:content_end]
        receive_id = payload[content_end:].decode("utf-8")
        if receive_id != corp_id:
            raise ValueError("unexpected receive id")
        return content.decode("utf-8")
    except (ValueError, IndexError, struct.error, UnicodeDecodeError) as exc:
        raise WecomOaCallbackError("回调消息解密失败", code="WECOM_OA_DECRYPT_FAILED") from exc


def parse_approval_change_xml(xml_text: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WecomOaCallbackError("回调 XML 无效", code="WECOM_OA_BAD_XML") from exc

    def _text(*tags: str) -> str:
        for tag in tags:
            node = root.find(tag)
            if node is not None and node.text:
                return str(node.text).strip()
        return ""

    # 企微审批变更常见字段
    event = _text("Event", "MsgType")
    sp_no = _text("SpNo", "SpNoStr", "ApprovalInfo/SpNo")
    if not sp_no:
        # ApprovalInfo nested
        info = root.find("ApprovalInfo")
        if info is not None:
            for tag in ("SpNo", "SpNoStr"):
                node = info.find(tag)
                if node is not None and node.text:
                    sp_no = str(node.text).strip()
                    break
    sp_status = _text("SpStatus", "ApprovalInfo/SpStatus")
    if not sp_status:
        info = root.find("ApprovalInfo")
        if info is not None:
            node = info.find("SpStatus")
            if node is not None and node.text:
                sp_status = str(node.text).strip()
    return {
        "event": event or "sys_approval_change",
        "sp_no": sp_no,
        "sp_status": sp_status or "",
        "raw_xml": xml_text,
    }


def apply_status_update(
    db: Session,
    *,
    sp_no: str,
    sp_status: str,
    change_event: str,
    source: str,
    payload: dict[str, Any] | None = None,
    tenant_id: str | None = None,
    write_session_message: bool = True,
) -> WecomApprovalStatusEvent | None:
    """共享写入路径：主动查询与回调均经此函数。未知 sp_no 不写会话。"""
    sp_no = str(sp_no or "").strip()
    if not sp_no:
        return None
    intent = get_intent_by_sp_no(db, tenant_id=tenant_id, sp_no=sp_no)
    if intent is None:
        logger.info("wecom_oa callback: unknown sp_no=%s ignored", sp_no)
        return None
    sp_status = str(sp_status or "").strip() or "unknown"
    change_event = str(change_event or "").strip() or source
    existing = None
    from sqlmodel import select

    existing = db.exec(
        select(WecomApprovalStatusEvent).where(
            WecomApprovalStatusEvent.intent_id == intent.id,
            WecomApprovalStatusEvent.sp_status == sp_status,
            WecomApprovalStatusEvent.change_event == change_event,
        )
    ).first()
    if existing is not None:
        return existing

    event = WecomApprovalStatusEvent(
        tenant_id=intent.tenant_id,
        intent_id=intent.id,
        session_id=intent.session_id,
        sp_no=sp_no,
        sp_status=sp_status,
        change_event=change_event,
        source=source,
        payload_json=payload or {},
    )
    db.add(event)
    intent.last_progress_json = {
        "sp_no": sp_no,
        "sp_status": sp_status,
        "change_event": change_event,
        "source": source,
        "updated_at": utc_now().isoformat(),
    }
    intent.updated_at = utc_now()
    db.add(intent)
    if write_session_message:
        status_label = _status_label(sp_status)
        db.add(
            Message(
                tenant_id=intent.tenant_id,
                session_id=intent.session_id,
                role="assistant",
                content=f"企微审批进度更新：单号 {sp_no}，状态 {status_label}。",
                metadata_json={
                    "wecom_oa_approval_progress": {
                        "intent_id": intent.id,
                        "sp_no": sp_no,
                        "sp_status": sp_status,
                        "change_event": change_event,
                        "source": source,
                    }
                },
            )
        )
    db.commit()
    db.refresh(event)
    return event


def _status_label(sp_status: str) -> str:
    mapping = {
        "1": "审批中",
        "2": "已通过",
        "3": "已驳回",
        "4": "已撤销",
        "6": "通过后撤销",
        "7": "已删除",
        "10": "已支付",
    }
    return mapping.get(str(sp_status), str(sp_status))

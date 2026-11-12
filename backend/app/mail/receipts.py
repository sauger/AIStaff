"""Authoritative attachment checks and eligibility for a direct mail completion reply."""

from __future__ import annotations

import hashlib

from app.cabinet.errors import CabinetError
from app.cabinet.paths import storage_path
from app.cabinet.service import get_entry


def verify_attachments(db, tenant_id: str, agent_id: str, attachments: list[dict]) -> list[dict]:
    checked = []
    for attachment in attachments:
        item = {**attachment, "verified": False}
        path = item.get("cabinet_path")
        if not item.get("saved") or not path:
            checked.append(item)
            continue
        try:
            entry = get_entry(db, tenant_id, agent_id, path)
            if entry is None or entry.kind != "file" or not entry.sha256:
                raise ValueError("附件保存记录不完整。")
            file_path = storage_path(tenant_id, agent_id, path)
            expected_size = item.get("size_bytes")
            if file_path.stat().st_size != entry.size_bytes or expected_size != entry.size_bytes:
                raise ValueError("附件大小与保存记录不一致。")
            digest = hashlib.sha256()
            with file_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(128 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != entry.sha256:
                raise ValueError("附件校验值与保存记录不一致。")
            item.update(verified=True, sha256=entry.sha256, error=None)
        except (CabinetError, OSError, ValueError):
            item.update(saved=False, error="附件文件缺失或校验失败，尚不能确认下载成功。")
        checked.append(item)
    return checked


def can_reuse_mail_reply(result, reply: str) -> bool:
    """Keep structured/non-mail synthesis unless every selected mail has a checked receipt."""
    if result.status != "completed" or result.error or not result.capability_results:
        return False
    messages = []
    for capability in result.capability_results:
        if (
            not isinstance(capability, dict)
            or capability.get("success") is not True
            or capability.get("truncated")
            or capability.get("data_status") == "empty"
            or capability.get("tool_name") not in {"mail_list", "mail_read"}
        ):
            return False
        if capability["tool_name"] != "mail_read":
            continue
        data = capability.get("data")
        if not isinstance(data, dict):
            return False
        if data.get("errors"):
            return False
        selected = data.get("messages") if "messages" in data else [data]
        if not isinstance(selected, list) or not selected:
            return False
        messages.extend(selected)
    if not messages:
        return False
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("attachment_verification_version") != 1
            or message.get("all_attachments_verified") is not True
            or not message.get("subject")
            or message["subject"] not in reply
        ):
            return False
        attachments = message.get("attachments")
        if not isinstance(attachments, list):
            return False
        for attachment in attachments:
            if (
                attachment.get("verified") is not True
                or attachment.get("saved") is not True
                or not attachment.get("sha256")
                or not attachment.get("filename")
                or attachment["filename"] not in reply
            ):
                return False
    return True

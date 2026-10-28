from __future__ import annotations

import hmac
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.channels.artifact_links import artifact_link_is_expired
from app.core.harness_session_cleanup import harness_task_workspace_path
from app.db import get_session
from app.db.models import ChannelBinding, ChannelDelivery, ChatSession
from app.harness.artifacts import HarnessArtifactAccessError, open_harness_artifact

router = APIRouter(prefix="/api/channel-artifacts", tags=["channel-artifacts"])


@router.get("/{delivery_id}")
def download_channel_artifact(
    delivery_id: str,
    db: Annotated[Session, Depends(get_session)],
    token: str = Query(..., min_length=16),
):
    """Download a file staged for an IM channel via its unguessable share token."""

    delivery = db.get(ChannelDelivery, delivery_id)
    if delivery is None or delivery.kind != "file":
        raise HTTPException(status_code=404, detail="Artifact delivery not found")
    artifact = dict(delivery.artifact_json or {})
    expected = str(artifact.get("share_token") or "")
    if not expected or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=404, detail="Artifact delivery not found")
    if artifact_link_is_expired(artifact):
        raise HTTPException(status_code=410, detail="Artifact link has expired")

    session = db.get(ChatSession, delivery.session_id)
    binding = db.get(ChannelBinding, delivery.binding_id)
    if (
        session is None
        or binding is None
        or session.tenant_id != delivery.tenant_id
        or binding.tenant_id != delivery.tenant_id
        or session.channel_binding_id != binding.id
    ):
        raise HTTPException(status_code=404, detail="Artifact delivery not found")

    task_frame_id = str(artifact.get("task_frame_id") or "").strip()
    relative_path = str(artifact.get("path") or "").strip()
    if not task_frame_id or not relative_path:
        raise HTTPException(status_code=404, detail="Artifact delivery not found")
    try:
        workspace_root = harness_task_workspace_path(
            tenant_id=delivery.tenant_id,
            session_id=delivery.session_id,
            task_frame_id=task_frame_id,
            db=db,
        )
        opened = open_harness_artifact(workspace_root, relative_path)
    except (HarnessArtifactAccessError, OSError):
        raise HTTPException(status_code=404, detail="Artifact file not found") from None

    filename = str(artifact.get("display_name") or opened.filename or "artifact").strip()
    filename = filename[:180] or "artifact"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Content-Length": str(opened.size),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        opened.iter_bytes(64 * 1024),
        media_type=str(artifact.get("content_type") or "application/octet-stream"),
        headers=headers,
    )

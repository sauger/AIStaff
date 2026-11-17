"""Internal demo endpoints for screen dialogue.

Authentication is the service token plus an optional short-lived signed user
context. The route only returns a limited intent; it never mutates ai-screen.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import ModelConfig
from app.llm.client import LLMClient
from app.llm.model_config_resolver import resolve_model_config_for_runtime
from app.security.internal_service import require_internal_service
from app.workflow_packs.screen.draft_intent import (
    INTENT_SYSTEM_PROMPT,
    build_intent,
    parse_model_intent,
    projected_catalog,
)

router = APIRouter(
    prefix="/api/internal/screen",
    tags=["internal-screen"],
    dependencies=[Depends(require_internal_service)],
)


class DraftIntentRequest(BaseModel):
    tenantId: str = Field(min_length=1, max_length=80)
    spaceId: str = Field(min_length=1, max_length=80)
    userId: str = Field(min_length=1, max_length=80)
    planId: str = Field(min_length=1, max_length=80)
    taskId: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    draft: dict[str, Any]
    catalog: dict[str, Any]


def _verify_user_context(request: Request, payload: DraftIntentRequest) -> None:
    secret = (os.getenv("SCREEN_INTERNAL_USER_SECRET") or "").strip()
    if not secret:
        return
    user = (request.headers.get("X-AI-Screen-User-Id") or "").strip()
    space = (request.headers.get("X-AI-Screen-Space-Id") or "").strip()
    timestamp = (request.headers.get("X-AI-Screen-Timestamp") or "").strip()
    signature = (request.headers.get("X-AI-Screen-Signature") or "").strip()
    if user != payload.userId or space != payload.spaceId or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="SIGNED_USER_CONTEXT_REQUIRED")
    try:
        age = abs(int(time.time()) - int(timestamp))
    except ValueError:
        raise HTTPException(status_code=401, detail="SIGNED_USER_CONTEXT_INVALID") from None
    if age > 300:
        raise HTTPException(status_code=401, detail="SIGNED_USER_CONTEXT_EXPIRED")
    message = f"{user}|{space}|{timestamp}".encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="SIGNED_USER_CONTEXT_INVALID")


def _model_intent(db: Session, payload: DraftIntentRequest):
    row = db.exec(
        select(ModelConfig).where(
            ModelConfig.tenant_id == payload.tenantId,
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.enabled == True,  # noqa: E712
        )
    ).first()
    if row is None:
        return None
    config = resolve_model_config_for_runtime(db, payload.tenantId, row.id)
    client = LLMClient(config)

    def _call():
        return client.generate_json(
            INTENT_SYSTEM_PROMPT,
            {
                "user_message": payload.text,
                "draft": payload.draft,
                "catalog": projected_catalog(payload.catalog),
            },
        )

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        raw = pool.submit(_call).result(timeout=5)
    finally:
        pool.shutdown(wait=False)
    return parse_model_intent(raw)


@router.post("/draft-intent")
def draft_intent(
    payload: DraftIntentRequest,
    request: Request,
    db: Session = Depends(get_session),
    _: str | None = Header(default=None, alias="X-UltraRAG-Internal-Token"),
) -> dict[str, Any]:
    _verify_user_context(request, payload)
    intent = None
    try:
        intent = _model_intent(db, payload)
    except Exception:
        intent = None
    if intent is None:
        # Model unavailable, timed out, or returned an invalid shape only.
        intent = build_intent(payload.text, payload.draft, payload.catalog)
    return {"intent": intent.model_dump()}

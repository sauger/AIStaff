"""Read-only access to built-in workflow packs.

The pack definition lives in reviewed code (``app.workflow_packs``); this
endpoint exposes the same SkillCard so deployment scripts can publish it
through the ordinary skills API instead of duplicating the SOP JSON.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.security.auth import get_current_user
from app.workflow_packs.screen.sop import build_screen_sop

router = APIRouter(
    prefix="/api/enterprise/workflow-packs",
    tags=["enterprise:workflow-packs"],
)

_PACK_BUILDERS = {
    "screen": build_screen_sop,
}


@router.get("/{pack_id}", dependencies=[Depends(get_current_user)])
def get_workflow_pack(pack_id: str) -> dict[str, object]:
    builder = _PACK_BUILDERS.get(pack_id)
    if builder is None:
        raise HTTPException(status_code=404, detail="Unknown workflow pack")
    return {"pack_id": pack_id, "content": builder()}

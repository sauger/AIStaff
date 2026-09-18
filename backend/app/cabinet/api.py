from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session

from app.cabinet.errors import CabinetError
from app.cabinet.schema import (
    CabinetEntryRead,
    CabinetFolderCreateRequest,
    CabinetListResponse,
    CabinetMoveRequest,
    CabinetUploadRequest,
)
from app.cabinet.service import (
    can_write_cabinet,
    decode_base64_content,
    delete_entry,
    ensure_reader,
    ensure_writer,
    entry_read,
    join_path,
    list_folder,
    make_folder,
    move_entry,
    read_file_bytes,
    save_bytes,
)
from app.db import get_session
from app.db.models import User
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.tenant import ensure_tenant

router = APIRouter(
    prefix="/api/enterprise/cabinet",
    tags=["enterprise:cabinet"],
    dependencies=[Depends(get_current_user)],
)

SessionDep = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _raise(error: CabinetError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.as_http_detail()) from error


@router.get("", response_model=CabinetListResponse)
def list_cabinet(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    path: str = Query(""),
) -> CabinetListResponse:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        agent = ensure_reader(db, tenant_id, agent_id, current_user)
        return list_folder(
            db,
            tenant_id,
            agent_id,
            path,
            can_write=can_write_cabinet(agent, current_user),
        )
    except CabinetError as error:
        _raise(error)
        raise


@router.post("/folders", response_model=CabinetEntryRead)
def create_folder(
    request: CabinetFolderCreateRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> CabinetEntryRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        row = make_folder(db, request.tenant_id, agent_id, request.path, request.name)
        return entry_read(row)
    except CabinetError as error:
        _raise(error)
        raise


@router.post("/files", response_model=CabinetEntryRead)
def upload_file(
    request: CabinetUploadRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> CabinetEntryRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        data = decode_base64_content(request.filename, request.content_base64)
        row = save_bytes(
            db,
            request.tenant_id,
            agent_id,
            join_path(request.path, request.filename),
            data,
            overwrite=request.overwrite,
            source="console",
            create_parents=False,
        )
        return entry_read(row)
    except CabinetError as error:
        _raise(error)
        raise


@router.get("/files/content")
def download_file(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    path: str = Query(...),
) -> Response:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_reader(db, tenant_id, agent_id, current_user)
        row, data = read_file_bytes(db, tenant_id, agent_id, path)
    except CabinetError as error:
        _raise(error)
        raise
    encoded = quote(row.name)
    return Response(
        content=data,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "X-Cabinet-Path": quote(row.path),
        },
    )


@router.patch("/entries", response_model=CabinetEntryRead)
def rename_or_move(
    request: CabinetMoveRequest,
    db: SessionDep,
    current_user: CurrentUser,
    agent_id: str = Query(...),
) -> CabinetEntryRead:
    ensure_current_user_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    try:
        ensure_writer(db, request.tenant_id, agent_id, current_user)
        row = move_entry(
            db,
            request.tenant_id,
            agent_id,
            request.path,
            request.destination_path,
            overwrite=request.overwrite,
        )
        return entry_read(row)
    except CabinetError as error:
        _raise(error)
        raise


@router.delete("/entries")
def remove_entry(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    path: str = Query(...),
) -> dict[str, str]:
    ensure_current_user_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    try:
        ensure_writer(db, tenant_id, agent_id, current_user)
        delete_entry(db, tenant_id, agent_id, path)
        return {"status": "deleted"}
    except CabinetError as error:
        _raise(error)
        raise

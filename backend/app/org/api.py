from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db import get_session
from app.db.models import User
from app.org.errors import OrgError
from app.org.schema import (
    AffiliationWriteRequest,
    DepartmentListResponse,
    DepartmentPeopleResponse,
    DepartmentRead,
    DepartmentWriteRequest,
    HeadWriteRequest,
    OrgContactListResponse,
    OrgHeadRead,
    OrgPersonRead,
    OrgSettingsRead,
    OrgSettingsUpdateRequest,
    TenureUpdateRequest,
)
from app.org.service import (
    create_affiliation,
    create_department,
    create_head,
    delete_affiliation,
    delete_department,
    delete_head,
    department_people,
    list_departments,
    list_org_contacts,
    read_settings,
    update_affiliation,
    update_department,
    update_head,
    update_settings,
)
from app.security.auth import get_current_user
from app.security.permissions import ensure_tenant_admin
from app.security.tenant import ensure_tenant

router = APIRouter(
    prefix="/api/enterprise/org",
    tags=["enterprise:org"],
    dependencies=[Depends(get_current_user)],
)

SessionDep = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _raise(error: OrgError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.as_http_detail()) from error


def _guard(db: Session, tenant_id: str, current_user: User) -> None:
    ensure_tenant_admin(tenant_id, current_user)
    ensure_tenant(db, tenant_id)


@router.get("/settings", response_model=OrgSettingsRead)
def get_org_settings(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
) -> OrgSettingsRead:
    _guard(db, tenant_id, current_user)
    try:
        return read_settings(db, tenant_id)
    except OrgError as error:
        _raise(error)
        raise


@router.patch("/settings", response_model=OrgSettingsRead)
def patch_org_settings(
    request: OrgSettingsUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> OrgSettingsRead:
    _guard(db, request.tenant_id, current_user)
    try:
        return update_settings(db, request.tenant_id, request)
    except OrgError as error:
        _raise(error)
        raise


@router.get("/departments", response_model=DepartmentListResponse)
def get_departments(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
) -> DepartmentListResponse:
    _guard(db, tenant_id, current_user)
    try:
        return list_departments(db, tenant_id)
    except OrgError as error:
        _raise(error)
        raise


@router.post("/departments", response_model=DepartmentRead)
def post_department(
    request: DepartmentWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> DepartmentRead:
    _guard(db, request.tenant_id, current_user)
    try:
        return create_department(db, request.tenant_id, request)
    except OrgError as error:
        _raise(error)
        raise


@router.patch("/departments/{department_id}", response_model=DepartmentRead)
def patch_department(
    department_id: str,
    request: DepartmentWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> DepartmentRead:
    _guard(db, request.tenant_id, current_user)
    try:
        return update_department(db, request.tenant_id, department_id, request)
    except OrgError as error:
        _raise(error)
        raise


@router.delete("/departments/{department_id}")
def remove_department(
    department_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
) -> dict[str, str]:
    _guard(db, tenant_id, current_user)
    try:
        delete_department(db, tenant_id, department_id)
        return {"status": "deleted"}
    except OrgError as error:
        _raise(error)
        raise


@router.get("/departments/{department_id}/people", response_model=DepartmentPeopleResponse)
def get_department_people(
    department_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
    as_of: str | None = Query(None),
) -> DepartmentPeopleResponse:
    _guard(db, tenant_id, current_user)
    try:
        return department_people(db, tenant_id, department_id, as_of)
    except OrgError as error:
        _raise(error)
        raise


@router.get("/contacts", response_model=OrgContactListResponse)
def get_org_contacts(
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
) -> OrgContactListResponse:
    _guard(db, tenant_id, current_user)
    try:
        return list_org_contacts(db, tenant_id)
    except OrgError as error:
        _raise(error)
        raise


@router.post("/affiliations", response_model=OrgPersonRead)
def post_affiliation(
    request: AffiliationWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> OrgPersonRead:
    _guard(db, request.tenant_id, current_user)
    try:
        return create_affiliation(db, request.tenant_id, request)
    except OrgError as error:
        _raise(error)
        raise


@router.patch("/affiliations/{affiliation_id}", response_model=OrgPersonRead)
def patch_affiliation(
    affiliation_id: str,
    request: TenureUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> OrgPersonRead:
    _guard(db, request.tenant_id, current_user)
    try:
        return update_affiliation(db, request.tenant_id, affiliation_id, request)
    except OrgError as error:
        _raise(error)
        raise


@router.delete("/affiliations/{affiliation_id}")
def remove_affiliation(
    affiliation_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
) -> dict[str, str]:
    _guard(db, tenant_id, current_user)
    try:
        delete_affiliation(db, tenant_id, affiliation_id)
        return {"status": "deleted"}
    except OrgError as error:
        _raise(error)
        raise


@router.post("/heads", response_model=OrgHeadRead)
def post_head(
    request: HeadWriteRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> OrgHeadRead:
    _guard(db, request.tenant_id, current_user)
    try:
        return create_head(db, request.tenant_id, request)
    except OrgError as error:
        _raise(error)
        raise


@router.patch("/heads/{head_id}", response_model=OrgHeadRead)
def patch_head(
    head_id: str,
    request: TenureUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> OrgHeadRead:
    _guard(db, request.tenant_id, current_user)
    try:
        return update_head(db, request.tenant_id, head_id, request)
    except OrgError as error:
        _raise(error)
        raise


@router.delete("/heads/{head_id}")
def remove_head(
    head_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    tenant_id: str = Query(...),
) -> dict[str, str]:
    _guard(db, tenant_id, current_user)
    try:
        delete_head(db, tenant_id, head_id)
        return {"status": "deleted"}
    except OrgError as error:
        _raise(error)
        raise

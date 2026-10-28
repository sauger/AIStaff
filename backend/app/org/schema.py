from __future__ import annotations

from pydantic import BaseModel, Field


class OrgSettingsRead(BaseModel):
    tenant_id: str
    enterprise_name: str


class OrgSettingsUpdateRequest(BaseModel):
    tenant_id: str
    enterprise_name: str = ""


class DepartmentWriteRequest(BaseModel):
    tenant_id: str
    name: str = ""
    parent_id: str | None = None
    sort_order: int = 0


class DepartmentRead(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    path: str
    sort_order: int = 0
    created_at: str
    updated_at: str


class DepartmentListResponse(BaseModel):
    departments: list[DepartmentRead] = Field(default_factory=list)


class OrgContactOption(BaseModel):
    id: str
    name: str
    agent_id: str
    agent_name: str
    title: str | None = None


class OrgContactListResponse(BaseModel):
    contacts: list[OrgContactOption] = Field(default_factory=list)


class AffiliationWriteRequest(BaseModel):
    tenant_id: str
    department_id: str
    contact_id: str
    valid_from: str | None = None
    valid_to: str | None = None


class TenureUpdateRequest(BaseModel):
    tenant_id: str
    contact_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    clear_valid_to: bool = False


class OrgPersonRead(BaseModel):
    affiliation_id: str
    contact_id: str
    contact_name: str
    agent_id: str
    agent_name: str
    title: str | None = None
    valid_from: str
    valid_to: str | None = None


class HeadWriteRequest(BaseModel):
    tenant_id: str
    department_id: str
    contact_id: str
    valid_from: str | None = None
    valid_to: str | None = None


class OrgHeadRead(BaseModel):
    head_id: str
    department_id: str
    contact_id: str
    contact_name: str
    agent_id: str
    agent_name: str
    valid_from: str
    valid_to: str | None = None


class DepartmentPeopleResponse(BaseModel):
    department_id: str
    as_of: str
    people: list[OrgPersonRead] = Field(default_factory=list)
    affiliations: list[OrgPersonRead] = Field(default_factory=list)
    heads: list[OrgHeadRead] = Field(default_factory=list)
    current_head: OrgHeadRead | None = None

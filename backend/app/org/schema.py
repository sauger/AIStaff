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


class EnterprisePersonWriteRequest(BaseModel):
    tenant_id: str
    name: str = ""
    employee_no: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str = "active"
    primary_user_id: str | None = None


class EnterprisePersonRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    employee_no: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str
    primary_user_id: str | None = None
    primary_user_name: str | None = None
    primary_username: str | None = None
    source: str = "manual"
    external_id: str | None = None
    created_at: str
    updated_at: str


class EnterprisePersonListResponse(BaseModel):
    people: list[EnterprisePersonRead] = Field(default_factory=list)


class AffiliationWriteRequest(BaseModel):
    tenant_id: str
    department_id: str
    person_id: str
    position_title: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


class TenureUpdateRequest(BaseModel):
    """区间收尾/改岗位用。

    不携带 department_id：跨部门移动走专用 move 端点，由"结束旧任职
    +新建新任职"保留原部门历史，不允许原地改写部门。
    """

    tenant_id: str
    person_id: str | None = None
    position_title: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    clear_valid_to: bool = False


class AffiliationMoveRequest(BaseModel):
    tenant_id: str
    department_id: str
    position_title: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


class OrgPersonRead(BaseModel):
    affiliation_id: str
    person_id: str
    person_name: str
    department_id: str
    position_title: str | None = None
    primary_user_id: str | None = None
    valid_from: str
    valid_to: str | None = None


class HeadWriteRequest(BaseModel):
    tenant_id: str
    department_id: str
    person_id: str
    valid_from: str | None = None
    valid_to: str | None = None


class OrgHeadRead(BaseModel):
    head_id: str
    department_id: str
    person_id: str
    person_name: str
    valid_from: str
    valid_to: str | None = None


class OrgAgentMemberRead(BaseModel):
    agent_id: str
    name: str
    status: str
    primary_owner_user_id: str | None = None
    primary_owner_name: str | None = None
    department_id: str
    is_primary: bool = False
    assignment_role: str = "service_department"


class DepartmentPeopleResponse(BaseModel):
    department_id: str
    as_of: str
    people: list[OrgPersonRead] = Field(default_factory=list)
    affiliations: list[OrgPersonRead] = Field(default_factory=list)
    heads: list[OrgHeadRead] = Field(default_factory=list)
    current_head: OrgHeadRead | None = None
    agents: list[OrgAgentMemberRead] = Field(default_factory=list)


class AgentDepartmentsUpdateRequest(BaseModel):
    tenant_id: str
    department_ids: list[str] = Field(default_factory=list)
    primary_department_id: str | None = None


class OrgAgentDepartmentRead(BaseModel):
    department_id: str
    department_name: str
    is_primary: bool = False
    assignment_role: str = "service_department"


class OrgAgentRead(BaseModel):
    agent_id: str
    name: str
    description: str | None = None
    status: str
    primary_owner_user_id: str | None = None
    primary_owner_name: str | None = None
    departments: list[OrgAgentDepartmentRead] = Field(default_factory=list)


class OrgAgentListResponse(BaseModel):
    agents: list[OrgAgentRead] = Field(default_factory=list)


class OrgTreePersonRead(BaseModel):
    affiliation_id: str
    person_id: str
    name: str
    department_id: str
    position_title: str | None = None
    valid_from: str
    valid_to: str | None = None


class OrgTreeAgentRead(BaseModel):
    agent_id: str
    name: str
    department_id: str
    status: str
    is_primary: bool = False
    assignment_role: str = "service_department"
    primary_owner_name: str | None = None


class OrgTreeResponse(BaseModel):
    departments: list[DepartmentRead] = Field(default_factory=list)
    people: list[OrgTreePersonRead] = Field(default_factory=list)
    agents: list[OrgTreeAgentRead] = Field(default_factory=list)

"""把组织读接口收成成员可见的通讯录。

管理员仍走 service 的完整结果。成员能读同租户花名册，但拿不到数字员工
部门分配、负责人履历，以及人员与登录账号的关联。
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import EnterprisePerson
from app.org.schema import (
    DepartmentPeopleDirectoryResponse,
    DepartmentPeopleResponse,
    EnterprisePersonDirectoryListResponse,
    EnterprisePersonDirectoryRead,
    EnterprisePersonListResponse,
    EnterprisePersonRead,
    OrgPersonDirectoryRead,
    OrgPersonRead,
    OrgTreeResponse,
)

_ACCOUNT_LINK_FIELDS = {
    "primary_user_id",
    "primary_user_name",
    "primary_username",
    "wecom_userid",
}


def project_org_tree_for_member(tree: OrgTreeResponse) -> OrgTreeResponse:
    return tree.model_copy(update={"agents": []})


def project_people_for_member(
    payload: EnterprisePersonListResponse,
) -> EnterprisePersonDirectoryListResponse:
    return EnterprisePersonDirectoryListResponse(
        people=[_directory_person(row) for row in payload.people if row.status == "active"]
    )


def project_department_people_for_member(
    db: Session,
    tenant_id: str,
    payload: DepartmentPeopleResponse,
) -> DepartmentPeopleDirectoryResponse:
    active_ids = {
        row.id
        for row in db.exec(
            select(EnterprisePerson).where(
                EnterprisePerson.tenant_id == tenant_id,
                EnterprisePerson.status == "active",
            )
        ).all()
    }
    return DepartmentPeopleDirectoryResponse(
        department_id=payload.department_id,
        as_of=payload.as_of,
        people=[
            _directory_affiliation(row)
            for row in payload.people
            if row.person_id in active_ids
        ],
    )


def _directory_person(row: EnterprisePersonRead) -> EnterprisePersonDirectoryRead:
    return EnterprisePersonDirectoryRead.model_validate(
        row.model_dump(exclude=_ACCOUNT_LINK_FIELDS)
    )


def _directory_affiliation(row: OrgPersonRead) -> OrgPersonDirectoryRead:
    return OrgPersonDirectoryRead.model_validate(row.model_dump(exclude={"primary_user_id"}))

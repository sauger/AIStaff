from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import (
    ORG_ROOT_PARENT_ID,
    AgentDepartmentAssignment,
    AgentProfile,
    EnterprisePerson,
    OrgAffiliation,
    OrgDepartment,
    OrgDepartmentHead,
    Tenant,
    User,
    new_id,
    utc_now,
)
from app.org.errors import OrgError
from app.org.schema import (
    AffiliationMoveRequest,
    AffiliationWriteRequest,
    AgentDepartmentsUpdateRequest,
    DepartmentListResponse,
    DepartmentPeopleResponse,
    DepartmentRead,
    DepartmentWriteRequest,
    EnterprisePersonListResponse,
    EnterprisePersonRead,
    EnterprisePersonWriteRequest,
    HeadWriteRequest,
    OrgAgentDepartmentRead,
    OrgAgentListResponse,
    OrgAgentMemberRead,
    OrgAgentRead,
    OrgHeadRead,
    OrgPersonRead,
    OrgSettingsRead,
    OrgSettingsUpdateRequest,
    OrgTreeAgentRead,
    OrgTreePersonRead,
    OrgTreeResponse,
    TenureUpdateRequest,
)

_NAME_LIMIT = 80
_ENTERPRISE_NAME_LIMIT = 120
_PERSON_STATUSES = {"active", "left"}
# Same default calendar as scheduled tasks. Tenure is a date, inclusive on both ends.
_ORG_CALENDAR = ZoneInfo("Asia/Shanghai")


def read_settings(db: Session, tenant_id: str) -> OrgSettingsRead:
    tenant = _require_tenant(db, tenant_id)
    return OrgSettingsRead(tenant_id=tenant.id, enterprise_name=tenant.name)


def update_settings(
    db: Session, tenant_id: str, request: OrgSettingsUpdateRequest
) -> OrgSettingsRead:
    tenant = _require_tenant(db, tenant_id)
    tenant.name = _require_enterprise_name(request.enterprise_name)
    tenant.updated_at = utc_now()
    db.add(tenant)
    _commit(db)
    db.refresh(tenant)
    return OrgSettingsRead(tenant_id=tenant.id, enterprise_name=tenant.name)


def list_departments(db: Session, tenant_id: str) -> DepartmentListResponse:
    _require_tenant(db, tenant_id)
    rows = db.exec(
        select(OrgDepartment)
        .where(OrgDepartment.tenant_id == tenant_id)
        .order_by(OrgDepartment.sort_order, OrgDepartment.name, OrgDepartment.id)
    ).all()
    return DepartmentListResponse(departments=[department_read(row) for row in rows])


def create_department(
    db: Session, tenant_id: str, request: DepartmentWriteRequest
) -> DepartmentRead:
    _sqlite_write_lock(db)
    _require_tenant(db, tenant_id)
    name = _require_department_name(request.name)
    parent = _optional_parent(db, tenant_id, request.parent_id)
    parent_id = _parent_key(parent)
    _ensure_sibling_name(db, tenant_id, parent_id, name)
    department_id = new_id("dept")
    row = OrgDepartment(
        id=department_id,
        tenant_id=tenant_id,
        name=name,
        parent_id=parent_id,
        path=_path_for(parent, department_id),
        sort_order=_sort_order(request.sort_order),
    )
    db.add(row)
    _commit(db)
    db.refresh(row)
    return department_read(row)


def update_department(
    db: Session, tenant_id: str, department_id: str, request: DepartmentWriteRequest
) -> DepartmentRead:
    _sqlite_write_lock(db)
    row = _require_department(db, tenant_id, department_id)
    name = _require_department_name(request.name)
    parent = _optional_parent(db, tenant_id, request.parent_id)
    parent_id = _parent_key(parent)
    _ensure_sibling_name(db, tenant_id, parent_id, name, exclude_id=row.id)
    row, parent = _reload_move(db, row, parent)
    row.name = name
    row.sort_order = _sort_order(request.sort_order)
    if row.parent_id != parent_id:
        row.parent_id = parent_id
        _rewrite_path(db, row, _path_for(parent, row.id))
    row.updated_at = utc_now()
    db.add(row)
    _commit(db)
    db.refresh(row)
    return department_read(row)


def delete_department(db: Session, tenant_id: str, department_id: str) -> None:
    _sqlite_write_lock(db)
    row = _require_department(db, tenant_id, department_id)
    db.refresh(row)
    child = db.exec(
        select(OrgDepartment).where(
            OrgDepartment.tenant_id == tenant_id,
            OrgDepartment.parent_id == row.id,
        )
    ).first()
    if child is not None:
        raise OrgError(
            "ORG_DEPARTMENT_HAS_CHILDREN",
            "该部门还有下级，请先删除或移走下级部门。",
            status_code=409,
        )
    affiliations = _department_affiliations(db, tenant_id, row.id)
    if affiliations:
        _raise_department_occupied(affiliations, "人员任职", "ORG_DEPARTMENT_HAS_PEOPLE")
    heads = _department_heads(db, tenant_id, row.id)
    if heads:
        _raise_department_occupied(heads, "负责人记录", "ORG_DEPARTMENT_HAS_HEADS")
    if _department_assignments(db, tenant_id, row.id):
        raise OrgError("ORG_DEPARTMENT_HAS_AGENTS", "该部门还有数字员工，请先移出数字员工。", status_code=409)
    db.delete(row)
    _commit(db)


# ---------- 企业人员主档 ----------


def list_people(db: Session, tenant_id: str) -> EnterprisePersonListResponse:
    _require_tenant(db, tenant_id)
    rows = db.exec(
        select(EnterprisePerson)
        .where(EnterprisePerson.tenant_id == tenant_id)
        .order_by(EnterprisePerson.status, EnterprisePerson.name, EnterprisePerson.id)
    ).all()
    return EnterprisePersonListResponse(people=_people_reads(db, tenant_id, rows))


def create_person(
    db: Session, tenant_id: str, request: EnterprisePersonWriteRequest
) -> EnterprisePersonRead:
    _require_tenant(db, tenant_id)
    name = _require_person_name(request.name)
    status = _require_person_status(request.status)
    primary_user_id = _optional_user_link(db, tenant_id, request.primary_user_id)
    row = EnterprisePerson(
        tenant_id=tenant_id,
        name=name,
        employee_no=_optional_text(request.employee_no, 80),
        phone=_optional_text(request.phone, 120),
        email=_optional_text(request.email, 120),
        status=status,
        primary_user_id=primary_user_id,
    )
    db.add(row)
    _commit(db)
    db.refresh(row)
    return _people_reads(db, tenant_id, [row])[0]


def update_person(
    db: Session,
    tenant_id: str,
    person_id: str,
    request: EnterprisePersonWriteRequest,
) -> EnterprisePersonRead:
    row = _require_person(db, tenant_id, person_id)
    row.name = _require_person_name(request.name)
    row.employee_no = _optional_text(request.employee_no, 80)
    row.phone = _optional_text(request.phone, 120)
    row.email = _optional_text(request.email, 120)
    row.status = _require_person_status(request.status)
    row.primary_user_id = _optional_user_link(db, tenant_id, request.primary_user_id, exclude_id=row.id)
    row.updated_at = utc_now()
    db.add(row)
    _commit(db)
    db.refresh(row)
    return _people_reads(db, tenant_id, [row])[0]


def delete_person(db: Session, tenant_id: str, person_id: str) -> None:
    row = _require_person(db, tenant_id, person_id)
    for item in db.exec(
        select(OrgAffiliation).where(
            OrgAffiliation.tenant_id == tenant_id,
            OrgAffiliation.person_id == person_id,
        )
    ).all():
        db.delete(item)
    for item in db.exec(
        select(OrgDepartmentHead).where(
            OrgDepartmentHead.tenant_id == tenant_id,
            OrgDepartmentHead.person_id == person_id,
        )
    ).all():
        db.delete(item)
    db.delete(row)
    _commit(db)


# ---------- 部门成员 / 负责人 ----------


def department_people(
    db: Session, tenant_id: str, department_id: str, as_of: str | None
) -> DepartmentPeopleResponse:
    department = _require_department(db, tenant_id, department_id)
    day = _parse_as_of(as_of)
    affiliations = _department_affiliations(db, tenant_id, department.id)
    heads = _department_heads(db, tenant_id, department.id)
    people = [row for row in affiliations if _covers(row.valid_from, row.valid_to, day)]
    current = _pick_current_head(heads, day)
    return DepartmentPeopleResponse(
        department_id=department.id,
        as_of=day.isoformat(),
        people=_person_reads(db, tenant_id, people),
        affiliations=_person_reads(db, tenant_id, affiliations),
        heads=_head_reads(db, tenant_id, heads),
        current_head=_head_read(db, tenant_id, current) if current is not None else None,
        agents=_department_agents(db, tenant_id, department.id),
    )


def create_affiliation(
    db: Session, tenant_id: str, request: AffiliationWriteRequest
) -> OrgPersonRead:
    _require_department(db, tenant_id, request.department_id)
    person = _require_person(db, tenant_id, request.person_id)
    _require_active_person(person)
    valid_from, valid_to = _tenure_from_write(request.valid_from, request.valid_to)
    _ensure_no_overlap(db, tenant_id, request.department_id, person.id, valid_from, valid_to)
    row = OrgAffiliation(
        tenant_id=tenant_id,
        department_id=request.department_id,
        person_id=person.id,
        position_title=_optional_text(request.position_title, 120),
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(row)
    _commit(db)
    db.refresh(row)
    return _person_reads(db, tenant_id, [row])[0]


def update_affiliation(
    db: Session, tenant_id: str, affiliation_id: str, request: TenureUpdateRequest
) -> OrgPersonRead:
    row = db.get(OrgAffiliation, affiliation_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_AFFILIATION_NOT_FOUND", "任职不存在。", status_code=404)
    if request.person_id is not None and str(request.person_id).strip():
        person = _require_person(db, tenant_id, request.person_id)
        # 只在换人时校验在职：收尾离职人员任职(补 valid_to)合法。
        if person.id != row.person_id:
            _require_active_person(person)
        row.person_id = person.id
    if request.position_title is not None:
        row.position_title = _optional_text(request.position_title, 120)
    _apply_tenure(row, request)
    row.updated_at = utc_now()
    db.add(row)
    _commit(db)
    db.refresh(row)
    return _person_reads(db, tenant_id, [row])[0]


def move_affiliation(
    db: Session, tenant_id: str, affiliation_id: str, request: AffiliationMoveRequest
) -> OrgPersonRead:
    """跨部门移动：结束原部门任职并在目标部门新建任职。

    旧任职 valid_to 置为新任职前一天。原地改写 department_id 会抹掉
    原部门的任职历史，不做。移动生效日当天(前一天早于原 valid_from)
    等价于任职未发生，直接删除原记录。
    """
    _sqlite_write_lock(db)
    row = db.get(OrgAffiliation, affiliation_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_AFFILIATION_NOT_FOUND", "任职不存在。", status_code=404)
    department_id = str(request.department_id or "").strip()
    if not department_id:
        raise OrgError("ORG_DEPARTMENT_REQUIRED", "请选择目标部门。", status_code=400)
    department = _require_department(db, tenant_id, department_id)
    if department.id == row.department_id:
        raise OrgError(
            "ORG_AFFILIATION_SAME_DEPARTMENT",
            "该任职已在此部门，无需移动。",
            status_code=400,
        )
    person = _require_person(db, tenant_id, row.person_id)
    if (request.valid_from or "").strip():
        valid_from = _parse_required_date(request.valid_from, "任职开始")
    else:
        # 未传生效日时沿用原任职开始日，保持区间语义连续。
        valid_from = _as_date(row.valid_from) or _today()
    valid_to = (
        _parse_required_date(request.valid_to, "任职结束")
        if (request.valid_to or "").strip()
        else None
    )
    _ensure_range(valid_from, valid_to)
    _ensure_no_overlap(
        db, tenant_id, department.id, person.id, valid_from, valid_to, exclude_id=row.id
    )
    end_of_old = valid_from - timedelta(days=1)
    if end_of_old < (_as_date(row.valid_from) or valid_from):
        db.delete(row)
    else:
        row.valid_to = end_of_old
        row.updated_at = utc_now()
        db.add(row)
    new_row = OrgAffiliation(
        tenant_id=tenant_id,
        department_id=department.id,
        person_id=person.id,
        position_title=_optional_text(request.position_title, 120),
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(new_row)
    _commit(db)
    db.refresh(new_row)
    return _person_reads(db, tenant_id, [new_row])[0]


def delete_affiliation(db: Session, tenant_id: str, affiliation_id: str) -> None:
    row = db.get(OrgAffiliation, affiliation_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_AFFILIATION_NOT_FOUND", "任职不存在。", status_code=404)
    db.delete(row)
    _commit(db)


def create_head(db: Session, tenant_id: str, request: HeadWriteRequest) -> OrgHeadRead:
    _require_department(db, tenant_id, request.department_id)
    person = _require_person(db, tenant_id, request.person_id)
    _require_active_person(person)
    valid_from, valid_to = _tenure_from_write(request.valid_from, request.valid_to)
    row = OrgDepartmentHead(
        tenant_id=tenant_id,
        department_id=request.department_id,
        person_id=person.id,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(row)
    _commit(db)
    db.refresh(row)
    read = _head_read(db, tenant_id, row)
    if read is None:
        raise OrgError("ORG_PERSON_NOT_FOUND", "人员不存在。", status_code=404)
    return read


def update_head(
    db: Session, tenant_id: str, head_id: str, request: TenureUpdateRequest
) -> OrgHeadRead:
    row = db.get(OrgDepartmentHead, head_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_HEAD_NOT_FOUND", "负责人记录不存在。", status_code=404)
    if request.person_id is not None and str(request.person_id).strip():
        person = _require_person(db, tenant_id, request.person_id)
        # 只在换人时校验在职：收尾离职负责人记录(补 valid_to)合法。
        if person.id != row.person_id:
            _require_active_person(person)
        row.person_id = person.id
    _apply_tenure(row, request)
    row.updated_at = utc_now()
    db.add(row)
    _commit(db)
    db.refresh(row)
    read = _head_read(db, tenant_id, row)
    if read is None:
        raise OrgError("ORG_PERSON_NOT_FOUND", "人员不存在。", status_code=404)
    return read


def delete_head(db: Session, tenant_id: str, head_id: str) -> None:
    row = db.get(OrgDepartmentHead, head_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_HEAD_NOT_FOUND", "负责人记录不存在。", status_code=404)
    db.delete(row)
    _commit(db)


# ---------- 数字员工组织归属 ----------


def assign_agent_departments(
    db: Session, tenant_id: str, agent_id: str, request: AgentDepartmentsUpdateRequest
) -> OrgAgentRead:
    agent = _require_company_agent(db, tenant_id, agent_id)
    department_ids = list(dict.fromkeys(str(item).strip() for item in request.department_ids if str(item).strip()))
    for department_id in department_ids:
        _require_department(db, tenant_id, department_id)
    primary_id = str(request.primary_department_id or "").strip() or None
    if primary_id is not None and primary_id not in department_ids:
        raise OrgError("ORG_AGENT_PRIMARY_DEPARTMENT", "主责部门必须在所选部门中。", status_code=400)
    if department_ids and primary_id is None:
        raise OrgError("ORG_AGENT_PRIMARY_DEPARTMENT_REQUIRED", "请为数字员工选择主责部门。", status_code=400)

    for row in _agent_assignments(db, tenant_id, agent.id):
        db.delete(row)
    for department_id in department_ids:
        is_primary = department_id == primary_id
        db.add(
            AgentDepartmentAssignment(
                tenant_id=tenant_id,
                agent_id=agent.id,
                department_id=department_id,
                is_primary=is_primary,
                assignment_role="owner_department" if is_primary else "service_department",
            )
        )
    _commit(db)
    return _agent_reads(db, tenant_id, [agent])[0]


def list_org_agents(db: Session, tenant_id: str) -> OrgAgentListResponse:
    _require_tenant(db, tenant_id)
    rows = db.exec(
        select(AgentProfile)
        .where(
            AgentProfile.tenant_id == tenant_id,
            AgentProfile.is_overall == False,  # noqa: E712
        )
        .order_by(AgentProfile.status, AgentProfile.name)
    ).all()
    rows = [row for row in rows if not _agent_hidden(row)]
    return OrgAgentListResponse(agents=_agent_reads(db, tenant_id, rows))


def org_tree(db: Session, tenant_id: str) -> OrgTreeResponse:
    _require_tenant(db, tenant_id)
    departments = db.exec(
        select(OrgDepartment)
        .where(OrgDepartment.tenant_id == tenant_id)
        .order_by(OrgDepartment.sort_order, OrgDepartment.name, OrgDepartment.id)
    ).all()
    today = _today()
    affiliations = [
        row
        for row in db.exec(select(OrgAffiliation).where(OrgAffiliation.tenant_id == tenant_id)).all()
        if _covers(row.valid_from, row.valid_to, today)
    ]
    active_person_ids = {
        row.id
        for row in db.exec(
            select(EnterprisePerson).where(
                EnterprisePerson.tenant_id == tenant_id,
                EnterprisePerson.status == "active",
            )
        ).all()
    }
    affiliations = [row for row in affiliations if row.person_id in active_person_ids]
    people = _person_reads(db, tenant_id, affiliations)
    tree_people = [
        OrgTreePersonRead(
            affiliation_id=row.affiliation_id,
            person_id=row.person_id,
            name=row.person_name,
            department_id=row.department_id,
            position_title=row.position_title,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
        )
        for row in people
    ]
    assignments = db.exec(
        select(AgentDepartmentAssignment).where(AgentDepartmentAssignment.tenant_id == tenant_id)
    ).all()
    agent_ids = {row.agent_id for row in assignments}
    agents = {
        row.id: row
        for row in db.exec(
            select(AgentProfile).where(
                AgentProfile.tenant_id == tenant_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
    } if agent_ids else {}
    users = _users_by_id(db, tenant_id, {row.primary_owner_user_id for row in agents.values() if row.primary_owner_user_id})
    tree_agents = []
    for assignment in assignments:
        agent = agents.get(assignment.agent_id)
        if agent is None or _agent_hidden(agent):
            continue
        owner = users.get(agent.primary_owner_user_id or "")
        tree_agents.append(
            OrgTreeAgentRead(
                agent_id=agent.id,
                name=agent.name,
                department_id=assignment.department_id,
                status=agent.status,
                is_primary=assignment.is_primary,
                assignment_role=assignment.assignment_role,
                primary_owner_name=(owner.display_name or owner.username) if owner else _owner_metadata_name(agent),
            )
        )
    return OrgTreeResponse(
        departments=[department_read(row) for row in departments],
        people=tree_people,
        agents=tree_agents,
    )


# ---------- reads ----------


def department_read(row: OrgDepartment) -> DepartmentRead:
    return DepartmentRead(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id or None,
        path=row.path,
        sort_order=row.sort_order,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


# ---------- helpers ----------


def _ensure_no_overlap(
    db: Session,
    tenant_id: str,
    department_id: str,
    person_id: str,
    valid_from: date,
    valid_to: date | None,
    *,
    exclude_id: str | None = None,
) -> None:
    """同人同部门不允许区间重叠任职；不重叠多段历史(调岗前后)合法。"""
    rows = db.exec(
        select(OrgAffiliation).where(
            OrgAffiliation.tenant_id == tenant_id,
            OrgAffiliation.department_id == department_id,
            OrgAffiliation.person_id == person_id,
        )
    ).all()
    for existing in rows:
        if exclude_id is not None and existing.id == exclude_id:
            continue
        existing_from = _as_date(existing.valid_from)
        existing_to = _as_date(existing.valid_to)
        overlaps = (existing_to is None or existing_to >= valid_from) and (
            valid_to is None or existing_from is None or existing_from <= valid_to
        )
        if overlaps:
            raise OrgError(
                "ORG_AFFILIATION_OVERLAP",
                "该人员在此部门已有时间重叠的任职记录。",
                status_code=409,
            )


def _raise_department_occupied(
    rows: list, label: str, code: str
) -> None:
    today = _today()
    current = [row for row in rows if _covers(row.valid_from, row.valid_to, today)]
    if current:
        raise OrgError(
            code,
            f"该部门还有在任{label}，请先移出或结束。",
            status_code=409,
        )
    raise OrgError(
        "ORG_DEPARTMENT_HAS_HISTORY",
        f"该部门存在已结束的历史{label}，请先删除对应记录再删除部门。",
        status_code=409,
    )


def _parent_key(parent: OrgDepartment | None) -> str:
    return parent.id if parent is not None else ORG_ROOT_PARENT_ID


def _sqlite_write_lock(db: Session) -> None:
    """Take SQLite's reserved lock before reading paths or sibling names."""
    connection = db.connection()
    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql("UPDATE org_departments SET sort_order = sort_order WHERE 0")


def _reload_move(
    db: Session, row: OrgDepartment, parent: OrgDepartment | None
) -> tuple[OrgDepartment, OrgDepartment | None]:
    db.refresh(row)
    if parent is not None:
        db.refresh(parent)
        if parent.tenant_id != row.tenant_id:
            raise OrgError("ORG_DEPARTMENT_NOT_FOUND", "部门不存在。", status_code=404)
    _ensure_not_cycle(row, parent)
    return row, parent


def _path_for(parent: OrgDepartment | None, department_id: str) -> str:
    if parent is None:
        return f"/{department_id}"
    return f"{parent.path}/{department_id}"


def _rewrite_path(db: Session, row: OrgDepartment, new_path: str) -> None:
    old_path = row.path
    if old_path == new_path:
        row.path = new_path
        return
    prefix = old_path + "/"
    descendants = [
        item
        for item in db.exec(
            select(OrgDepartment).where(OrgDepartment.tenant_id == row.tenant_id)
        ).all()
        if item.path.startswith(prefix)
    ]
    row.path = new_path
    for child in descendants:
        suffix = child.path[len(old_path) :]
        child.path = f"{new_path}{suffix}"
        child.updated_at = utc_now()
        db.add(child)


def _ensure_not_cycle(row: OrgDepartment, parent: OrgDepartment | None) -> None:
    if parent is None:
        return
    if parent.id == row.id or parent.path == row.path or parent.path.startswith(row.path + "/"):
        raise OrgError(
            "ORG_DEPARTMENT_CYCLE",
            "不能把部门移到自己或它的下级下面。",
            status_code=400,
        )


def _ensure_sibling_name(
    db: Session,
    tenant_id: str,
    parent_id: str,
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    existing = db.exec(
        select(OrgDepartment).where(
            OrgDepartment.tenant_id == tenant_id,
            OrgDepartment.parent_id == parent_id,
            OrgDepartment.name == name,
        )
    ).first()
    if existing is not None and existing.id != exclude_id:
        raise OrgError(
            "ORG_DEPARTMENT_NAME_DUPLICATE",
            "同一上级下已有同名部门。",
            status_code=409,
        )


def _department_affiliations(
    db: Session, tenant_id: str, department_id: str
) -> list[OrgAffiliation]:
    return list(
        db.exec(
            select(OrgAffiliation).where(
                OrgAffiliation.tenant_id == tenant_id,
                OrgAffiliation.department_id == department_id,
            )
        ).all()
    )


def _department_heads(
    db: Session, tenant_id: str, department_id: str
) -> list[OrgDepartmentHead]:
    return list(
        db.exec(
            select(OrgDepartmentHead).where(
                OrgDepartmentHead.tenant_id == tenant_id,
                OrgDepartmentHead.department_id == department_id,
            )
        ).all()
    )


def _department_assignments(
    db: Session, tenant_id: str, department_id: str
) -> list[AgentDepartmentAssignment]:
    return list(
        db.exec(
            select(AgentDepartmentAssignment).where(
                AgentDepartmentAssignment.tenant_id == tenant_id,
                AgentDepartmentAssignment.department_id == department_id,
            )
        ).all()
    )


def _agent_assignments(
    db: Session, tenant_id: str, agent_id: str
) -> list[AgentDepartmentAssignment]:
    return list(
        db.exec(
            select(AgentDepartmentAssignment).where(
                AgentDepartmentAssignment.tenant_id == tenant_id,
                AgentDepartmentAssignment.agent_id == agent_id,
            )
        ).all()
    )


def _department_agents(
    db: Session, tenant_id: str, department_id: str
) -> list[OrgAgentMemberRead]:
    assignments = _department_assignments(db, tenant_id, department_id)
    if not assignments:
        return []
    agents = _agents_by_id(db, tenant_id, {row.agent_id for row in assignments})
    users = _users_by_id(
        db, tenant_id, {row.primary_owner_user_id for row in agents.values() if row.primary_owner_user_id}
    )
    result: list[OrgAgentMemberRead] = []
    for assignment in assignments:
        agent = agents.get(assignment.agent_id)
        if agent is None or _agent_hidden(agent):
            continue
        owner = users.get(agent.primary_owner_user_id or "")
        result.append(
            OrgAgentMemberRead(
                agent_id=agent.id,
                name=agent.name,
                status=agent.status,
                primary_owner_user_id=agent.primary_owner_user_id,
                primary_owner_name=(owner.display_name or owner.username) if owner else _owner_metadata_name(agent),
                department_id=department_id,
                is_primary=assignment.is_primary,
                assignment_role=assignment.assignment_role,
            )
        )
    result.sort(key=lambda item: (not item.is_primary, item.name))
    return result


def _agent_reads(
    db: Session, tenant_id: str, rows: list[AgentProfile]
) -> list[OrgAgentRead]:
    departments = {
        row.id: row
        for row in db.exec(select(OrgDepartment).where(OrgDepartment.tenant_id == tenant_id)).all()
    }
    assignments: dict[str, list[AgentDepartmentAssignment]] = {}
    if rows:
        for item in db.exec(
            select(AgentDepartmentAssignment).where(
                AgentDepartmentAssignment.tenant_id == tenant_id,
                AgentDepartmentAssignment.agent_id.in_([row.id for row in rows]),
            )
        ).all():
            assignments.setdefault(item.agent_id, []).append(item)
    users = _users_by_id(
        db, tenant_id, {row.primary_owner_user_id for row in rows if row.primary_owner_user_id}
    )
    result = []
    for row in rows:
        owner = users.get(row.primary_owner_user_id or "")
        result.append(
            OrgAgentRead(
                agent_id=row.id,
                name=row.name,
                description=row.description,
                status=row.status,
                primary_owner_user_id=row.primary_owner_user_id,
                primary_owner_name=(owner.display_name or owner.username) if owner else _owner_metadata_name(row),
                departments=[
                    OrgAgentDepartmentRead(
                        department_id=item.department_id,
                        department_name=departments[item.department_id].name
                        if item.department_id in departments
                        else item.department_id,
                        is_primary=item.is_primary,
                        assignment_role=item.assignment_role,
                    )
                    for item in sorted(
                        assignments.get(row.id, []),
                        key=lambda item: (not item.is_primary, item.department_id),
                    )
                ],
            )
        )
    return result


def _people_reads(
    db: Session, tenant_id: str, rows: list[EnterprisePerson]
) -> list[EnterprisePersonRead]:
    if not rows:
        return []
    user_ids = {row.primary_user_id for row in rows if row.primary_user_id}
    users = _users_by_id(db, tenant_id, user_ids)
    return [
        _person_read_with_users(row, users.get(row.primary_user_id or "")) for row in rows
    ]


def _person_read_with_users(
    row: EnterprisePerson, user: User | None
) -> EnterprisePersonRead:
    return EnterprisePersonRead(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        employee_no=row.employee_no,
        phone=row.phone,
        email=row.email,
        status=row.status,
        primary_user_id=row.primary_user_id,
        primary_user_name=(user.display_name or user.username) if user else None,
        primary_username=user.username if user else None,
        source=row.source,
        external_id=row.external_id,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _person_reads(
    db: Session, tenant_id: str, rows: list[OrgAffiliation]
) -> list[OrgPersonRead]:
    if not rows:
        return []
    people = {
        row.id: row
        for row in db.exec(
            select(EnterprisePerson).where(
                EnterprisePerson.tenant_id == tenant_id,
                EnterprisePerson.id.in_({row.person_id for row in rows}),
            )
        ).all()
    }
    result: list[OrgPersonRead] = []
    for row in rows:
        person = people.get(row.person_id)
        if person is None:
            continue
        start = _as_date(row.valid_from)
        result.append(
            OrgPersonRead(
                affiliation_id=row.id,
                person_id=person.id,
                person_name=person.name,
                department_id=row.department_id,
                position_title=row.position_title,
                primary_user_id=person.primary_user_id,
                valid_from=start.isoformat() if start else "",
                valid_to=_date_out(row.valid_to),
            )
        )
    result.sort(key=lambda item: (item.person_name, item.valid_from, item.affiliation_id))
    return result


def _head_reads(db: Session, tenant_id: str, rows: list[OrgDepartmentHead]) -> list[OrgHeadRead]:
    reads = [item for row in rows if (item := _head_read(db, tenant_id, row)) is not None]
    reads.sort(key=lambda item: (item.valid_from, item.head_id))
    return reads


def _head_read(db: Session, tenant_id: str, row: OrgDepartmentHead | None) -> OrgHeadRead | None:
    if row is None:
        return None
    person = db.get(EnterprisePerson, row.person_id)
    if person is None or person.tenant_id != tenant_id:
        return None
    start = _as_date(row.valid_from)
    return OrgHeadRead(
        head_id=row.id,
        department_id=row.department_id,
        person_id=person.id,
        person_name=person.name,
        valid_from=start.isoformat() if start else "",
        valid_to=_date_out(row.valid_to),
    )


def _pick_current_head(rows: list[OrgDepartmentHead], day: date) -> OrgDepartmentHead | None:
    matching = [row for row in rows if _covers(row.valid_from, row.valid_to, day)]
    if not matching:
        return None
    matching.sort(key=lambda row: (_as_date(row.valid_from) or day, row.created_at, row.id))
    return matching[-1]


def _covers(
    valid_from: date | datetime | str | None,
    valid_to: date | datetime | str | None,
    day: date,
) -> bool:
    start = _as_date(valid_from)
    end = _as_date(valid_to)
    if start is None or start > day:
        return False
    return end is None or end >= day


def _agents_by_id(db: Session, tenant_id: str, agent_ids: set[str]) -> dict[str, AgentProfile]:
    if not agent_ids:
        return {}
    return {
        row.id: row
        for row in db.exec(
            select(AgentProfile).where(
                AgentProfile.tenant_id == tenant_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
    }


def _users_by_id(db: Session, tenant_id: str, user_ids: set[str]) -> dict[str, User]:
    if not user_ids:
        return {}
    return {
        row.id: row
        for row in db.exec(
            select(User).where(User.tenant_id == tenant_id, User.id.in_(user_ids))
        ).all()
    }


def _owner_metadata_name(agent: AgentProfile) -> str | None:
    metadata = agent.metadata_json or {}
    value = metadata.get("owner_display_name") or metadata.get("owner_username")
    return str(value).strip() if value else None


def _agent_hidden(agent: AgentProfile) -> bool:
    return (agent.metadata_json or {}).get("hidden_from_staffdeck") is True


def _apply_tenure(row: OrgAffiliation | OrgDepartmentHead, request: TenureUpdateRequest) -> None:
    if request.valid_from is not None and str(request.valid_from).strip():
        valid_from = _parse_required_date(request.valid_from, "任职开始")
    else:
        valid_from = _as_date(row.valid_from)
    if request.clear_valid_to:
        valid_to = None
    elif request.valid_to is not None and str(request.valid_to).strip():
        valid_to = _parse_required_date(request.valid_to, "任职结束")
    else:
        valid_to = _as_date(row.valid_to)
    if valid_from is None:
        raise OrgError(
            "ORG_DATE_INVALID",
            "任职开始格式应为 YYYY-MM-DD。",
            status_code=400,
        )
    _ensure_range(valid_from, valid_to)
    row.valid_from = valid_from
    row.valid_to = valid_to


def _tenure_from_write(
    valid_from: str | None, valid_to: str | None
) -> tuple[date, date | None]:
    start = (
        _parse_required_date(valid_from, "任职开始")
        if (valid_from or "").strip()
        else _today()
    )
    end = (
        _parse_required_date(valid_to, "任职结束")
        if (valid_to or "").strip()
        else None
    )
    _ensure_range(start, end)
    return start, end


def _ensure_range(start: date, end: date | None) -> None:
    if end is not None and end < start:
        raise OrgError("ORG_DATE_RANGE", "任职结束不能早于任职开始。", status_code=400)


def _parse_as_of(value: str | None) -> date:
    if not (value or "").strip():
        return _today()
    return _parse_required_date(value, "查询日期")


def _parse_required_date(value: str | None, label: str) -> date:
    text = (value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise OrgError(
            "ORG_DATE_INVALID",
            f"{label}格式应为 YYYY-MM-DD。",
            status_code=400,
        ) from error


def _today() -> date:
    return datetime.now(_ORG_CALENDAR).date()


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _date_out(value: date | datetime | str | None) -> str | None:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else None


def _require_tenant(db: Session, tenant_id: str) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise OrgError("ORG_TENANT_NOT_FOUND", "租户不存在。", status_code=404)
    return tenant


def _require_department(db: Session, tenant_id: str, department_id: str) -> OrgDepartment:
    row = db.get(OrgDepartment, department_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_DEPARTMENT_NOT_FOUND", "部门不存在。", status_code=404)
    return row


def _optional_parent(db: Session, tenant_id: str, parent_id: str | None) -> OrgDepartment | None:
    text = str(parent_id or "").strip()
    if not text:
        return None
    return _require_department(db, tenant_id, text)


def _require_person(db: Session, tenant_id: str, person_id: str) -> EnterprisePerson:
    row = db.get(EnterprisePerson, str(person_id or "").strip())
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_PERSON_NOT_FOUND", "人员不存在。", status_code=404)
    return row


def _require_active_person(person: EnterprisePerson) -> None:
    if person.status != "active":
        raise OrgError("ORG_PERSON_LEFT", "该人员已离职，不能新增任职或负责人。", status_code=400)


def _require_company_agent(db: Session, tenant_id: str, agent_id: str) -> AgentProfile:
    row = db.get(AgentProfile, str(agent_id or "").strip())
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_AGENT_NOT_FOUND", "数字员工不存在。", status_code=404)
    if row.is_overall:
        raise OrgError("ORG_AGENT_OVERALL", "整体智能体不能归属部门。", status_code=400)
    if _agent_hidden(row):
        raise OrgError("ORG_AGENT_NOT_FOUND", "数字员工不存在。", status_code=404)
    return row


def _optional_user_link(
    db: Session, tenant_id: str, user_id: str | None, *, exclude_id: str | None = None
) -> str | None:
    text = str(user_id or "").strip()
    if not text:
        return None
    user = db.get(User, text)
    if user is None or user.tenant_id != tenant_id:
        raise OrgError("ORG_USER_NOT_FOUND", "登录账号不存在。", status_code=404)
    if user.source != "web":
        raise OrgError("ORG_USER_EXTERNAL", "只能关联内部登录账号。", status_code=400)
    existing = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant_id,
            EnterprisePerson.primary_user_id == text,
        )
    ).first()
    if existing is not None and existing.id != exclude_id:
        raise OrgError("ORG_USER_ALREADY_LINKED", "该账号已关联其他企业人员。", status_code=409)
    return text


def _require_person_name(value: str | None) -> str:
    name = str(value or "").strip()
    if not name:
        raise OrgError("ORG_PERSON_NAME_REQUIRED", "请填写姓名。", status_code=400)
    if len(name) > _NAME_LIMIT:
        raise OrgError("ORG_PERSON_NAME_TOO_LONG", "姓名过长。", status_code=400)
    return name


def _require_person_status(value: str | None) -> str:
    status = str(value or "").strip() or "active"
    if status not in _PERSON_STATUSES:
        raise OrgError("ORG_PERSON_STATUS_INVALID", "人员状态不合法。", status_code=400)
    return status


def _optional_text(value: str | None, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise OrgError("ORG_TEXT_TOO_LONG", "字段超出长度限制。", status_code=400)
    return text


def _require_department_name(value: str | None) -> str:
    name = str(value or "").strip()
    if not name:
        raise OrgError("ORG_DEPARTMENT_NAME_REQUIRED", "请填写部门名称。", status_code=400)
    if len(name) > _NAME_LIMIT:
        raise OrgError("ORG_DEPARTMENT_NAME_TOO_LONG", "部门名称过长。", status_code=400)
    return name


def _require_enterprise_name(value: str | None) -> str:
    name = str(value or "").strip()
    if not name:
        raise OrgError("ORG_ENTERPRISE_NAME_REQUIRED", "请填写企业名称。", status_code=400)
    if len(name) > _ENTERPRISE_NAME_LIMIT:
        raise OrgError("ORG_ENTERPRISE_NAME_TOO_LONG", "企业名称过长。", status_code=400)
    return name


def _sort_order(value: int | None) -> int:
    if value is None:
        return 0
    return int(value)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise _integrity_error(error) from error


def _integrity_error(error: IntegrityError) -> OrgError:
    text = f"{error} {getattr(error, 'orig', '')}".lower()
    if "enterprise_person" in text and "primary_user" in text:
        return OrgError("ORG_USER_ALREADY_LINKED", "该账号已关联其他企业人员。", status_code=409)
    if "enterprise_person" in text and "external_id" in text:
        return OrgError(
            "ORG_PERSON_EXTERNAL_ID_CONFLICT",
            "该外部来源已存在同名企业人员，请勿重复导入。",
            status_code=409,
        )
    if "org_departments" in text and "name" in text:
        return OrgError(
            "ORG_DEPARTMENT_NAME_DUPLICATE",
            "同一上级下已有同名部门。",
            status_code=409,
        )
    if "agent_department" in text:
        return OrgError("ORG_AGENT_DEPARTMENT_CONFLICT", "数字员工部门归属冲突，请刷新后重试。", status_code=409)
    return OrgError("ORG_CONFLICT", "组织数据冲突，请刷新后重试。", status_code=409)

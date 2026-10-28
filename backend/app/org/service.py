from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import (
    AgentProfile,
    Contact,
    OrgAffiliation,
    OrgDepartment,
    OrgDepartmentHead,
    Tenant,
    new_id,
    utc_now,
)
from app.org.errors import OrgError
from app.org.schema import (
    AffiliationWriteRequest,
    DepartmentListResponse,
    DepartmentPeopleResponse,
    DepartmentRead,
    DepartmentWriteRequest,
    HeadWriteRequest,
    OrgContactListResponse,
    OrgContactOption,
    OrgHeadRead,
    OrgPersonRead,
    OrgSettingsRead,
    OrgSettingsUpdateRequest,
    TenureUpdateRequest,
)

_NAME_LIMIT = 80
_ENTERPRISE_NAME_LIMIT = 120


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
    _require_tenant(db, tenant_id)
    name = _require_department_name(request.name)
    parent = _optional_parent(db, tenant_id, request.parent_id)
    parent_id = parent.id if parent else None
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
    row = _require_department(db, tenant_id, department_id)
    name = _require_department_name(request.name)
    parent = _optional_parent(db, tenant_id, request.parent_id)
    _ensure_not_cycle(row, parent)
    parent_id = parent.id if parent else None
    _ensure_sibling_name(db, tenant_id, parent_id, name, exclude_id=row.id)
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
    row = _require_department(db, tenant_id, department_id)
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
    _delete_department_links(db, tenant_id, row.id)
    db.delete(row)
    _commit(db)


def list_org_contacts(db: Session, tenant_id: str) -> OrgContactListResponse:
    _require_tenant(db, tenant_id)
    rows = db.exec(
        select(Contact).where(Contact.tenant_id == tenant_id).order_by(Contact.name, Contact.id)
    ).all()
    agents = _agents_by_id(db, {row.agent_id for row in rows})
    return OrgContactListResponse(
        contacts=[
            OrgContactOption(
                id=row.id,
                name=row.name,
                agent_id=row.agent_id,
                agent_name=_profile_name(agents.get(row.agent_id), row.agent_id),
                title=row.title,
            )
            for row in rows
        ]
    )


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
        people=_person_reads(db, people),
        affiliations=_person_reads(db, affiliations),
        heads=_head_reads(db, heads),
        current_head=_head_read(db, current) if current is not None else None,
    )


def create_affiliation(
    db: Session, tenant_id: str, request: AffiliationWriteRequest
) -> OrgPersonRead:
    _require_department(db, tenant_id, request.department_id)
    contact = _require_contact(db, tenant_id, request.contact_id)
    valid_from, valid_to = _tenure_from_write(request.valid_from, request.valid_to)
    row = OrgAffiliation(
        tenant_id=tenant_id,
        department_id=request.department_id,
        contact_id=contact.id,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(row)
    _commit(db)
    db.refresh(row)
    people = _person_reads(db, [row])
    return people[0]


def update_affiliation(
    db: Session, tenant_id: str, affiliation_id: str, request: TenureUpdateRequest
) -> OrgPersonRead:
    row = db.get(OrgAffiliation, affiliation_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_AFFILIATION_NOT_FOUND", "任职不存在。", status_code=404)
    if request.contact_id is not None and str(request.contact_id).strip():
        contact = _require_contact(db, tenant_id, request.contact_id)
        row.contact_id = contact.id
    _apply_tenure(row, request)
    row.updated_at = utc_now()
    db.add(row)
    _commit(db)
    db.refresh(row)
    return _person_reads(db, [row])[0]


def delete_affiliation(db: Session, tenant_id: str, affiliation_id: str) -> None:
    row = db.get(OrgAffiliation, affiliation_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_AFFILIATION_NOT_FOUND", "任职不存在。", status_code=404)
    db.delete(row)
    _commit(db)


def create_head(db: Session, tenant_id: str, request: HeadWriteRequest) -> OrgHeadRead:
    _require_department(db, tenant_id, request.department_id)
    contact = _require_contact(db, tenant_id, request.contact_id)
    valid_from, valid_to = _tenure_from_write(request.valid_from, request.valid_to)
    row = OrgDepartmentHead(
        tenant_id=tenant_id,
        department_id=request.department_id,
        contact_id=contact.id,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(row)
    _commit(db)
    db.refresh(row)
    read = _head_read(db, row)
    if read is None:
        raise OrgError("ORG_CONTACT_NOT_FOUND", "联系人不存在。", status_code=404)
    return read


def update_head(
    db: Session, tenant_id: str, head_id: str, request: TenureUpdateRequest
) -> OrgHeadRead:
    row = db.get(OrgDepartmentHead, head_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_HEAD_NOT_FOUND", "负责人记录不存在。", status_code=404)
    if request.contact_id is not None and str(request.contact_id).strip():
        contact = _require_contact(db, tenant_id, request.contact_id)
        row.contact_id = contact.id
    _apply_tenure(row, request)
    row.updated_at = utc_now()
    db.add(row)
    _commit(db)
    db.refresh(row)
    read = _head_read(db, row)
    if read is None:
        raise OrgError("ORG_CONTACT_NOT_FOUND", "联系人不存在。", status_code=404)
    return read


def delete_head(db: Session, tenant_id: str, head_id: str) -> None:
    row = db.get(OrgDepartmentHead, head_id)
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_HEAD_NOT_FOUND", "负责人记录不存在。", status_code=404)
    db.delete(row)
    _commit(db)


def purge_contact_links(db: Session, tenant_id: str, contact_id: str) -> None:
    """Remove org rows that point at one employee contact. Caller commits."""
    affiliations = db.exec(
        select(OrgAffiliation).where(
            OrgAffiliation.tenant_id == tenant_id,
            OrgAffiliation.contact_id == contact_id,
        )
    ).all()
    heads = db.exec(
        select(OrgDepartmentHead).where(
            OrgDepartmentHead.tenant_id == tenant_id,
            OrgDepartmentHead.contact_id == contact_id,
        )
    ).all()
    for item in (*affiliations, *heads):
        db.delete(item)


def department_read(row: OrgDepartment) -> DepartmentRead:
    return DepartmentRead(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id,
        path=row.path,
        sort_order=row.sort_order,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _path_for(parent: OrgDepartment | None, department_id: str) -> str:
    if parent is None:
        return f"/{department_id}"
    return f"{parent.path}/{department_id}"


def _rewrite_path(db: Session, row: OrgDepartment, new_path: str) -> None:
    old_path = row.path
    if old_path == new_path:
        row.path = new_path
        return
    # Python prefix match: department ids contain "_", which LIKE would treat as a wildcard.
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
    parent_id: str | None,
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    statement = select(OrgDepartment).where(
        OrgDepartment.tenant_id == tenant_id,
        OrgDepartment.name == name,
    )
    if parent_id is None:
        statement = statement.where(OrgDepartment.parent_id.is_(None))
    else:
        statement = statement.where(OrgDepartment.parent_id == parent_id)
    existing = db.exec(statement).first()
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


def _delete_department_links(db: Session, tenant_id: str, department_id: str) -> None:
    for item in (
        *_department_affiliations(db, tenant_id, department_id),
        *_department_heads(db, tenant_id, department_id),
    ):
        db.delete(item)


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


def _person_reads(db: Session, rows: list[OrgAffiliation]) -> list[OrgPersonRead]:
    if not rows:
        return []
    contacts = _contacts_by_id(db, {row.contact_id for row in rows})
    agents = _agents_by_id(db, {row.agent_id for row in contacts.values()})
    people: list[OrgPersonRead] = []
    for row in rows:
        contact = contacts.get(row.contact_id)
        if contact is None:
            continue
        agent = agents.get(contact.agent_id)
        start = _as_date(row.valid_from)
        people.append(
            OrgPersonRead(
                affiliation_id=row.id,
                contact_id=contact.id,
                contact_name=contact.name,
                agent_id=contact.agent_id,
                agent_name=agent.name if agent is not None else contact.agent_id,
                title=contact.title,
                valid_from=start.isoformat() if start else "",
                valid_to=_date_out(row.valid_to),
            )
        )
    people.sort(key=lambda item: (item.contact_name, item.valid_from, item.affiliation_id))
    return people


def _head_reads(db: Session, rows: list[OrgDepartmentHead]) -> list[OrgHeadRead]:
    reads = [item for row in rows if (item := _head_read(db, row)) is not None]
    reads.sort(key=lambda item: (item.valid_from, item.head_id))
    return reads


def _head_read(db: Session, row: OrgDepartmentHead | None) -> OrgHeadRead | None:
    if row is None:
        return None
    contacts = _contacts_by_id(db, {row.contact_id})
    contact = contacts.get(row.contact_id)
    if contact is None:
        return None
    agents = _agents_by_id(db, {contact.agent_id})
    agent = agents.get(contact.agent_id)
    start = _as_date(row.valid_from)
    return OrgHeadRead(
        head_id=row.id,
        department_id=row.department_id,
        contact_id=contact.id,
        contact_name=contact.name,
        agent_id=contact.agent_id,
        agent_name=agent.name if agent is not None else contact.agent_id,
        valid_from=start.isoformat() if start else "",
        valid_to=_date_out(row.valid_to),
    )


def _profile_name(profile: AgentProfile | None, fallback: str) -> str:
    return profile.name if profile is not None else fallback


def _contacts_by_id(db: Session, contact_ids: set[str]) -> dict[str, Contact]:
    if not contact_ids:
        return {}
    rows = db.exec(select(Contact).where(Contact.id.in_(contact_ids))).all()
    return {row.id: row for row in rows}


def _agents_by_id(db: Session, agent_ids: set[str]) -> dict[str, AgentProfile]:
    if not agent_ids:
        return {}
    rows = db.exec(select(AgentProfile).where(AgentProfile.id.in_(agent_ids))).all()
    return {row.id: row for row in rows}


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
    return datetime.now(UTC).date()


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


def _require_contact(db: Session, tenant_id: str, contact_id: str) -> Contact:
    row = db.get(Contact, str(contact_id or "").strip())
    if row is None or row.tenant_id != tenant_id:
        raise OrgError("ORG_CONTACT_NOT_FOUND", "联系人不存在。", status_code=404)
    return row


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
        raise OrgError(
            "ORG_CONFLICT",
            "组织数据冲突，请刷新后重试。",
            status_code=409,
        ) from error

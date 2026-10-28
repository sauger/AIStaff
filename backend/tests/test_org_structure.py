from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.contacts.api import post_contact, remove_contact
from app.contacts.schema import ContactWriteRequest
from app.db.models import (
    AgentProfile,
    OrgAffiliation,
    OrgDepartment,
    OrgDepartmentHead,
    Tenant,
    User,
)
from app.org.api import (
    get_department_people,
    get_departments,
    get_org_contacts,
    get_org_settings,
    patch_affiliation,
    patch_department,
    patch_head,
    patch_org_settings,
    post_affiliation,
    post_department,
    post_head,
    remove_department,
)
from app.org.schema import (
    AffiliationWriteRequest,
    DepartmentWriteRequest,
    HeadWriteRequest,
    OrgSettingsUpdateRequest,
    TenureUpdateRequest,
)


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session) -> tuple[User, User, AgentProfile, AgentProfile]:
    db.add(Tenant(id="tenant_demo", name="Demo Enterprise"))
    owner = User(
        id="user_owner",
        tenant_id="tenant_demo",
        username="owner",
        display_name="Owner",
        password_hash="x",
    )
    admin = User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="admin",
        display_name="Admin",
        role="admin",
        password_hash="x",
    )
    agent_a = AgentProfile(
        id="agent_a",
        tenant_id="tenant_demo",
        name="员工A",
        is_overall=False,
        metadata_json={"owner_user_id": owner.id},
    )
    agent_b = AgentProfile(
        id="agent_b",
        tenant_id="tenant_demo",
        name="员工B",
        is_overall=False,
        metadata_json={"owner_user_id": owner.id},
    )
    db.add(owner)
    db.add(admin)
    db.add(agent_a)
    db.add(agent_b)
    db.commit()
    return owner, admin, agent_a, agent_b


@pytest.fixture
def org_db():
    engine = _engine()
    with Session(engine) as db:
        yield db, _seed(db)


def _dept(name: str, parent_id: str | None = None, sort_order: int = 0) -> DepartmentWriteRequest:
    return DepartmentWriteRequest(
        tenant_id="tenant_demo",
        name=name,
        parent_id=parent_id,
        sort_order=sort_order,
    )


def _contact(db: Session, owner: User, agent_id: str, name: str) -> str:
    created = post_contact(
        ContactWriteRequest(tenant_id="tenant_demo", name=name),
        agent_id=agent_id,
        db=db,
        current_user=owner,
    )
    return created.id


def _stored_department(db: Session, department_id: str) -> OrgDepartment:
    row = db.get(OrgDepartment, department_id)
    assert row is not None
    db.refresh(row)
    return row


def test_org_tables_use_adjacency_and_materialized_path(org_db) -> None:
    db, _seeded = org_db
    columns = {column["name"]: column for column in inspect(db.get_bind()).get_columns("org_departments")}
    assert columns["parent_id"]["nullable"] is True
    assert "path" in columns
    assert "closure" not in inspect(db.get_bind()).get_table_names()
    affiliation = {
        column["name"] for column in inspect(db.get_bind()).get_columns("org_affiliations")
    }
    assert {"department_id", "contact_id", "valid_from", "valid_to"} <= affiliation
    head = {column["name"] for column in inspect(db.get_bind()).get_columns("org_department_heads")}
    assert {"department_id", "contact_id", "valid_from", "valid_to"} <= head


def test_create_move_and_reject_cycle_maintains_paths(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    root = post_department(_dept("公司"), db=db, current_user=admin)
    child = post_department(_dept("研发", root.id), db=db, current_user=admin)
    grand = post_department(_dept("平台", child.id), db=db, current_user=admin)
    market = post_department(_dept("市场"), db=db, current_user=admin)

    assert root.path == f"/{root.id}"
    assert child.path == f"/{root.id}/{child.id}"
    assert _stored_department(db, grand.id).path == f"/{root.id}/{child.id}/{grand.id}"

    renamed = patch_department(
        child.id, _dept("研发中心", root.id, 3), db=db, current_user=admin
    )
    assert renamed.name == "研发中心"
    assert renamed.path == child.path
    assert renamed.sort_order == 3

    moved = patch_department(
        child.id, _dept("研发中心", market.id, 3), db=db, current_user=admin
    )
    assert moved.path == f"/{market.id}/{child.id}"
    assert _stored_department(db, grand.id).path == f"/{market.id}/{child.id}/{grand.id}"

    rooted = patch_department(child.id, _dept("研发中心"), db=db, current_user=admin)
    assert rooted.parent_id is None
    assert rooted.path == f"/{child.id}"
    assert _stored_department(db, grand.id).path == f"/{child.id}/{grand.id}"

    with pytest.raises(HTTPException) as error:
        patch_department(child.id, _dept("研发中心", grand.id), db=db, current_user=admin)
    assert error.value.status_code == 400
    assert error.value.detail["code"] == "ORG_DEPARTMENT_CYCLE"
    assert _stored_department(db, child.id).parent_id is None


def test_duplicate_sibling_name_and_child_delete_rules(org_db) -> None:
    db, (owner, admin, agent_a, _agent_b) = org_db
    root = post_department(_dept("公司"), db=db, current_user=admin)
    child = post_department(_dept("研发", root.id), db=db, current_user=admin)
    with pytest.raises(HTTPException) as duplicate:
        post_department(_dept("研发", root.id), db=db, current_user=admin)
    assert duplicate.value.status_code == 409
    assert duplicate.value.detail["code"] == "ORG_DEPARTMENT_NAME_DUPLICATE"
    post_department(_dept("研发"), db=db, current_user=admin)
    with pytest.raises(HTTPException) as root_duplicate:
        post_department(_dept("公司"), db=db, current_user=admin)
    assert root_duplicate.value.status_code == 409
    assert root_duplicate.value.detail["code"] == "ORG_DEPARTMENT_NAME_DUPLICATE"

    contact_id = _contact(db, owner, agent_a.id, "张三")
    affiliation = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=child.id,
            contact_id=contact_id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    with pytest.raises(HTTPException) as blocked:
        remove_department(root.id, db=db, current_user=admin, tenant_id="tenant_demo")
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "ORG_DEPARTMENT_HAS_CHILDREN"

    removed = remove_department(child.id, db=db, current_user=admin, tenant_id="tenant_demo")
    assert removed["status"] == "deleted"
    assert db.get(OrgAffiliation, affiliation.affiliation_id) is None
    assert db.get(OrgDepartment, child.id) is None
    assert db.get(OrgDepartment, root.id) is not None


def test_query_people_by_department_respects_tenure_and_contact_agent(org_db) -> None:
    db, (owner, admin, agent_a, agent_b) = org_db
    department = post_department(_dept("研发"), db=db, current_user=admin)
    other = post_department(_dept("市场"), db=db, current_user=admin)
    zhang = _contact(db, owner, agent_a.id, "张三")
    li = _contact(db, owner, agent_b.id, "李四")
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            contact_id=zhang,
            valid_from="2026-01-01",
            valid_to="2026-06-01",
        ),
        db=db,
        current_user=admin,
    )
    open_ended = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            contact_id=li,
            valid_from="2026-03-01",
        ),
        db=db,
        current_user=admin,
    )
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=other.id,
            contact_id=zhang,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )

    march = get_department_people(
        department.id, db=db, current_user=admin, tenant_id="tenant_demo", as_of="2026-03-01"
    )
    assert march.as_of == "2026-03-01"
    assert [item.contact_name for item in march.people] == ["张三", "李四"]
    assert march.people[1].agent_name == "员工B"
    assert len(march.affiliations) == 2

    on_end = get_department_people(
        department.id, db=db, current_user=admin, tenant_id="tenant_demo", as_of="2026-06-01"
    )
    assert [item.contact_name for item in on_end.people] == ["张三", "李四"]

    july = get_department_people(
        department.id, db=db, current_user=admin, tenant_id="tenant_demo", as_of="2026-07-01"
    )
    assert [item.contact_name for item in july.people] == ["李四"]

    before = get_department_people(
        department.id, db=db, current_user=admin, tenant_id="tenant_demo", as_of="2025-12-31"
    )
    assert before.people == []

    closed = patch_affiliation(
        open_ended.affiliation_id,
        TenureUpdateRequest(
            tenant_id="tenant_demo",
            valid_from="2026-03-01",
            valid_to="2026-04-01",
        ),
        db=db,
        current_user=admin,
    )
    assert closed.valid_to == "2026-04-01"
    after_close = get_department_people(
        department.id, db=db, current_user=admin, tenant_id="tenant_demo", as_of="2026-05-01"
    )
    assert [item.contact_name for item in after_close.people] == ["张三"]

    options = get_org_contacts(tenant_id="tenant_demo", db=db, current_user=admin)
    assert {item.agent_id for item in options.contacts} == {agent_a.id, agent_b.id}


def test_department_head_tenure_picks_latest_valid_from(org_db) -> None:
    db, (owner, admin, agent_a, agent_b) = org_db
    department = post_department(_dept("研发"), db=db, current_user=admin)
    zhang = _contact(db, owner, agent_a.id, "张三")
    li = _contact(db, owner, agent_b.id, "李四")
    first = post_head(
        HeadWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            contact_id=zhang,
            valid_from="2026-01-01",
            valid_to="2026-12-31",
        ),
        db=db,
        current_user=admin,
    )
    post_head(
        HeadWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            contact_id=li,
            valid_from="2026-06-01",
        ),
        db=db,
        current_user=admin,
    )
    spring = get_department_people(
        department.id, db=db, current_user=admin, tenant_id="tenant_demo", as_of="2026-03-01"
    )
    assert spring.current_head is not None
    assert spring.current_head.contact_name == "张三"
    summer = get_department_people(
        department.id, db=db, current_user=admin, tenant_id="tenant_demo", as_of="2026-07-01"
    )
    assert summer.current_head is not None
    assert summer.current_head.contact_name == "李四"
    assert len(summer.heads) == 2

    updated = patch_head(
        first.head_id,
        TenureUpdateRequest(tenant_id="tenant_demo", clear_valid_to=True, valid_from="2026-01-01"),
        db=db,
        current_user=admin,
    )
    assert updated.valid_to is None


def test_enterprise_name_reuses_tenant_and_member_is_forbidden(org_db) -> None:
    db, (owner, admin, _agent_a, _agent_b) = org_db
    current = get_org_settings(tenant_id="tenant_demo", db=db, current_user=admin)
    assert current.enterprise_name == "Demo Enterprise"
    updated = patch_org_settings(
        OrgSettingsUpdateRequest(tenant_id="tenant_demo", enterprise_name="  星河科技  "),
        db=db,
        current_user=admin,
    )
    assert updated.enterprise_name == "星河科技"
    tenant = db.get(Tenant, "tenant_demo")
    assert tenant is not None
    db.refresh(tenant)
    assert tenant.name == "星河科技"

    with pytest.raises(HTTPException) as error:
        get_departments(tenant_id="tenant_demo", db=db, current_user=owner)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        post_affiliation(
            AffiliationWriteRequest(
                tenant_id="tenant_demo",
                department_id="dept_missing",
                contact_id="contact_missing",
            ),
            db=db,
            current_user=owner,
        )
    assert missing.value.status_code == 403


def test_unknown_contact_rejected_and_contact_delete_purges_org_links(org_db) -> None:
    db, (owner, admin, agent_a, _agent_b) = org_db
    department = post_department(_dept("研发"), db=db, current_user=admin)
    with pytest.raises(HTTPException) as missing:
        post_affiliation(
            AffiliationWriteRequest(
                tenant_id="tenant_demo",
                department_id=department.id,
                contact_id="contact_missing",
                valid_from="2026-01-01",
            ),
            db=db,
            current_user=admin,
        )
    assert missing.value.status_code == 404
    assert missing.value.detail["code"] == "ORG_CONTACT_NOT_FOUND"

    contact_id = _contact(db, owner, agent_a.id, "张三")
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            contact_id=contact_id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    post_head(
        HeadWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            contact_id=contact_id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    remove_contact(
        contact_id,
        db=db,
        current_user=owner,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
    )
    assert db.exec(select(OrgAffiliation)).all() == []
    assert db.exec(select(OrgDepartmentHead)).all() == []


def test_inverted_tenure_is_rejected(org_db) -> None:
    db, (owner, admin, agent_a, _agent_b) = org_db
    department = post_department(_dept("研发"), db=db, current_user=admin)
    contact_id = _contact(db, owner, agent_a.id, "张三")
    with pytest.raises(HTTPException) as error:
        post_affiliation(
            AffiliationWriteRequest(
                tenant_id="tenant_demo",
                department_id=department.id,
                contact_id=contact_id,
                valid_from="2026-06-01",
                valid_to="2026-01-01",
            ),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 400
    assert error.value.detail["code"] == "ORG_DATE_RANGE"

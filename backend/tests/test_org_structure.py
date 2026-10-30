from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    AgentDepartmentAssignment,
    AgentProfile,
    Contact,
    EnterprisePerson,
    OrgAffiliation,
    OrgDepartmentHead,
    Tenant,
    User,
)
from app.org.api import (
    get_department_people,
    get_departments,
    get_org_agents,
    get_org_settings,
    get_org_tree,
    get_people,
    patch_affiliation,
    patch_department,
    patch_head,
    patch_org_settings,
    patch_person,
    post_affiliation,
    post_affiliation_move,
    post_department,
    post_head,
    post_person,
    put_agent_departments,
    remove_affiliation,
    remove_department,
    remove_head,
    remove_person,
)
from app.org.schema import (
    AffiliationMoveRequest,
    AffiliationWriteRequest,
    AgentDepartmentsUpdateRequest,
    DepartmentWriteRequest,
    EnterprisePersonWriteRequest,
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


def _person(admin: User, name: str = "张三", **overrides) -> EnterprisePersonWriteRequest:
    values = {
        "tenant_id": "tenant_demo",
        "name": name,
        "employee_no": None,
        "phone": None,
        "email": None,
        "status": "active",
        "primary_user_id": None,
    }
    values.update(overrides)
    return EnterprisePersonWriteRequest(**values)


def test_create_list_update_and_delete_enterprise_person(org_db) -> None:
    db, (owner, admin, _agent_a, _agent_b) = org_db
    created = post_person(_person(admin, "张三", primary_user_id=owner.id), db=db, current_user=admin)
    assert created.name == "张三"
    assert created.primary_user_id == owner.id
    assert created.primary_username == "owner"

    listing = get_people(tenant_id="tenant_demo", db=db, current_user=admin)
    assert [item.id for item in listing.people] == [created.id]

    updated = patch_person(
        created.id,
        _person(admin, "张三丰", employee_no="E001", phone="13800000000"),
        db=db,
        current_user=admin,
    )
    assert updated.name == "张三丰"
    assert updated.employee_no == "E001"

    remove_person(created.id, tenant_id="tenant_demo", db=db, current_user=admin)
    assert db.get(EnterprisePerson, created.id) is None


def test_person_account_link_is_unique_and_internal_only(org_db) -> None:
    db, (owner, admin, _agent_a, _agent_b) = org_db
    post_person(_person(admin, "张三", primary_user_id=owner.id), db=db, current_user=admin)
    with pytest.raises(HTTPException) as error:
        post_person(_person(admin, "李四", primary_user_id=owner.id), db=db, current_user=admin)
    assert error.value.status_code == 409

    channel_user = User(
        id="user_channel",
        tenant_id="tenant_demo",
        username="wechat_user",
        source="wechat",
        password_hash="x",
    )
    db.add(channel_user)
    db.commit()
    with pytest.raises(HTTPException) as error:
        post_person(_person(admin, "王五", primary_user_id=channel_user.id), db=db, current_user=admin)
    assert error.value.status_code == 400


def _all_org_endpoints():
    """(名称, 调用闭包) 列表：普通 member 对企业管理全部端点都必须 403。"""
    person_request = EnterprisePersonWriteRequest(tenant_id="tenant_demo", name="张三")
    department_request = DepartmentWriteRequest(tenant_id="tenant_demo", name="研发")
    tenure_request = TenureUpdateRequest(tenant_id="tenant_demo")
    read = {
        "settings:get": lambda db, user: get_org_settings(
            tenant_id="tenant_demo", db=db, current_user=user
        ),
        "departments:get": lambda db, user: get_departments(
            tenant_id="tenant_demo", db=db, current_user=user
        ),
        "tree:get": lambda db, user: get_org_tree(
            tenant_id="tenant_demo", db=db, current_user=user
        ),
        "people:get": lambda db, user: get_people(
            tenant_id="tenant_demo", db=db, current_user=user
        ),
        "agents:get": lambda db, user: get_org_agents(
            tenant_id="tenant_demo", db=db, current_user=user
        ),
    }
    write = {
        "settings:patch": lambda db, user: patch_org_settings(
            OrgSettingsUpdateRequest(tenant_id="tenant_demo", enterprise_name="越权"),
            db=db,
            current_user=user,
        ),
        "departments:post": lambda db, user: post_department(
            department_request, db=db, current_user=user
        ),
        "departments:patch": lambda db, user: patch_department(
            "dept_x", department_request, db=db, current_user=user
        ),
        "departments:delete": lambda db, user: remove_department(
            "dept_x", tenant_id="tenant_demo", db=db, current_user=user
        ),
        "department-people:get": lambda db, user: get_department_people(
            "dept_x", tenant_id="tenant_demo", as_of=None, db=db, current_user=user
        ),
        "people:post": lambda db, user: post_person(person_request, db=db, current_user=user),
        "people:patch": lambda db, user: patch_person(
            "person_x", person_request, db=db, current_user=user
        ),
        "people:delete": lambda db, user: remove_person(
            "person_x", tenant_id="tenant_demo", db=db, current_user=user
        ),
        "affiliations:post": lambda db, user: post_affiliation(
            AffiliationWriteRequest(
                tenant_id="tenant_demo", department_id="dept_x", person_id="person_x"
            ),
            db=db,
            current_user=user,
        ),
        "affiliations:patch": lambda db, user: patch_affiliation(
            "aff_x", tenure_request, db=db, current_user=user
        ),
        "affiliations:move": lambda db, user: post_affiliation_move(
            "aff_x",
            AffiliationMoveRequest(tenant_id="tenant_demo", department_id="dept_x"),
            db=db,
            current_user=user,
        ),
        "affiliations:delete": lambda db, user: remove_affiliation(
            "aff_x", tenant_id="tenant_demo", db=db, current_user=user
        ),
        "heads:post": lambda db, user: post_head(
            HeadWriteRequest(tenant_id="tenant_demo", department_id="dept_x", person_id="person_x"),
            db=db,
            current_user=user,
        ),
        "heads:patch": lambda db, user: patch_head(
            "head_x", tenure_request, db=db, current_user=user
        ),
        "heads:delete": lambda db, user: remove_head(
            "head_x", tenant_id="tenant_demo", db=db, current_user=user
        ),
        "agents:put-departments": lambda db, user: put_agent_departments(
            "agent_x",
            AgentDepartmentsUpdateRequest(tenant_id="tenant_demo", department_ids=["dept_x"]),
            db=db,
            current_user=user,
        ),
    }
    return [*read.items(), *write.items()]


@pytest.mark.parametrize(
    "name,call",
    _all_org_endpoints(),
    ids=[item[0] for item in _all_org_endpoints()],
)
def test_member_cannot_manage_any_org_endpoint(org_db, name, call) -> None:
    db, (owner, _admin, _agent_a, _agent_b) = org_db
    with pytest.raises(HTTPException) as error:
        call(db, owner)
    assert error.value.status_code == 403


def test_create_move_and_reject_cycle_maintains_paths(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    root = post_department(_dept("研发"), db=db, current_user=admin)
    child = post_department(_dept("平台组", root.id), db=db, current_user=admin)
    assert child.path == f"{root.path}/{child.id}"
    moved = patch_department(
        child.id,
        DepartmentWriteRequest(tenant_id="tenant_demo", name="平台组", parent_id=None),
        db=db,
        current_user=admin,
    )
    assert moved.path == f"/{child.id}"

    parent = post_department(_dept("市场"), db=db, current_user=admin)
    nested = post_department(_dept("华东", parent.id), db=db, current_user=admin)
    with pytest.raises(HTTPException) as error:
        patch_department(
            parent.id,
            DepartmentWriteRequest(tenant_id="tenant_demo", name="市场", parent_id=nested.id),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 400


def test_duplicate_sibling_name_and_non_empty_delete_rules(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    root = post_department(_dept("研发"), db=db, current_user=admin)
    with pytest.raises(HTTPException) as error:
        post_department(_dept("研发"), db=db, current_user=admin)
    assert error.value.status_code == 409

    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=root.id,
            person_id=person.id,
            position_title="工程师",
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    with pytest.raises(HTTPException) as error:
        remove_department(root.id, tenant_id="tenant_demo", db=db, current_user=admin)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "ORG_DEPARTMENT_HAS_PEOPLE"

    patch_affiliation(
        db.exec(select(OrgAffiliation)).first().id,
        TenureUpdateRequest(tenant_id="tenant_demo", valid_to="2026-01-02"),
        db=db,
        current_user=admin,
    )
    # Historical affiliation still blocks delete, with a distinct error code.
    with pytest.raises(HTTPException) as error:
        remove_department(root.id, tenant_id="tenant_demo", db=db, current_user=admin)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "ORG_DEPARTMENT_HAS_HISTORY"


def test_query_people_by_department_respects_tenure_and_position(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    department = post_department(_dept("研发"), db=db, current_user=admin)
    zhang = post_person(_person(admin, "张三"), db=db, current_user=admin)
    li = post_person(_person(admin, "李四"), db=db, current_user=admin)
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            person_id=zhang.id,
            position_title="工程师",
            valid_from="2026-01-01",
            valid_to="2026-06-01",
        ),
        db=db,
        current_user=admin,
    )
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            person_id=li.id,
            position_title="产品经理",
            valid_from="2026-03-01",
        ),
        db=db,
        current_user=admin,
    )
    march = get_department_people(
        department.id,
        tenant_id="tenant_demo",
        as_of="2026-03-01",
        db=db,
        current_user=admin,
    )
    assert [item.person_name for item in march.people] == ["张三", "李四"]
    assert march.people[0].position_title == "工程师"
    july = get_department_people(
        department.id,
        tenant_id="tenant_demo",
        as_of="2026-07-01",
        db=db,
        current_user=admin,
    )
    assert [item.person_name for item in july.people] == ["李四"]


def test_department_head_tenure_picks_latest_valid_from(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    department = post_department(_dept("研发"), db=db, current_user=admin)
    zhang = post_person(_person(admin, "张三"), db=db, current_user=admin)
    li = post_person(_person(admin, "李四"), db=db, current_user=admin)
    post_head(
        HeadWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            person_id=zhang.id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    latest = post_head(
        HeadWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            person_id=li.id,
            valid_from="2026-03-01",
        ),
        db=db,
        current_user=admin,
    )
    result = get_department_people(
        department.id, tenant_id="tenant_demo", as_of="2026-04-01", db=db, current_user=admin
    )
    assert result.current_head is not None
    assert result.current_head.head_id == latest.head_id
    assert result.current_head.person_name == "李四"


def test_enterprise_name_reuses_tenant_and_member_is_forbidden(org_db) -> None:
    db, (owner, admin, _agent_a, _agent_b) = org_db
    updated = patch_org_settings(
        OrgSettingsUpdateRequest(tenant_id="tenant_demo", enterprise_name="星河科技"),
        db=db,
        current_user=admin,
    )
    assert updated.enterprise_name == "星河科技"
    assert db.get(Tenant, "tenant_demo").name == "星河科技"
    with pytest.raises(HTTPException) as error:
        patch_org_settings(
            OrgSettingsUpdateRequest(tenant_id="tenant_demo", enterprise_name="越权"),
            db=db,
            current_user=owner,
        )
    assert error.value.status_code == 403


def test_delete_person_purges_affiliations_and_heads(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    department = post_department(_dept("研发"), db=db, current_user=admin)
    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            person_id=person.id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    post_head(
        HeadWriteRequest(
            tenant_id="tenant_demo",
            department_id=department.id,
            person_id=person.id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    remove_person(person.id, tenant_id="tenant_demo", db=db, current_user=admin)
    assert db.exec(select(OrgAffiliation)).all() == []
    assert db.exec(select(OrgDepartmentHead)).all() == []


def test_agent_multi_department_assignment_and_tree(org_db) -> None:
    db, (_owner, admin, agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    service = post_department(_dept("客服"), db=db, current_user=admin)
    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=person.id,
            position_title="销售经理",
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    assigned = put_agent_departments(
        agent_a.id,
        AgentDepartmentsUpdateRequest(
            tenant_id="tenant_demo",
            department_ids=[sales.id, service.id],
            primary_department_id=sales.id,
        ),
        db=db,
        current_user=admin,
    )
    assert {item.department_id for item in assigned.departments} == {sales.id, service.id}
    assert [item.department_id for item in assigned.departments if item.is_primary] == [sales.id]
    tree = get_org_tree(tenant_id="tenant_demo", db=db, current_user=admin)
    assert {item.id for item in tree.departments} == {sales.id, service.id}
    assert any(item.name == "张三" for item in tree.people)
    assert {(item.agent_id, item.department_id) for item in tree.agents} == {
        (agent_a.id, sales.id),
        (agent_a.id, service.id),
    }
    assert any(item.is_primary and item.agent_id == agent_a.id for item in tree.agents)

    with pytest.raises(HTTPException) as error:
        put_agent_departments(
            agent_a.id,
            AgentDepartmentsUpdateRequest(
                tenant_id="tenant_demo",
                department_ids=[sales.id],
                primary_department_id=None,
            ),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 400


def test_agent_assignment_clears_when_empty_and_rejects_overall(org_db) -> None:
    db, (_owner, admin, agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    put_agent_departments(
        agent_a.id,
        AgentDepartmentsUpdateRequest(
            tenant_id="tenant_demo", department_ids=[sales.id], primary_department_id=sales.id
        ),
        db=db,
        current_user=admin,
    )
    cleared = put_agent_departments(
        agent_a.id,
        AgentDepartmentsUpdateRequest(
            tenant_id="tenant_demo", department_ids=[], primary_department_id=None
        ),
        db=db,
        current_user=admin,
    )
    assert cleared.departments == []
    assert db.exec(select(AgentDepartmentAssignment)).all() == []

    overall = AgentProfile(
        id="agent_overall", tenant_id="tenant_demo", name="整体", is_overall=True
    )
    db.add(overall)
    db.commit()
    with pytest.raises(HTTPException) as error:
        put_agent_departments(
            overall.id,
            AgentDepartmentsUpdateRequest(
                tenant_id="tenant_demo", department_ids=[sales.id], primary_department_id=sales.id
            ),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 400


def test_department_people_returns_agents(org_db) -> None:
    db, (_owner, admin, agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    put_agent_departments(
        agent_a.id,
        AgentDepartmentsUpdateRequest(
            tenant_id="tenant_demo", department_ids=[sales.id], primary_department_id=sales.id
        ),
        db=db,
        current_user=admin,
    )
    result = get_department_people(
        sales.id, tenant_id="tenant_demo", as_of="2026-01-01", db=db, current_user=admin
    )
    assert [item.agent_id for item in result.agents] == [agent_a.id]
    assert result.agents[0].is_primary is True


def test_move_affiliation_keeps_history_in_source_department(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    service = post_department(_dept("客服"), db=db, current_user=admin)
    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    created = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=person.id,
            position_title="销售经理",
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    moved = post_affiliation_move(
        created.affiliation_id,
        AffiliationMoveRequest(
            tenant_id="tenant_demo",
            department_id=service.id,
            position_title="客服主管",
            valid_from="2026-04-01",
            valid_to="2026-04-30",
        ),
        db=db,
        current_user=admin,
    )
    assert moved.department_id == service.id
    assert moved.position_title == "客服主管"
    assert moved.valid_from == "2026-04-01"

    rows = db.exec(select(OrgAffiliation).order_by(OrgAffiliation.valid_from)).all()
    assert len(rows) == 2
    historical = rows[0]
    assert historical.department_id == sales.id
    assert historical.valid_from == date(2026, 1, 1)
    assert historical.valid_to == date(2026, 3, 31)

    # 同日移动等价于原任职未发生：删除原记录，不留零天历史。
    same_day = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=person.id,
            valid_from="2026-05-01",
        ),
        db=db,
        current_user=admin,
    )
    post_affiliation_move(
        same_day.affiliation_id,
        AffiliationMoveRequest(
            tenant_id="tenant_demo",
            department_id=service.id,
            valid_from="2026-05-01",
        ),
        db=db,
        current_user=admin,
    )
    rows = db.exec(select(OrgAffiliation).order_by(OrgAffiliation.valid_from)).all()
    assert len(rows) == 3
    assert [row.department_id for row in rows if row.department_id == sales.id] == [sales.id]
    assert rows[-1].valid_from == date(2026, 5, 1)


def test_move_affiliation_rejects_same_department_and_missing_target(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    created = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=person.id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    with pytest.raises(HTTPException) as error:
        post_affiliation_move(
            created.affiliation_id,
            AffiliationMoveRequest(tenant_id="tenant_demo", department_id=sales.id),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 400
    with pytest.raises(HTTPException) as error:
        post_affiliation_move(
            created.affiliation_id,
            AffiliationMoveRequest(tenant_id="tenant_demo", department_id=""),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 400


def test_left_person_tenure_can_be_closed(org_db) -> None:
    """离职收尾：补 valid_to 必须放行；换成另一个在职人员仍需校验。"""
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    zhang = post_person(_person(admin, "张三"), db=db, current_user=admin)
    li = post_person(_person(admin, "李四"), db=db, current_user=admin)
    zhang_affiliation = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=zhang.id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    li_affiliation = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=li.id,
            valid_from="2026-02-01",
        ),
        db=db,
        current_user=admin,
    )
    patch_person(
        zhang.id, _person(admin, "张三", status="left"), db=db, current_user=admin
    )
    closed = patch_affiliation(
        zhang_affiliation.affiliation_id,
        TenureUpdateRequest(tenant_id="tenant_demo", valid_to="2026-06-30"),
        db=db,
        current_user=admin,
    )
    assert closed.valid_to == "2026-06-30"

    with pytest.raises(HTTPException) as error:
        patch_affiliation(
            li_affiliation.affiliation_id,
            TenureUpdateRequest(tenant_id="tenant_demo", person_id=zhang.id),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 400
    assert error.value.detail["code"] == "ORG_PERSON_LEFT"


def test_overlapping_affiliation_in_same_department_rejected(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    created = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=person.id,
            valid_from="2026-01-01",
            valid_to="2026-06-30",
        ),
        db=db,
        current_user=admin,
    )
    with pytest.raises(HTTPException) as error:
        post_affiliation(
            AffiliationWriteRequest(
                tenant_id="tenant_demo",
                department_id=sales.id,
                person_id=person.id,
                valid_from="2026-06-01",
            ),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "ORG_AFFILIATION_OVERLAP"

    # 不重叠的多段历史(调岗后回任)允许。
    again = post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=person.id,
            valid_from="2026-07-01",
        ),
        db=db,
        current_user=admin,
    )
    assert again.affiliation_id != created.affiliation_id


def test_cross_tenant_access_is_rejected(org_db) -> None:
    db, (_owner, admin, _agent_a, _agent_b) = org_db
    db.add(Tenant(id="tenant_other", name="Other"))
    other_admin = User(
        id="user_other_admin",
        tenant_id="tenant_other",
        username="other_admin",
        display_name="Other Admin",
        role="admin",
        password_hash="x",
    )
    db.add(other_admin)
    db.commit()

    # 他租户管理员访问本租户组织数据：403。
    with pytest.raises(HTTPException) as error:
        get_people(tenant_id="tenant_demo", db=db, current_user=other_admin)
    assert error.value.status_code == 403

    # 本租户管理员引用他租户资源：404，不落库。
    other_dept = post_department(
        DepartmentWriteRequest(tenant_id="tenant_other", name="他方部门"),
        db=db,
        current_user=other_admin,
    )
    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    with pytest.raises(HTTPException) as error:
        post_affiliation(
            AffiliationWriteRequest(
                tenant_id="tenant_demo",
                department_id=other_dept.id,
                person_id=person.id,
            ),
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 404
    assert db.exec(select(OrgAffiliation)).all() == []


def test_delete_agent_purges_department_assignments(org_db) -> None:
    from app.api.agents import delete_agent

    db, (_owner, admin, agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    put_agent_departments(
        agent_a.id,
        AgentDepartmentsUpdateRequest(
            tenant_id="tenant_demo", department_ids=[sales.id], primary_department_id=sales.id
        ),
        db=db,
        current_user=admin,
    )
    assert db.exec(select(AgentDepartmentAssignment)).all()

    delete_agent(agent_a.id, tenant_id="tenant_demo", db=db, current_user=admin)

    assert db.exec(select(AgentDepartmentAssignment)).all() == []
    assert db.get(AgentProfile, agent_a.id) is None


def test_delete_user_unlinks_person_and_agent_owner(org_db, monkeypatch) -> None:
    from app.api.auth import delete_user as auth_delete_user
    from app.db import database

    db, (owner, admin, _agent_a, _agent_b) = org_db
    disabled_agent = AgentProfile(
        id="agent_c",
        tenant_id="tenant_demo",
        name="员工C",
        is_overall=False,
        status="disabled",
        primary_owner_user_id=owner.id,
        metadata_json={
            "owner_user_id": owner.id,
            "owner_username": "owner",
            "owner_display_name": "Owner",
        },
    )
    db.add(disabled_agent)
    person = post_person(
        _person(admin, "张三", primary_user_id=owner.id), db=db, current_user=admin
    )
    db.commit()

    auth_delete_user(owner.id, tenant_id="tenant_demo", db=db, current_user=admin)

    assert db.get(User, owner.id) is None
    refreshed_person = db.get(EnterprisePerson, person.id)
    assert refreshed_person is not None
    assert refreshed_person.primary_user_id is None
    refreshed_agent = db.get(AgentProfile, "agent_c")
    assert refreshed_agent is not None
    assert refreshed_agent.primary_owner_user_id is None
    assert "owner_user_id" not in (refreshed_agent.metadata_json or {})

    # 模拟重启跑迁移回填：已删除用户不得被写回负责人字段。
    db.commit()
    monkeypatch.setattr(database, "engine", db.get_bind())
    monkeypatch.setattr(database, "database_url", "sqlite://")
    database._migrate_agent_primary_owner()
    db.expire_all()
    assert db.get(AgentProfile, "agent_c").primary_owner_user_id is None


def test_contact_directory_stays_independent_of_org(org_db) -> None:
    from app.contacts.schema import ContactWriteRequest
    from app.contacts.service import create_contact, delete_contact

    db, (_owner, admin, agent_a, _agent_b) = org_db
    sales = post_department(_dept("销售"), db=db, current_user=admin)
    person = post_person(_person(admin, "张三"), db=db, current_user=admin)
    post_affiliation(
        AffiliationWriteRequest(
            tenant_id="tenant_demo",
            department_id=sales.id,
            person_id=person.id,
            valid_from="2026-01-01",
        ),
        db=db,
        current_user=admin,
    )
    contact = create_contact(
        db,
        "tenant_demo",
        agent_a.id,
        ContactWriteRequest(tenant_id="tenant_demo", name="张三", phone="138"),
    )

    # 删除私有通讯录联系人：组织任职与企业人员不受影响。
    delete_contact(db, "tenant_demo", agent_a.id, contact.id)
    assert db.get(Contact, contact.id) is None
    assert db.get(EnterprisePerson, person.id) is not None
    assert len(db.exec(select(OrgAffiliation)).all()) == 1
    tree = get_org_tree(tenant_id="tenant_demo", db=db, current_user=admin)
    assert [item.name for item in tree.people] == ["张三"]

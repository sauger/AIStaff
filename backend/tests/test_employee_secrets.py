from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.schema import AgentProfileCreateRequest
from app.api.agents import create_agent
from app.core.capability_manifest import (
    RESERVED_HARNESS_CAPABILITY_NAMES,
    CapabilityManifestBuilder,
)
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    EmployeeLoginGuide,
    EmployeeSecret,
    GeneralSkill,
    Skill,
    Tenant,
    User,
)
from app.secrets.api import (
    console_test_login as run_test_login,
)
from app.secrets.api import (
    copy_published_login_guide,
    get_login_guide_gallery,
    get_login_guides,
    get_secrets,
    post_login_guide,
    post_secret,
    remove_secret,
)
from app.secrets.executor import FakeLoginExecutor, set_login_executor
from app.secrets.harness import invoke_secret_tool
from app.secrets.schema import (
    LoginGuideCopyRequest,
    LoginGuideWriteRequest,
    SecretCreateRequest,
)
from app.secrets.service import prompt_context
from app.security.encryption import decrypt_secret

PASSWORD = "super-secret-password"
USERNAME = "token-user"


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session) -> tuple[User, User, User, AgentProfile, AgentProfile]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    owner = User(
        id="user_owner",
        tenant_id="tenant_demo",
        username="owner",
        display_name="Owner",
        password_hash="x",
    )
    other = User(
        id="user_other",
        tenant_id="tenant_demo",
        username="other",
        display_name="Other",
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
        metadata_json={"owner_user_id": other.id},
    )
    db.add(owner)
    db.add(other)
    db.add(admin)
    db.add(agent_a)
    db.add(agent_b)
    db.commit()
    return owner, other, admin, agent_a, agent_b


@pytest.fixture
def secret_db():
    engine = _engine()
    fake = FakeLoginExecutor()
    set_login_executor(fake)
    with Session(engine) as db:
        users = _seed(db)
        yield db, users, fake
    set_login_executor(None)


def _create_password(db: Session, user: User, agent_id: str, name: str = "主账号") -> object:
    return post_secret(
        SecretCreateRequest(
            tenant_id="tenant_demo",
            name=name,
            description="日常操作账号",
            secret_type="password",
            username=USERNAME,
            value=PASSWORD,
        ),
        agent_id=agent_id,
        db=db,
        current_user=user,
    )


def _create_guide(
    db: Session,
    user: User,
    agent_id: str,
    *,
    name: str = "Token Channel 登录",
    default_secret_name: str | None = "主账号",
    published: bool = False,
) -> object:
    return post_login_guide(
        LoginGuideWriteRequest(
            tenant_id="tenant_demo",
            name=name,
            url="https://token-channel.example.com/login",
            username_selector="#login-username",
            username_label="用户名",
            password_selector="#login-password",
            password_label="密码",
            submit_selector="button[type=submit]",
            submit_label="登录",
            default_secret_name=default_secret_name,
            published=published,
        ),
        agent_id=agent_id,
        db=db,
        current_user=user,
    )


def _invoker(db: Session, agent_id: str, **extra):
    values = {
        "db": db,
        "tenant_id": "tenant_demo",
        "agent_id": agent_id,
        "active_skill": None,
        "session": SimpleNamespace(id="sess_1"),
        "latest_user_text": "",
        "_activated_names": set(),
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _secret_row(db: Session, name: str) -> EmployeeSecret:
    row = db.exec(select(EmployeeSecret).where(EmployeeSecret.name == name)).first()
    assert row is not None
    return row


def test_owner_saves_secret_without_echoing_ciphertext(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _fake = secret_db
    created = _create_password(db, owner, agent_a.id)
    dumped = created.model_dump()
    assert PASSWORD not in str(dumped)
    assert USERNAME not in str(dumped)
    assert "value" not in dumped
    assert "value_encrypted" not in dumped
    listing = get_secrets(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    assert listing.can_write is True
    assert listing.secrets[0].name == "主账号"
    assert listing.secrets[0].secret_type == "password"
    assert PASSWORD not in str(listing.model_dump())
    row = _secret_row(db, "主账号")
    assert row.value_encrypted != PASSWORD
    payload = json.loads(decrypt_secret(row.value_encrypted))
    assert payload["password"] == PASSWORD
    assert payload["username"] == USERNAME


def test_admin_can_read_metadata_but_cannot_write(secret_db) -> None:
    db, (owner, _other, admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    listing = get_secrets(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=admin
    )
    assert listing.can_write is False
    assert listing.secrets[0].name == "主账号"
    assert PASSWORD not in str(listing.model_dump())
    with pytest.raises(Exception) as error:
        post_secret(
            SecretCreateRequest(
                tenant_id="tenant_demo",
                name="不该创建",
                secret_type="password",
                username="x",
                value="y",
            ),
            agent_id=agent_a.id,
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 403
    with pytest.raises(Exception) as delete_error:
        remove_secret(
            listing.secrets[0].id,
            tenant_id="tenant_demo",
            agent_id=agent_a.id,
            db=db,
            current_user=admin,
        )
    assert delete_error.value.status_code == 403


def test_non_admin_cannot_see_other_employee_secrets(secret_db) -> None:
    db, (owner, other, _admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    with pytest.raises(Exception) as error:
        get_secrets(
            tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=other
        )
    assert error.value.status_code == 403


def test_ae1_default_secret_used_when_chat_omits_name(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), fake = secret_db
    _create_password(db, owner, agent_a.id)
    _create_password(db, owner, agent_a.id, name="代操作-张三")
    _create_guide(db, owner, agent_a.id)
    result = invoke_secret_tool(
        _invoker(db, agent_a.id),
        "secure_login",
        {"login_guide_name": "Token Channel 登录"},
    )
    assert result["success"] is True
    assert result["data"]["secret_name_used"] == "主账号"
    assert fake.attempts[0].password == PASSWORD
    assert PASSWORD not in str(result)


def test_ae2_named_secret_overrides_default_binding(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), fake = secret_db
    _create_password(db, owner, agent_a.id)
    _create_password(db, owner, agent_a.id, name="代操作-张三")
    _create_guide(db, owner, agent_a.id)
    result = invoke_secret_tool(
        _invoker(db, agent_a.id),
        "secure_login",
        {"login_guide_name": "Token Channel 登录", "secret_name": "代操作-张三"},
    )
    assert result["success"] is True
    assert result["data"]["secret_name_used"] == "代操作-张三"
    assert result["data"]["secret_type_used"] == "password"
    assert fake.attempts[-1].password == PASSWORD


def test_ae3_captcha_without_snapshot_fails_clearly(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), fake = secret_db
    fake.mode = "captcha"
    _create_password(db, owner, agent_a.id)
    guide = _create_guide(db, owner, agent_a.id)
    listed = invoke_secret_tool(
        _invoker(db, agent_a.id),
        "secure_login",
        {"login_guide_name": guide.name},
    )
    assert listed["success"] is False
    assert listed["error"]["code"] == "LOGIN_CAPTCHA_REQUIRED"
    assert "需要人工处理验证码" in listed["error"]["message"]
    assert PASSWORD not in str(listed)


def test_ae4_captcha_switches_to_session_snapshot(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), fake = secret_db
    _create_password(db, owner, agent_a.id)
    guide = _create_guide(db, owner, agent_a.id)
    fake.mode = "success"
    first = run_test_login(
        guide.id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert first.snapshot_created is True
    fake.mode = "captcha"
    fake.attempts.clear()
    listed = invoke_secret_tool(
        _invoker(db, agent_a.id),
        "secure_login",
        {"login_guide_name": guide.name},
    )
    assert listed["success"] is True
    assert listed["data"]["switched_to_snapshot"] is True
    assert listed["data"]["secret_type_used"] == "session_snapshot"
    assert fake.attempts[0].secret_type == "password"
    assert fake.attempts[1].secret_type == "session_snapshot"


def test_ae5_and_ae9_success_creates_or_updates_snapshot_not_password(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), fake = secret_db
    password = _create_password(db, owner, agent_a.id)
    guide = _create_guide(db, owner, agent_a.id)
    before = _secret_row(db, "主账号").value_encrypted
    first = run_test_login(
        guide.id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert first.success is True
    assert first.snapshot_created is True
    assert first.snapshot_name
    assert _secret_row(db, "主账号").value_encrypted == before
    assert json.loads(decrypt_secret(before))["password"] == PASSWORD
    snapshots = db.exec(
        select(EmployeeSecret).where(EmployeeSecret.secret_type == "session_snapshot")
    ).all()
    assert len(snapshots) == 1
    assert snapshots[0].linked_login_guide_id == guide.id
    fake.snapshot_payload = '{"cookies":[{"name":"sid","value":"rotated"}]}'
    second = run_test_login(
        guide.id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert second.snapshot_created is False
    assert second.snapshot_updated is True
    assert second.snapshot_name == first.snapshot_name
    updated = db.exec(
        select(EmployeeSecret).where(EmployeeSecret.secret_type == "session_snapshot")
    ).all()
    assert len(updated) == 1
    payload = json.loads(decrypt_secret(updated[0].value_encrypted))
    assert "rotated" in payload["storage_state"]
    assert _secret_row(db, password.name).value_encrypted == before


def test_ae6_secret_list_refuses_ciphertext(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    listed = invoke_secret_tool(_invoker(db, agent_a.id), "secret_list", {})
    assert listed["success"] is True
    assert listed["data"]["secrets"][0]["name"] == "主账号"
    assert "value" not in listed["data"]["secrets"][0]
    assert PASSWORD not in str(listed)
    prompt = prompt_context(db, "tenant_demo", agent_a.id)
    assert "主账号" in prompt
    assert PASSWORD not in prompt
    assert "必须拒绝" in prompt


def test_ae7_plaza_copy_clears_default_secret(secret_db) -> None:
    db, (owner, other, _admin, agent_a, agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    guide = _create_guide(db, owner, agent_a.id, published=True)
    gallery = get_login_guide_gallery(
        tenant_id="tenant_demo", db=db, current_user=other
    )
    assert gallery[0].id == guide.id
    assert not hasattr(gallery[0], "default_secret_name") or gallery[0].model_dump().get(
        "default_secret_name"
    ) in (None, "")
    copied = copy_published_login_guide(
        guide.id,
        LoginGuideCopyRequest(tenant_id="tenant_demo", target_agent_id=agent_b.id),
        db=db,
        current_user=other,
    )
    assert copied.default_secret_name is None
    assert copied.copied_from_id == guide.id
    assert db.exec(select(EmployeeSecret).where(EmployeeSecret.agent_id == agent_b.id)).all() == []


def test_ae8_unpublished_guides_hidden_from_other_members(secret_db) -> None:
    db, (owner, other, _admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    _create_guide(db, owner, agent_a.id, published=False)
    gallery = get_login_guide_gallery(
        tenant_id="tenant_demo", db=db, current_user=other
    )
    assert gallery == []
    with pytest.raises(Exception) as error:
        get_login_guides(
            tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=other
        )
    assert error.value.status_code == 403


def test_ae10_admin_readonly_flags_on_other_employee(secret_db) -> None:
    db, (owner, _other, admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    _create_guide(db, owner, agent_a.id)
    guides = get_login_guides(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=admin
    )
    assert guides.can_write is False
    secrets = get_secrets(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=admin
    )
    assert secrets.can_write is False
    with pytest.raises(Exception) as error:
        run_test_login(
            guides.guides[0].id,
            tenant_id="tenant_demo",
            agent_id=agent_a.id,
            db=db,
            current_user=admin,
        )
    assert error.value.status_code == 403


def test_ae11_expired_snapshot_has_no_proactive_warning(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), fake = secret_db
    _create_password(db, owner, agent_a.id)
    guide = _create_guide(db, owner, agent_a.id)
    run_test_login(
        guide.id,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    listing = get_secrets(
        tenant_id="tenant_demo", agent_id=agent_a.id, db=db, current_user=owner
    )
    dumped = str(listing.model_dump())
    assert "过期" not in dumped
    assert "expired" not in dumped.lower()
    fake.mode = "fail"
    listed = invoke_secret_tool(
        _invoker(db, agent_a.id),
        "secure_login",
        {"login_guide_name": guide.name, "secret_name": listing.secrets[-1].name},
    )
    assert listed["success"] is False
    assert listed["error"]["code"] in {"LOGIN_SNAPSHOT_INVALID", "LOGIN_FAILED"}


def test_runtime_cannot_use_another_employee_secret(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    _create_guide(db, owner, agent_a.id)
    listed = invoke_secret_tool(
        _invoker(db, agent_b.id),
        "secure_login",
        {"login_guide_name": "Token Channel 登录"},
    )
    assert listed["success"] is False
    assert listed["error"]["code"] == "LOGIN_GUIDE_NOT_FOUND"
    assert PASSWORD not in str(listed)


def test_copy_agent_does_not_copy_secrets_or_guides(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    _create_guide(db, owner, agent_a.id, published=True)
    copied = create_agent(
        AgentProfileCreateRequest(
            tenant_id="tenant_demo",
            name="复制员工",
            copy_from_agent_id=agent_a.id,
            source_mode="copy",
        ),
        db=db,
        current_user=owner,
    )
    assert (
        db.exec(select(EmployeeSecret).where(EmployeeSecret.agent_id == copied.id)).all()
        == []
    )
    assert (
        db.exec(
            select(EmployeeLoginGuide).where(EmployeeLoginGuide.agent_id == copied.id)
        ).all()
        == []
    )


def test_copying_skill_does_not_copy_secrets(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    skill = Skill(
        tenant_id="tenant_demo",
        skill_id="skill_login",
        name="登录后工作",
        version="1.0.0",
        content_json={"steps": ["do work"]},
        status="published",
    )
    db.add(skill)
    db.flush()
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=agent_a.id,
            resource_type="skill",
            resource_id=skill.id,
            status="active",
        )
    )
    db.commit()
    from app.api.agents import _copy_agent_scope_from_source

    _copy_agent_scope_from_source(db, "tenant_demo", agent_a, agent_b)
    assert db.exec(select(EmployeeSecret).where(EmployeeSecret.agent_id == agent_b.id)).all() == []


def test_secrets_stay_out_of_skills_and_prompt(secret_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    skill = GeneralSkill(
        tenant_id="tenant_demo",
        slug="after-login",
        name="登录后工作",
        skill_markdown="# 登录成功后整理报表",
        runtime_config_json={},
    )
    db.add(skill)
    db.commit()
    assert PASSWORD not in (skill.skill_markdown or "")
    prompt = prompt_context(db, "tenant_demo", agent_a.id)
    assert PASSWORD not in prompt
    assert "不要写进技能或 SOP" in prompt


def test_copied_guide_cannot_test_login_until_rebound(secret_db) -> None:
    db, (owner, other, _admin, agent_a, agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    guide = _create_guide(db, owner, agent_a.id, published=True)
    copied = copy_published_login_guide(
        guide.id,
        LoginGuideCopyRequest(tenant_id="tenant_demo", target_agent_id=agent_b.id),
        db=db,
        current_user=other,
    )
    with pytest.raises(Exception) as error:
        run_test_login(
            copied.id,
            tenant_id="tenant_demo",
            agent_id=agent_b.id,
            db=db,
            current_user=other,
        )
    assert error.value.detail["code"] == "LOGIN_SECRET_REQUIRED"


def test_manifest_includes_secure_login(secret_db) -> None:
    db, (_owner, _other, _admin, agent_a, _agent_b), _fake = secret_db
    manifest = CapabilityManifestBuilder(db).build("tenant_demo", agent_a.id, None, None)
    names = {item.name for item in manifest.available}
    assert {"secret_list", "login_guide_list", "secure_login"} <= names
    assert "secure_login" in RESERVED_HARNESS_CAPABILITY_NAMES

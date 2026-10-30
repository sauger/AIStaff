from __future__ import annotations

from sqlalchemy import create_engine, text

from app.db import database
from app.db.models import EnterprisePerson, User


def test_org_contact_schema_migrates_to_enterprise_people(tmp_path, monkeypatch) -> None:
    path = tmp_path / "company-migration.db"
    engine = create_engine(f"sqlite:///{path}")
    User.__table__.create(engine)
    EnterprisePerson.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE contacts ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, agent_id VARCHAR, name VARCHAR, "
                "phone VARCHAR, email VARCHAR, primary_user_id VARCHAR, "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE org_departments ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, name VARCHAR, parent_id VARCHAR, "
                "path VARCHAR, sort_order INTEGER, created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE org_affiliations ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, department_id VARCHAR, contact_id VARCHAR, "
                "valid_from DATE, valid_to DATE, created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE org_department_heads ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, department_id VARCHAR, contact_id VARCHAR, "
                "valid_from DATE, valid_to DATE, created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, agent_id, name, phone, email, primary_user_id, "
                "created_at, updated_at) VALUES "
                "('contact_1', 'tenant_demo', 'agent_1', '张三', '138', 'z@example.com', 'user_1', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, agent_id, name, phone, email, primary_user_id, "
                "created_at, updated_at) VALUES "
                "('contact_2', 'tenant_demo', 'agent_2', '张三(渠道)', '139', NULL, 'user_1', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO org_departments (id, tenant_id, name, parent_id, path, sort_order, "
                "created_at, updated_at) VALUES "
                "('dept_1', 'tenant_demo', '研发', '', '/dept_1', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO org_affiliations (id, tenant_id, department_id, contact_id, valid_from, "
                "valid_to, created_at, updated_at) VALUES "
                "('aff_1', 'tenant_demo', 'dept_1', 'contact_1', '2026-01-01', NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO org_affiliations (id, tenant_id, department_id, contact_id, valid_from, "
                "valid_to, created_at, updated_at) VALUES "
                "('aff_2', 'tenant_demo', 'dept_1', 'contact_2', '2026-02-01', NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        # 孤儿任职：contact 不存在，迁移应丢弃且不影响其余行。
        conn.execute(
            text(
                "INSERT INTO org_affiliations (id, tenant_id, department_id, contact_id, valid_from, "
                "valid_to, created_at, updated_at) VALUES "
                "('aff_orphan', 'tenant_demo', 'dept_1', 'contact_missing', '2026-03-01', NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO org_department_heads (id, tenant_id, department_id, contact_id, valid_from, "
                "valid_to, created_at, updated_at) VALUES "
                "('head_1', 'tenant_demo', 'dept_1', 'contact_1', '2026-01-01', NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "database_url", f"sqlite:///{path}")
    database._migrate_org_people_schema()

    with engine.connect() as conn:
        affiliation_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(org_affiliations)")).fetchall()
        }
        assert "person_id" in affiliation_columns
        assert "contact_id" not in affiliation_columns
        assert "position_title" in affiliation_columns
        people = conn.execute(text("SELECT id, name, primary_user_id, source FROM enterprise_people")).mappings().all()
        # 两个 contact 共享同一 primary_user_id：合并为一条企业人员。
        assert len(people) == 1
        assert people[0]["name"] == "张三"
        assert people[0]["primary_user_id"] == "user_1"
        assert people[0]["source"] == "migration"
        affiliations = conn.execute(
            text("SELECT id, person_id FROM org_affiliations ORDER BY id")
        ).fetchall()
        assert [row[0] for row in affiliations] == ["aff_1", "aff_2"]
        assert {row[1] for row in affiliations} == {people[0]["id"]}
        head = conn.execute(text("SELECT person_id FROM org_department_heads WHERE id = 'head_1'")).first()
        assert head[0] == people[0]["id"]

    # Re-running the migration is a no-op.
    database._migrate_org_people_schema()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM enterprise_people")).scalar() == 1


def test_agent_primary_owner_backfills_from_metadata(tmp_path, monkeypatch) -> None:
    path = tmp_path / "agent-owner-migration.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE agent_profiles ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, name VARCHAR, metadata_json JSON)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_profiles (id, tenant_id, name, metadata_json) VALUES "
                "('agent_1', 'tenant_demo', '客户助手', '{\"owner_user_id\": \"user_1\"}')"
            )
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "database_url", f"sqlite:///{path}")
    database._migrate_agent_primary_owner()
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(agent_profiles)")).fetchall()}
        assert "primary_owner_user_id" in columns
        owner = conn.execute(
            text("SELECT primary_owner_user_id FROM agent_profiles WHERE id = 'agent_1'")
        ).scalar()
        assert owner == "user_1"


def test_agent_primary_owner_backfill_runs_only_once(tmp_path, monkeypatch) -> None:
    path = tmp_path / "agent-owner-once.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE agent_profiles ("
                "id VARCHAR PRIMARY KEY, tenant_id VARCHAR, name VARCHAR, metadata_json JSON)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_profiles (id, tenant_id, name, metadata_json) VALUES "
                "('agent_1', 'tenant_demo', '客户助手', "
                "'{\"owner_user_id\": \"user_1\", \"owner_display_name\": \"Owner\"}')"
            )
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "database_url", f"sqlite:///{path}")
    database._migrate_agent_primary_owner()
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT primary_owner_user_id FROM agent_profiles WHERE id = 'agent_1'")
        ).scalar() == "user_1"

        # 模拟管理员显式清空负责人(如删除账号后的解绑)。
        conn.execute(text("UPDATE agent_profiles SET primary_owner_user_id = NULL"))
        conn.commit()

    # 再次启动：守卫生效，不得把已删除/已清空的 owner 从 metadata 回填回来。
    database._migrate_agent_primary_owner()
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT primary_owner_user_id FROM agent_profiles WHERE id = 'agent_1'")
        ).scalar() is None

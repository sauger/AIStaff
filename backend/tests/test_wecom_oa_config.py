"""U3: 员工审批配置与控件白名单。"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, AgentResourceBinding, Tenant
from app.wecom_oa.policy import WecomOaConfigError, validate_approval_config
from app.wecom_oa.service import load_agent_approval_config, save_agent_approval_config


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _valid(**overrides):
    base = {
        "template_id": "tpl_expense",
        "scenario_key": "expense",
        "applicant_mode": "fixed",
        "mode_a_userid": "bot_user",
        "field_mappings": [
            {
                "control_id": "Text-1",
                "widget_type": "Text",
                "slot_key": "reason",
                "title": "事由",
            }
        ],
        "compliance_enabled": False,
    }
    base.update(overrides)
    return base


def test_valid_text_mapping_saves():
    cfg = validate_approval_config(_valid())
    assert cfg["template_id"] == "tpl_expense"
    assert cfg["field_mappings"][0]["control_id"] == "Text-1"


def test_file_widget_rejected_at_config_time():
    with pytest.raises(WecomOaConfigError, match="不支持的控件"):
        validate_approval_config(
            _valid(
                field_mappings=[
                    {
                        "control_id": "File-1",
                        "widget_type": "File",
                        "slot_key": "attach",
                    }
                ]
            )
        )


def test_mode_a_requires_userid():
    with pytest.raises(WecomOaConfigError, match="mode_a_userid"):
        validate_approval_config(_valid(mode_a_userid=None))


def test_two_agents_independent_templates():
    engine = _engine()
    with Session(engine) as db:
        db.add(Tenant(id="t1", name="T"))
        db.add(AgentProfile(id="a1", tenant_id="t1", name="A1"))
        db.add(AgentProfile(id="a2", tenant_id="t1", name="A2"))
        db.commit()
        save_agent_approval_config(
            db, tenant_id="t1", agent_id="a1", config=_valid(template_id="tpl_a")
        )
        save_agent_approval_config(
            db,
            tenant_id="t1",
            agent_id="a2",
            config=_valid(
                template_id="tpl_b",
                scenario_key="expense",
                applicant_mode="chat_partner",
                mode_a_userid=None,
            ),
        )
        c1 = load_agent_approval_config(
            db, tenant_id="t1", agent_id="a1", scenario_key="expense"
        )
        c2 = load_agent_approval_config(
            db, tenant_id="t1", agent_id="a2", scenario_key="expense"
        )
        assert c1["template_id"] == "tpl_a"
        assert c2["template_id"] == "tpl_b"
        assert c2["applicant_mode"] == "chat_partner"


def test_compliance_requires_policy_ref():
    with pytest.raises(WecomOaConfigError, match="compliance_policy_ref"):
        validate_approval_config(_valid(compliance_enabled=True))

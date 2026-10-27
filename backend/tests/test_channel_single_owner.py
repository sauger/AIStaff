from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.api.channels as channels_api
from app.db import get_session
from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelBindingAgent,
    ChatSession,
    Tenant,
    User,
    utc_now,
)
from app.security.auth import create_access_token


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _client(engine):
    app = FastAPI()
    app.include_router(channels_api.router)

    def override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _seed(engine) -> dict:
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        owner = User(
            id="user_owner",
            tenant_id="tenant_demo",
            username="owner",
            password_hash="x",
        )
        a1 = AgentProfile(
            id="agent_1",
            tenant_id="tenant_demo",
            name="员工甲",
            is_overall=False,
            metadata_json={"owner_user_id": owner.id, "owner_username": owner.username},
        )
        a2 = AgentProfile(
            id="agent_2",
            tenant_id="tenant_demo",
            name="员工乙",
            is_overall=False,
            metadata_json={"owner_user_id": owner.id, "owner_username": owner.username},
        )
        binding = ChannelBinding(
            id="chan_1",
            tenant_id="tenant_demo",
            agent_id="agent_1",
            channel="wecom",
            status="active",
            created_by_user_id=owner.id,
        )
        mount = ChannelBindingAgent(
            tenant_id="tenant_demo",
            binding_id="chan_1",
            agent_id="agent_1",
            is_default=True,
            sort_order=0,
        )
        db.add_all([owner, a1, a2, binding, mount])
        db.commit()
        db.refresh(owner)
        db.expunge(owner)
        return {"owner": owner, "binding_id": binding.id}


def test_put_rejects_two_agents() -> None:
    engine = _engine()
    seeded = _seed(engine)
    client = _client(engine)
    response = client.put(
        f"/api/enterprise/channels/{seeded['binding_id']}?tenant_id=tenant_demo",
        json={
            "agents": [
                {"agent_id": "agent_1", "is_default": True},
                {"agent_id": "agent_2", "is_default": False},
            ]
        },
        headers=_auth(seeded["owner"]),
    )
    assert response.status_code == 400
    assert "一个员工" in response.json()["detail"]
    with Session(engine) as db:
        rows = db.exec(
            select(ChannelBindingAgent).where(ChannelBindingAgent.binding_id == "chan_1")
        ).all()
        assert len(rows) == 1
        assert rows[0].agent_id == "agent_1"
        assert db.get(ChannelBinding, "chan_1").agent_id == "agent_1"


def test_put_single_agent_replaces_owner() -> None:
    engine = _engine()
    seeded = _seed(engine)
    client = _client(engine)
    response = client.put(
        f"/api/enterprise/channels/{seeded['binding_id']}?tenant_id=tenant_demo",
        json={"agents": [{"agent_id": "agent_2", "is_default": True}]},
        headers=_auth(seeded["owner"]),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_id"] == "agent_2"
    assert [(a["agent_id"], a["is_default"]) for a in payload["agents"]] == [
        ("agent_2", True)
    ]
    assert payload["auto_route"] is False
    with Session(engine) as db:
        binding = db.get(ChannelBinding, "chan_1")
        assert binding.agent_id == "agent_2"
        rows = db.exec(
            select(ChannelBindingAgent).where(ChannelBindingAgent.binding_id == "chan_1")
        ).all()
        assert [(r.agent_id, r.is_default) for r in rows] == [("agent_2", True)]


def test_put_auto_route_true_is_forced_false() -> None:
    engine = _engine()
    seeded = _seed(engine)
    client = _client(engine)
    response = client.put(
        f"/api/enterprise/channels/{seeded['binding_id']}?tenant_id=tenant_demo",
        json={"auto_route": True},
        headers=_auth(seeded["owner"]),
    )
    assert response.status_code == 200
    assert response.json()["auto_route"] is False
    with Session(engine) as db:
        assert db.get(ChannelBinding, "chan_1").config_json.get("auto_route") is False


def test_prune_multi_agent_mounts_keeps_owner_row() -> None:
    from app.channels.service_single_owner import prune_multi_agent_mounts

    engine = _engine()
    _seed(engine)
    with Session(engine) as db:
        db.add(
            ChannelBindingAgent(
                tenant_id="tenant_demo",
                binding_id="chan_1",
                agent_id="agent_2",
                is_default=False,
                sort_order=1,
            )
        )
        db.commit()
        removed = prune_multi_agent_mounts(db)
        db.commit()
        assert removed == 1
        rows = db.exec(
            select(ChannelBindingAgent).where(ChannelBindingAgent.binding_id == "chan_1")
        ).all()
        assert len(rows) == 1
        assert rows[0].agent_id == "agent_1"


def test_transfer_reassigns_binding_keeps_old_session_agent() -> None:
    engine = _engine()
    seeded = _seed(engine)
    with Session(engine) as db:
        db.add(
            ChatSession(
                id="sess_old",
                tenant_id="tenant_demo",
                agent_id="agent_1",
                user_id="user_owner",
                title="旧会话",
                channel_binding_id="chan_1",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        db.commit()
    client = _client(engine)
    response = client.post(
        f"/api/enterprise/channels/{seeded['binding_id']}/transfer?tenant_id=tenant_demo",
        json={"target_agent_id": "agent_2"},
        headers=_auth(seeded["owner"]),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_id"] == "agent_2"
    assert [(a["agent_id"], a["is_default"]) for a in payload["agents"]] == [
        ("agent_2", True)
    ]
    with Session(engine) as db:
        assert db.get(ChannelBinding, "chan_1").agent_id == "agent_2"
        assert db.get(ChatSession, "sess_old").agent_id == "agent_1"
        rows = db.exec(
            select(ChannelBindingAgent).where(ChannelBindingAgent.binding_id == "chan_1")
        ).all()
        assert [(r.agent_id, r.is_default) for r in rows] == [("agent_2", True)]

    listed = client.get(
        "/api/enterprise/channels?tenant_id=tenant_demo&agent_id=agent_2",
        headers=_auth(seeded["owner"]),
    )
    assert listed.status_code == 200
    assert any(row["id"] == "chan_1" for row in listed.json())

    listed_old = client.get(
        "/api/enterprise/channels?tenant_id=tenant_demo&agent_id=agent_1",
        headers=_auth(seeded["owner"]),
    )
    assert listed_old.status_code == 200
    assert all(row["id"] != "chan_1" for row in listed_old.json())


def test_transfer_404_unknown_binding() -> None:
    engine = _engine()
    seeded = _seed(engine)
    client = _client(engine)
    response = client.post(
        "/api/enterprise/channels/chan_missing/transfer?tenant_id=tenant_demo",
        json={"target_agent_id": "agent_2"},
        headers=_auth(seeded["owner"]),
    )
    assert response.status_code == 404


def test_transfer_rejects_same_agent() -> None:
    engine = _engine()
    seeded = _seed(engine)
    client = _client(engine)
    response = client.post(
        f"/api/enterprise/channels/{seeded['binding_id']}/transfer?tenant_id=tenant_demo",
        json={"target_agent_id": "agent_1"},
        headers=_auth(seeded["owner"]),
    )
    assert response.status_code == 400
    assert "目标员工" in response.json()["detail"]


def test_transfer_rejects_team_binding() -> None:
    engine = _engine()
    seeded = _seed(engine)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, "chan_1")
        binding.team_id = "team_x"
        db.add(binding)
        db.commit()
    client = _client(engine)
    response = client.post(
        f"/api/enterprise/channels/{seeded['binding_id']}/transfer?tenant_id=tenant_demo",
        json={"target_agent_id": "agent_2"},
        headers=_auth(seeded["owner"]),
    )
    assert response.status_code == 400
    assert "团队" in response.json()["detail"]

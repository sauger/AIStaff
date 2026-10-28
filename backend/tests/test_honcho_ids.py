from __future__ import annotations

import pytest

from app.honcho.ids import HonchoIdError, build_honcho_identity


def test_logical_ids_use_design_prefixes_and_wire_ids_replace_one_colon() -> None:
    identity = build_honcho_identity(
        tenant_id="tenant_demo",
        user_id="user_abc",
        agent_id="agent_xyz",
        session_id="session_1",
        workspace_prefix="tenant:",
    )

    assert identity.workspace_logical == "tenant:tenant_demo"
    assert identity.user_peer_logical == "user:user_abc"
    assert identity.agent_peer_logical == "agent:agent_xyz"
    assert identity.session_logical == "chat:session_1"
    assert identity.workspace_id == "tenant_tenant_demo"
    assert identity.user_peer_id == "user_user_abc"
    assert identity.agent_peer_id == "agent_agent_xyz"
    assert identity.session_id == "chat_session_1"
    assert ":" not in identity.workspace_id
    assert ":" not in identity.user_peer_id
    assert ":" not in identity.session_id


def test_channel_external_id_is_metadata_and_not_part_of_the_peer() -> None:
    identity = build_honcho_identity(
        tenant_id="tenant_demo",
        user_id="user_abc",
        agent_id="agent_xyz",
        session_id="session_1",
        channel="wecom",
        account_scope="corp_a",
        external_user_id="zhangsan",
    )

    assert identity.user_peer_id == "user_user_abc"
    assert "zhangsan" not in identity.user_peer_id
    assert "zhangsan" not in identity.session_id
    assert identity.user_peer_metadata["external_user_id"] == "zhangsan"
    assert identity.user_peer_metadata["channel"] == "wecom"
    assert identity.user_peer_metadata["account_scope"] == "corp_a"
    assert identity.user_peer_metadata["staff_peer"] == "user:user_abc"
    assert identity.session_metadata["external_user_id"] == "zhangsan"
    assert identity.session_metadata["staff_session"] == "chat:session_1"
    assert "external_user_id" not in identity.agent_peer_metadata
    assert identity.agent_peer_metadata["staff_peer"] == "agent:agent_xyz"


def test_same_external_id_on_different_users_stays_different_peers() -> None:
    wecom = build_honcho_identity(
        tenant_id="tenant_demo",
        user_id="user_wecom",
        agent_id="agent_xyz",
        session_id="session_wecom",
        channel="wecom",
        external_user_id="same-person",
    )
    feishu = build_honcho_identity(
        tenant_id="tenant_demo",
        user_id="user_feishu",
        agent_id="agent_xyz",
        session_id="session_feishu",
        channel="feishu",
        external_user_id="same-person",
    )

    assert wecom.user_peer_logical != feishu.user_peer_logical
    assert wecom.user_peer_id != feishu.user_peer_id
    assert wecom.user_peer_metadata["external_user_id"] == feishu.user_peer_metadata[
        "external_user_id"
    ]


def test_workspace_prefix_changes_only_the_workspace_id() -> None:
    identity = build_honcho_identity(
        tenant_id="tenant_demo",
        user_id="user_abc",
        agent_id="agent_xyz",
        session_id="session_1",
        workspace_prefix="acme:",
    )

    assert identity.workspace_logical == "acme:tenant_demo"
    assert identity.workspace_id == "acme_tenant_demo"
    assert identity.user_peer_id == "user_user_abc"


def test_raw_id_containing_colon_is_rejected() -> None:
    with pytest.raises(HonchoIdError):
        build_honcho_identity(
            tenant_id="tenant_demo",
            user_id="user:merged",
            agent_id="agent_xyz",
            session_id="session_1",
        )


def test_tenants_do_not_share_a_workspace() -> None:
    one = build_honcho_identity(
        tenant_id="tenant_a",
        user_id="user_abc",
        agent_id="agent_xyz",
        session_id="session_1",
    )
    two = build_honcho_identity(
        tenant_id="tenant_b",
        user_id="user_abc",
        agent_id="agent_xyz",
        session_id="session_1",
    )

    assert one.workspace_id != two.workspace_id
    assert one.user_peer_id == two.user_peer_id

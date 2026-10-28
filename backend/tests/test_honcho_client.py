from __future__ import annotations

import httpx
import pytest

from app.honcho.client import HonchoClient, HonchoRequestError, HonchoTimeout


def _client(handler, api_key: str = "") -> HonchoClient:
    return HonchoClient(
        base_url="http://honcho.test",
        api_key=api_key,
        transport=httpx.MockTransport(handler),
    )


def test_add_message_uses_peer_id_and_keeps_external_id_in_metadata() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/messages/"):
            return httpx.Response(200, json=[{"id": "msg_1"}])
        return httpx.Response(200, json={"id": "ok"})

    client = _client(handler, api_key="secret-token")
    client.ensure_workspace("tenant_demo", {"staff_workspace": "tenant:tenant_demo"})
    client.ensure_peer("tenant_demo", "user_user_abc", {"external_user_id": "zhangsan"})
    client.ensure_peer("tenant_demo", "agent_agent_xyz", {"staff_peer": "agent:agent_xyz"})
    client.ensure_session(
        "tenant_demo",
        "chat_session_1",
        ["user_user_abc", "agent_agent_xyz"],
        {"staff_session": "chat:session_1"},
    )
    client.add_message(
        "tenant_demo",
        "chat_session_1",
        peer_id="user_user_abc",
        content="hello",
        metadata={"external_user_id": "zhangsan", "channel": "wecom"},
    )

    paths = [request.url.path for request in seen]
    assert paths == [
        "/v2/workspaces",
        "/v2/workspaces/tenant_demo/peers",
        "/v2/workspaces/tenant_demo/peers",
        "/v2/workspaces/tenant_demo/sessions",
        "/v2/workspaces/tenant_demo/sessions/chat_session_1/messages/",
    ]
    assert all(request.headers["authorization"] == "Bearer secret-token" for request in seen)
    message = seen[-1].read().decode()
    assert "user_user_abc" in message
    assert "zhangsan" in message
    assert "zhangsan" not in paths[-1]


def test_omits_authorization_when_api_key_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"representation": "喜欢上午沟通"})

    text = _client(handler).fetch_representation(
        "tenant_demo",
        "user_user_abc",
        timeout_seconds=0.2,
    )

    assert text == "喜欢上午沟通"


def test_representation_normalizes_conclusion_lists() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"explicit": ["住在上海"], "deductive": ["偏好简短回复"]},
        )

    text = _client(handler).fetch_representation(
        "tenant_demo",
        "user_user_abc",
        timeout_seconds=0.2,
    )

    assert text == "住在上海\n偏好简短回复"


def test_http_5xx_becomes_request_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    with pytest.raises(HonchoRequestError) as raised:
        _client(handler).fetch_representation(
            "tenant_demo",
            "user_user_abc",
            timeout_seconds=0.2,
        )

    assert raised.value.status_code == 503


def test_transport_timeout_becomes_honcho_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    with pytest.raises(HonchoTimeout):
        _client(handler).fetch_representation(
            "tenant_demo",
            "user_user_abc",
            timeout_seconds=0.2,
        )

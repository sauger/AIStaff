# ruff: noqa: F811
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from sqlmodel import Session
from test_employee_secrets import (  # noqa: F401
    PASSWORD,
    _create_api_guide,
    _create_password,
    secret_db,
)

from app.agents.branching import ensure_private_resource_binding
from app.api.tools import create_tool, get_tool, probe_tool, update_tool
from app.db.models import Tool
from app.secrets.service import perform_secure_login
from app.tools import ToolExecutor
from app.tools.tool_schema import (
    ToolCall,
    ToolCreateRequest,
    ToolProbeRequest,
    ToolUpdateRequest,
)

BUSINESS_URL = "https://biz.example.test/api/report"
LOGIN_URL = "https://example.test/login"


class FakeNetwork:
    """Routes httpx traffic for the business API and the login endpoint."""

    def __init__(self) -> None:
        self.login_calls = 0
        self.login_token = "t1"
        self.login_ok = True
        self.business_requests: list[httpx.Request] = []
        self.accepted_token = "t1"
        self.business_override: Any = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == LOGIN_URL:
            self.login_calls += 1
            if not self.login_ok:
                return httpx.Response(
                    200, json={"success": False, "message": "bad credentials"}, request=request
                )
            return httpx.Response(
                200,
                json={"success": True, "data": {"token": self.login_token, "id": 3}},
                request=request,
            )
        self.business_requests.append(request)
        if self.business_override is not None:
            return self.business_override(request)
        if request.headers.get("Authorization") != self.accepted_token:
            return httpx.Response(401, json={"success": False, "message": "unauthorized"},
                                  request=request)
        return httpx.Response(200, json={"success": True, "data": {"rows": 1}}, request=request)


@pytest.fixture
def network(monkeypatch) -> FakeNetwork:
    net = FakeNetwork()
    real_client = httpx.Client

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(net.handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    return net


def _tool(
    db: Session,
    agent_id: str,
    *,
    name: str = "report.query",
    guide_id: str | None = None,
    auth: dict[str, Any] | None = None,
    method: str = "GET",
) -> Tool:
    tool = Tool(
        tenant_id="tenant_demo",
        name=name,
        method=method,
        url=BUSINESS_URL,
        auth_json=auth or {},
        login_guide_id=guide_id,
    )
    db.add(tool)
    db.flush()
    ensure_private_resource_binding(db, "tenant_demo", agent_id, "tool", tool.id, "active")
    db.commit()
    db.refresh(tool)
    return tool


def _run(db: Session, agent_id: str, name: str = "report.query"):
    return ToolExecutor(db).execute(
        "tenant_demo", ToolCall(name=name, arguments={"period": "2026-05"}), agent_id=agent_id
    )


def _setup(secret_db):
    db, (owner, _other, _admin, agent_a, _agent_b), _fake = secret_db
    _create_password(db, owner, agent_a.id)
    guide = _create_api_guide(db, owner, agent_a.id)
    return db, owner, agent_a, guide


def test_unbound_tool_never_logs_in_and_keeps_auth_json_bearer(secret_db, network) -> None:
    db, _owner, agent_a, _guide = _setup(secret_db)
    network.accepted_token = "Bearer static-token"
    _tool(db, agent_a.id, auth={"type": "bearer", "token": "static-token"})

    result = _run(db, agent_a.id)

    assert result.success is True
    assert network.login_calls == 0
    assert network.business_requests[0].headers["Authorization"] == "Bearer static-token"


def test_unbound_tool_unauthorized_keeps_http_error_without_login(secret_db, network) -> None:
    db, _owner, agent_a, _guide = _setup(secret_db)
    _tool(db, agent_a.id)

    result = _run(db, agent_a.id)

    assert result.success is False
    assert result.error is not None and result.error.code == "HTTP_ERROR"
    assert network.login_calls == 0
    assert len(network.business_requests) == 1


def test_two_tools_share_one_guide_session(secret_db, network) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, name="report.one", guide_id=guide.id)
    _tool(db, agent_a.id, name="report.two", guide_id=guide.id)

    first = _run(db, agent_a.id, "report.one")
    assert first.success is True
    assert network.login_calls == 1

    second = _run(db, agent_a.id, "report.two")
    assert second.success is True
    assert network.login_calls == 1
    last = network.business_requests[-1]
    assert last.headers["Authorization"] == "t1"
    assert last.headers["New-Api-User"] == "3"


def test_business_401_refreshes_once_and_retries(secret_db, network) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, guide_id=guide.id)
    perform_secure_login(db, "tenant_demo", agent_a.id, login_guide_id=guide.id)
    assert network.login_calls == 1
    network.login_token = "t2"
    network.accepted_token = "t2"

    result = _run(db, agent_a.id)

    assert result.success is True
    assert network.login_calls == 2
    assert [r.headers.get("Authorization") for r in network.business_requests] == ["t1", "t2"]


def test_second_401_in_same_call_does_not_login_again(secret_db, network) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, guide_id=guide.id)
    network.accepted_token = "never-matches"

    result = _run(db, agent_a.id)

    assert result.success is False
    assert result.error is not None and result.error.code == "HTTP_ERROR"
    assert network.login_calls == 1
    assert len(network.business_requests) == 2


def test_json_unauthenticated_on_200_refreshes_once(secret_db, network) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, guide_id=guide.id)
    perform_secure_login(db, "tenant_demo", agent_a.id, login_guide_id=guide.id)
    network.login_token = "t2"

    def business(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") == "t2":
            return httpx.Response(200, json={"success": True, "data": {}}, request=request)
        return httpx.Response(200, json={"success": False, "message": "未登录"}, request=request)

    network.business_override = business

    result = _run(db, agent_a.id)

    assert result.success is True
    assert network.login_calls == 2


def test_business_400_invalid_period_does_not_login(secret_db, network) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, guide_id=guide.id)
    perform_secure_login(db, "tenant_demo", agent_a.id, login_guide_id=guide.id)
    assert network.login_calls == 1

    def business(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"success": False, "message": "period 无效"}, request=request
        )

    network.business_override = business

    result = _run(db, agent_a.id)

    assert result.success is False
    assert result.error is not None and result.error.code == "HTTP_ERROR"
    assert network.login_calls == 1
    assert len(network.business_requests) == 1


@pytest.mark.parametrize(
    "auth",
    [
        {"Authorization": "static-literal", "X-Extra": "keep"},
        {"type": "bearer", "token": "static-bearer"},
    ],
)
def test_session_template_overrides_auth_json_authorization(secret_db, network, auth) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, guide_id=guide.id, auth=auth)
    perform_secure_login(db, "tenant_demo", agent_a.id, login_guide_id=guide.id)

    result = _run(db, agent_a.id)

    assert result.success is True
    assert network.login_calls == 1
    request = network.business_requests[-1]
    assert request.headers["Authorization"] == "t1"
    assert request.headers["New-Api-User"] == "3"
    if "X-Extra" in auth:
        assert request.headers["X-Extra"] == "keep"


def test_login_failure_error_never_leaks_credentials(secret_db, network) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, guide_id=guide.id)
    network.login_ok = False

    result = _run(db, agent_a.id)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "LOGIN_FAILED"
    dumped = json.dumps(result.model_dump(), ensure_ascii=False)
    assert PASSWORD not in dumped
    assert network.login_calls == 1
    assert len(network.business_requests) == 1


def test_bound_tool_without_agent_skips_login(secret_db, network) -> None:
    db, _owner, agent_a, guide = _setup(secret_db)
    _tool(db, agent_a.id, guide_id=guide.id)

    result = ToolExecutor(db).execute(
        "tenant_demo", ToolCall(name="report.query", arguments={})
    )

    assert result.success is False
    assert network.login_calls == 0


def test_probe_uses_session_inject_and_refresh(secret_db, network) -> None:
    db, owner, agent_a, guide = _setup(secret_db)

    response = probe_tool(
        ToolProbeRequest(
            tenant_id="tenant_demo",
            name="probe.report",
            method="GET",
            url=BUSINESS_URL,
            sample_arguments={"period": "2026-05"},
            login_guide_id=guide.id,
        ),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )

    assert response.success is True
    assert response.status_code == 200
    assert network.login_calls == 1
    assert network.business_requests[-1].headers["Authorization"] == "t1"
    assert "t1" not in json.dumps(response.model_dump(), ensure_ascii=False)


def test_probe_without_guide_does_not_login(secret_db, network) -> None:
    db, owner, agent_a, _guide = _setup(secret_db)
    network.accepted_token = "x"

    response = probe_tool(
        ToolProbeRequest(
            tenant_id="tenant_demo", name="probe.report", method="GET", url=BUSINESS_URL
        ),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )

    assert response.success is False
    assert response.status_code == 401
    assert network.login_calls == 0


def test_tool_api_round_trips_login_guide_id(secret_db) -> None:
    db, owner, agent_a, guide = _setup(secret_db)

    created = create_tool(
        ToolCreateRequest(
            tenant_id="tenant_demo",
            name="report.api",
            method="GET",
            url=BUSINESS_URL,
            login_guide_id=guide.id,
        ),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert created.login_guide_id == guide.id
    fetched = get_tool(created.id, tenant_id="tenant_demo", agent_id=agent_a.id, db=db)
    assert fetched.login_guide_id == guide.id

    kept = update_tool(
        created.id,
        ToolUpdateRequest(
            tenant_id="tenant_demo", name="report.api", method="GET", url=BUSINESS_URL
        ),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert kept.login_guide_id == guide.id

    cleared = update_tool(
        created.id,
        ToolUpdateRequest(
            tenant_id="tenant_demo",
            name="report.api",
            method="GET",
            url=BUSINESS_URL,
            login_guide_id=None,
        ),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    assert cleared.login_guide_id is None


def test_tool_api_rejects_unknown_or_foreign_guide(secret_db) -> None:
    db, owner, agent_a, _guide = _setup(secret_db)

    with pytest.raises(HTTPException) as exc:
        create_tool(
            ToolCreateRequest(
                tenant_id="tenant_demo",
                name="report.bad",
                method="GET",
                url=BUSINESS_URL,
                login_guide_id="missing-guide",
            ),
            agent_id=agent_a.id,
            db=db,
            current_user=owner,
        )
    assert exc.value.status_code == 400

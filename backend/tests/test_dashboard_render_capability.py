from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.capability_manifest import CapabilityManifestBuilder, _dashboard_render_descriptor
from app.dashboard.chart_policy import normalize_spec
from app.dashboard.harness import invoke_dashboard_tool
from app.dashboard.service import create_view, get_view, is_expired, spec_digest
from app.dashboard.spec import RenderInput
from app.db.models import AgentProfile, ChatSession, DashboardView, Tenant


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db() -> Session:
    engine = _engine()
    with Session(engine) as session:
        session.add(Tenant(id="tenant_demo", name="Demo"))
        session.add(
            AgentProfile(id="agent_1", tenant_id="tenant_demo", name="员工A", is_overall=False)
        )
        session.add(
            ChatSession(id="session_1", tenant_id="tenant_demo", user_id="user_1", agent_id="agent_1")
        )
        session.commit()
        yield session


def _invoker(db: Session, events: list[tuple[str, dict]] | None = None):
    session = db.get(ChatSession, "session_1")
    sink = events if events is not None else []

    def emit(event_type: str, payload: dict) -> None:
        sink.append((event_type, payload))

    return SimpleNamespace(
        db=db,
        tenant_id="tenant_demo",
        agent_id="agent_1",
        session=session,
        _emit_trace=emit,
        events=sink,
    )


def _payload(chart_type=None, intent="trend", rows=None):
    return {
        "title": "华东区销售",
        "as_of": "2026-09-30",
        "kpis": [{"label": "销售额", "value": "1,284 万"}],
        "charts": [
            {
                "id": "trend",
                "title": "月度趋势",
                "intent": intent,
                "type": chart_type,
                "encoding": {"x": "month", "y": "sales"},
                "data": {
                    "columns": [
                        {"name": "month", "type": "string"},
                        {"name": "sales", "type": "number"},
                    ],
                    "rows": rows or [["2026-07", 1], ["2026-08", 2], ["2026-09", 3]],
                },
            }
        ],
        "meta": {"source": "erp.sales"},
    }


def test_render_persists_view_and_emits_event(db: Session) -> None:
    invoker = _invoker(db)
    result = invoke_dashboard_tool(invoker, _payload())

    assert result["success"] is True
    view_id = result["data"]["view_id"]
    view = get_view(db, tenant_id="tenant_demo", view_id=view_id)
    assert view is not None
    assert view.session_id == "session_1"
    assert view.title == "华东区销售"
    assert view.spec_json["charts"][0]["type"] == "line"

    assert len(invoker.events) == 1
    event_type, payload = invoker.events[0]
    assert event_type == "harness_dashboard_view"
    assert payload["view_id"] == view_id
    assert payload["chart_count"] == 1
    assert "调整" not in str(payload)


def test_invalid_spec_returns_field_paths(db: Session) -> None:
    invoker = _invoker(db)
    payload = _payload()
    payload["charts"][0]["encoding"]["y"] = "missing"
    result = invoke_dashboard_tool(invoker, payload)

    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_SPEC"
    assert any("encoding" in item["path"] for item in result["error"]["errors"])
    assert invoker.events == []


def test_downgraded_chart_is_reported_to_the_model(db: Session) -> None:
    invoker = _invoker(db)
    payload = _payload(chart_type="pie", intent="composition")
    payload["charts"][0]["data"] = {
        "columns": [{"name": "month", "type": "string"}, {"name": "sales", "type": "number"}],
        "rows": [[f"cat{index}", 7 - index] for index in range(7)],
    }
    result = invoke_dashboard_tool(invoker, payload)

    assert result["success"] is True
    adjustments = result["data"]["adjustments"]
    assert adjustments and adjustments[0]["from"] == "pie"
    assert adjustments[0]["to"] == "bar"
    assert "饼图" in adjustments[0]["reason"]


def test_same_spec_reuses_the_existing_view(db: Session) -> None:
    invoker = _invoker(db)
    first = invoke_dashboard_tool(invoker, _payload())
    second = invoke_dashboard_tool(invoker, _payload())

    assert first["data"]["view_id"] == second["data"]["view_id"]
    assert second["data"]["reused"] is True
    assert len(invoker.events) == 2


def test_view_is_scoped_by_tenant(db: Session) -> None:
    invoker = _invoker(db)
    view_id = invoke_dashboard_tool(invoker, _payload())["data"]["view_id"]
    assert get_view(db, tenant_id="tenant_other", view_id=view_id) is None


def test_expired_view_is_detected(db: Session) -> None:
    view = create_view(
        db,
        tenant_id="tenant_demo",
        session_id="session_1",
        spec=normalize_spec(RenderInput.model_validate(_payload()))[0],
        ttl=timedelta(seconds=-1),
    )[0]
    assert is_expired(view) is True


def test_digest_ignores_key_order() -> None:
    first = normalize_spec(RenderInput.model_validate(_payload()))[0]
    second = normalize_spec(RenderInput.model_validate(_payload()))[0]
    assert spec_digest(first) == spec_digest(second)


def test_capability_appears_in_the_employee_manifest(db: Session) -> None:
    manifest = CapabilityManifestBuilder(db).build("tenant_demo", "agent_1", None, None)
    names = {item.name for item in manifest.available}
    assert "render_dashboard" in names
    descriptor = next(item for item in manifest.available if item.name == "render_dashboard")
    assert descriptor.read_only is True
    assert descriptor.metadata.get("provider") == "dashboard"
    assert "$ref" not in str(descriptor.input_schema)


def test_descriptor_is_flat_and_read_only() -> None:
    descriptor = _dashboard_render_descriptor()
    assert descriptor.name == "render_dashboard"
    assert descriptor.kind == "internal"
    assert descriptor.read_only is True
    dumped = str(descriptor.input_schema)
    assert "$ref" not in dumped
    assert "intent" in descriptor.input_schema["properties"]["charts"]["items"]["properties"]


def test_missing_session_returns_error(db: Session) -> None:
    invoker = _invoker(db)
    invoker.session = SimpleNamespace(id="")
    result = invoke_dashboard_tool(invoker, _payload())
    assert result["success"] is False
    assert result["error"]["code"] == "NO_SESSION"


def test_view_rows_are_not_stored_in_the_event(db: Session) -> None:
    invoker = _invoker(db)
    invoke_dashboard_tool(invoker, _payload())
    _event_type, payload = invoker.events[0]
    assert "spec" not in payload
    assert "rows" not in str(payload)
    assert isinstance(payload["view_id"], str)


def test_view_tables_are_created(db: Session) -> None:
    assert db.get(DashboardView, "missing") is None

"""compose_dashboard through the Harness: reference resolution, audit and events."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.capability_discovery import model_descriptor
from app.core.capability_manifest import (
    CapabilityManifestBuilder,
    _dashboard_compose_descriptor,
    _dashboard_render_descriptor,
    tool_snapshot_digest,
)
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.core.task_request_compiler import CapabilityManifest
from app.dashboard.harness import invoke_compose_tool
from app.db.models import (
    AgentProfile,
    ChatSession,
    DashboardView,
    HarnessInvocationRecord,
    ModelConfig,
    Tenant,
    Tool,
)
from app.tools.tool_schema import ToolResult

DEMO = {
    "as_of": "2026-09-30",
    "blocks": [
        {"name": "销售额", "value": 12840000, "unit": "元", "delta": 0.12},
        {"name": "订单数", "value": 3821, "unit": "单", "delta": -0.03},
        {
            "name": "月度趋势",
            "columns": [{"name": "month", "type": "date"}, {"name": "sales", "type": "number"}],
            "rows": [["2026-07", 431], ["2026-08", 452], ["2026-09", 433]],
        },
        {
            "name": "渠道分布",
            "columns": [{"name": "channel"}, {"name": "amount", "type": "number"}],
            "rows": [["直营", 310], ["经销", 280], ["线上", 190]],
        },
        {
            "name": "区域排名",
            "columns": [{"name": "region"}, {"name": "sales", "type": "number"}],
            # long enough that the data tool result spills to a sandbox_json_file reference
            "rows": [[f"城市{index:02d}-{'甲' * 12}", 900 - index * 7] for index in range(60)],
        },
    ],
}


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
    with Session(_engine()) as session:
        session.add(Tenant(id="tenant_demo", name="Demo"))
        session.add(AgentProfile(id="agent_1", tenant_id="tenant_demo", name="员工A", is_overall=False))
        session.add(ChatSession(id="session_1", tenant_id="tenant_demo", user_id="user_1", agent_id="agent_1"))
        session.commit()
        yield session


def _fake_invoker(db: Session):
    events: list[tuple[str, dict]] = []
    return SimpleNamespace(
        db=db,
        tenant_id="tenant_demo",
        agent_id="agent_1",
        session=db.get(ChatSession, "session_1"),
        _emit_trace=lambda event_type, payload: events.append((event_type, payload)),
        events=events,
    )


def _harness(db: Session, tmp_path, monkeypatch, payload=DEMO):
    """A real invoker whose data tool returns ``payload`` (spilled to a reference when large)."""

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    tool = Tool(
        id="tool-sales",
        tenant_id="tenant_demo",
        name="query_sales",
        method="GET",
        url="https://example.test/sales",
    )
    db.add(tool)
    db.add(ModelConfig(id="model-test", tenant_id="tenant_demo", name="m", api_key_encrypted="x", model="m"))
    db.commit()

    monkeypatch.setattr(
        "app.core.harness_capability_invoker.ToolExecutor.execute",
        lambda _self, _tenant, call, **_kwargs: ToolResult(tool_name=call.name, success=True, data=payload),
    )
    events: list[tuple[str, dict]] = []
    invoker = HarnessCapabilityInvoker(
        db,
        tenant_id="tenant_demo",
        session=db.get(ChatSession, "session_1"),
        task_frame_id="task-dashboard",
        model_config=db.get(ModelConfig, "model-test"),
        manifest=CapabilityManifest(
            available=[_dashboard_compose_descriptor(), _dashboard_render_descriptor()]
        ),
        active_skill=None,
        active_step_id=None,
        agent_id="agent_1",
        trace_sink=lambda event_type, event_payload: events.append((event_type, event_payload)),
    )
    result = invoker._invoke_external_tool(
        tool.id,
        {"source_tool_name": tool.name, "content_digest": tool_snapshot_digest(db, tool)},
        tool.name,
        {},
        call_id="hcall-sales",
    )
    return invoker, result, events


def _audit(db: Session, tool_name: str) -> HarnessInvocationRecord:
    return db.exec(
        select(HarnessInvocationRecord).where(HarnessInvocationRecord.tool_name == tool_name)
    ).one()


def test_large_tool_result_composes_through_its_reference(db: Session, tmp_path, monkeypatch) -> None:
    invoker, tool_result, events = _harness(db, tmp_path, monkeypatch)
    reference = tool_result["data"]
    assert reference["kind"] == "sandbox_json_file"

    result = invoker.invoke(
        "compose_dashboard", {"title": "华东区 Q3 经营看板", "data": reference, "theme": "tech"}
    )

    assert result["success"] is True, result
    data = result["data"]
    assert (data["kpi_count"], data["chart_count"], data["template"]) == (2, 3, "pulse")
    assert data["reused"] is False
    view = db.get(DashboardView, data["view_id"])
    assert view.spec_version == 2
    assert view.spec_json["layout"]["template"] == "pulse"
    ranking = next(chart for chart in view.spec_json["charts"] if chart["role"] == "ranking")
    assert ranking["orientation"] == "horizontal"

    audit = _audit(db, "compose_dashboard")
    assert audit.status == "completed"
    assert audit.arguments_json["data"] == reference
    audited = json.dumps(audit.arguments_json, ensure_ascii=False)
    assert "城市00" not in audited and "blocks" not in audited
    cached = json.dumps(audit.response_cache_json, ensure_ascii=False)
    assert "城市01" not in cached and "rows" not in cached

    assert [event_type for event_type, _payload in events] == [
        "harness_dashboard_composed",
        "harness_dashboard_view",
    ]
    for _event_type, payload in events:
        dumped = json.dumps(payload, ensure_ascii=False)
        assert "城市" not in dumped and "rows" not in dumped


def test_view_event_carries_v2_fields_only_as_references(db: Session, tmp_path, monkeypatch) -> None:
    invoker, tool_result, events = _harness(db, tmp_path, monkeypatch)
    invoker.invoke("compose_dashboard", {"title": "看板", "data": tool_result["data"]})
    composed = dict(events)["harness_dashboard_composed"]
    assert composed["blocks_found"] == 5
    assert composed["blocks_used"] == 5
    assert composed["template"] == "pulse"
    view_event = dict(events)["harness_dashboard_view"]
    assert view_event["tool_name"] == "compose_dashboard"
    assert view_event["spec_version"] == 2
    assert view_event["template"] == "pulse"
    assert isinstance(view_event["warnings"], list)
    assert set(view_event) == {
        "view_id",
        "tenant_id",
        "title",
        "kpi_count",
        "chart_count",
        "template",
        "spec_version",
        "warnings",
        "reused",
        "tool_name",
    }


def test_stale_references_ask_for_a_fresh_tool_call(db: Session, tmp_path, monkeypatch) -> None:
    invoker, tool_result, _events = _harness(db, tmp_path, monkeypatch)
    reference = tool_result["data"]
    stored = invoker.workspace_root / ".harness" / "tool-results" / "hcall-sales.json"

    stored.write_text(json.dumps({"blocks": []}), encoding="utf-8")
    changed = invoker.invoke("compose_dashboard", {"title": "看板", "data": reference})
    assert changed["error"]["code"] == "TOOL_RESULT_REFERENCE_CHANGED"
    assert changed["error"]["retryable"] is True
    assert "重新调用数据工具" in changed["error"]["message"]

    stored.unlink()
    missing = invoker.invoke("compose_dashboard", {"title": "看板2", "data": reference})
    assert missing["error"]["code"] == "TOOL_RESULT_REFERENCE_UNAVAILABLE"
    assert missing["error"]["retryable"] is True

    forged = dict(reference, sandbox_path="/workspace/../etc/passwd.json")
    invalid = invoker.invoke("compose_dashboard", {"title": "看板3", "data": forged})
    assert invalid["error"]["code"] == "INVALID_TOOL_RESULT_REFERENCE"
    assert invalid["error"]["retryable"] is False
    assert db.exec(select(DashboardView)).all() == []


def test_render_dashboard_never_resolves_references(db: Session, tmp_path, monkeypatch) -> None:
    invoker, tool_result, _events = _harness(db, tmp_path, monkeypatch)
    result = invoker.invoke(
        "render_dashboard",
        {"title": "旧能力", "kpis": [{"label": "引用", "value": tool_result["data"]}]},
    )
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_SPEC"


def test_small_inline_results_compose_directly(db: Session) -> None:
    invoker = _fake_invoker(db)
    result = invoke_compose_tool(invoker, {"title": "小结果", "data": {"success": True, "data": DEMO}})
    assert result["success"] is True
    assert result["data"]["blocks"][0] == {
        "id": "kpi-data-blocks-0",
        "path": "data.blocks[0]",
        "title": "销售额",
        "component": "kpi",
        "role": "kpi",
    }
    assert {"view_id", "title", "kpi_count", "chart_count", "template", "blocks", "warnings", "reused", "notice"} == set(
        result["data"]
    )


def test_same_input_reuses_the_view(db: Session) -> None:
    invoker = _fake_invoker(db)
    first = invoke_compose_tool(invoker, {"title": "看板", "data": DEMO})
    second = invoke_compose_tool(invoker, {"title": "看板", "data": DEMO})
    assert first["data"]["view_id"] == second["data"]["view_id"]
    assert second["data"]["reused"] is True


def test_outline_mode_stores_nothing_and_emits_nothing(db: Session) -> None:
    invoker = _fake_invoker(db)
    result = invoke_compose_tool(invoker, {"title": "看板", "data": DEMO, "mode": "outline"})
    assert result["success"] is True
    assert result["data"]["mode"] == "outline"
    assert invoker.events == []
    assert db.exec(select(DashboardView)).all() == []


def test_failures_use_the_contract_shape(db: Session) -> None:
    invoker = _fake_invoker(db)
    empty = invoke_compose_tool(invoker, {"title": "看板", "data": []})
    assert empty == {
        "success": False,
        "error": {
            "code": "EMPTY_DATA",
            "message": empty["error"]["message"],
            "retryable": False,
            "details": empty["error"]["details"],
        },
    }
    assert invoke_compose_tool(invoker, "nope")["error"]["code"] == "INVALID_ARGUMENTS"
    invoker.session = SimpleNamespace(id="")
    assert invoke_compose_tool(invoker, {"title": "x", "data": DEMO})["error"]["code"] == "NO_SESSION"
    assert invoker.events == []


def test_descriptor_is_flat_internal_and_within_the_description_budget(db: Session) -> None:
    descriptor = _dashboard_compose_descriptor()
    assert (descriptor.name, descriptor.kind, descriptor.read_only) == ("compose_dashboard", "internal", True)
    assert len(descriptor.description) <= 1000
    assert model_descriptor(descriptor).description == descriptor.description
    assert "$ref" not in json.dumps(descriptor.input_schema)
    render = _dashboard_render_descriptor()
    assert len(render.description) <= 1000
    assert "compose_dashboard" in render.description

    manifest = CapabilityManifestBuilder(db).build("tenant_demo", "agent_1", None, None)
    names = [item.name for item in manifest.available]
    assert "compose_dashboard" in names and "render_dashboard" in names

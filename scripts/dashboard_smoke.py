"""Smoke-test the dashboard render capability without going through a model.

Dry run (default) validates + normalizes a sample spec and prints the chart
decisions.  ``--write`` additionally persists a view in the current database so
``GET /api/chat/dashboard-views/{id}`` can be exercised.  ``--compose`` runs the
phase-2 pipeline (normalize → split → select → layout) over a dashboard fixture.

Run from ``backend/`` so the default sqlite URL resolves to the dev database:

    cd backend && .venv/bin/python ../scripts/dashboard_smoke.py
    cd backend && .venv/bin/python ../scripts/dashboard_smoke.py --compose demo_blocks
    cd backend && .venv/bin/python ../scripts/dashboard_smoke.py --compose demo_blocks --write
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from app.dashboard.chart_policy import normalize_spec
from app.dashboard.spec import RenderInput
from app.db.database import engine, init_db
from app.db.models import ChatSession
from sqlmodel import Session, select

FIXTURES = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "dashboard"

SAMPLE = {
    "title": "华东区 Q3 经营看板",
    "subtitle": "冒烟测试数据",
    "as_of": "2026-09-30",
    "theme": "tech",
    "kpis": [
        {"label": "销售额", "value": "1,284 万", "delta": 0.12, "hint": "环比"},
        {"label": "订单数", "value": 3821},
    ],
    "charts": [
        {
            "id": "trend",
            "title": "月度趋势（只有 2 个点，应降级为柱状）",
            "intent": "trend",
            "span": 2,
            "encoding": {"x": "month", "y": "sales"},
            "data": {
                "columns": [
                    {"name": "month", "type": "string"},
                    {"name": "sales", "type": "number"},
                ],
                "rows": [["2026-08", 431], ["2026-09", 433]],
            },
        },
        {
            "id": "share",
            "title": "渠道占比（3 个类目，应出环形图）",
            "intent": "composition",
            "encoding": {"x": "channel", "y": "amount"},
            "data": {
                "columns": [
                    {"name": "channel", "type": "string"},
                    {"name": "amount", "type": "number"},
                ],
                "rows": [
                    ["直营", 310],
                    ["天猫", 280],
                    ["京东", 220],
                ],
            },
        },
        {
            "id": "detail",
            "title": "门店明细",
            "intent": "detail",
            "encoding": {},
            "data": {
                "columns": [
                    {"name": "store", "type": "string"},
                    {"name": "amount", "type": "number"},
                ],
                "rows": [["静安店", 1200], ["徐汇店", 980]],
            },
        },
    ],
    "filters": [{"id": "region", "label": "区域", "type": "select", "default": "华东"}],
    "meta": {"source": "smoke-test"},
}


def load_fixture(name: str) -> dict:
    path = Path(name)
    if not path.exists():
        candidate = FIXTURES / (name if name.endswith(".json") else f"{name}.json")
        if not candidate.exists():
            raise SystemExit(f"fixture not found: {name} (looked in {FIXTURES})")
        path = candidate
    return json.loads(path.read_text(encoding="utf-8"))


def run_compose(name: str, *, write: bool) -> int:
    from app.dashboard.compose import ComposeError, compose_dashboard

    data = load_fixture(name)
    arguments = {
        "title": f"Compose smoke · {Path(name).stem}",
        "data": data,
        "theme": "tech",
        "mode": "render",
    }
    try:
        result = compose_dashboard(arguments)
    except ComposeError as exc:
        print(f"compose failed: {exc.code} {exc.message}")
        return 1
    assert result.spec is not None
    spec = result.spec
    print(f"title      : {spec.title}")
    print(f"template   : {result.template}")
    print(f"kpis       : {len(spec.kpis)}")
    for kpi in spec.kpis:
        print(f"kpi        : {kpi.id:<14} {kpi.label}")
    for chart in spec.charts:
        print(f"chart      : {chart.id:<14} {chart.type:<14} role={chart.role or '-'}")
    assert spec.layout is not None
    for slot in spec.layout.slots:
        print(
            f"slot       : {slot.id:<14} {slot.kind:<5} {slot.region:<9} "
            f"x={slot.x} y={slot.y} w={slot.w} h={slot.h}"
        )
    for warning in spec.warnings:
        print(f"warning    : {warning.code} {warning.message}")
    print(f"blocks     : found={result.blocks_found} used={len(result.used)}")

    if not write:
        print("\ndry run only; add --write to persist a view")
        return 0

    init_db()
    with Session(engine) as db:
        session = db.exec(
            select(ChatSession)
            .where(ChatSession.tenant_id == "tenant_demo")
            .order_by(ChatSession.id.desc())
        ).first()
        if session is None:
            print("no session found; pass --write only after seeding a demo session")
            return 2
        events: list[tuple[str, dict]] = []

        def emit(event_type: str, payload: dict) -> None:
            events.append((event_type, payload))

        from app.dashboard.harness import invoke_compose_tool

        invoker = SimpleNamespace(
            db=db,
            tenant_id=session.tenant_id,
            agent_id=session.agent_id,
            session=session,
            _emit_trace=emit,
        )
        response = invoke_compose_tool(invoker, arguments)
        print("\nresult     :", json.dumps(response, ensure_ascii=False))
        if response.get("success"):
            view_id = response["data"]["view_id"]
            print(f"\nview id    : {view_id}")
            print(f"session    : {session.id} (user {session.user_id})")
            print(f"fetch      : GET /api/chat/dashboard-views/{view_id}?tenant_id={session.tenant_id}")
            print(f"share      : GET /api/chat/dashboard-views/{view_id} -> share_url")
        return 0 if response.get("success") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="persist the view to the database")
    parser.add_argument("--session", default="", help="session id to attach the view to")
    parser.add_argument("--user", default="admin", help="preferred session owner (default: admin)")
    parser.add_argument("--compose", default="", help="run compose over a fixture name/path")
    args = parser.parse_args()

    if args.compose:
        return run_compose(args.compose, write=args.write)

    spec, adjustments = normalize_spec(RenderInput.model_validate(SAMPLE))
    print(f"title      : {spec.title}")
    print(f"kpis       : {len(spec.kpis)}")
    for chart in spec.charts:
        note = f"  // {chart.note}" if chart.note else ""
        print(f"chart      : {chart.id:<8} {chart.type}{note}")
    for item in adjustments:
        print(f"adjustment : {item.chart_id} {item.from_type or '-'} -> {item.to_type} ({item.reason})")

    if not args.write:
        print("\ndry run only; add --write to persist a view")
        return 0

    init_db()
    with Session(engine) as db:
        session = (
            db.get(ChatSession, args.session)
            if args.session
            else db.exec(
                select(ChatSession)
                .where(
                    ChatSession.tenant_id == "tenant_demo",
                    ChatSession.user_id == args.user,
                )
                .order_by(ChatSession.id.desc())
            ).first()
            or db.exec(
                select(ChatSession)
                .where(ChatSession.tenant_id == "tenant_demo")
                .order_by(ChatSession.id.desc())
            ).first()
        )
        if session is None:
            print("no session found; pass --session")
            return 2

        events: list[tuple[str, dict]] = []

        def emit(event_type: str, payload: dict) -> None:
            events.append((event_type, payload))

        from app.dashboard.harness import invoke_dashboard_tool

        invoker = SimpleNamespace(
            db=db,
            tenant_id=session.tenant_id,
            agent_id=session.agent_id,
            session=session,
            _emit_trace=emit,
        )
        result = invoke_dashboard_tool(invoker, SAMPLE)
        print("\nresult     :", json.dumps(result, ensure_ascii=False))
        if result.get("success"):
            view_id = result["data"]["view_id"]
            print(f"\nview id    : {view_id}")
            print(f"session    : {session.id} (user {session.user_id})")
            print(f"fetch      : GET /api/chat/dashboard-views/{view_id}?tenant_id={session.tenant_id}")
            print(f"cleanup    : DELETE FROM dashboard_views WHERE id = '{view_id}';")
        return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

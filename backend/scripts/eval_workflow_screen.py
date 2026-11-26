"""Deterministic eval for the screen workflow pack.

# ruff: noqa: E402, I001

Runs T1-T4 through the real ``AgentLoop.handle_turn`` / ``HarnessV2Engine.run``
with a scripted planner and an in-memory MCP fake. This measures orchestration
correctness (tools, parameters, stop condition, asking) without spending model
tokens; latency numbers are in-process and not a substitute for live timing.

Usage:
    cd backend && .venv/bin/python scripts/eval_workflow_screen.py --runs 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import test_workflow_e2e as e2e
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, ChatSession, Tenant
from app.session.turn_context import ScreenTurnContext, SelectedComponent


def _fresh_db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(Tenant(id="tenant-demo", name="Demo"))
    session.add(
        AgentProfile(id="agent-1", tenant_id="tenant-demo", name="数字大屏助手")
    )
    session.add(
        ChatSession(
            id="sess-1",
            tenant_id="tenant-demo",
            agent_id="agent-1",
            user_id="user-1",
        )
    )
    session.commit()
    return session


def _plan_call(screen) -> dict:
    """Arguments of the plan_screen call, or {} when the plan spine never ran."""

    for name, args in screen.calls:
        if name == "plan_screen":
            return args
    return {}


def _t1() -> tuple[bool, dict]:
    screen = e2e.FakeScreen(components=[], revision=1)
    context = ScreenTurnContext(
        project_id="proj_1",
        screen_id=None,
        screen_name=None,
        revision=None,
        component_count=None,
    )
    response = e2e._run_turn(
        _fresh_db(), screen, message="帮我做一个销售经营大屏", context=context
    )
    names = e2e._tool_names(screen)
    args = _plan_call(screen)
    # Whole-screen generation goes through the plan spine: read the catalogue,
    # submit a draft for confirmation, never write the screen in this turn.
    ok = (
        names == ["describe_dataset", "plan_screen"]
        and args.get("placementHint") == "new"
        and not args.get("screenId")
        and bool((args.get("draft") or {}).get("goal"))
        and not ({"document", "operations", "frame", "x", "y"} & set(args))
        and "方案" in response.reply
    )
    return ok, {"tools": names, "params": args, "reply": response.reply}


def _t2() -> tuple[bool, dict]:
    screen = e2e.FakeScreen(components=[], revision=10)
    screen.next_plan = {
        "change_kind": "add_components",
        "target_component_ids": ["cmp_sales_1"],
        "components": {
            "cmp_sales_1": {
                "type": "bar",
                "frame": {"x": 0, "y": 0, "width": 6, "height": 4},
                "props": {"title": "销售看板"},
            }
        },
    }
    context = ScreenTurnContext(
        project_id="proj_1",
        screen_id="scr_1",
        screen_name="销售大屏",
        revision=10,
        component_count=0,
    )
    response = e2e._run_turn(
        _fresh_db(), screen, message="在这个大屏上生成一个销售看板", context=context
    )
    names = e2e._tool_names(screen)
    args = _plan_call(screen)
    # A blank existing screen is filled through the same plan spine, anchored to
    # the current screen and its revision; nothing is written before confirmation.
    ok = (
        names == ["describe_dataset", "plan_screen"]
        and args.get("placementHint") == "current"
        and args.get("screenId") == "scr_1"
        and args.get("expectedRevision") == 10
        and "方案" in response.reply
    )
    return ok, {"tools": names, "params": args, "reply": response.reply}


def _t3() -> tuple[bool, dict]:
    screen = e2e.FakeScreen(
        components=[{"componentId": "kpi_3", "name": "合同总额", "type": "metric"}],
        revision=11,
    )
    screen.next_plan = {
        "change_kind": "update_component",
        "target_component_ids": ["kpi_3"],
        "patches": {"kpi_3": {"props": {"title": "合同总额"}}},
    }
    context = ScreenTurnContext(
        project_id="proj_1",
        screen_id="scr_1",
        screen_name="销售大屏",
        revision=11,
        component_count=1,
        selected=[
            SelectedComponent(
                component_id="kpi_3", name="合同总额", component_type="metric"
            )
        ],
    )
    response = e2e._run_turn(
        _fresh_db(), screen, message="把这个指标改成合同总额", context=context
    )
    apply_args = screen.calls[1][1] if len(screen.calls) > 1 else {}
    ops = apply_args.get("operations") or []
    ok = (
        screen.plan_calls == 1
        and len(ops) == 1
        and ops[0].get("componentId") == "kpi_3"
        and apply_args.get("expectedRevision") == 11
        and "已完成修改" in response.reply
    )
    return ok, {
        "tools": e2e._tool_names(screen),
        "params": apply_args,
        "plan_calls": screen.plan_calls,
        "reply": response.reply,
    }


def _t4() -> tuple[bool, dict]:
    screen = e2e.FakeScreen(
        components=[
            {"componentId": f"cmp_{i}", "name": f"组件{i}", "type": "text"}
            for i in range(5)
        ],
        revision=11,
    )
    db = _fresh_db()
    context = ScreenTurnContext(
        project_id="proj_1",
        screen_id="scr_1",
        screen_name="销售大屏",
        revision=11,
        component_count=5,
    )
    response = e2e._run_turn(db, screen, message="把这个改一下", context=context)
    session = db.get(ChatSession, "sess-1")
    awaiting = bool((session.awaiting_input_json if session else None) or {})
    ok = screen.calls == [] and screen.plan_calls == 0 and awaiting
    return ok, {
        "tools": e2e._tool_names(screen),
        "plan_calls": screen.plan_calls,
        "awaiting": awaiting,
        "reply": response.reply,
    }


CASES = {"T1_create": _t1, "T2_fill": _t2, "T3_edit_one": _t3, "T4_ask": _t4}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report: dict[str, dict] = {}
    for name, case in CASES.items():
        outcomes: list[bool] = []
        durations: list[float] = []
        sample: dict = {}
        for _ in range(args.runs):
            started = time.perf_counter()
            try:
                ok, detail = case()
            except Exception as exc:  # noqa: BLE001 - eval must report, not crash
                ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
            durations.append((time.perf_counter() - started) * 1000)
            outcomes.append(ok)
            sample = detail
        report[name] = {
            "runs": args.runs,
            "passed": sum(outcomes),
            "pass_rate": round(sum(outcomes) / args.runs, 3),
            "p50_ms": round(statistics.median(durations), 1),
            "p95_ms": round(
                sorted(durations)[min(len(durations) - 1, int(len(durations) * 0.95))],
                1,
            ),
            "last_detail": sample,
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("| case | pass | rate | p50 ms | p95 ms |")
        print("|---|---|---|---|---|")
        for name, row in report.items():
            print(
                f"| {name} | {row['passed']}/{row['runs']} | "
                f"{row['pass_rate']:.0%} | {row['p50_ms']} | {row['p95_ms']} |"
            )
    return 0 if all(row["passed"] == row["runs"] for row in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

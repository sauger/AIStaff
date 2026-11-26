"""Offline eval for screen intent recognition.

Runs the real ``TurnPlanner`` (Harness v2 routing stage) over a labelled
corpus of editor utterances and compares the produced ``screen_intent`` with
the expected ``kind`` / ``target`` / placement flags.

This measures the routing decision itself, so unlike
``eval_workflow_screen.py`` it does **not** stub the planner: it spends real
model tokens.

Usage:
    cd backend && .venv/bin/python scripts/eval_screen_intent.py
    cd backend && .venv/bin/python scripts/eval_screen_intent.py --json --runs 2
    cd backend && .venv/bin/python scripts/eval_screen_intent.py --only blank
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlmodel import Session, select

from app.core.turn_planner import TurnPlanner
from app.db.database import engine
from app.db.models import ChatSession, ModelConfig, Skill
from app.session.turn_context import (
    ScreenTurnContext,
    SelectedComponent,
)
from app.workflow_packs.screen.sop import build_screen_sop

TENANT_ID = "tenant_demo"
SKILL_ID = "screen.change"

SKILL_DESCRIPTION = (
    "数字大屏助手：按业务目标生成经营大屏方案、补齐空白屏内容、"
    "定向修改选中组件或整屏样式，并回答数据口径问题。"
)


def _selected(name: str, component_type: str = "bar") -> SelectedComponent:
    return SelectedComponent(
        component_id="cmp_1", name=name, component_type=component_type
    )


def _context(name: str) -> ScreenTurnContext:
    """Editor states the corpus runs against."""

    base: dict[str, object] = {"project_id": "proj_1", "session_id": "sess_eval"}
    if name == "no_screen":
        return ScreenTurnContext(**base)
    if name == "blank":
        return ScreenTurnContext(
            **base,
            screen_id="scr_1",
            screen_name="销售大屏",
            revision=3,
            component_count=0,
            blank=True,
        )
    if name == "full_selected":
        return ScreenTurnContext(
            **base,
            screen_id="scr_1",
            screen_name="销售大屏",
            revision=5,
            component_count=6,
            blank=False,
            primary_component_id="cmp_1",
            selected=[_selected("部门合同额")],
        )
    if name == "full_idle":
        return ScreenTurnContext(
            **base,
            screen_id="scr_1",
            screen_name="销售大屏",
            revision=5,
            component_count=6,
            blank=False,
        )
    if name == "plan_waiting":
        return ScreenTurnContext(
            **base,
            screen_id="scr_1",
            screen_name="销售大屏",
            revision=5,
            component_count=6,
            blank=False,
            plan_id="pln_1",
            plan_status="needs_confirmation",
            plan_goal="本月合同额趋势与区域对比",
            plan_slots=["合同额", "月份"],
        )
    raise ValueError(f"unknown context {name!r}")


@dataclass
class Case:
    case_id: str
    message: str
    context: str
    kind: str
    target: str | None = None
    placement_confirmed: bool | None = None
    new_generation: bool | None = None
    alt_kinds: tuple[str, ...] = ()
    alt_targets: tuple[str, ...] = ()
    note: str = ""


CASES: list[Case] = [
    # --- 无屏 -------------------------------------------------------------
    Case(
        "c01",
        "帮我做一个销售经营大屏",
        "no_screen",
        "create",
        "unspecified",
        alt_targets=("new",),
        note="没有当前屏时 target=new 也合理，两者都算通过",
    ),
    Case("c02", "新建一张全国运营大屏", "no_screen", "create", "new", True),
    Case(
        "c03",
        "你能帮我看哪些数据？",
        "no_screen",
        "help",
        alt_kinds=("query",),
        note="能力询问与数据询问都合理，两者都算通过",
    ),
    Case("c04", "帮我把上个季度的合同额拉个看板", "no_screen", "create"),
    # --- 空白屏 -----------------------------------------------------------
    Case("c05", "生成一个销售看板", "blank", "create", "current", False),
    Case("c06", "放几个 KPI 上来", "blank", "create", "current"),
    Case("c07", "帮我加个柱状图", "blank", "create", "current"),
    Case("c08", "这个屏现在有几个组件？", "blank", "query"),
    # --- 有内容 + 选中 ----------------------------------------------------
    Case("c09", "把选中的组件改成饼图", "full_selected", "modify_selected", "current"),
    Case("c10", "标题改成合同额趋势", "full_selected", "modify_selected", "current"),
    Case("c11", "颜色换成深一点的蓝", "full_selected", "modify_selected", "current"),
    Case("c12", "改成折线图", "full_selected", "modify_selected", "current"),
    Case("c13", "这个组件删掉", "full_selected", "modify_selected", "current"),
    # --- 有内容 + 未选中 --------------------------------------------------
    Case("c14", "把部门合同额改成饼图", "full_idle", "modify_selected", "current"),
    Case(
        "c15",
        "整体换个布局",
        "full_idle",
        "modify_screen",
        "current",
        note="整屏级布局改动属于 modify_screen，不是新方案",
    ),
    Case("c16", "合同额多少？", "full_idle", "query"),
    Case("c17", "项目进度怎么样", "full_idle", "query"),
    Case("c18", "这个不太对", "full_idle", "help"),
    Case("c19", "帮我弄一下", "full_idle", "help"),
    Case("c20", "算了，不做了", "full_idle", "cancel"),
    Case("c21", "再加一个饼图", "full_idle", "modify_screen", "current"),
    Case("c22", "这张屏换成蓝色主题", "full_idle", "modify_screen", "current"),
    Case("c23", "另建一张大屏", "full_idle", "create", "new", True),
    # --- 方案待确认 -------------------------------------------------------
    Case(
        "c24",
        "可以",
        "plan_waiting",
        "confirm",
        "current",
        alt_targets=("unspecified",),
        note="确认方案时 target 不影响后续绑定，未指明也算通过",
    ),
    Case(
        "c25",
        "就这样吧",
        "plan_waiting",
        "confirm",
        "current",
        alt_targets=("unspecified",),
        note="确认方案时 target 不影响后续绑定，未指明也算通过",
    ),
    Case(
        "c26",
        "按这个生成",
        "plan_waiting",
        "confirm",
        "current",
        alt_targets=("unspecified",),
        note="确认方案时 target 不影响后续绑定，未指明也算通过",
    ),
    Case("c27", "直接做", "plan_waiting", "create", "current", False),
    Case("c28", "按默认来", "plan_waiting", "create", "current", False),
    Case("c29", "重新生成", "plan_waiting", "create", "current", False, True),
    Case("c30", "再来一版", "plan_waiting", "create", "current", False, True),
    Case(
        "c31",
        "可以，但是标题太长了，改短一点",
        "plan_waiting",
        "modify_screen",
        "current",
        alt_kinds=("modify_selected",),
        note="带未完成条件的确认不是 confirm；改标题算整屏还是单组件都合理",
    ),
    Case(
        "c32",
        "项目名称用文字，合同额用柱状图按部门",
        "plan_waiting",
        "modify_screen",
        "current",
        note="字段+图表答法属于方案调整",
    ),
    Case("c33", "取消这个方案", "plan_waiting", "cancel"),
    Case("c34", "这个方案用的什么数据？", "plan_waiting", "query"),
]


@dataclass
class Outcome:
    case: Case
    actual_kind: str = ""
    actual_target: str = ""
    actual_placement: bool = False
    actual_new_generation: bool = False
    confidence: float = 0.0
    reason: str = ""
    error: str = ""
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())


def _load_model_config(session: Session, model_config_id: str | None) -> ModelConfig:
    if model_config_id:
        row = session.get(ModelConfig, model_config_id)
        if row is None:
            raise SystemExit(f"model config {model_config_id!r} not found")
        return row
    rows = session.exec(
        select(ModelConfig).where(ModelConfig.tenant_id == TENANT_ID)
    ).all()
    if not rows:
        raise SystemExit(f"no model config for tenant {TENANT_ID!r}")
    for row in rows:
        if row.is_default:
            return row
    return rows[0]


def _load_session(session: Session) -> ChatSession:
    row = session.exec(
        select(ChatSession).where(ChatSession.tenant_id == TENANT_ID)
    ).first()
    if row is None:
        raise SystemExit(f"no chat session for tenant {TENANT_ID!r}")
    return row


def _screen_skill() -> Skill:
    return Skill(
        id="skill_eval_screen",
        tenant_id=TENANT_ID,
        skill_id=SKILL_ID,
        name="数字大屏助手",
        description=SKILL_DESCRIPTION,
        content_json=build_screen_sop(),
        status="published",
    )


def _evaluate(case: Case, planner: TurnPlanner, session, skill, model_config) -> Outcome:
    outcome = Outcome(case=case)
    try:
        plan = planner.plan(
            case.message,
            session,
            [skill],
            model_config,
            turn_context=_context(case.context),
        )
    except Exception as exc:  # noqa: BLE001 - eval must report, not crash
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome

    intent = plan.screen_intent
    if intent is None:
        outcome.error = "planner returned no screen_intent"
        return outcome

    outcome.actual_kind = intent.kind
    outcome.actual_target = intent.target
    outcome.actual_placement = intent.placement_confirmed
    outcome.actual_new_generation = intent.new_generation
    outcome.confidence = intent.confidence
    outcome.reason = intent.reason

    outcome.checks["kind"] = intent.kind == case.kind or intent.kind in case.alt_kinds
    if case.target is not None:
        outcome.checks["target"] = (
            intent.target == case.target or intent.target in case.alt_targets
        )
    if case.placement_confirmed is not None:
        outcome.checks["placement"] = (
            intent.placement_confirmed == case.placement_confirmed
        )
    if case.new_generation is not None:
        outcome.checks["new_generation"] = (
            intent.new_generation == case.new_generation
        )
    return outcome


def _confusion(outcomes: list[Outcome]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        if outcome.error:
            continue
        row = matrix.setdefault(outcome.case.kind, {})
        row[outcome.actual_kind] = row.get(outcome.actual_kind, 0) + 1
    return matrix


def _print_table(outcomes: list[Outcome]) -> None:
    print("| case | ctx | message | expect | actual | conf | ok |")
    print("|---|---|---|---|---|---|---|")
    for outcome in outcomes:
        case = outcome.case
        actual = outcome.error or (
            f"{outcome.actual_kind}/{outcome.actual_target}"
            f"{' place' if outcome.actual_placement else ''}"
            f"{' newgen' if outcome.actual_new_generation else ''}"
        )
        mark = "✅" if outcome.passed else "❌"
        print(
            f"| {case.case_id} | {case.context} | {case.message} | "
            f"{case.kind}/{case.target or '-'} | {actual} | "
            f"{outcome.confidence:.2f} | {mark} |"
        )


def _print_confusion(matrix: dict[str, dict[str, int]]) -> None:
    kinds = sorted(
        {expected for expected in matrix}
        | {actual for row in matrix.values() for actual in row}
    )
    print()
    print("| expect \\ actual | " + " | ".join(kinds) + " |")
    print("|---|" + "---|" * len(kinds))
    for expected in kinds:
        row = matrix.get(expected, {})
        cells = [str(row.get(actual, 0)) if row.get(actual) else "·" for actual in kinds]
        print(f"| {expected} | " + " | ".join(cells) + " |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="repeat each case N times")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of tables")
    parser.add_argument("--only", default="", help="only run cases whose context matches")
    parser.add_argument("--case", default="", help="only run one case id (e.g. c14)")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="minimum kind accuracy; exit 1 below it",
    )
    parser.add_argument("--model-config-id", default="", help="override the model config")
    args = parser.parse_args()

    cases = CASES
    if args.only:
        cases = [case for case in cases if case.context == args.only]
    if args.case:
        cases = [case for case in cases if case.case_id == args.case]
    if not cases:
        raise SystemExit("no cases selected")

    with Session(engine) as db:
        model_config = _load_model_config(db, args.model_config_id or None)
        session = _load_session(db)
        skill = _screen_skill()
        planner = TurnPlanner()
        print(
            f"model={model_config.model} tenant={TENANT_ID} cases={len(cases)} "
            f"runs={args.runs}",
            file=sys.stderr,
        )
        outcomes: list[Outcome] = []
        for _ in range(args.runs):
            for case in cases:
                outcomes.append(_evaluate(case, planner, session, skill, model_config))

    passed = sum(1 for outcome in outcomes if outcome.passed)
    kind_ok = sum(1 for outcome in outcomes if outcome.checks.get("kind"))
    errors = sum(1 for outcome in outcomes if outcome.error)
    matrix = _confusion(outcomes)

    if args.json:
        print(
            json.dumps(
                {
                    "model": model_config.model,
                    "runs": args.runs,
                    "cases": len(outcomes),
                    "passed": passed,
                    "pass_rate": round(passed / len(outcomes), 3),
                    "kind_accuracy": round(kind_ok / len(outcomes), 3),
                    "errors": errors,
                    "confusion": matrix,
                    "failures": [
                        {
                            "case_id": outcome.case.case_id,
                            "message": outcome.case.message,
                            "context": outcome.case.context,
                            "expect": outcome.case.kind,
                            "actual": outcome.error or outcome.actual_kind,
                            "confidence": outcome.confidence,
                            "reason": outcome.reason,
                            "checks": outcome.checks,
                            "note": outcome.case.note,
                        }
                        for outcome in outcomes
                        if not outcome.passed
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_table(outcomes)
        _print_confusion(matrix)
        print()
        print(
            f"pass {passed}/{len(outcomes)} ({passed / len(outcomes):.0%}) · "
            f"kind accuracy {kind_ok / len(outcomes):.0%} · errors {errors}"
        )

    return 0 if kind_ok / len(outcomes) >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Clear a demo tenant's runtime data without touching its configuration.

Keeps employees, SOPs, tools, knowledge bases, model configs and channel
bindings; removes sessions, messages, runs, memories and feedback so the next
demo starts from an empty "工作记录" view.

Usage:
    cd backend && .venv/bin/python scripts/demo_reset.py
    cd backend && .venv/bin/python scripts/demo_reset.py --dry-run
    cd backend && .venv/bin/python scripts/demo_reset.py --include-knowledge
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlmodel import Session, delete, select

from app.db.database import engine, init_db
from app.db.models import (
    AgentEvent,
    ChatSession,
    HarnessRunRecord,
    HarnessSessionLeaseRecord,
    HarnessTaskFrameRecord,
    HarnessTurnRecord,
    KnowledgeDocument,
    MemoryRecord,
    Message,
    MessageFeedback,
    ScheduledTaskRun,
)

# Children first: SQLite does not enforce foreign keys here, but ordering keeps
# the intent obvious and stays correct if enforcement is enabled later.
RUNTIME_MODELS = [
    ("消息反馈", MessageFeedback),
    ("消息", Message),
    ("对话轮次", HarnessTurnRecord),
    ("任务帧", HarnessTaskFrameRecord),
    ("运行记录", HarnessRunRecord),
    ("会话租约", HarnessSessionLeaseRecord),
    ("定时任务运行", ScheduledTaskRun),
    ("事件日志", AgentEvent),
    ("长期记忆", MemoryRecord),
    ("会话", ChatSession),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset runtime data for a demo tenant")
    parser.add_argument("--tenant", default="tenant_demo")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不删除")
    parser.add_argument(
        "--include-knowledge",
        action="store_true",
        help="同时删除以 demo_seed 来源写入的知识文档（可随后重新 seed）",
    )
    args = parser.parse_args()
    tenant_id = args.tenant

    init_db()
    with Session(engine) as session:
        print(f"租户 {tenant_id}{'（dry-run）' if args.dry_run else ''}\n")
        total = 0
        for label, model in RUNTIME_MODELS:
            rows = session.exec(select(model).where(model.tenant_id == tenant_id)).all()
            total += len(rows)
            print(f"  {label:<10} {len(rows)} 条")
            if rows and not args.dry_run:
                session.exec(delete(model).where(model.tenant_id == tenant_id))
                session.commit()

        if args.include_knowledge:
            docs = session.exec(
                select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
            ).all()
            demo_docs = [row for row in docs if (row.metadata_json or {}).get("source") == "demo_seed"]
            print(f"  {'演示知识文档':<10} {len(demo_docs)} 条")
            total += len(demo_docs)
            if demo_docs and not args.dry_run:
                for row in demo_docs:
                    session.delete(row)
                session.commit()

        print(f"\n{'将删除' if args.dry_run else '已删除'} {total} 条运行数据。")
        if not args.dry_run:
            print("配置（员工、SOP、工具、知识库、模型、渠道）未改动。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

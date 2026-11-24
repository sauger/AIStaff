"""Prepare a repeatable AIStaff demo tenant.

Idempotent: re-running only fills in what is missing, so a partially prepared
demo can be repaired without wiping configuration.

Usage:
    cd backend && .venv/bin/python scripts/demo_seed.py
    cd backend && .venv/bin/python scripts/demo_seed.py --skip-knowledge
    cd backend && .venv/bin/python scripts/demo_seed.py --agent 数字大屏助手
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlmodel import Session, select

from app.config import get_settings, runtime_profile_problems
from app.db.database import engine, init_db
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    KnowledgeBase,
    KnowledgeDocument,
    MCPServer,
    ModelConfig,
    Skill,
    Tenant,
    Tool,
)
from app.db.seed import seed_demo_data
from app.knowledge.service import IngestPayload, KnowledgeService

DEMO_TENANT = "tenant_demo"
DEMO_KB_NAME = "演示知识库（客户拜访）"
DEMO_DOC_FILENAME = "客户拜访准备手册.md"
DEMO_DOC_TITLE = "客户拜访准备手册"
DEMO_SKILL_ID = "screen.change"

DEMO_DOC = """# 客户拜访准备手册

## 适用范围
本手册用于售前与交付团队拜访制造业客户前的准备，覆盖目标确认、材料准备与会后跟进。

## 拜访前
1. 明确本次拜访目标：需求澄清、方案确认或商务推进，一次只推进一个目标。
2. 阅读客户近三次沟通记录，列出已知需求、未决问题与关键决策人。
3. 准备两页纸材料：现状与问题、我们的建议路径。
4. 涉及报价时，只准备区间与构成说明，不给出最终价格。

## 拜访中
1. 前十分钟只问不答，确认客户对现状的真实描述。
2. 用客户自己的表述复述问题，得到确认后再给建议。
3. 记录客户提到的系统（ERP、CRM、大屏）、数据来源与责任人。

## 拜访后
1. 当天发出会议纪要，包含结论、待办与责任人。
2. 未解决问题进入下次沟通议程，不留在口头承诺里。

## 报价与承诺边界
- 报价、折扣、交付周期与合同条款属于对外承诺，必须由商务负责人确认后发出。
- 数字员工可以提供区间测算与历史成交参考，不得直接对外报出最终价格。
- 涉及交付排期调整、验收标准变更时，需人工接管并同步项目经理。
"""


def _say(ok: bool, label: str, detail: str = "") -> None:
    mark = "OK  " if ok else "TODO"
    suffix = f" — {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}")


def _pick_agent(session: Session, tenant_id: str, wanted: str | None) -> AgentProfile | None:
    rows = session.exec(
        select(AgentProfile)
        .where(AgentProfile.tenant_id == tenant_id, AgentProfile.is_overall == False)
        .order_by(AgentProfile.created_at.asc())
    ).all()
    if not rows:
        return None
    if wanted:
        needle = wanted.strip()
        for row in rows:
            if needle and (needle in row.name or row.name in needle):
                return row
    for row in rows:
        if "大屏" in row.name:
            return row
    return rows[0]


def _ensure_demo_knowledge_base(session: Session, tenant_id: str) -> KnowledgeBase:
    existing = session.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.name == DEMO_KB_NAME,
        )
    ).first()
    if existing:
        return existing
    row = KnowledgeBase(
        tenant_id=tenant_id,
        name=DEMO_KB_NAME,
        description="演示用知识库：客户拜访准备与报价边界",
        status="active",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _ensure_demo_document(session: Session, tenant_id: str, knowledge_base: KnowledgeBase) -> tuple[bool, str]:
    existing = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.filename == DEMO_DOC_FILENAME,
        )
    ).first()
    if existing:
        return existing.status == "ready", f"已存在，状态 {existing.status}"

    service = KnowledgeService(session)
    job = service.create_ingest_job(
        IngestPayload(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base.id,
            filename=DEMO_DOC_FILENAME,
            title=DEMO_DOC_TITLE,
            content_base64=base64.b64encode(DEMO_DOC.encode("utf-8")).decode("ascii"),
            metadata={"source": "demo_seed"},
        )
    )
    service.run_ingest_job(job.id)
    session.refresh(job)
    document = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.filename == DEMO_DOC_FILENAME,
        )
    ).first()
    if document is None:
        return False, f"入库任务 {job.status}/{job.stage}，未生成文档：{job.error or '无错误信息'}"
    return document.status == "ready", f"入库 {job.stage}，分块 {document.chunk_count}"


def _ensure_binding(session: Session, tenant_id: str, agent: AgentProfile, resource_type: str, resource_id: str, name: str) -> bool:
    existing = session.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent.id,
            AgentResourceBinding.resource_type == resource_type,
            AgentResourceBinding.resource_id == resource_id,
        )
    ).first()
    if existing:
        return False
    session.add(
        AgentResourceBinding(
            tenant_id=tenant_id,
            agent_id=agent.id,
            resource_type=resource_type,
            resource_id=resource_id,
            status="active",
            metadata_json={"name": name},
        )
    )
    session.commit()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the AIStaff demo tenant")
    parser.add_argument("--tenant", default=DEMO_TENANT)
    parser.add_argument("--agent", default=None, help="员工名称（模糊匹配），默认取第一个非总控员工")
    parser.add_argument("--skip-knowledge", action="store_true", help="只准备账号与配置，不写入演示文档")
    parser.add_argument("--bind-all-sops", action="store_true", help="把全部已发布 SOP 绑到员工，默认只绑 screen.change")
    args = parser.parse_args()

    settings = get_settings()
    tenant_id = args.tenant
    print(f"部署档位 DEPLOYMENT_PROFILE={settings.normalized_deployment_profile} 租户 {tenant_id}\n")

    problems = runtime_profile_problems(settings)
    for problem in problems:
        print(f"[WARN] 档位校验：{problem}")
    if problems:
        print("[WARN] 演示前请先修正上述配置，否则启动会直接失败。\n")

    init_db()
    with Session(engine) as session:
        if session.get(Tenant, tenant_id) is None and tenant_id != DEMO_TENANT:
            print(f"[FAIL] 租户 {tenant_id} 不存在；脚本只在 tenant_demo 上创建基础数据。")
            return 2
        seed_demo_data(session)

        model_rows = session.exec(
            select(ModelConfig).where(ModelConfig.tenant_id == tenant_id, ModelConfig.enabled == True)
        ).all()
        _say(bool(model_rows), "模型配置", f"{len(model_rows)} 个可用配置" if model_rows else "没有可用模型，对话会直接失败")

        agent = _pick_agent(session, tenant_id, args.agent)
        if agent is None:
            print("[FAIL] 没有可用的数字员工，请先创建员工。")
            return 2
        print(f"      演示员工：{agent.name}（{agent.id}）")

        if not args.skip_knowledge:
            knowledge_base = _ensure_demo_knowledge_base(session, tenant_id)
            ok, detail = _ensure_demo_document(session, tenant_id, knowledge_base)
            _say(ok, f"演示知识《{DEMO_DOC_TITLE}》", detail)
            if ok:
                created = _ensure_binding(session, tenant_id, agent, "knowledge_base", knowledge_base.id, knowledge_base.name)
                _say(True, "知识库绑定到员工", "已新增绑定" if created else "已绑定")

        skills = session.exec(
            select(Skill).where(Skill.tenant_id == tenant_id, Skill.status == "published")
        ).all()
        _say(bool(skills), "已发布 SOP", f"{len(skills)} 个" if skills else "没有已发布 SOP，声明式流程无法运行")
        demo_skills = [row for row in skills if row.skill_id == DEMO_SKILL_ID]
        if args.bind_all_sops:
            demo_skills = list(skills)
        if not demo_skills:
            _say(False, f"  {DEMO_SKILL_ID} 未发布", "大屏演示需要先发布 screen.change SOP")
        for skill in demo_skills:
            bound = _ensure_binding(session, tenant_id, agent, "skill", skill.id, skill.skill_id or skill.name)
            _say(True, f"  SOP 绑定 {skill.skill_id}", "已新增绑定" if bound else "已绑定")

        tools = session.exec(select(Tool).where(Tool.tenant_id == tenant_id)).all()
        _say(bool(tools), "可用工具", f"{len(tools)} 个" if tools else "没有工具，员工无法操作外部系统")

        servers = session.exec(select(MCPServer).where(MCPServer.tenant_id == tenant_id)).all()
        screen_servers = [row for row in servers if row.name == "ai_screen"]
        _say(
            bool(screen_servers),
            "ai-screen MCP 接入",
            "已配置" if screen_servers else "未接入；大屏演示需要先在 ai-screen 侧运行 aistaff_setup.py",
        )

        bindings = session.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == tenant_id,
                AgentResourceBinding.agent_id == agent.id,
            )
        ).all()
        counts: dict[str, int] = {}
        for row in bindings:
            counts[row.resource_type] = counts.get(row.resource_type, 0) + 1
        _say(
            bool(bindings),
            "员工资源绑定",
            "、".join(f"{key} {value}" for key, value in sorted(counts.items())) or "尚未绑定任何资源",
        )

    print(
        "\n下一步：\n"
        "  1. 用 admin 登录，确认「员工档案 → 资料/技能/SOP/工具」里能看到上面的绑定。\n"
        "  2. 大屏演示另需 ai-screen 侧：scripts/demo.sh 或 aistaff_setup.py。\n"
        "  3. 演示结束后运行 scripts/demo_reset.py 清空运行数据。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

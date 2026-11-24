"""Import the ERP MCP read-only tools into AIStaff.

Idempotent: re-running updates the stored connection and re-syncs the same
read-only subset, so a rotated token only needs a re-run.

Only these tools are imported on purpose:
    list_projects, get_project, list_milestones, list_tasks

Write tools (create_task, delete_task, ...) are never imported into AIStaff, so
the digital employee cannot modify the ERP even if a model asks for it.

Usage:
    cd backend && ERP_MCP_TOKEN=... .venv/bin/python scripts/erp_mcp_seed.py
    cd backend && ERP_MCP_TOKEN=... .venv/bin/python scripts/erp_mcp_seed.py --check
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlmodel import Session, delete, select

from app.api.tools import create_mcp_server, sync_mcp_tools
from app.db.database import engine, init_db
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    MCPServer,
    Tool,
    User,
)
from app.knowledge.service import IngestPayload, KnowledgeService
from app.tools.tool_schema import (
    MCPServerConnection,
    MCPServerCreateRequest,
    MCPSyncRequest,
)

TENANT = "tenant_demo"
SERVER_NAME = "erp_pm"
SERVER_LABEL = "ERP 项目经营（只读）"
DEFAULT_URL = "http://wx.wl-kj.cn/prod-api/mcp/http/erp-pm-api"
READ_ONLY_TOOLS = ["get_project", "list_projects", "list_milestones", "list_tasks"]
DEFAULT_AGENT = "项目管理"
ERP_KB_NAME = "ERP 项目经营（只读）"
ERP_DOC_FILENAME = "ERP 项目经营数据使用约定.md"
ERP_DOC_TITLE = "ERP 项目经营数据使用约定"

ERP_PERSONA = """你是「项目管理」数字员工，服务交付与项目管理部门。

职责范围：
- 项目台账查询：项目基本信息、阶段、负责人、客户、合同额与实际成本、进度
- 里程碑与任务查询：项目当前在哪个节点、有哪些待办任务
- 经营跟进：项目阶段分布、交付风险线索、需要跟进的项目清单

边界（明确不做）：
- 只读：不能修改、审批、删除 ERP 中的任何单据，不能发起付款或开票
- 不能承诺工期、报价、赔付等对外口径，遇到此类问题必须说明"需业务负责人确认"
- 不臆测数据：所有项目事实必须来自 ERP 查询结果，查询不到就说查询不到

查询约定（重要）：
- `list_projects` 只有一个参数 `limit`（默认 50，上限 2000），没有部门/阶段过滤参数；筛选只能在结果里自己做
- 返回体里的 total 是全量条数，truncated=true 表示还有更多；不要用单页数据冒充全量总数，回答时写清"基于已取到的 N 条"
- 只用平台提供的工具查询业务系统，不要用命令行或脚本绕过工具直接访问内部接口
- 不要为了统计把上千条项目一次性拉进上下文：读大结果会超时。遇到"全公司/全部门多少项目"这类问题，说明当前工具不支持服务端过滤，建议按项目名或客户名定位后逐个查，或改用数据导出
- 回答项目数据时写清项目名称与客户名称，并注明数据来自 ERP 项目台账
"""

ERP_DOC = """# ERP 项目经营数据使用约定

本文件说明「项目管理」数字员工可以使用的 ERP 数据能力、字段口径与回答边界。

## 一、可用工具（均为只读）

- `list_projects`：查询项目列表。**唯一参数是 `limit`（默认 50，上限 2000）**，没有按部门、阶段、客户过滤的参数
- `get_project`：按 `projectId` 查询单个项目明细
- `list_milestones`：按 `projectId` 查询里程碑
- `list_tasks`：按 `projectId` 查询任务（参数 `limit` 默认 200，上限 500）

系统没有为数字员工开通任何写操作：不能新建、修改、删除项目或任务，也不能提交审批。

## 二、分页与统计口径

- `list_projects` 不带条件：默认只取 50 条，`limit` 最大 2000
- 返回体包含 `total`（全量条数）、`returned`（本次返回条数）、`truncated`（是否还有更多）
- 需要按部门、阶段、客户筛选时，只能在**返回结果内部**自行过滤
- 把 `limit` 调到 2000 会把上千条记录（每条上百字段，约 1.4MB）塞进上下文，读大结果会超时：优先先按项目名/客户名定位目标项目，再用 `get_project` 取明细
- 当前工具**不支持**"全公司有多少项目"这类汇总统计：需要汇总时说明限制，建议按项目/客户逐个查或走导出
- 回答数量类问题时，必须写清统计口径，例如"在已取到的 50 条记录中，交付一部有 2 个项目"；不要用单页数据冒充全量总数

## 三、字段口径

- 项目阶段：prepare 准备中、working 进行中、finish 已完成、pause 已暂停
- 项目类型：develop 开发、maintenance 运维、staffing 人力外包、hr 人力、product1 产品、sms 短信、research 研发、other 其他
- 合同额（contractAmount）、实际成本（actualCost）单位为元
- 进度（progressPercent）为 0 到 100 的百分比

## 四、回答要求

- 引用项目时写清项目名称与客户名称，并说明数据来自 ERP 项目台账
- 说明数据时点（如"截至本次查询"）
- 结果被截断时主动说明"当前仅取到前 N 条，共 total 条"
- 涉及改单、审批、对外报价与承诺，一律回到人工确认
"""


def _ensure_erp_kb(db: Session) -> KnowledgeBase:
    row = db.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.tenant_id == TENANT, KnowledgeBase.name == ERP_KB_NAME
        )
    ).first()
    if row is not None:
        return row
    row = KnowledgeBase(
        tenant_id=TENANT,
        name=ERP_KB_NAME,
        description="ERP 项目经营数据的字段口径、分页约定与只读边界",
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _ensure_erp_doc(db: Session, knowledge_base: KnowledgeBase, *, reingest: bool = False) -> str:
    existing = db.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == TENANT,
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.filename == ERP_DOC_FILENAME,
        )
    ).first()
    if existing is not None:
        if not reingest:
            return f"已存在，状态 {existing.status}，分块 {existing.chunk_count}"
        db.exec(
            delete(KnowledgeChunk).where(KnowledgeChunk.document_id == existing.id)
        )
        db.delete(existing)
        db.commit()

    service = KnowledgeService(db)
    job = service.create_ingest_job(
        IngestPayload(
            tenant_id=TENANT,
            knowledge_base_id=knowledge_base.id,
            filename=ERP_DOC_FILENAME,
            title=ERP_DOC_TITLE,
            content_base64=base64.b64encode(ERP_DOC.encode("utf-8")).decode("ascii"),
            metadata={"source": "demo_seed"},
        )
    )
    service.run_ingest_job(job.id)
    document = db.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == TENANT,
            KnowledgeDocument.filename == ERP_DOC_FILENAME,
        )
    ).first()
    if document is None:
        return f"入库任务 {job.status}/{job.stage}，未生成文档：{job.error or '无错误信息'}"
    return f"入库 {job.stage}，状态 {document.status}，分块 {document.chunk_count}"


TOOL_POLICY_ALLOWLIST = [
    "capability_search",
    "capability_describe",
    "knowledge_search",
    # MCP 大结果会落到沙箱文件，只给读文件能力、不给命令行
    "read_file",
] + [f"{SERVER_NAME}.{name}" for name in READ_ONLY_TOOLS]


def _ensure_tool_policy(db: Session, agent: AgentProfile) -> str:
    """Narrow the employee to read-only data tools (no shell, no file writes)."""
    metadata = dict(agent.metadata_json or {})
    workflow = dict(metadata.get("workflow") or {})
    policy = dict(workflow.get("tool_policy") or {})
    current = list(policy.get("conversation_allowlist") or [])
    if current == TOOL_POLICY_ALLOWLIST:
        return "已是最新工具策略"
    policy["conversation_allowlist"] = list(TOOL_POLICY_ALLOWLIST)
    policy.setdefault("sop_include_general_tools", False)
    workflow["tool_policy"] = policy
    metadata["workflow"] = workflow
    agent.metadata_json = metadata
    db.add(agent)
    db.commit()
    return (
        f"工具策略已收敛为只读：{', '.join(TOOL_POLICY_ALLOWLIST)}"
        "（不含命令行与文件写入）"
    )


def _ensure_persona(db: Session, agent: AgentProfile) -> str:
    current = (agent.persona_prompt or "").strip()
    if current and not current.startswith("你是「项目管理」数字员工"):
        return "已有自定义人设，保持不变"
    if current == ERP_PERSONA.strip():
        return "已是最新人设"
    agent.persona_prompt = ERP_PERSONA
    db.add(agent)
    db.commit()
    return "已写入岗位人设"



def _bind_resource(db: Session, agent: AgentProfile, resource_type: str, resource_id: str, name: str) -> None:
    row = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == agent.tenant_id,
            AgentResourceBinding.agent_id == agent.id,
            AgentResourceBinding.resource_type == resource_type,
            AgentResourceBinding.resource_id == resource_id,
        )
    ).first()
    if row is None:
        db.add(
            AgentResourceBinding(
                tenant_id=agent.tenant_id,
                agent_id=agent.id,
                resource_type=resource_type,
                resource_id=resource_id,
                status="active",
                metadata_json={"name": name},
            )
        )
    elif row.status != "active":
        row.status = "active"
        db.add(row)
    db.commit()


def _bind_tools(db: Session, agent: AgentProfile, tools: list[Tool]) -> int:
    """Append tool bindings without touching the employee's other resources."""
    existing = {
        row.resource_id: row
        for row in db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == agent.tenant_id,
                AgentResourceBinding.agent_id == agent.id,
                AgentResourceBinding.resource_type == "tool",
            )
        ).all()
    }
    added = 0
    for tool in tools:
        row = existing.get(tool.id)
        if row is None:
            db.add(
                AgentResourceBinding(
                    tenant_id=agent.tenant_id,
                    agent_id=agent.id,
                    resource_type="tool",
                    resource_id=tool.id,
                    status="active",
                    metadata_json={"name": tool.name},
                )
            )
            added += 1
        elif row.status != "active":
            row.status = "active"
            db.add(row)
            added += 1
    db.commit()
    return added

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查当前状态，不写入")
    parser.add_argument("--url", default=os.environ.get("ERP_MCP_URL") or DEFAULT_URL)
    parser.add_argument("--agent", default=DEFAULT_AGENT, help="把工具绑定到这个岗位（默认：项目管理）")
    parser.add_argument("--no-bind", action="store_true", help="只导入工具，不绑定到岗位")
    parser.add_argument("--no-knowledge", action="store_true", help="不写入岗位人设与 ERP 使用约定知识")
    parser.add_argument(
        "--no-policy", action="store_true", help="不改员工的工具策略白名单"
    )
    parser.add_argument(
        "--reingest-knowledge",
        action="store_true",
        help="删除并重新入库 ERP 使用约定（知识文档内容变化后使用）",
    )
    args = parser.parse_args()

    token = (os.environ.get("ERP_MCP_TOKEN") or "").strip()
    if not args.check and not token:
        print("缺少 ERP_MCP_TOKEN 环境变量（只读接入需要它完成 tools/list 与调用鉴权）")
        return 2

    init_db()
    with Session(engine) as db:
        admin = db.exec(
            select(User).where(User.tenant_id == TENANT, User.role == "admin")
        ).first()
        if admin is None:
            print("找不到 tenant_demo 的管理员账号，先跑一次应用启动或 demo_seed.py")
            return 2

        server = db.exec(
            select(MCPServer).where(MCPServer.tenant_id == TENANT, MCPServer.name == SERVER_NAME)
        ).first()

        if args.check:
            if server is None:
                print(f"[TODO] MCP server {SERVER_NAME} 尚未注册")
            else:
                print(f"[OK  ] MCP server {SERVER_NAME} → {server.url}")
            tools = db.exec(
                select(Tool).where(Tool.tenant_id == TENANT, Tool.mcp_server_id == (server.id if server else ""))
            ).all()
            for tool in tools:
                print(f"[{'OK  ' if tool.enabled else 'OFF '}] {tool.name} ({tool.tool_type})")
            return 0

        if server is None:
            created = create_mcp_server(
                MCPServerCreateRequest(
                    tenant_id=TENANT,
                    name=SERVER_NAME,
                    display_name=SERVER_LABEL,
                    description="ERP 项目经营数据（只读工具子集）",
                    bucket="业务系统",
                    connection=MCPServerConnection(
                        transport="streamable_http",
                        url=args.url,
                        headers={"Authorization": f"Bearer {token}"},
                    ),
                    apps_mode="disabled",
                    capability_scope="general",
                    enabled=True,
                ),
                db=db,
                current_user=admin,
            )
            server = db.exec(select(MCPServer).where(MCPServer.id == created.id)).one()
            print(f"[OK  ] 注册 MCP server {SERVER_NAME} → {server.url}")
        else:
            server.url = args.url
            server.headers_json = {"Authorization": f"Bearer {token}"}
            server.enabled = True
            db.add(server)
            db.commit()
            print(f"[OK  ] 更新 MCP server {SERVER_NAME} → {server.url}")

        server_id = server.id

        result = sync_mcp_tools(
            server_id,
            MCPSyncRequest(tenant_id=TENANT, tool_names=list(READ_ONLY_TOOLS)),
            db=db,
            agent_id=None,
            current_user=admin,
        )
        if not result.success:
            print(f"[FAIL] 工具同步失败：{result.error}")
            return 1
        print(f"[OK  ] 导入只读工具：{', '.join(result.imported) or '（已是最新）'}")
        if result.updated:
            print(f"[OK  ] 更新工具 schema：{', '.join(result.updated)}")

        rows = db.exec(
            select(Tool).where(Tool.tenant_id == TENANT, Tool.mcp_server_id == server_id)
        ).all()
        print(f"\n当前 {SERVER_LABEL} 下的工具：")
        for row in rows:
            print(f"  [{'启用' if row.enabled else '停用'}] {row.name}")

        if not args.no_bind:
            agent = db.exec(
                select(AgentProfile).where(
                    AgentProfile.tenant_id == TENANT, AgentProfile.name == args.agent
                )
            ).first()
            if agent is None:
                print(f"[WARN] 找不到岗位「{args.agent}」，跳过绑定（可用 --agent 指定）")
            else:
                added = _bind_tools(db, agent, rows)
                print(f"[OK  ] 绑定 {len(rows)} 个工具到「{agent.name}」（新增 {added} 条）")

                if not args.no_knowledge:
                    print(f"[OK  ] 岗位人设：{_ensure_persona(db, agent)}")
                    if not args.no_policy:
                        print(f"[OK  ] 工具边界：{_ensure_tool_policy(db, agent)}")
                    kb = _ensure_erp_kb(db)
                    print(
                        f"[OK  ] 知识库「{kb.name}」："
                        f"{_ensure_erp_doc(db, kb, reingest=args.reingest_knowledge)}"
                    )
                    _bind_resource(db, agent, "knowledge_base", kb.id, kb.name)
                    print(f"[OK  ] 绑定知识库到「{agent.name}」")

        forbidden = {row.name for row in rows} & {"create_task", "delete_task"}
        if forbidden:
            print(f"[WARN] 存在写操作工具：{', '.join(sorted(forbidden))}（应保持未导入）")
        else:
            print("[OK  ] 未导入任何写操作工具")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

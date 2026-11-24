# AIStaff 演示版 Runbook

面向"客户演示"这一目标：让演示可重复、可解释、可收尾。生产交付另需安全、迁移、可观测等能力，不在本文范围。

## 1. 演示版与生产版的边界

演示版与生产版共用同一套底座：Harness v2 对话运行时、SOP 状态机、知识检索、记忆、模型协议、渠道适配器。
演示版额外允许三件事，且都受显式开关约束：

| 能力 | 开关 | 演示 | 生产 |
| --- | --- | --- | --- |
| 声明式 SOP 真正生效 | `WORKFLOW_RUNTIME_ENABLED=true` | 必须开启 | 必须开启（`demo`/`prod` 档缺失会启动失败） |
| 样例数据集 / 内置参考方案 | ai-screen `AI_SCREEN_DEMO_MODE` + `AI_SCREEN_DEMO_SPACE_IDS` | 可在隔离空间开启 | 强制关闭 |
| 开发态身份头 `X-Dev-User` | ai-screen `AI_SCREEN_DEV_AUTH_ENABLED` | 允许（prod 直接拒绝） | 不可用 |

AIStaff 侧的口径：`DEPLOYMENT_PROFILE=dev` 保留历史默认口令（`admin/admin`），`demo`/`prod` 必须显式提供 `DEMO_ADMIN_PASSWORD`，否则账号口令随机生成并写到用户数据目录的 `demo_credentials.txt`。

## 2. 演示前 24 小时检查表

- [ ] `DEPLOYMENT_PROFILE` 明确（客户现场建议 `demo`），`WORKFLOW_RUNTIME_ENABLED=true`
- [ ] `DEMO_ADMIN_PASSWORD` 已设置并记录在安全位置；`APP_SECRET` 非默认值且长度 ≥ 16
- [ ] 模型配置可用：`ModelsPage` 里至少一个启用中的配置，点"测试连接"通过
- [ ] 演示员工存在，且 `员工档案 → 资料/技能/SOP/工具` 四类绑定可见（本轮新增的勾选保存即为此准备）
- [ ] `screen.change` SOP 已发布（未发布时 `demo_seed.py` 会打 `TODO`）
- [ ] ai-screen 侧已完成 `aistaff_setup.py`，AIStaff 里能看到 `ai_screen` MCP server（未完成时 `demo_seed.py` 会打 `TODO`）
- [ ] 演示用知识文档已入库到 `done`（`demo_seed.py` 会写入《客户拜访准备手册》）
- [ ] 若要演示 ERP 只读查询：`ERP_MCP_TOKEN` 有效，`erp_mcp_seed.py` 已跑过，`员工档案 → 工具` 能看到 4 个 `erp_pm.*` 工具
- [ ] ai-screen 的演示空间已列入 `AI_SCREEN_DEMO_SPACE_IDS`，客户空间不在名单内
- [ ] 网络：模型网关、ai-screen、MCP endpoint 从演示机可达；准备离线兜底（录屏/截图）
- [ ] 已跑过一次完整演示，确认"运行轨迹" tab 能看到上一轮的会话

## 3. 起停

```bash
# 首次 / 每次演示前（幂等，只补缺失项）
scripts/demo_up.sh

# 清空上一轮运行数据后重新准备（推荐每次演示前执行）
scripts/demo_up.sh --reset

# 只准备数据、不启动服务
scripts/demo_up.sh --seed-only

# 收尾
scripts/dev_down.sh
```

单独使用底层脚本：

```bash
backend/.venv/bin/python backend/scripts/demo_seed.py --agent 数字大屏助手
backend/.venv/bin/python backend/scripts/demo_reset.py --dry-run
backend/.venv/bin/python backend/scripts/demo_reset.py --include-knowledge
```

`demo_reset.py` 只删运行数据（会话、消息、轮次、运行记录、记忆、反馈、定时任务运行），保留员工、SOP、工具、知识库、模型、渠道等配置；加 `--include-knowledge` 会一并删除 `demo_seed` 写入的演示文档。

ERP 只读工具接入（AIStaff 侧，4 个只读工具 + 绑定到「项目管理」岗位）：

```bash
cd backend
set -a && . /path/to/ai-screen/.env && set +a   # 取 ERP_MCP_TOKEN
ERP_MCP_TOKEN="$ERP_MCP_TOKEN" .venv/bin/python scripts/erp_mcp_seed.py          # 导入并绑定
ERP_MCP_TOKEN="$ERP_MCP_TOKEN" .venv/bin/python scripts/erp_mcp_seed.py --check  # 只检查
ERP_MCP_TOKEN="$ERP_MCP_TOKEN" .venv/bin/python scripts/erp_mcp_seed.py --agent 数据分析 --no-bind
```

- 只导入 `list_projects / get_project / list_milestones / list_tasks`；`create_task`、`delete_task` 等写工具**永不导入**，员工没有改 ERP 的能力面。
- Token 存放在 `mcp_servers.headers_json`（当前平台对 MCP 头不做加密，只适合本地/演示机）。
- `list_projects` 只有一个 `limit` 参数（默认 50、上限 2000），没有部门/阶段过滤，也没有 offset；它返回 `total/returned/truncated`。因此演示问"某项目进展/里程碑/任务"，不要问"全公司或全部门有多少项目"。
- 脚本同时会把「项目管理」员工的工具白名单收敛为 `capability_search / capability_describe / knowledge_search / read_file` + 4 个 `erp_pm.*`，并写入岗位人设与《ERP 项目经营数据使用约定》知识（`--no-policy`、`--no-knowledge` 可跳过）。

示例数据集与内置参考方案（只在隔离演示空间可用）：

```bash
# ai-screen：重置演示库（drop schema + alembic upgrade + seed）
scripts/demo.sh --reset
```

## 4. 建议演示脚本（7 分钟，大屏主线）

1. **岗位**（40s）：打开员工档案，指出服务范围、可用资料/SOP/工具，强调"不是聊天框，是岗位"。
2. **知识**（60s）：问一个边界问题（例如"客户压价 30% 能不能直接答应"），让员工引用《客户拜访准备手册》并给出"需商务确认"的结论。演示前需确认检索召回到该文档。
3. **业务目标**（40s）：提出"做一张本月经营大屏，看合同额趋势和区域对比"。
4. **系统取数**（90s）：ai-screen 返回数据目录、字段映射、假设与未匹配项——讲"字段来自真实数据目录，不是猜的"。
5. **人工确认**（60s）：方案卡片上点"按此生成"；强调模型不能自行确认，写屏只有这一条路径。
6. **改版与并发**（60s）：选中组件改标题/换图表，讲 revision 校验（别人先改了会被拒绝而不是覆盖）。
7. **执行记录**（60s）：切到 `员工档案 → 运行轨迹`，展示本轮会话的工具调用与状态；回到对话给一次反馈。
8. **收尾**（30s）：说明演示空间与样例数据是隔离的，客户空间里不会出现示例数据。

### 备选支线：ERP 只读查询（约 2 分钟，替换第 3–6 步）

1. 切到「项目管理」员工，问「TEST2601A 这个项目做到哪一步了？有没有风险？」
2. 员工用 `list_projects` 定位项目 ID，再用 `get_project`/`list_milestones`/`list_tasks` 取真实数据；讲"工具是只读的，写工具根本没导入"。
3. 回答会指出"无里程碑、无任务、进度 0%、合同未上传"等卡点，并追问目标与验收条件——这是"查单据 → 找卡点 → 给下一步"的样板。
4. 补充说明工具边界：该员工的人设与工具白名单已收敛为只读数据查询，**没有**命令行与文件写入能力。

时间不够时的取舍：保留 2、4、5、7；砍掉 6 和 8 的展开。

## 5. 失败兜底

| 现象 | 处理 |
| --- | --- |
| 模型连不通 | 切到备用模型配置（会话内可换模型），或改讲"运行轨迹"历史记录 |
| ai-screen 未启动 | 演示改为纯 AIStaff 侧（知识 + SOP + 工具），大屏部分用录屏 |
| 方案卡片一直澄清 | 把目标说具体（时间范围 + 指标 + 维度），或换用演示数据里存在的字段 |
| 写屏冲突 `REVISION_CONFLICT` | 这是真实并发保护，现场讲"被拒绝而不是覆盖"，然后重新生成 |
| 样例数据出现在客户空间 | 立即停止演示：检查 `AI_SCREEN_DEMO_MODE` 与 `AI_SCREEN_DEMO_SPACE_IDS`，这是配置事故 |
| ERP 问题问成"全公司/全部门有多少项目" | 会超时（工具无服务端过滤，全量约 1.4MB）。改成问单个项目的进展/里程碑/任务 |
| ERP 工具报 401/超时 | 重新导出 `ERP_MCP_TOKEN` 并重跑 `erp_mcp_seed.py`；仍失败则改讲大屏主线 |

## 6. 现场核验项

以下内容无法从仓库证明，必须在演示机上确认：

- 模型网关、ai-screen、MCP endpoint 的连通性与延迟
- ai-screen 侧 `aistaff_setup.py` 跑完后的工具清单与 scope（MCP 同步可能重置 scope，需要重跑脚本）
- 客户空间里是否真的看不到样例数据集（`AI_SCREEN_DEMO_SPACE_IDS` 生效）
- 演示账号口令落盘位置与权限（`demo_credentials.txt`）
- 大屏发布后的分享链接是否可从客户网络访问

## 7. 已知限制（演示时不要索取）

- 执行记录只有会话级"运行轨迹"，没有逐节点的业务化视图
- 员工级模型绑定已废弃，模型按会话/消息选择
- 岗位"负责什么/不负责什么/能碰哪些数据"目前落在人设提示词与元数据里，没有结构化字段
- 知识入库在无模型配置时只能产出粗粒度分块，检索效果会明显下降
- 模板与字段别名仍带演示口径（合同额、进度、部门等），客户字段不同需要改代码而非改配置
- ERP 工具只有 `limit` 参数（默认 50、上限 2000），没有部门/阶段过滤，也没有 offset；"全公司多少项目"这类汇总会因读取千万字节结果而超时，演示只问单项目
- `erp_mcp_seed.py` 在触发知识入库的那一次运行里，脚本会在入库完成后卡住（`run_ingest_job` 在纯脚本进程里不返回），文档本身已 ready，`Ctrl-C` 后重跑 `--check` 即可确认
- MCP 凭证以明文存在本地库 `mcp_servers.headers_json`，没有走密钥托管

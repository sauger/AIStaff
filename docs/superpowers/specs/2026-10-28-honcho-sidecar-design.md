# Honcho 侧车长期记忆接入设计

日期：2026-10-28  
仓库：sauger/AIStaff  
状态：Honcho 待实现（2026-10-28 修订）。通讯录 v1 已在 main（PR #24）；组织架构 v1 见 PR #25（已落地或正在落地）。二者提供联系人与部门结构，**不**把跨渠道身份收成同一个 `User.id`。Honcho v1 可按单渠道 / 已是同一 `User.id` 开工。

## 1. 目标与非目标

### 目标

为数字员工增加**可选的跨会话用户画像记忆**，增强「更懂这个人」，不替换现有能力。

- 自托管 Honcho（对话数据不出我方环境）
- 按数字员工开关，先给个别员工开
- 覆盖网页对话 + 已接入**私聊**渠道
- Honcho 失败/超时则**跳过记忆，对话照常**

Honcho 的 peer 稳键是 `user:{User.id}` / `agent:{agent_id}`。Honcho v1 只保证**单渠道**跨会话，或身份**已经**绑在同一个 `User.id` 上的记忆。把企微 / 飞书 / 邮箱 / 网页收成同一个 `User.id` **尚未交付**，也不是通讯录 v1 或组织架构 v1 的能力。那是后续单独的身份/合并交付，不挡住 Honcho v1。

### 依赖与交付顺序

1. **通讯录 v1（已完成，PR #24）** — 员工维度联系人（`tenant_id` + `agent_id`），`primary_user_id` 懒连 `User.id`。`ChannelIdentity` 不变。入站只对**已有**联系人 soft-fill，不新建联系人，也不做跨渠道 User 合并。
2. **组织架构 v1（PR #25，已落地或正在落地）** — 企业后台 `/enterprise/organization`。隶属与负责人用 `contact_id`。不跨员工合并联系人，不把各渠道 User 收成一个 `User.id`。
3. **Honcho v1（现在可以开工）** — 单渠道，或已经是同一个 `User.id` 的双写与 representation 注入。通讯录/组织是人或部门的基础数据，**不是** Honcho v1 的硬前置。
4. **跨渠道 User 合并（另排，未交付）** — 多个 `User` 收成一个 `User.id` 后，Honcho peer 才会跟着共享。不由 Contacts v1 / Org v1 提供，也不作为 Honcho v1 的阻塞项。

`ChannelIdentity` 仍是一条渠道三元组 → 一个 `User`。渠道 external id 只进 Honcho metadata，不能代替尚未交付的跨渠道 User 合并。

自动审办已决内容保持不变，不并进 Honcho：

- 自动审办第一期仍用临时身份绑定表（`staffdeck_user_id` → 企微 userid）。
- 调用方走已预留的身份解析接口（`IdentityResolver`），不把解析写死在临时表上。
- 该表不是 Honcho peer 键。通讯录 v1 / 组织架构 v1 不替换这张表，也不据此把多个渠道 User 收成一个 `User.id`。以后若把代发解析从临时表迁走，那是单独的身份迁移，不是 Honcho v1。

### 非目标（Honcho 第一期不做）

- 不替换知识库 / 知识提案确认短路
- 不替换本地 `MemoryService`（profile/preference/fact）
- 不做群聊写入或群级 peer
- 不接 Honcho Cloud
- 不挂 `peer.chat` 员工工具（可后续加）
- 不在 Honcho 中改写 ChannelBinding / OA，也不把通讯录或组织架构的权威数据存进 Honcho
- 不把自动审办临时身份绑定表写死为长期身份源，或当作 Honcho 的跨渠道合并实现
- 不做跨渠道 User 合并。Honcho v1 不把 wecom / feishu / email / web 收成同一个 `User.id`；通讯录 v1 与组织架构 v1 也不提供这项能力
- 不靠 peer/session metadata 中的渠道 external id 把不同渠道收成同一个 peer

## 2. 架构

独立部署一套 Honcho（API + deriver + Postgres，按官方 compose）。AIStaff 只增加薄适配层 `backend/app/honcho/`。

```
入站(web/channel) → AgentLoop.handle_turn → HarnessV2Engine.run
  ├─ 用户消息落库 → 异步双写 Honcho（不阻塞）
  ├─ 组装 memory_context → 同步拉 user representation（短超时）→ 注入
  ├─ TurnPlanner / ResponseGenerator（须真正传入 memory_context）
  └─ 助手消息落库 → 异步双写 Honcho（不阻塞）
```

知识库网页/渠道确认短路仍在 `AgentLoop` 之前，**不经过** Honcho。

## 3. 身份映射

| Staff 概念 | Honcho |
|---|---|
| 租户 `tenant_id` | workspace = `tenant:{tenant_id}` |
| 对话用户 `User.id` | peer = `user:{user_id}` |
| 数字员工 `AgentProfile.id` | peer = `agent:{agent_id}` |
| `ChatSession.id` | session = `chat:{session_id}` |
| 渠道 `(channel, account_scope, external_user_id)` | 仅写入 peer/session **metadata**，不作稳键 |

跨会话画像依赖同一 `user:{id}` peer 上累积的 conclusions；Honcho session 与 Staff 会话 1:1，便于按会话取 context，不把多人/多会话糊进一条 session。

**单渠道今天即可。** 渠道入站经 `ChannelIdentity` 懒建或解析到一个 `User`（`resolve_or_provision_user`）。同一渠道、同一 external id 的多次会话共享这个 `User.id`，因此 peer `user:{User.id}` 能累积该渠道上的跨会话画像。网页账号同理，稳键就是登录用户的 `User.id`。

**跨渠道同一人尚未交付。** 企微、飞书、邮箱、网页若没有已经绑到同一个 `User`，懒建会各自落到不同的 `User.id`，Honcho 上就是不同 peer。渠道三元组只进 metadata，不能把这些 external id 收成同一个 `user:{User.id}`。通讯录 v1 给出员工维度的 Contact（次级键可空，`primary_user_id` 懒连）；组织架构 v1 用 `contact_id` 表达部门与负责人。两层都不改 `ChannelIdentity` 的「一条渠道三元组 → 一个 User」，也不把多个 User 合成一个。Honcho v1 只保证单渠道，以及身份已经落在同一个 `User.id` 上的记忆。跨渠道 peer 共享留给后续的身份/合并交付。

自动审办第一期的临时身份绑定表解决的是「Staff 用户 → 企微 userid」代发，不是跨渠道人物合并，也不能当作 Honcho 的 peer 合并键。解析继续走已预留接口，不把临时表写进 Honcho。通讯录 v1 / 组织架构 v1 不替换这张表。

群聊第一期：检测到 `channel_is_group`（或等价）则**不双写、不注入**。

解绑/迁移身份后 Staff 仍以 `User.id` 为稳键，故 peer 不得用裸企微/飞书 userid。

## 4. 数据流细节

### 4.1 写路径（异步）

挂点：`HarnessV2Engine.run` 内 `_append_message` 成功之后（用户消息与助手消息各一次）。

仿 `enqueue_memory_capture`：入队异步任务调用 Honcho `session.add_messages`（user peer / agent peer 标注正确）。**不等待** deriver。

失败只打日志/指标，不影响回合。

### 4.2 读路径（同步、可降级）

挂点：`HarnessV2Engine.run` 在本地 `MemoryService.context_memories` 组装成 `memory_context` 之后。

若员工开启 Honcho：请求 `user` peer 的 `representation`（可选带当前 session 作用域）。超时默认 **200ms**（可配）。失败或超时 → 不注入，继续回合。

注入方式：在 `memory_context` 中增加**单独标注**的一块（例如明确标题「长期用户画像」），避免与本地短事实混写难排查。

**必修伴随项：** `ResponseGenerator._stage_payload` 当前把 `memory_context` 传成 `None`，第一期一并改为真正传入，否则终答轮看不到画像。

本轮刚说的话仍以 `conversation_context` 为准；representation 可能滞后于刚双写的消息。

### 4.3 与本地 Memory 的分工

| 层 | 职责 |
|---|---|
| 本地 MemoryService | 短事实、偏好抽取（现有异步 job） |
| Honcho | 跨会话叙述性画像 / 结论 |
| 知识库 | 文档与提案入库 |

两者可同时进 prompt；禁止用 Honcho 替换本地 capture。

## 5. 配置与产品开关

### 全局（Settings / env）

- `HONCHO_ENABLED`（默认 `false`）— 总闸；false 时零调用
- `HONCHO_BASE_URL` — 自托管 API，如 `http://honcho:8000`
- `HONCHO_API_KEY` 或等价鉴权（若自托管开启 auth）
- `HONCHO_TIMEOUT_MS`（默认 `200`）— 仅用于同步 representation
- 可选 `HONCHO_WORKSPACE_PREFIX`（默认 `tenant:`）

### 员工级

- `AgentProfile.metadata` / `metadata_json`：`honcho_enabled: true|false`
- 员工配置页增加开关（读写上述 metadata）
- 生效条件：`HONCHO_ENABLED && metadata.honcho_enabled && 非群聊`

### 部署

- 旁挂 `docker compose`（Honcho 官方示例改编），与 AIStaff 同机或同网段
- 运维文档：启动顺序、LLM key（deriver 需要）、备份 Postgres、健康检查

## 6. 模块边界

建议新增：

- `backend/app/honcho/client.py` — HTTP/SDK 封装，超时与错误归一
- `backend/app/honcho/ids.py` — workspace/peer/session 映射
- `backend/app/honcho/service.py` — `maybe_dual_write` / `maybe_fetch_representation`
- `backend/app/honcho/jobs.py` — 异步双写任务
- 测试：`backend/tests/test_honcho_*.py`（mock client）

不在渠道 adapter 里散落 Honcho 调用；统一走 harness 缝，保证 web/channel 一致。

## 7. 验收标准

1. 总闸或员工开关关闭 → 对 Honcho **零网络调用**，行为与现网一致  
2. 两开关均开 + 私聊/网页 → 用户与助手消息异步双写成功（可在 Honcho 侧查到）  
3. 拼 prompt / planner / **终答** 阶段可见单独标注的画像块（mock representation 可测）  
4. Honcho 超时或 5xx → 对话仍 200/流式成功，无用户可见错误  
5. 知识库确认/否定短路路径不变，不触发双写  
6. 群聊不双写、不注入  
7. 渠道 external id 只出现在 metadata；不同渠道未落到同一个 `User.id` 时保持不同 peer  
8. Honcho v1 只对单渠道，或已经是同一个 `User.id` 的身份，提供跨会话画像。跨渠道把多个 User 收成一个 peer 不在本阶段验收；通讯录 v1 / 组织架构 v1 不算这项能力，也不许用 metadata 冒充合并

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 同步读增加延迟 | 硬超时 + 失败跳过 |
| 与本地 memory 内容重复 | 分块标注；后续再调去重 |
| representation 陈旧 | 本轮以会话上下文为准 |
| 身份迁移 | peer 稳键 = User.id |
| 用 external id metadata 或通讯录/组织行误并跨渠道同一人 | peer 只用 User.id；多个 User 未合并时保持不同 peer。Contacts v1 / Org v1 不做这次合并 |
| 临时绑定表被写成长期身份源或 Honcho peer 键 | 自动审办继续走预留接口；Honcho 不读取该表做 peer。通讯录/组织 v1 不替换该表 |
| 租户串数据 | workspace 强制绑 tenant_id |
| AGPL 自托管改源码义务 | 第一期尽量不改 Honcho 本体，只旁挂 |
| 运维负担 | 文档 + 健康检查；默认可关 |

## 9. 实现分期建议

通讯录 v1 与组织架构 v1 是人或部门的基础，不是 Honcho v1 的硬阻塞。跨渠道把多个 User 收成一个 `User.id` 另排，Honcho v1 不依赖它。

### 9.1 已交付或在交付的身份基础（不包含 User 合并）

- **通讯录 v1（已完成）**：员工维度 Contact；`primary_user_id` 懒连；入站仅 soft-fill 已有联系人；`ChannelIdentity` 仍是一条渠道三元组 → 一个 User。
- **组织架构 v1（PR #25）**：部门、隶属、负责人指向 `contact_id`。不跨员工合并联系人，不统一渠道 User。

自动审办第一期的决定保持不变：继续用临时身份绑定表；调用走已预留接口，不写死临时方案。该表不是 Honcho peer 键，通讯录/组织 v1 也不把它变成跨渠道 User 合并。

### 9.2 Honcho v1（现在可以开工）

范围是单渠道，以及身份已经是同一个 `User.id` 的会话。不承诺 wecom / feishu / email / web 共享一个 peer。

1. Compose 自托管 + 全局配置 + client/ids  
2. harness 双写 job + representation 注入 + 修 ResponseGenerator memory 传递  
3. 员工 metadata 开关 + 配置页 UI  
4. 单测/集成测按 §7；选 1 个员工灰度  

### 9.3 另排：跨渠道 User 合并

把同一人在多个渠道上的多个 `User` 收成一个 `User.id`（或等价的显式合并）。完成后 Honcho 的 `user:{User.id}` 会自然共享画像。这项不是 Contacts v1 或 Org v1，也不是 Honcho v1 的验收项。

设计评审通过后，另开实现计划（writing-plans）再动 Honcho 代码。Honcho 计划以现有 `User.id` 为 peer 稳键，不把 9.3 写进 v1 前置。

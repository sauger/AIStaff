# 通讯录（Contacts）设计

日期：2026-10-28  
仓库：sauger/AIStaff  
状态：待实现（2026-10-28 语音已决；第一交付物，先于 Honcho）

Honcho 侧车设计见 `docs/superpowers/specs/2026-10-28-honcho-sidecar-design.md`（PR #21）。本文只定通讯录，不改该规格，也不改 Honcho 代码。

## 1. 目标与非目标

### 目标

给数字员工一块**通讯录**能力：维护真人的姓名，以及怎么联系到这个人。`Contact` 是这个人的权威行。组织架构与 Honcho 记忆排在后面，并引用这一层。

### 交付顺序

1. **通讯录（本文，第一交付物）** — 人的主档、基本联系方式，以及渠道外部 id 列。
2. **Honcho 侧车** — peer 稳键仍是 `User.id`。实现排在通讯录之后。PR #21 以「组织/通讯录身份工作先于记忆侧车」为前提；本 PR 不修改 Honcho。
3. **组织架构** — 更晚。节点按 `Contact.id` 指向人；通讯录不拥有组织树。
4. **渠道 id 的获取方式、企微通讯录同步、临时绑定表迁移** — 都不是 v1 要定的做法。列本身在 v1。

### 非目标（v1 不做）

- 不放进员工「资料」/员工档案
- 不把企微 userid、飞书 openid、钉钉 userid 当作 `Contact` 主键
- 不规定这些渠道 id 如何填入（手填、入站回填、企微同步均未决）
- 不建组织架构，不让 `Contact` 持有上下级边
- 不替换 `ChannelIdentity`，不改入站懒映射
- 不迁移 `WecomTempIdentityBinding`
- 不规定企微通讯录同步
- 不做详细 UI（v1 只定列表 + 表单）
- 本 PR 不写模型、迁移、API 或页面

## 2. 放置

通讯录是**员工能力**，不是资料。

入口在当前数字员工侧栏的「员工能力」下，与知识库、文件柜、邮件、技能同级。不放在「基本资料」（员工档案、接入渠道等），也不做成员工档案上的字段。

范围与其他员工能力一致：挂在该数字员工的能力面上。v1 的列表和表单都从这里进入。

## 3. 字段 v1

`Contact` 表 v1 收录这些列。行主键是 `Contact` 自己的 id，不是其中任何一列。

| 字段 | 含义 | 角色 |
|---|---|---|
| 姓名 | name | 认人 |
| 手机号 | phone | 联系渠道 |
| 邮箱 | email | 联系渠道 |
| 职位 | title | 基本信息 |
| 所在城市 | city | 基本信息 |
| 备注 | notes | 基本信息 |
| 企微 userid | wecom_userid | 次级查找键，可空 |
| 飞书 openid | feishu_openid | 次级查找键，可空 |
| 钉钉 userid | dingtalk_userid | 次级查找键，可空 |

姓名用来在列表里认人。手机号、邮箱是可以直接写的联系渠道。职位、所在城市、备注一并收录；本文不另写校验细则。

飞书 openid 亦称 open_id，是同一个标识。三列渠道 id 都在 v1 的 `Contact` 表上，有值时用来按渠道找到这一个人。它们不是主键，也不能单独定义这个人。

### 推迟的是获取方式，不是列

今天这些 id 不好当成批量通讯录主键去采集：对方发消息时经入站 `ChannelIdentity` 出现，或靠人工临时绑定。因此 **v1 不选定如何填充**：

- 表单手填
- 从入站 `ChannelIdentity` 懒回填
- 企微通讯录同步

以上都未决。列先放在表上，允许为空。实现计划不要把某一种获取方式写成已定方案，也不要因为还没选定获取方式就把列挪出 v1。

## 4. 数据模型

三层分开。v1 只定义 `Contact`（本文不建表）。

| 层 | 职责 | v1 |
|---|---|---|
| `Contact` | 真人权威行：姓名、基本联系方式、渠道外部 id 列 | 本文定义 |
| `User` | 账号 / 对话身份。`User.id` 是渠道懒建和 Honcho peer 的稳键 | 已有，不改 |
| `ChannelIdentity` | 入站懒映射：`tenant_id + channel + external_account_scope + external_user_id → staffdeck_user_id`（即 `User.id`） | 保持；通讯录不替换 |

`Contact` 用可空外键连到已知账号，建议字段名 `primary_user_id`（等价命名即可），指向 `User.id`。还不知道账号时为空。一个联系人可以先只有姓名和手机，没有 `User`。

渠道外部 id 是 `Contact` 上的次级查找键，不是这条外键，也不是行主键。`ChannelIdentity` 继续做入站映射（唯一约束 `uq_channel_identity_scope_external`）。有人从企微、飞书或钉钉发消息时，仍由它解析或懒建 `User`。通讯录 v1 不读、不写这张表，也不在本阶段把入站 external id 回填进 `Contact`。

### 与组织架构的方向

本规格**没有**组织架构。以后的组织节点引用 `Contact.id`，表示这个位子上的人如何联系。边在组织侧。`Contact` v1 不拥有上级、下级或部门树。

## 5. 维护

v1 界面从简：列表 + 表单。人可读的字段即 §3 的姓名到备注。本文不定筛选、导入或视觉。

渠道 id 三列在表上，可空。列表与表单是否露出它们，跟获取方式一起未决；v1 不实现手填流程、入站回填或同步任务。

企微通讯录同步**搁置**，不在本规格范围内。不规定同步方向、字段映射或冲突策略。同步只是尚未选定的一种填充办法，不是「列不存在」的理由。

## 6. 优先级与后续迁移

通讯录先于 Honcho。Honcho 仍把跨会话记忆落在稳定的 `User.id` 上；面向人的主档先由通讯录承接。PR #21 已写明不把渠道 external id 当成 peer 键，本文不重复其记忆设计。`Contact` 上的渠道 id 同样只是次级查找键。

自动审办的临时表 `WecomTempIdentityBinding`（`staffdeck_user_id` → 企微 userid）**以后迁到 Contact**，不是 v1 工作。`Contact.wecom_userid` 列 v1 就在，迁移之前可以空着。

今天 `IdentityResolver`（`DefaultIdentityResolver.resolve_wecom_userid`）的顺序是：先 `WecomTempIdentityBinding`，再 `ChannelIdentity`。调用方继续走这个接口。长期由 `Contact` 成为面向人的身份层，解析实现再从临时表迁走。v1 不改解析顺序，也不在本阶段把临时表写入 `Contact`。

## 7. 开放问题

`Contact` 与 `User` 何时相连，本文不决：

- **急切**：创建联系人时就创建或绑上 `User`，写入 `primary_user_id`。
- **懒**：只有已经知道 `User.id` 时才写这条外键（例如 `ChannelIdentity` 已经解析到这个人）。

两种都与可空外键兼容。实现计划选定之前，表单不暗示联系人必须已有账号。

渠道 id 三列如何填入（手填、入站懒回填、企微同步等）同样保持开放，见 §3。这与 `User` 急切或懒连接是两个问题，不要绑成一次决定。

## 8. 实现时须守住的决定

另开实现计划再写代码。计划须同时满足：

1. 入口在员工能力，不在资料。
2. v1 表包含 §3 全部列。渠道外部 id 是可空次级查找键，不是主键；获取方式按 §3 先有结论再做填充。
3. `primary_user_id` 可空；`ChannelIdentity` 仍是入站映射。
4. 不建组织树；不规定企微同步；不迁移 `WecomTempIdentityBinding`。
5. 不改 Honcho 规格与代码。急切或懒连接按 §7 先有结论再写迁移。

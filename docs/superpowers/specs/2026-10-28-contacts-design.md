# 通讯录（Contacts）设计

日期：2026-10-28  
仓库：sauger/AIStaff  
状态：v1 已实现（员工能力上的列表 + 表单）。组织架构、企微同步、临时绑定迁移仍未做。

Honcho 侧车设计见 `docs/superpowers/specs/2026-10-28-honcho-sidecar-design.md`（PR #21）。本文只定通讯录，不改该规格，也不改 Honcho 代码。

## 1. 目标与非目标

### 目标

给数字员工一块**通讯录**能力：维护真人的姓名，以及怎么联系到这个人。`Contact` 是这个人的权威行。组织架构与 Honcho 记忆排在后面，并引用这一层。

### 交付顺序

1. **通讯录（本文，第一交付物）** — 人的主档、基本联系方式，以及渠道外部 id 列。
2. **Honcho 侧车** — peer 稳键仍是 `User.id`。实现排在通讯录之后。PR #21 以「组织/通讯录身份工作先于记忆侧车」为前提；本 PR 不修改 Honcho。
3. **组织架构** — 更晚。节点按 `Contact.id` 指向人；通讯录不拥有组织树。
4. **企微通讯录同步、临时绑定表迁移** — 仍不做。渠道 id 可以手填；入站只补空字段。列本身在 v1。

### 非目标（v1 不做）

- 不放进员工「资料」/员工档案
- 不把企微 userid、飞书 openid、钉钉 userid 当作 `Contact` 主键
- 不实现企微通讯录同步。表单可以手填渠道 id。入站不创建联系人，只在已有行上补空的次级 id，并在 `primary_user_id` 为空时懒写入
- 不建组织架构，不让 `Contact` 持有上下级边
- 不替换 `ChannelIdentity`，不改入站懒映射
- 不迁移 `WecomTempIdentityBinding`
- 不规定企微通讯录同步
- 不做详细 UI（v1 只做列表 + 表单）

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

### 已定的填充，以及仍推迟的同步

这些 id 仍然不是主键。v1 已经定了两种填充，都不自动建联系人：

- **表单手填**。三列都在新建/编辑表单里，留空表示还没有。
- **入站只补空字段**。`process_inbound` 在 `resolve_or_provision_user` 之后，对非 `group:` 发送者：已有联系人按 `primary_user_id` 命中，否则按本渠道的次级 id 命中。命中且 `primary_user_id` 为空则写入；次级 id 为空则可以用这条消息的 external id 补上。已有值不覆盖。没有联系人就跳过，不创建。

企微通讯录同步仍未做，不规定同步方向、字段映射或冲突策略。同步不是「列不存在」的理由，也不是 v1 的填充方式。

## 4. 数据模型

三层分开。v1 已建 `contacts` 表。

| 层 | 职责 | v1 |
|---|---|---|
| `Contact` | 真人权威行：姓名、基本联系方式、渠道外部 id 列 | 本文定义 |
| `User` | 账号 / 对话身份。`User.id` 是渠道懒建和 Honcho peer 的稳键 | 已有，不改 |
| `ChannelIdentity` | 入站懒映射：`tenant_id + channel + external_account_scope + external_user_id → staffdeck_user_id`（即 `User.id`） | 保持；通讯录不替换 |

`Contact` 用可空外键连到已知账号，建议字段名 `primary_user_id`（等价命名即可），指向 `User.id`。还不知道账号时为空。一个联系人可以先只有姓名和手机，没有 `User`。

渠道外部 id 是 `Contact` 上的次级查找键，不是这条外键，也不是行主键。`ChannelIdentity` 继续做入站映射（唯一约束 `uq_channel_identity_scope_external`）。有人从企微、飞书或钉钉发消息时，仍由它解析或懒建 `User`。通讯录不替换这张表。入站软填充只改已有 `Contact` 的空字段，不写 `ChannelIdentity`。

`Contact` 与 `User` 的连接是**懒**的：表单不创建、也不要求 `User`。只有入站已经解析到 `User.id`，并且找到了已有联系人时，才写入空的 `primary_user_id`。

### 与组织架构的方向

本规格**没有**组织架构。以后的组织节点引用 `Contact.id`，表示这个位子上的人如何联系。边在组织侧。`Contact` v1 不拥有上级、下级或部门树。

## 5. 维护

v1 界面从简：员工能力下的列表 + 表单。人可读的字段即 §3 的姓名到备注，表单同时露出三列渠道 id，允许手填。本文不定筛选、导入或视觉。

入站不创建联系人。它只在已有行上补空的 `primary_user_id` 和空的次级 id（见 §3）。唯一约束冲突只放弃这一次软填充，不中断入站消息。

企微通讯录同步**搁置**，不在本规格范围内。不规定同步方向、字段映射或冲突策略。同步只是尚未选定的一种填充办法，不是「列不存在」的理由。

## 6. 优先级与后续迁移

通讯录先于 Honcho。Honcho 仍把跨会话记忆落在稳定的 `User.id` 上；面向人的主档先由通讯录承接。PR #21 已写明不把渠道 external id 当成 peer 键，本文不重复其记忆设计。`Contact` 上的渠道 id 同样只是次级查找键。

自动审办的临时表 `WecomTempIdentityBinding`（`staffdeck_user_id` → 企微 userid）**以后迁到 Contact**，不是 v1 工作。`Contact.wecom_userid` 列 v1 就在，迁移之前可以空着。

今天 `IdentityResolver`（`DefaultIdentityResolver.resolve_wecom_userid`）的顺序是：先 `WecomTempIdentityBinding`，再 `ChannelIdentity`。调用方继续走这个接口。长期由 `Contact` 成为面向人的身份层，解析实现再从临时表迁走。v1 不改解析顺序，也不在本阶段把临时表写入 `Contact`。

## 7. 已决与仍开放

`Contact` 与 `User` 用**懒**连接，见 §4。表单不暗示联系人必须已有账号，也不自动为每条入站消息创建联系人。

渠道 id 的手填和入站补空已经定了，见 §3。企微通讯录同步、组织架构、`WecomTempIdentityBinding` 迁移仍然不做。次级 id 目前是消息上的原始 external id，不含渠道账号作用域。

## 8. 实现时须守住的决定

1. 入口在员工能力，不在资料。
2. v1 表包含 §3 全部列。渠道外部 id 是可空次级查找键，不是主键。表单可手填；入站只补空字段，不自动创建 `Contact`。
3. `primary_user_id` 可空，懒写入；`ChannelIdentity` 仍是入站映射。
4. 不建组织树；不规定企微同步；不迁移 `WecomTempIdentityBinding`。
5. 不改 Honcho 规格与代码。

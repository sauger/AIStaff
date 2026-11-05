# Auto-generate follow-up tasks from conversations (FollowUpTask)

日期：2026-10-28  
仓库：sauger/AIStaff  
状态：**未来工作 / 现在不实现**

本文只记录待做能力。不写模型、迁移、API、会话钩子、worker 或页面。实现另开计划。

人员目录见 [`docs/superpowers/specs/2026-10-28-contacts-design.md`](2026-10-28-contacts-design.md)。本文不改那份规格。

## 1. 要解决的事

数字员工和人聊完之后，系统应自动产出跟进事项，而不是等人手工建任务。

今天 `ScheduledTask`（`scheduled_tasks`）由人通过 `create_scheduled_task` 手工添加（`created_by_user_id` 为当前用户）。对话结束后，agent 不会自己建任务。

## 2. 需要的能力

两段分工，缺一不可：

1. **代码定义流程。** 会话结束触发**一次**抽取：有跟进则写入数据库，并挂上扫描；没有则不建行。
2. **LLM 做理解。** 从这段对话里决定：有没有要跟进的事、跟谁、什么时候。代码不替模型做这三问的判断。

## 3. 数据模型方向

新建 `FollowUpTask`，键在 `contact_id`（Contact 这一层）。今天没有 Contact 级的跟进 / 待办表。

短期调度引擎继续用现有 `ScheduledTask` 定时器（`backend/app/scheduled_tasks/worker.py` 扫描到期行并执行）。人的关系来自通讯录，不在本表里重做人员目录。

## 4. 在三层里的位置

接在已定方向上，不另起一套身份：

| 层 | 职责 | 与本文 |
|---|---|---|
| 人员目录 | `Contact`：人的权威行 | 通讯录规格；本文只引用 `contact_id` |
| 解析 | 自然语言 → 人 | 已有方向；抽取结果落到已解析的联系人 |
| 跟进状态机 | 跟进事项本身 | 待建 `FollowUpTask` |
| 何时提醒 | 自动生成的待办扫描 | **本文**：会话结束抽出的待办，交给现有定时器去扫 |

## 5. 现有表：用哪张，不用哪张

- **短期要用：** `ScheduledTask` 及其 worker，只作调度引擎。跟进事项本身不塞进 `scheduled_tasks` 代替 `FollowUpTask`。
- **不是本能力：**
  - `TeamTask`（`team_tasks`）— 团队任务状态机
  - `ExternalBusinessTask`（`external_business_tasks`）— 异步 HTTP 工具的业务状态
  - `WecomTempIdentityBinding`（`wecom_temp_identity_bindings`）— 组织架构上线前的企微临时身份绑定

跟进事项不写入这三张表，也不借它们的状态机。

## 6. 现在不做

- 不建 `FollowUpTask` 表，不写迁移
- 不在会话结束处接抽取，不调 LLM 做跟进判断
- 不改 `ScheduledTask` 的人工创建入口
- 不改通讯录、解析顺序、Honcho 或组织架构

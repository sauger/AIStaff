# 企微自建应用平台化 P0

日期：2026-10-30  
修订：2026-10-30，信息架构改为可折叠侧栏子菜单，路由仍沿用已合并的 PR #30（`abfabde`，`feat(company): 企业管理与成员管理`）。  
仓库：sauger/AIStaff  
状态：设计。本文件是 P0 的实现规格。本 PR 不改应用代码。  
已决：三层模型、多应用与默认应用、凭证加密方式，由药 李子（sauger）拍板。企微应用留在企业管理里，和渠道管理分开。入口是侧栏里可折叠的子菜单，不是 `CompanyPage` 的页签。下文按这个目标写。与仓库现状不一致的地方记在 §3，不把已决项再改回现状。

相关现状代码（只读，P0 实现时再改）：

- 凭证表 `WecomOaAppCredential` / `wecom_oa_app_credentials`：`backend/app/db/models.py`
- 存取与加密：`backend/app/wecom_oa/credentials.py`，Fernet 在 `backend/app/channels/crypto.py`
- 管理接口与回调：`backend/app/api/wecom_oa.py`
- 审批代发读凭证：`backend/app/wecom_oa/service.py` 的 `get_oa_credential`
- 渠道页嵌套 OA 面板：`frontend-enterprise/src/pages/ChannelsPage.tsx` 引入 `WecomOaAdminPanel`
- 员工档案上的审批场景表单：`WecomOaAgentConfigPanel`（`EmployeeProfileEditor`），不是应用凭证
- 企业管理壳：`frontend-enterprise/src/pages/company/CompanyPage.tsx`（PR #30）。侧栏目前只有一项「企业管理」→ `/enterprise/company`。页内 `UnderlineTabs` 分成组织架构 `/enterprise/company/organization`、成员 `/enterprise/company/members`。`/enterprise/organization` 重定向到组织架构路由。`AppSidebar` 里「基本资料 / 员工能力」是静态分组，没有可折叠子菜单。整栏收起记在 `localStorage` 键 `ultrarag_enterprise_sidebar_expanded`（`App.tsx`）

## 1. 目标与非目标

### 目标（P0 要交付的行为）

把企微**自建应用**收成租户级平台凭证，和管理员能操作的独立页面。

1. 一个租户从第一天起可以有**多个**自建应用（多企业 corp、同一企业多个 agent）。
2. 指定**一个默认应用**，供现有 OA 审批代发使用。组织同步以后也用这一个，但 P0 不做同步。
3. 管理员可以增删改应用凭证、复制回调地址、做连通性自检（`gettoken` + `agent/get`）、设为默认。
4. 回调按应用分开。推荐路径是 `/api/public/wecom-app/callback/{tenant_id}/{app_id}`。旧 OA 路径在 P0 保留，只打到默认应用。
5. 已有 `wecom_oa_app_credentials` 行迁入新表。密文原样拷贝，不在迁移时解密。
6. 渠道管理只留智能机器人。自建应用凭证从渠道卡片上拿掉。

P0 的「员工领用」沿用现在 OA 的用法：员工不持有 corp secret / agent secret。审批代发继续走租户默认应用。P0 **不**做员工领取、认领或按员工复制一份凭证。

### 非目标（P0 明确不做）

- 事件池的表、接口、页面
- 员工认领事件、把认领结果开成会话
- 用自建应用拉取组织架构或部门同步
- 重写审批 Mode A / Mode B（申请人模式、临时身份绑定的匹配规则、确认卡、`applyevent`）
- 改智能机器人：`ChannelBinding`、`WecomSetup`、机器人 ID / Secret、入站会话路由
- 多应用之间按模板、部门或事件类型自动分流（那是 P3）
- 把 `WecomTempIdentityBinding` 迁进 `Contact`

### 后续阶段（P0 只点名，不实现）

| 阶段 | 内容 |
| --- | --- |
| P1 | 自建应用入站事件写入租户事件池。池是租户级的，还没有员工认领。 |
| P2 | 员工认领池中事件，认领之后才进入会话。组织 / 部门同步走默认应用。 |
| P3 | 更细的多应用路由，以及审批模板同步的打磨。 |

事件池的目标形态见 §10。P0 的回调在验签解密之后，审批状态仍走现有 `apply_status_update`；其他事件只记一条不含正文的日志并返回 `success`，**不落池**。

## 2. 三层模型

三层分开。凭证不混用，入口不混用。

| 层 | 持有者 | 凭证 | 入口 | P0 |
| --- | --- | --- | --- | --- |
| 智能机器人（Bot） | 数字员工，1:1 | 机器人 ID + Secret，外加绑定上的企业 ID。放在 `ChannelBinding` | 渠道管理里的企微卡片，只保留 `WecomSetup`。员工侧「接入渠道」同样只绑机器人 | 不改机器人行为 |
| 自建应用（App） | 租户 / 平台 | `corp_id`、`agentid`、secret，以及回调 Token / EncodingAESKey | 侧栏「企业管理」展开后的子项「企微应用」，路由 `/enterprise/company/wecom-apps` | 本文 |
| 事件池 + 认领 | 租户池，员工认领后才进会话 | 不另发凭证。认领的是事件，不是 secret | 尚未建页面 | 不建 |

自建应用的用法与现有 OA 相同，叫「平台持证 + 员工领用」：secret 只在租户行上，员工侧能力（今天的审批代发）使用平台凭证，员工档案里不出现 secret 输入框。

机器人卡片上可以有企业 ID，那是机器人绑定的身份作用域，不是自建应用凭证。两套 corp_id 允许相同，也允许不同。P0 不要求二者一致，也不用机器人的 corp_id 去推断默认应用。

## 3. 现状差距

下面每条都是仓库里的事实。P0 按 §2 和 §4 的目标改，不维持这些形状。

1. **表只能一企业一行。** `wecom_oa_app_credentials` 的唯一约束是 `(tenant_id, corp_id)`（`uq_wecom_oa_app_tenant_corp`）。同一企业的第二个 agent 存不进去。`agentid` 可空。没有名称、没有默认标记、没有自检结果。
2. **读取是「任意一行」。** `get_oa_credential` 在 `status == active` 上 `first()`。未按 `corp_id` 过滤时，没有「默认应用」。审批预览（`preview_approval`）不传 `corp_id`；提交和查询传 `intent.corp_id`。
3. **管理入口嵌在渠道卡片里。** `ChannelsPage` 在 `binding.channel === 'wecom'` 时渲染 `WecomOaAdminPanel`。这个面板同时包含「OA 自建应用凭证」和「临时身份绑定（Mode B）」。接口是单条 `GET/PUT /api/enterprise/wecom-oa/credentials`。
4. **分区还在页面页签上，侧栏不能折叠子项。** PR #30 的侧栏是一项「企业管理」（`EnterpriseRoute.Company` = `/enterprise/company`）。`CompanyPage` 用 `UnderlineTabs` 放组织架构 `/enterprise/company/organization` 和成员 `/enterprise/company/members`。`/enterprise/company` 重定向到组织架构。旧路由 `/enterprise/organization` 重定向到 `/enterprise/company/organization`。`AppSidebar` 没有可折叠的导航子组：「基本资料」「员工能力」只是静态 `GroupLabel`。整栏图标模式的展开状态写在 `ultrarag_enterprise_sidebar_expanded`，那是侧栏宽窄，不是某一组的子项。P0 要把这三个分区做成「企业管理」下可折叠的侧栏子项，并去掉页面页签。账号管理、模型配置、运行设置仍是侧栏上的平级项。不使用 `/enterprise/wecom-apps`。
5. **回调不区分应用。** 公开路径是 `GET/POST /api/public/wecom-oa/callback/{tenant_id}`。验签用该租户 `get_oa_credential` 返回的那一行。多应用之后这条路径无法选择 Token。
6. **员工档案上的审批场景不是应用凭证。** `WecomOaAgentConfigPanel` 写 `AgentResourceBinding`（`resource_type = wecom_oa_approval`）：模板 ID、Mode A/B、字段映射。P0 保留这个表单和这些接口。它不搬到「企微应用」，也不在 P0 重写。
7. **还没有事件池，也没有认领表。** 回调里非审批、或没有 `sp_no` 的消息只打日志。这和 P0「先不建池」一致，不要在 P0 顺手加表。

迁移之后，审批代发只使用默认应用。若历史意图上的 `corp_id` 与默认应用的 `corp_id` 不一致，提交失败并返回明确错误，不在其他应用里再搜一遍。今天如果库里真有多行，`first()` 本来也不是稳定选择；失败关闭比猜一行更安全。多应用分流留给 P3。

## 4. 信息架构

依赖已合并的 PR #30 的路由，不依赖它的页签。P0 把 Company 从页面级 `UnderlineTabs` 迁到侧栏可折叠子菜单。企微应用是「企业管理」下的同级子项，不是 `CompanyPage` 上的第三个页签。

父项 **企业管理** 只负责展开和收起，不单独占一个内容页。展开时显示三个子项，收起时子项不渲染。子项才导航：

| 侧栏子项 | 路由 | P0 |
| --- | --- | --- |
| 组织架构 | `/enterprise/company/organization` | 已有路由，从页签挪到子项 |
| 成员 | `/enterprise/company/members` | 已有路由，从页签挪到子项 |
| 企微应用 | `/enterprise/company/wecom-apps` | 新路由。`EnterpriseRoute` 增加常量。页面内容是应用凭证，不是 `CompanyTab` |

`/enterprise/company` 继续重定向到 `/enterprise/company/organization`。`/enterprise/organization` 继续重定向到组织架构路由，不要改回独立页面。不新增 `/enterprise/wecom-apps`。`/enterprise/company/people` 与 `/enterprise/company/agents` 继续重定向到成员。

交互：

- 父行是按钮，带展开指示（现有侧栏的 chevron 一类），`aria-expanded` 表示开或关。点击父行只切换开合，不把父行当成第四个路由。
- 展开时三个子项按上表顺序出现，缩进在父项下面，视觉沿用现有侧栏行（父行接近 `PrimaryNavButton`，子行接近「基本资料」下的 `CardNavButton`）。当前子路由高亮。任一子路由激活时，父行也保持选中态。
- 收起时只留父行。若当前就在某个子路由上，允许收起；父行仍然呈选中态，避免人找不到这一组。
- 非管理员不渲染这一组，和今天的企业管理入口一样。账号管理、模型配置、运行设置仍是侧栏平级项，不放进这组。
- 侧栏收成图标时，子项文字藏起，父图标仍在。点父图标展开侧栏，或直接进入当前已选子路由；没有已选子路由时进入组织架构。不要在图标模式下把三行文字挤进窄栏。

展开记忆：`AppSidebar` 里没有现成的「一组子导航可折叠」可照搬。「基本资料 / 员工能力」不能折叠。能持久化的只有整栏宽窄 `ultrarag_enterprise_sidebar_expanded`。P0 另用一个 `localStorage` 布尔键，读写方式和那个键相同，例如 `ultrarag_enterprise_company_nav_expanded`。缺省为展开。用户折叠或展开后写回。刷新后读回。若当前 URL 已经在 `/enterprise/company/` 下而存的是收起，进入该路由时展开并写回，保证深链能看到被选中的子项。不要把这组的开合写进整栏那个键。

页面：

- 去掉 `CompanyPage` 的 `UnderlineTabs` 和 `CompanyTab` 切换。组织架构、成员、企微应用各自是一条路由。
- 三个路由仍可共用现在的页头外壳，标题保持「企业管理」。描述句补上自建应用。分区切换只在侧栏发生。
- `App.tsx` 为 `/enterprise/company/wecom-apps` 增加管理员路由。非管理员重定向到广场，与组织架构、成员一致。
- 企微应用页只渲染凭证内容，不再从 `CompanyPage` 的 `tab === 'wecom-apps'` 分支挂出来。

```
管理员侧栏
  开放广场平台
  我的数字员工
  我的团队
  渠道管理          ← 企微卡片只留智能机器人
  企业管理          ← 可折叠父项，点击展开/收起
    组织架构        ← /enterprise/company/organization
    成员            ← /enterprise/company/members
    企微应用        ← /enterprise/company/wecom-apps
  账号管理
  模型配置
  运行设置
```

接口仍走租户管理员校验（与组织架构、现有 OA 凭证相同：`ensure_current_user_tenant` + `is_admin_user`）。

渠道管理：

- 企微绑定卡片只保留 `WecomSetup`（机器人 ID、机器人 Secret、企业 ID）
- 去掉嵌在卡片上的整块 `WecomOaAdminPanel`。凭证表单和 Mode B 临时绑定在同一块面板里，一起离开渠道页
- 不在渠道页保留「只是藏起来」的应用 secret 表单

企微应用子页（管理员，路由 `/enterprise/company/wecom-apps`）：

- 空状态：说明自建应用是租户级凭证，和智能机器人不是同一套，并给出新建入口
- 列表：名称、企业 ID、AgentId、是否默认、最近一次自检结果、回调地址（可复制）
- 新建 / 编辑：名称、企业 ID、AgentId、Secret、回调 Token、EncodingAESKey
- 操作：保存、设为默认、连通性自检、删除
- 密钥输入框只在填写时出现。已保存的密钥不回显，用「Secret 已存 / 回调 Token 已存」这样的标记
- 页底单独一节 **临时身份绑定（Mode B）**。它跟着凭证一起从 `WecomOaAdminPanel` 搬来，不留在渠道卡片上。绑定规则、接口路径、字段都不改。文案写明：这不是应用凭证，P0 不改 Mode A/B

员工侧：

- 「接入渠道」继续只绑机器人和其他渠道，不增加应用 secret
- 员工档案里的 `WecomOaAgentConfigPanel` 留在原处
- 数字员工上不出现 corp secret、agent secret、回调 Token、EncodingAESKey

页面文案用中文。实现时跟组织架构一样跑前端的 `i18n:check`，把新词条补进英文目录。

## 5. 数据模型

新表 `wecom_apps`。旧表 `wecom_oa_app_credentials` 在 P0 迁移后只作来源，业务读写改走新表。不新建 Alembic。本仓库生产迁移以 SQLite 为准：`init_db` 里 `SQLModel.metadata.create_all` 建表，再用一段可重复执行的启动迁移拷数据、补索引。做法对齐 `ensure_org_department_sibling_unique`，但数据拷贝不要写成「仅 SQLite」——有表就拷，没有旧行就跳过。

| 列 | 含义 |
| --- | --- |
| `id` | `wapp_` 前缀。不复用旧的 `woaapp_` 主键 |
| `tenant_id` | 租户 |
| `name` | 管理员看到的名称，必填 |
| `corp_id` | 企业 ID，必填 |
| `agentid` | 自建应用 AgentId。新建必填。从旧表迁入时允许先为空 |
| `secret_enc` | corp secret 的 Fernet 密文 |
| `callback_token_enc` | 回调 Token 密文，可空 |
| `callback_aes_key_enc` | EncodingAESKey 密文，可空 |
| `is_default` | 该租户是否把此应用当作 OA / 以后同步的默认应用 |
| `status` | `active` / `disabled` |
| `legacy_oa_credential_id` | 来源 `wecom_oa_app_credentials.id`，可空，唯一。用来让迁移可重复执行 |
| `last_check_at` | 最近一次自检时间，可空 |
| `last_check_ok` | 最近一次自检是否成功，可空 |
| `last_check_summary` | 给人看的短句，例如「已连通」或企微 `errmsg`。不写 token |
| `last_callback_verified_at` | 最近一次回调 URL 校验（GET echostr）成功的时间，可空 |
| `created_by_user_id` | 创建人 |
| `created_at` / `updated_at` | 时间 |

约束：

- `UNIQUE(tenant_id, corp_id, agentid)`。同一企业的两个 agent 可以并存。
- 空的 `agentid` 存空字符串 `''`，不存 NULL。SQLite 的 UNIQUE 把多个 NULL 当成互不重复，同一企业可以塞进两行空白 AgentId。空字符串才会被这条唯一约束挡住。新建接口仍然拒绝空白 AgentId，不依赖数据库去拦截手填的空值。
- 每个租户最多一个 `is_default = 1`。启动时执行 `CREATE UNIQUE INDEX IF NOT EXISTS uq_wecom_app_one_default ON wecom_apps (tenant_id) WHERE is_default = 1`。设默认的接口也要在同一事务里清掉同租户其他行的默认标记。
- 新建接口拒绝空的 `agentid`。旧行 `agentid` 为空时仍迁入（写成 `''`），自检在调用企微之前直接返回「请先填写 AgentId」。审批代发今天并不读取 `agentid`，所以缺 AgentId 只挡住自检，不挡住已经在用的代发。
- `disabled` 的行不能当默认。设默认之前必须是 `active`。回调和代发都不使用 `disabled` 行。

P0 **不**给 `WecomApprovalIntent` 加 `app_id`。意图继续记 `corp_id`。代发时取默认应用，并核对该 `corp_id`。多应用路由需要的快照留到 P3，避免 P0 改审批写入路径。

P0 **不**建事件池表。目标表名和列放在 §10，避免 P1 另起一套对不上的名字。

## 6. 接口

管理接口都要求租户管理员。响应里的应用对象沿用现在的公开视图：`has_secret`、`has_callback_token`、`has_callback_aes_key`。永远不返回明文。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `GET` | `/api/enterprise/wecom-apps?tenant_id=` | 该租户全部应用。无行时 `apps: []` |
| `POST` | `/api/enterprise/wecom-apps?tenant_id=` | 新建。`name`、`corp_id`、`agentid`、`secret` 必填。回调 Token 与 EncodingAESKey 成对：都空表示还没配回调；只填一个返回 400。该租户还没有默认应用时，新建的这一行成为默认 |
| `PATCH` | `/api/enterprise/wecom-apps/{app_id}` | 改名称、企业 ID、AgentId、状态。secret / token / aes **省略**表示保持原密文；secret 传空字符串返回 400，不允许把 secret 清成空。token 与 aes 同时传空字符串表示清除回调材料 |
| `DELETE` | `/api/enterprise/wecom-apps/{app_id}` | 删除。若它是默认应用且租户还有其他应用，返回 409，要求先把另一行设为默认。最后一行可以删，删后租户没有默认应用 |
| `POST` | `/api/enterprise/wecom-apps/{app_id}/default` | 设为默认。行必须属于该租户且 `status=active` |
| `POST` | `/api/enterprise/wecom-apps/{app_id}/check` | 连通性自检，见下 |

自检顺序：

1. 行不存在、租户不匹配、已停用：404 或 409，不请求企微。
2. `agentid` 为空：400，不请求企微。
3. 解密 secret。解密失败返回 500 级的明确错误（密钥配置问题），日志里不带密文。
4. `GET https://qyapi.weixin.qq.com/cgi-bin/gettoken`，参数 `corpid`、`corpsecret`。
5. 成功后再 `GET .../agent/get`，参数 `access_token`、`agentid`。
6. 把 `last_check_at`、`last_check_ok`、`last_check_summary` 写回该行。摘要只用成功短句或企微 `errmsg` / `errcode`。
7. 响应给页面：`ok`、`checked_at`、`errcode`、`summary`，以及成功时企微返回的应用名称。不返回 `access_token`。

代发解析（替换 `get_oa_credential` 的调用点，不改 Mode A/B 状态机）：

- 预览、提交、查询都加载该租户 `is_default = 1` 且 `status = active` 的那一行。
- 没有默认应用：沿用今天的 `WECOM_OA_NO_CREDENTIAL`。
- 意图上已有 `corp_id`，且与默认应用的 `corp_id` 不同：失败，错误码 `WECOM_OA_APP_CORP_MISMATCH`。不回退到其他应用。
- 预览新意图时，写入意图的 `corp_id` 取默认应用的 `corp_id`。

旧管理接口：

- `PUT /api/enterprise/wecom-oa/credentials` 在 P0 改为 409，提示改用企业管理侧栏子项「企微应用」（`/enterprise/company/wecom-apps`）。不双写旧表。
- `GET /api/enterprise/wecom-oa/credentials` 改为只读投影：有默认应用则返回旧的 `credential` 公开字段（`id` 用新 `wapp_` id）；没有则 `credential: null`。给还没刷新的页面一个空状态，而不是继续写旧表。
- `/api/enterprise/wecom-oa/temp-identity-bindings` 与 `/api/enterprise/wecom-oa/agents/{agent_id}/approval-config` 保持原样。
- `/api/chat/wecom-oa/confirm` 与 `dismiss` 保持原样。

公开回调见 §7。

## 7. 回调 URL

P0 使用按应用区分的路径。多应用不能共用一个只含 `tenant_id` 的 Token。

```
/api/public/wecom-app/callback/{tenant_id}/{app_id}
```

`app_id` 必须属于该 `tenant_id`，否则 404。不要只靠 `app_id` 全局查找后再忽略租户。

演示环境（租户 `tenant_demo`）：

```
https://aistaffdemo.muli.group/api/public/wecom-app/callback/tenant_demo/{app_id}
```

生产环境用部署配置 `PUBLIC_BASE_URL`（`settings.public_base_url` / `normalized_public_base_url`）拼绝对地址：

```
{PUBLIC_BASE_URL}/api/public/wecom-app/callback/{tenant_id}/{app_id}
```

页面上的「复制回调地址」：基地址有效时给绝对 URL；未配置时只给路径，并提示先配置公网基地址再贴进企微后台。不要回退到 `tool_base_url`，也不要把 localhost 当成生产回调地址。

企微后台保存回调时会发 GET，查询参数为 `msg_signature`、`timestamp`、`nonce`、`echostr`。P0 用**该应用**的 Token 验签，用该应用的 EncodingAESKey 解密，并核对 `corp_id`。成功则原样返回明文，并写入 `last_callback_verified_at`。这就是页面上说的回调校验。

事件 POST 使用同一路径。验签解密之后：

- 能解析出审批变更且带 `sp_no`：调用现有 `apply_status_update`，`source=callback`。不要求该应用是默认应用。验签已经把事件绑到这一行的 Token 上。
- 其他事件：日志只记 `tenant_id`、`app_id`、事件名。返回正文 `success`。不写事件池，不建会话，不把明文正文打进日志。

### 旧路径

```
/api/public/wecom-oa/callback/{tenant_id}
```

演示环境今天的地址是：

```
https://aistaffdemo.muli.group/api/public/wecom-oa/callback/tenant_demo
```

P0 **保留**这条路径，作为默认应用的兼容入口，避免已经配在企微后台的 OA 回调在迁移当天失效。行为：

- 解析该租户的默认应用；没有默认应用则 404
- GET / POST 都只用默认应用的 Token 和 EncodingAESKey
- 审批变更仍走 `apply_status_update`
- 打一条不含 secret 的日志，说明命中的是旧路径
- 不接收「非默认应用」的事件。第二个应用必须把后台回调改成新路径
- 企微应用子页展示的、要求管理员复制的地址是新路径。旧路径不作为新应用的配置说明

去掉旧路径不是 P0。等操作者把企微后台改到新地址之后，再在后续阶段删除。P0 不设自动下线。

若租户迁移后只有一个应用，它就是默认应用，旧地址可以继续用，直到管理员改后台。

## 8. 迁移

启动时跑一次可重复的拷贝。旧行留在 `wecom_oa_app_credentials`，不在 P0 删除，以便回看。业务代码在迁移落地后不再读取旧表，除了这段迁移本身。

对每一行旧凭证：

1. 若新表已有 `legacy_oa_credential_id` 等于旧 `id`，跳过。
2. 插入 `wecom_apps`：`corp_id`、`agentid`、三份密文、`status`、`created_by_user_id`、时间都原样拷贝。**不要**解密再加密。Fernet 密钥不变，密文可以直接用。
3. `name` 缺省为 `企业微信应用 {corp_id}`。管理员可以之后改名。
4. 新 `id` 使用 `wapp_`。旧 id 只进 `legacy_oa_credential_id`。

默认应用，按租户：

- 该租户在新表里还没有 `is_default` 时，在刚迁入的 `status=active` 旧行中选 `updated_at` 最新的一行作为默认。时间相同则取 `legacy_oa_credential_id` 字典序较小的一行，保证重复执行结果一致。
- 没有 active 行则不设默认。
- 迁完后再跑设默认，不得破坏已经人工设好的默认（以「新表已有默认」为准，跳过自动选择）。

`agentid` 为空的旧行照搬，并写成 `''` 而不是 NULL，见 §5。唯一约束在 P0 新建时才要求 AgentId。旧数据里同一 `(tenant_id, corp_id)` 至多一行，迁移不会撞上「同一企业两个 agent」。同一企业若再迁入第二行空白 AgentId，唯一约束应失败并停在这一行，不要静默改成 NULL 绕过。

意图、临时身份绑定、员工审批配置都不改写。

操作者需要知道的唯一外部步骤：企微后台若仍填写旧回调地址，它只对应迁移后的默认应用。新增加的应用使用 §7 的新地址。

## 9. 安全

- 加密沿用 `encrypt_channel_secret` / `decrypt_channel_secret`：SHA-256 派生 Fernet 密钥，优先 `CHANNEL_SECRET`，未配置时用 `APP_SECRET` 派生。这是现有渠道凭证的做法。P0 不新做一套加密。生产必须配置 `CHANNEL_SECRET`；派生回退只当作开发兜底，页面和日志都不要鼓励生产依赖它。
- 明文只存在于当次请求的内存里，用于自检或回调验签。不写入日志、不写入 `last_check_summary`、不写入审计 JSON、不返回给前端。
- 日志允许的字段：`tenant_id`、`app_id`、`corp_id`、`agentid`、`errcode`、事件名。禁止：secret、Token、EncodingAESKey、`access_token`、请求体、`Encrypt` 节点、解密后的业务 XML。
- 回调 URL 里的 `tenant_id` 和 `app_id` 是标识，不是密钥。secret 不进 URL，不进查询字符串。
- 管理接口与页面仅租户管理员。员工档案和渠道页不能提交应用 secret。
- 停用和删除立即对回调生效。停用应用的回调返回 404，不验签失败与不存在混成同一种可枚举差异之外的成功响应。验签失败仍是 403，且详情不包含期望签名。
- 自检失败把企微 `errmsg` 放进摘要前，去掉其中若出现的 secret 样字符串。实现时不要把请求 URL（含 `corpsecret` 或 `access_token`）记进异常消息。

## 10. 事件池目标（P1 再用，P0 不建）

P1 的池是租户级收件箱。应用回调先入池，员工还没有会话。

建议表名 `wecom_app_events`，P0 不要创建：

| 列 | 含义 |
| --- | --- |
| `id` | 事件 id |
| `tenant_id` | 租户 |
| `app_id` | 来源 `wecom_apps.id` |
| `event_type` | 企微事件名 |
| `payload_enc` 或受控 JSON | 正文如何落库由 P1 再定。P0 不预写明文正文 |
| `status` | 目标值：`pooled` → `claimed` → `session` / `ignored` |
| `claimed_by_user_id` / `claimed_by_agent_id` | P2 认领 |
| `session_id` | P2 认领后的会话 |
| `created_at` | 入池时间 |

P0 不写这张表，也不做认领 API、认领页面、组织同步任务。审批状态回调继续直接更新已有意图，不绕进池里。这样 P1 加上池时，不必改 P0 已经上线的多应用回调路径，只是把「其他事件只打日志」换成「其他事件入池」。

P2 才规定谁可以认领、认领后会话挂在哪个数字员工。P3 才规定一条事件在多个应用之间如何选择，以及模板列表是否从默认应用同步到员工审批配置。

## 11. 实现时须守住的决定

1. 机器人凭证留在 `ChannelBinding`。应用凭证只在 `wecom_apps`。员工身上不保存应用 secret。
2. 渠道管理的企微卡片不再渲染 `WecomOaAdminPanel`。凭证和 Mode B 临时绑定一起挪到企微应用子页底部，绑定规则不变。
3. 「企业管理」是可折叠侧栏父项。展开后的子项是组织架构、成员、企微应用，路由分别是 `/enterprise/company/organization`、`/enterprise/company/members`、`/enterprise/company/wecom-apps`。去掉 `CompanyPage` 的 `UnderlineTabs`。展开状态单独记住，不做成页面页签，不新增 `/enterprise/wecom-apps`。
4. 一个租户多行应用，唯一键是 `(tenant_id, corp_id, agentid)`，且只有一个默认应用。
5. 审批代发只用默认应用。`corp_id` 对不上就失败，不扫描其他行。
6. 新回调路径带 `app_id`。旧路径只服务默认应用，P0 不删除。
7. 迁移拷贝密文，不解密。旧表保留。迁移可重复执行。
8. 不建事件池，不改 Mode A/B，不改机器人入站，不同步组织。
9. 日志和 API 响应不出现明文密钥和 `access_token`。

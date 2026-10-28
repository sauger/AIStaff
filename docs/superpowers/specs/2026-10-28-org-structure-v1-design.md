# 组织架构 v1

日期：2026-10-28  
仓库：sauger/AIStaff  
状态：v1 接线（企业名称、部门树、任职、部门负责人、任职区间）。不做企微同步，不做界面打磨。

通讯录规格见 `docs/superpowers/specs/2026-10-28-contacts-design.md` §9。本文只定组织架构 v1，不改 Honcho，不迁移 `WecomTempIdentityBinding`。

## 1. 放置

组织架构是**企业级 / 租户级**的。一个租户一套部门树，所有数字员工共用。

入口在企业端管理员区，和账号管理、模型配置、运行设置放在一起（侧栏顶部管理员项）。不放进「员工能力」。非管理员打不开这个页面，接口也走现有的租户管理员校验（`ensure_tenant_admin`）。

通讯录仍然是员工能力，仍然按 `tenant_id + agent_id` 各存一本。

## 2. v1 范围

只做这五件事：

1. 企业名称。复用已有 `tenants.name`，不另建 org_settings 表。管理员在本页修改的就是租户显示名。
2. 部门树。邻接表 `parent_id`，加上物化路径 `path`。路径是 id 拼出来的：根为 `/{id}`，子部门为 `{parent.path}/{id}`。没有闭包表。
3. 人与部门的任职（affiliation）。
4. 每个部门的负责人。单独的负责人行，不塞进任职的布尔标记。
5. 任职和负责人都有 `valid_from` / `valid_to`。`valid_to` 为空表示至今。两端都包含当天。

可以新建、修改、删除部门。可以按部门加上查询日期列出当天的直接成员。v1 不把子部门的人算进上级。

不做企微、飞书、钉钉的组织同步，不做导入。

## 3. Contact.agent_id 和租户级组织

通讯录规格要求组织节点引用 `Contact.id`。`Contact` 却挂在某一个数字员工上：同一真人可以在两本通讯录里各有一行，而且很多行的 `primary_user_id` 还是空的。

v1 的取舍（尽量少改已有通讯录）：

- 任职和负责人都存 `contact_id`，指向某一条 `Contact`。不用 `User.id`。
- 管理后台的人选来自**本租户全部通讯录**，选项上带所属员工名称，用来区分重名。
- 不把两本通讯录里的同一个人合并，也不要求先有对话账号。
- 删掉一条联系人时，指向它的任职和负责人一起删掉。

以后如果要做「租户级的人」，再单独做合并或主档。v1 不改 `Contact.agent_id`。

## 4. 数据

新表由启动时的 `SQLModel.metadata.create_all` 建出来，和 `contacts` 一样。这个仓库没有 Alembic；新表不需要 ALTER。

| 表 | 要点 |
|---|---|
| `org_departments` | `tenant_id`、`name`、可空 `parent_id`、`path`、`sort_order`。同一上级下部门名不重复。根部门之间也不重复。 |
| `org_affiliations` | `department_id` + `contact_id` + `valid_from` + 可空 `valid_to`。允许同一人多条任职，包括时间重叠。 |
| `org_department_heads` | 同样的人和区间。同一天若有多条覆盖，查询取 `valid_from` 最晚的一条当作当前负责人。历史行留着。 |

移动部门时改写自己和所有下级的 `path` 前缀。不能把部门移到自己或自己的下级下面。还有下级时不能删除；删叶子部门时一并删掉它的任职和负责人。重命名不改路径，因为路径用的是 id。

## 5. 接口

都在 `/api/enterprise/org`，要求租户管理员。

- `GET/PATCH /settings`：读、改 `tenants.name`
- `GET/POST /departments`，`PATCH/DELETE /departments/{id}`
- `GET /departments/{id}/people?as_of=`：当天直接成员、全部任职、负责人记录、当前负责人
- `GET /contacts`：本租户联系人选项（含所属员工名）
- `POST/PATCH/DELETE /affiliations` 与 `/heads`

## 6. 不做

- 企微 / 飞书 / 钉钉组织同步
- 闭包表
- 比现有管理员校验更细的权限
- 迁移 `WecomTempIdentityBinding`
- Honcho
- 上线部署
- 把子部门成员算进上级的查询结果

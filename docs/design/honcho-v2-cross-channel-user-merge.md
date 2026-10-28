# 跨渠道 User 合并设计草案（Honcho v2 前置）

日期：2026-10-28  
仓库：sauger/AIStaff  
状态：草案。不是 Honcho v1 的范围，也不随 v1 交付。  
前置：PR #21 设计（`docs/superpowers/specs/2026-10-28-honcho-sidecar-design.md`）把跨渠道合并明确后置；PR #26 只保证单渠道，或已经是同一个 `User.id`。

## 1. 问题

Honcho v1 的 peer 稳键是 `user:{User.id}`。企微、飞书、钉钉、邮箱、网页若各自懒建出不同的 `User`，Honcho 上就是不同 peer。渠道 external id 只在消息 metadata 里，不能把这些人收成同一个 peer。

通讯录 v1 的 `Contact` 是员工维度的真人行，`primary_user_id` 可空、懒连 `User.id`。组织架构 v1 的任职和负责人指向 `contact_id`，不指向 `User`。这两层都不改 `ChannelIdentity` 的「一条渠道三元组 → 一个 `User`」，也不提供 User 合并。

本草案只设计：**先在 AIStaff 把多个 `User` 收成一个存活的 `User.id`，Honcho 再跟着这个 id。** 不在 Honcho 里另做一套身份图。

## 2. 现有稳键（合并不得绕开）

| 对象 | 键 | 合并时怎么动 |
| --- | --- | --- |
| `User.id` | 人物账号 | 选出存活 id。被合并的 User 停用，不复用其 id 当 peer |
| `ChannelIdentity` | `(tenant_id, channel, external_account_scope, external_user_id)` → `staffdeck_user_id` | 只改 `staffdeck_user_id` 指向存活 User。三元组本身不变 |
| `Contact` | `(tenant_id, agent_id)` 下的真人行；`primary_user_id` 可空，且 `(tenant_id, agent_id, primary_user_id)` 唯一 | 不把两个 Contact 自动合成一行。组织仍挂 `contact_id` |
| `ChatSession.user_id` | 会话归属 | 改到存活 User。`ChatSession.id` 不变，因此 Honcho session `chat:{id}` 不变 |
| 本地 Memory | `user_id` + `agent_id` | `user_id` 改到存活 User |
| Honcho | workspace `tenant:{tenant_id}`，peer `user:{User.id}` / `agent:{agent_id}`，session `chat:{ChatSession.id}`。wire id 把这一个冒号换成 `_` | 只认存活的 `user:{User.id}`。不按 external id 建 peer |

`Contact` 按员工隔离：同一个真人可以在两个数字员工下各有一行。组织部门是租户共享的，任职指向 `contact_id`。合并 User 不会自动合并 Contact，也不会改部门树。

自动审办临时绑定表（`staffdeck_user_id` → 企微 userid）仍不是 peer 键，也不是本次合并的数据源。

## 3. 匹配策略

合并必须由操作者确认。下面的信号只产生候选，不自动落库。

### 3.1 候选从哪来

按强度从高到低，只在同一 `tenant_id` 内比较：

1. **显式指定。** 操作者点名存活 User 和被合并 User。这是唯一能直接执行的入口。
2. **已有 `Contact.primary_user_id`。** 两个 User 已经分别懒连到同一员工下的不同 Contact，且操作者认定这两行是同一人。Contact 行本身仍要按第 5 节处理，不能为了合并 User 而违反唯一约束。
3. **邮箱或电话。** 同一租户内、规范化之后的 `Contact.email` 或 `Contact.phone` 相同，且非空。这只是候选。空值、分机号差异、家庭共用号码都不能自动合并。
4. **`ChannelIdentity` 重绑。** 看到同一显示名或操作者已知的跨渠道关系。三元组相等时本来就是同一个 User，不会成为跨渠道候选。禁止用 Honcho 消息 metadata 里的 external id 去对 peer。

### 3.2 明确不做的匹配

- 不在 Honcho 里把 `user:{A}` 和 `user:{B}` 合成一个 peer，除非 AIStaff 已经把 B 并进 A。
- 不用 peer/session metadata 的渠道 external id 当合并键。
- 不把组织部门、负责人、`contact_id` 当成 User 合并的信号。
- 不跨 `tenant_id`。
- 不把群聊外部 id 当成个人。

### 3.3 建议

**先合并 AIStaff `User.id`，Honcho peer 跟着存活 id。** 审计用一张显式合并记录，它记录这次决定，不是第二套身份主键。

建议表 `user_merges`（实现时再落迁移）：

| 列 | 含义 |
| --- | --- |
| `id` | `merge_…` |
| `tenant_id` | 租户 |
| `survivor_user_id` | 存活 `User.id` |
| `retired_user_id` | 被合并 `User.id`，唯一 |
| `status` | `pending` / `applied` / `honcho_copied` / `failed` |
| `signals_json` | 操作者看到的邮箱、电话、渠道三元组，只读快照 |
| `created_by` | 操作者 |
| `created_at` / `applied_at` | 时间 |

`retired_user_id` 全局唯一：一个 User 只能被合并一次，不能再当存活者。存活者可以接收多次合并。

## 4. 执行顺序

一次合并是一个 AIStaff 事务，然后一个可重试的 Honcho 复制任务。Honcho 失败不回滚已经提交的 User 合并；新的对话已经写到存活 peer，复制任务补历史。

1. 校验：同租户、两个不同 User、retired 尚未出现在 `user_merges`、survivor 自己不是别人的 retired。
2. 在同一事务里改 AIStaff 行（第 5 节）。任一唯一约束冲突则整单失败，不写一半。
3. 提交后把 retired User 标为不可登录（保留行，供审计和外键）。不要物理删除，否则 `Contact.primary_user_id` 的 `ON DELETE SET NULL` 会把刚改过的链接清掉。
4. 入队 Honcho 历史复制（第 6 节）。复制完成把 `user_merges.status` 标成 `honcho_copied`。
5. 此后双写只使用 `user:{survivor}`。`build_honcho_identity` 不增加「按 external id 找 peer」的分支。

## 5. AIStaff 数据迁移与冲突

存活 User 记为 S，被合并 User 记为 R。

### 5.1 `ChannelIdentity`

把 `staffdeck_user_id = R` 的行改成 S。唯一约束在渠道三元组上，不在 User 上，所以 S 可以同时拥有企微行和飞书行。

冲突：不存在「两行同一三元组」。若实现时发现重复三元组，整单失败，不静默删行。

### 5.2 `Contact.primary_user_id`

唯一约束是 `(tenant_id, agent_id, primary_user_id)`。同一员工下不能有两行同时指向 S。

规则：

- R 的 Contact 若 `primary_user_id` 为空：不动。
- 该员工下还没有指向 S 的 Contact：把 R 的 `primary_user_id` 改成 S。
- 该员工下已经有指向 S 的 Contact：**不要**把第二行也改成 S，**不要**删除 R 的 Contact。组织任职和负责人挂的是 `contact_id`，删行会拆部门关系。把 R 这行的 `primary_user_id` 置空，并在 `signals_json` 里记下两行 `contact_id`。操作者以后若要合成一个 Contact，那是单独的联系人合并，不在本次 User 合并里自动做。
- 电话或邮箱不一致：不覆盖 S 那一行的 `phone` / `email`。两套值留在各自 Contact 上，合并记录里各写一份。不发明「哪个号码赢」。

### 5.3 `ChatSession.user_id`

`user_id = R` 的会话改成 S。`ChatSession.id` 不变。会话上的渠道三元组、`channel_account_key` 不动。

### 5.4 本地 Memory

`user_id = R` 的记忆行改成 S，`agent_id` 不动。内容冲突（同一 agent、同一 kind/key 两条事实）不在这次删除：两条都保留，键如果唯一则给 R 迁过来的行加后缀或跳过并记入合并记录。实现前先看该表的唯一约束；没有唯一约束就两条并存，读取侧已有的去重继续生效。

### 5.5 不动的表

- `org_affiliations`、部门负责人：仍指向原来的 `contact_id`。
- 渠道绑定、OA 临时身份表、Honcho metadata：不读、不写。
- 群聊会话：不因为合并去补写 Honcho。

## 6. Honcho 历史

v1 客户端对 peer 的 get-or-create 会整份替换 metadata，所以 peer 上只放稳定的 `staff_peer`。渠道字段在消息上。合并不能靠改 metadata 把两个 peer 粘起来。

Honcho 3.x 的 peer `id` 是调用方起的名字，`uid` 是服务端不可变 id。v3.4.1 没有「把 peer A 重命名成 peer B 并合并结论」的操作。因此：

1. 新消息只写 `user:{S}` 的 wire id（冒号换下划线，与 v1 相同）。
2. 历史复制：按 R 名下、已经双写过的 `ChatSession`，把 Honcho 里该 session 下、peer 为 `user:{R}` 的消息，再写入**同一个** `chat:{session_id}`，但 `peer_id` 改为 `user:{S}`。这是追加，不是原地改 peer。复制任务必须幂等（用消息 metadata 里的 `staff_message_id` 去重）。
3. 不删除 `user:{R}` peer，直到复制任务核对条数成功。核对失败则 `status=failed`，可以重跑。不在失败时回滚 AIStaff 的 User 合并。
4. 画像以 deriver 对存活 peer 的新结论为准。不把 R 的 representation 文本覆盖到 S 上。两边都有结论时，让 deriver 从合并后的消息重新推导；操作者接受画像滞后。
5. R 的 peer metadata 可写 `merged_into=user:{S}`，仅作运维标记。读取路径不看这个字段，也不据此改 peer 键。

会话 id 是 `chat:{ChatSession.id}`，User 合并不改变它，所以复制是在原 session 里追加存活 peer 的消息，而不是新建一套 session。

## 7. 冲突一览

| 情况 | 处理 |
| --- | --- |
| 两个 Contact、同一员工、都已指向不同 User | User 合并继续；第二个 `primary_user_id` 置空，不删 Contact |
| 电话或邮箱矛盾 | 不覆盖存活 Contact；两边都留在合并记录里 |
| R 已经是某次合并的存活者 | 拒绝。先处理更早的合并，或禁止链式自动折叠 |
| S 已经是别人的 retired | 拒绝 |
| 跨租户 | 拒绝 |
| 渠道三元组重复 | 整单回滚 |
| Honcho 复制超时或 5xx | AIStaff 合并保持；任务失败可重试；对话仍走存活 peer |
| 只用 external id 看起来像同一人 | 不合并。没有操作者确认就没有 `user_merges` 行 |

## 8. 范围

这次的代码落在 AIStaff 身份层：合并记录、`ChannelIdentity.staffdeck_user_id`、`Contact.primary_user_id` 的约束处理、`ChatSession.user_id`、本地 Memory 的 `user_id`，以及一个复制历史的后台任务。

Honcho 侧只接收存活的 `user:{User.id}`。不增加 Honcho peer 别名，不改 workspace/session 的稳键规则，不让 metadata 冒充合并。

## 9. 非目标

相对本草案，下面仍不做：

- 按邮箱或电话批量自动合并，没有操作者确认。
- 把两个 Contact 合成一行，或改组织任职的 `contact_id`。
- 跨租户、跨员工强行共用一个 Contact。
- 把自动审办临时绑定表改成长期身份源。
- 群聊 peer、Honcho Cloud、`peer.chat` 工具。
- 在 Honcho 里保留「R 和 S 两个 peer 同时作为同一人」的读路径。复制完成前，旧 peer 的画像可以仍被读到，但新的双写不再写它。
- 修改 v1 的 wire 规则（逻辑 id 用冒号，wire id 把这一个冒号换成 `_`）。

联系人合成、组织上「同一真人只有一个 contact_id」，是 User 合并之后的另一次交付。

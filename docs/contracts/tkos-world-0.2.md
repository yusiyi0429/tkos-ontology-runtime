# tkos.world/0.2 — 业务世界模型契约（草案）

状态：**草案，执行计划第 1 步的交付物，供里程碑 M1「协议定义可评审」使用**。本契约与登记 `docs/contracts/world-registry-0.2.json`（`tkos.world-registry` 0.2.0，`status` 为 `draft`）都没有锁版。第 1 至第 4 步在可清空的实验环境上实现与验证：本进程的协议支持集合已含 `tkos.world/0.2`，profile（结构版本 `tkos.world-profile/0.2`，修订 0.2.0）与唯一的迁移文件钉定两者当前的原始字节，0.2 只在新建的实验 scope 启用。其间本稿与登记可以直接改、不另出修订号，每改一次就重钉 profile 与迁移、从头重建实验库；第 5 步锁版时定稿钉定并冻结迁移（ADR-0009）。tkos.world/0.1 冻结：`tkos-world-0.1.md`、`world-registry-0.1.json`、`world-profile-0.1.json` 与钉定哈希不改，0.1 的验收与实验数据留作回归。

来源：《TKOS 语义模型与运行时重构方案 v0.2｜tkos.world/0.2 草案 2026-09-28》（飞书 rev 23，下称「方案」）第二至四节；《tkos.world/0.2 执行计划》（rev 14）工作项 A、C、D、F；CEO 2026-09-28 对 v0.1 方案的评审意见；《Agent 接入对接说明》（rev 31）第 9.3 节与第 11.2 节；0.1 契约；ADR-0001 至 0009；术语表 `CONTEXT.md`。

读法：方案写明的规则直接落稿。方案没写到、但落稿必须定下的细则标「补 n」，集中在第 18 节，是 E&O 的提议，评审时逐条过；其中注明「E&O 已定」的，E&O 已于 2026-09-28 确认。方案与执行计划列出的待决项先按方案的推荐写法落稿，标「决 n」，集中在第 19 节。方法侧的正式块清单、组件类型清单与各类对象的 State 内容项还没到，本稿用临时清单，到了整体替换登记。块清单、组件类型、payload、事件词表、动作表与各状态表以登记为准，本稿里这几张表由登记生成。

## 1. 版本、启用与隔离

- 协议 `tkos.world`，契约版本 `tkos.world/0.2`，profile 结构版本 `tkos.world-profile/0.2`。
- 0.2 是新版本：不迁移 0.1 数据、不建桥。0.1 对象保持 0.1 绑定，按 0.1 契约解释。实验环境里 0.2 只在新的 scope 启用；已有 0.1 数据的 scope（例如联调实例）怎样接 0.2，在第 5 步决定 [决 17]。
- 一个 scope 就是一家公司，Company 是 scope 内唯一的根对象。域与角色沿用 0.1 第 1 节：Company、Strategy、公司级长期目标在公司域；每个责任单元在自己的域；其余对象与主干上一级同域。角色 CEO、DOMAIN_DRI、OWNER、IC、AGENT，映射同 0.1。
- 0.2 的读侧兼容读 0.1 对象（第 15.4 节）。

## 2. 三层：业务对象、身份投影、时间记录

| 层 | 成员 | 含义 | 存储与读取 |
|-|-|-|-|
| 业务对象 | Company、Strategy、ResponsibilityUnit、LongTermGoal、PeriodGoal、Mission、Task；Activity 是候选类型（第 3.1 节）；Issue 不是类型，是问题组件（第 13 节）[决 2] | 有定义、有责任人、有正式内容、可被引用 | 对象存储与不可变修订；有门的按门写回 |
| 身份投影 | 责任主体（人与 Agent）、角色指派、当前有效的代记委托 | 谁在什么范围担任什么角色、可以代谁记什么，带生效与失效时间 | 由身份、角色指派与委托事件投影，不是对象 |
| 时间记录 | 状态快照、事件、上下文包 [决 16] | 某一时刻发生或观察到了什么 | 只追加；只读最新，或按时点、按窗口读；不确认、不修订 |

- 登记给每个类型标类别（`objects[].category`），三层的成员列在 `categories`。契约、说明与术语表按三层措辞，不再统称「九类一级对象」。
- 状态快照暂时与业务对象用同一种存储（对象表与修订表），语义上归时间记录：只经 `world_refresh_state` 写入，写入不走门，不修订，没有生命周期。是否改为独立的记录存储，在第 4 步决定 [决 15]。
- 读取时组装统一视图，三层在输出里分开给出（第 15.1 节）。

## 3. 业务对象

### 3.1 类型

| 类型 | 中文名 | 责任人 | 门 | 状态表 |
|-|-|-|-|-|
| Company | 公司 | CEO | 无；复盘确认作用在公司的快照上（第 7 节） | 无生命周期 |
| Strategy | 战略 | CEO | Agreement、确认生效、再确认 | 第 10.1 节 |
| ResponsibilityUnit | 责任单元 | 该单元的 DRI | 无 | 无生命周期 |
| LongTermGoal | 长期目标 | CEO | 确认（可带候选）、再确认 | 第 10.2 节 |
| PeriodGoal | 周期目标 | 该单元的 DRI | 承诺由 DRI；确认、再确认、复盘确认由 CEO | 第 10.3 节 |
| Mission | Mission | Owner | 立项：承诺由 Owner，确认由 DRI | 第 10.4 节 |
| Task | Task | 人 | 无 | 第 10.5 节 |
| Activity（候选） | Activity | 人或 Agent | 无 | 第 10.6 节 |

- Company 与 Strategy 不再是存根。Strategy 成为有门对象；两者的实验播种都用真实战略材料。
- Activity 是候选类型，登记标 `candidate: true`，契约不冻结这一部分。去留由方案第三节的对照实验决定 [决 4]。结论若是 Task-only，Activity 类型作废，它的内容改由执行计划块里带责任人的计划条目表达（第 4 节）。
- 责任主体（人与 Agent）由身份与角色指派投影，不是对象类型。

### 3.2 块、块类别与临时块清单

- 块路径只一层；块 id 为英文 snake_case，中文显示名在登记里。除状态快照外，每类对象固定「定义类块 + constraint」。
- 块分两类（方案 2.5）。**正式块**是对象的定义、结果、验收、打法核心路径与约束；**活动块**是执行计划与进展类内容。有门对象有了正式内容以后，正式块只能经门改（第 12 节）；活动块由该对象的责任人、下级责任人与有权限的 Agent 直接修订，出新修订、记修订事件、不走门。无门对象的块也标类别，只用来判断 Agent 的修订要不要人工验收（第 9.3 节）。
- 临时块清单（方法侧正式清单到了，整体替换登记）：

| 类型 | 正式块 | 活动块 |
|-|-|-|
| Company | identity 身份、constraint 约束 | — |
| Strategy | choices 战略选择、path 路径、assumptions 关键假设、capabilities 能力、responsibility_structure 责任结构、constraint 约束 | — |
| ResponsibilityUnit | definition 定义、boundary 边界、constraint 约束 | — |
| LongTermGoal | outcome 结果、measures 衡量、constraint 约束 | — |
| PeriodGoal | outcome 结果、realization_logic 实现逻辑、acceptance 验收标准、constraint 约束 | — |
| Mission | definition 定义、acceptance 验收标准、play 打法、constraint 约束 | execution_plan 执行计划 |
| Task | definition 定义、acceptance 验收标准、constraint 约束 | plan 计划 |
| Activity（候选） | instruction 执行指令、constraint 约束 | — |

- 相对 0.1 的变化：周期目标加实现逻辑块；Mission 加执行计划块。执行计划完整到 Task，当前推进的部分展开到 Activity 或计划条目；Task 的计划块保留，作 Task 内部的安排。打法块只写核心路径、关键逻辑与关键取舍，局部做法写进执行计划 [补 1]。

### 3.3 属性与责任人

- 属性不进块，可索引。所有业务对象有必填的 `title` 与可选的 `external_refs`（第 3.4 节）。其余属性同 0.1：ResponsibilityUnit 的 `unit_kind`（`battlefield` 战场、`domain` 域）；LongTermGoal 的 `scope`（`company`、`unit`）与 `horizon`；PeriodGoal 的 `period`（`YYYY-MM`）；Mission 的 `core_battle`（只由关注标记置真）。
- 属性也分类别：`title` 与上面各属性是正式属性，随正式块走门；`external_refs` 是活动属性，可以直接修订；`responsible`、`core_battle` 只由事件写。
- 责任人解析同 0.1 第 4 节：Company、Strategy、LongTermGoal 为 CEO；ResponsibilityUnit、PeriodGoal 为该单元的 DOMAIN_DRI；Mission、Task、Activity 由 `responsible` 属性给出，该属性只由指派写，且被指派者须当前在对象所在域持对应角色（Mission 为 OWNER，Task 为 IC，Activity 为 IC 或 AGENT）。
- 上一级责任人指主干上一级对象的责任人：Activity 的是其 Task 的责任人，Task 的是其 Mission 的 Owner，Mission 的是其周期目标的 DRI，周期目标的是其长期目标的 CEO，责任单元的是 Strategy 的 CEO。

### 3.4 外部引用

- 业务对象可以带 `external_refs`：列表，每项 `{system, id, url}`。`system` 与 `id` 为非空短文本，`url` 可选，为 http(s) 链接，不超过 2048 字符。用于外部系统与本体对象对照，例如天枢的战场 code、任务卡 id。
- 同一 scope 内同一 `(system, id)` 只指向一个对象，按各对象的最新修订判定，冲突拒绝 [补 2]。
- 读侧支持按 `(system, id)` 查找（第 15.2 节）。

## 4. 块值与语义组件

- 块值从 0.1 的三件套扩为 `{text, components[], refs[], artifacts[]}`：`text` 为 Markdown，`components` 为组件列表，`refs` 为引用（第 5 节），`artifacts` 为文档链接。
- 空块存 null，读取时渲染标准句「当前没有〈块名〉」。非空块至少含文字、组件、引用或链接之一；文字只有空白、其余为空的块视为空块，必须存 null。
- **语义组件**是块内一条可独立引用的内容，形状如下（方案 2.2）：
  - `id`：稳定标识，字符集 `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`，在所属对象内唯一。写入者可以给，例如天枢的 todo id、issue id；不给由服务生成 [决 7]。
  - `type`：登记的组件类型，须是所在块允许的类型。
  - `scope`：适用范围，可选，是指向一个业务对象的引用，缺省为所在对象 [补 3]。
  - `text`、`refs`、`artifacts`：同块值。
  - `attributes`：该组件类型登记的类型属性，可以为空。
- 组件 id 在对象修订之间稳定：修订按 id 合并，改写不换 id；删除留痕。对象维护一份组件台账，记下每个组件 id 的类型、所在块、出现与删除时的版本号；删除后 id 不再复用；组件不跨块移动，换块即删除再新增 [补 4]。
- 组件类型第一批（方法侧清单到了，整体替换登记）：

| 组件类型 | 中文名 | 允许的块 | 类型属性 | 来源 |
|-|-|-|-|-|
| `outcome` | 结果 | LongTermGoal.outcome、PeriodGoal.outcome | — | 方案 2.2 第一批 |
| `success_criterion` | 成功标准 | LongTermGoal.measures | — | 方案 2.2 第一批 |
| `acceptance_criterion` | 验收条件 | PeriodGoal.acceptance、Mission.acceptance、Task.acceptance | — | 方案 2.2 第一批 |
| `unit_entry` | 责任单元条目 | Strategy.responsibility_structure | — | 方案 2.2 第一批 |
| `assumption` | 假设 | Strategy.assumptions | — | 方案 2.2 第一批 |
| `issue` | 问题 | execution_state.issues、goal_state.issues、unit_state.issues、strategy_state.issues | `core_question` 核心判断问题（必填）、`responsible_hint` 最低充分责任主体 | 方案 2.2 第一批、2.6 |
| `plan_item` | 计划条目 | Mission.execution_plan、Task.plan | `responsible` 责任人（只作记录） | 本稿补充 |
| `progress_item` | 进展条目 | execution_state.progress | `principal_id` 本体主体 id、`principal_name` 姓名（写入时）、`external_status` 外部状态、`entries` 本期条目 | 本稿补充，字段取对接说明 11.2 草案 |

- 计划条目与进展条目是方案第一批之外补的 [补 5]。计划条目可以带责任人 [决 8]。这个责任人只是记录：不是指派，不产生权限，也没有生命周期。Task-only 线靠它表达「谁做哪一段」。
- 用法：责任单元的 `architecture_ref` 指到责任结构块里对应的责任单元条目；周期目标与 Mission 的验收条件逐条引用上层的验收条件或结果；周期目标的结果逐条引用长期目标的衡量；快照里的问题组件承载 Issue（第 13 节）。

## 5. 引用

- 业务形式四种。写入一律用字符串，读回同时给业务形式与钉定后的结构：

| 形式 | 写法 | 钉定为 |
|-|-|-|
| 对象 | `<对象 id>@<版本号>` | 对象 id、版本号、修订 id |
| 块 | `<对象 id>@<版本号>#<块 id>` | 另加块 id |
| 组件 | `<对象 id>@<版本号>#<块 id>/<组件 id>` | 另加组件 id |
| 事件 | `event:<事件 id>` | 事件 id [补 6] |

- 服务端写入时把版本号解析成修订 id 并钉住。引用固定在版本上，对象出新修订后不漂移；组件 id 稳定，同一条目在新版本里按同一个 id 找到。0.1 的整块引用在 0.2 照常有效。
- 指向不存在的对象、版本、块、组件或事件的引用被拒绝；指向非 world 记录或 scope 外对象的，按不存在处理。块须是该类型登记的块；空块可以引用，但里面没有组件。组件引用须指向该修订里现存、未删除的组件。
- 快照的 `subject_ref` 与写入声明的 `scene` 用对象形式；事件的 `subject_refs` 用对象形式或组件形式，组件形式同时钉住所在对象。

## 6. 关系与主干

沿用 0.1 第 6 节，改三处：

- `defines`：责任单元的 `architecture_ref` 必须指到 Strategy 责任结构块里对应的责任单元条目，写作 `<Strategy id>@<版本>#responsibility_structure/<条目 id>`。
- 新增 `based_on_review`：周期目标的 `review_ref` 指向已确认的公司复盘快照（第 7 节与第 10.3 节的守卫），建对象时写；草稿期可以改指另一条已确认的公司复盘，其余关系字段仍同 0.1，只能改钉到同一对象的另一版本 [补 7]。
- `depends_on` 扩到周期目标：周期目标的 `depends_on[]` 可以指向周期目标或 Mission，与 Mission、Task 的一样只经 `world_relate` 写 [补 8]。

主干不变：Activity → Task → Mission → 周期目标 → 长期目标 → 责任单元 → Strategy → Company。跨链关系只展示不递归。

## 7. 状态快照：统一外壳与各类型 payload

- 状态快照是时间记录，只经 `world_refresh_state` 写入，写入不走门，不修订，没有生命周期。写入即生效，无人确认（ADR-0003），读侧标明它未经确认；复盘确认是另记的门事件，不改快照本身。
- 外壳：
  - `subject_ref`：主体，对象形式的引用，主体可以是任一业务对象。快照与主体同域。
  - `as_of`：时点，以 UTC 规范文本存储，不晚于写入时刻；`period`：可选的周期（`YYYY-MM`）。
  - `generator`：生成者，由服务端按凭证填，是人或 Agent [补 9]。
  - `source_event_refs`：来源事件，事件引用的列表，至少一条 [补 10]。
  - `payload_type`：payload 类型，须是主体类型登记的那一种。
  - `title`：标题。
- payload 按主体类型定义，是一组块，块值与组件同第 4 节。第一批如下，方法侧给出各类对象的 State 内容项后整体替换 [补 11]：

| payload 类型 | 主体 | 块（括号里是允许的组件） |
|-|-|-|
| `execution_state` 执行状态 | Mission、Task、Activity | progress 进展（进展条目）、blockers 阻塞与偏差、issues 问题（问题组件）、materials 材料 |
| `goal_state` 目标状态 | LongTermGoal、PeriodGoal | progress 进展与偏差、issues 问题（问题组件）、materials 材料 |
| `unit_state` 单元状态 | ResponsibilityUnit | progress 进展与偏差、issues 问题（问题组件）、materials 材料 |
| `strategy_state` 战略状态 | Strategy | issues 问题（问题组件）、materials 材料 |
| `company_review` 公司复盘 | Company | results 结果、gaps 缺口、causes 原因、key_changes 关键变化、implications 经营含义、materials 材料 |

- 同一主体同一时点只有一条快照；`as_of` 同一时刻只有一种写法，唯一性按这一写法判定。只读最新（按 `as_of`），历史保留；错快照用新快照更正。
- Agent 起草的对象内容与候选稿放在 materials 里，只有经门写回对象后才是正式内容。
- 生命周期不由快照推导。0.1 里「此后第一条状态快照推出进行中」的规则取消，改由开始事件推出。
- 问题组件的 id 跨快照延续：同一主体同一核心判断问题沿用原 id（第 13 节）。
- 复盘确认（第 8 节）钉住某条快照、赋予它「已确认复盘」的效力。确认公司复盘，不改变任何生命周期；确认以周期目标为主体的快照，同时关闭该周期目标（第 10.3 节）[补 12]。同一主体有多条已确认复盘时，以 `as_of` 最新的为准。

## 8. 事件

### 8.1 三类与字段

- 事件只追加，是唯一的触发源。每个 world 动作在同一事务里恰好写一条事件。
- 三类（方案 2.3）：
  - **门事件**：承诺、确认、再确认、Agreement、复盘确认。由人记，可以代记（第 14 节），推动生命周期或赋予效力。
  - **生命周期事件**：开始、交付、验收通过、退回、重开、取消。由对象责任人或其上一级记，推动生命周期。关闭是验收通过、复盘确认的结果状态，不单独记。
  - **记录事件**：建对象、修订、状态刷新、外部事件（含更正）、指派、建立跨链关系、关注标记、Issue 的提出 / 路由 / 承接 / 处置 / 退回形成、委托的登记与撤销。记录事件不推动业务对象的生命周期，指派除外（Task、Activity 由未指派进入已指派）；Issue 事件只推动 Issue 自己的状态 [补 13]。
- 字段：`event_id`、`scope`、`kind`、`class`、`category`（外部事件）、`outcome`、`disposition`（处置）、`subject_refs`（至少一条）、`principal`（记录者）、`on_behalf_of`（代记时，被代记的人）、`external_confirmation`（代记时，外部记录 id 与外部确认时刻）、`occurred_at`、`recorded_at`、`late`（迟记，读侧给出，第 11 节）、`content`（块值形状）、`detail`（按种类的结构化内容，例如指派的被指派者、路由的承接人、Agreement 钉住的内容）、`action_id`（指回回执）、`supersedes_event_id`。
- 0.1 的 `phase` 取消：Mission 的门只在立项，交付改由生命周期事件表达。

### 8.2 事件词表

| kind | 类 | 含义 | 可补记 | 结果 / 处置 | 谁记 |
|-|-|-|-|-|-|
| `object.created` | 记录事件 | 建对象 | 否 | — | 新对象主干上某一级的责任人 |
| `object.revised` | 记录事件 | 修订 | 否 | — | 该对象或其主干上某一级的责任人；有门对象有正式内容后，下级责任人与有权限的 Agent 可直接修订活动块 |
| `state.refreshed` | 记录事件 | 状态刷新 | 是 | — | 主体主干上的责任人；在主体所在域持 AGENT 的 Agent |
| `event.recorded` | 记录事件 | 外部事件 | 是 | category 必带 | scope 内有生效指派的人或 Agent；更正同此 |
| `assign` | 记录事件 | 指派 | 否 | — | 上一级责任人；Strategy 的本轮责任人由 Strategy 的责任人指定 |
| `relate` | 记录事件 | 建立跨链关系 | 否 | — | 该对象或其主干上某一级的责任人 |
| `core_battle.marked` | 记录事件 | 关注标记（核心战役） | 否 | — | CEO |
| `issue.raised` | 记录事件 | 提出问题 | 否 | — | MF（在主受影响对象所在域持 AGENT 的 Agent）或主受影响对象主干上的责任人 |
| `issue.routed` | 记录事件 | 路由问题 | 否 | — | 同提出 |
| `issue.owned` | 记录事件 | 承接问题 | 否 | — | 路由指定的承接人本人 |
| `issue.disposed` | 记录事件 | 处置问题 | 否 | 六类处置之一（必带） | 承接人本人 |
| `issue.returned` | 记录事件 | 退回形成 | 否 | — | 路由者或承接人 |
| `delegation.granted` | 记录事件 | 登记委托 | 否 | — | 委托人本人 |
| `delegation.revoked` | 记录事件 | 撤销委托 | 否 | — | 委托人本人 |
| `commit` | 门事件 | 承诺 | 否 | 撤回 | 周期目标由 DRI；Mission 由 Owner 本人 |
| `confirm` | 门事件 | 确认 | 否 | 接受、退回、撤回（必带） | 长期目标、周期目标、Strategy 由 CEO；Mission 由 DRI |
| `reconfirm` | 门事件 | 再确认 | 否 | — | CEO |
| `agreement` | 门事件 | Agreement | 否 | 撤回 | 本轮被指定的责任人 |
| `review.confirmed` | 门事件 | 复盘确认 | 否 | 撤回 | CEO |
| `start` | 生命周期事件 | 开始 | 否 | 撤回 | 对象责任人；Mission 也可由 Owner 的 Agent |
| `deliver` | 生命周期事件 | 交付 | 否 | 撤回 | 对象责任人 |
| `accept` | 生命周期事件 | 验收通过 | 否 | 撤回 | 上一级责任人 |
| `reject` | 生命周期事件 | 退回 | 否 | 撤回 | 上一级责任人 |
| `reopen` | 生命周期事件 | 重开 | 否 | 撤回 | 上一级责任人 |
| `cancel` | 生命周期事件 | 取消 | 否 | 撤回 | 上一级责任人；长期目标由 CEO |

- 取值：
  - `category`（外部事件必填）：`meeting` 会议、`review` 评审、`delivery` 交付、`acceptance` 验收、`other` 其他、`correction` 更正。0.2 里交付类、验收类外部事件只作记录，不推动生命周期。
  - `outcome`：确认必填，取 `accepted` 接受、`returned` 退回、`withdrawn` 撤回；其余门事件与生命周期事件只在撤回时带 `withdrawn`。
  - `disposition`：处置必填，六类之一（第 13 节）。
- 「退回」两处同名：确认的结果 `returned` 退回的是承诺，生命周期事件 `reject` 退回的是交付。

## 9. 动作、判权与写入声明

### 9.1 动作

- 协议动作即事件类型。每个动作走 HTTP 的 prepare / commit 两段式、乐观并发、幂等键与回执；world 动作不入队、不外发。
- 门动作按目标类型拆名，角色写在激活策略的 `action_roles` 里（ADR-0005）。生命周期动作与指派一样是通用动作，谁能记由登记状态表里每条转移的 `by` 判定，策略里列这些角色的并集 [补 14]。

| 动作 | 事件 | 目标 | 策略角色 | 谁记 | Agent 面 | 可代记 |
|-|-|-|-|-|-|-|
| `world_create_object` | `object.created` | — | 全部 world 角色 | 新对象主干上某一级的责任人（同 0.1） | 否 | 否 |
| `world_revise_object` | `object.revised` | 公司、战略、责任单元、长期目标、周期目标、Mission、Task、Activity | 全部 world 角色 | 该对象或其主干上某一级的责任人；有门对象有正式内容后只能直接修订活动块 | 是：无门对象，或有门对象的活动块与活动属性 | 否 |
| `world_refresh_state` | `state.refreshed` | — | 全部 world 角色 | 主体主干上的责任人；在主体所在域持 AGENT 的 Agent | 是 | 否 |
| `world_record_event` | `event.recorded` | — | 按 scope | scope 内有生效指派的人或 Agent | 是 | 否 |
| `world_assign` | `assign` | 责任单元、Mission、Task、Activity | 全部 world 角色 | 上一级责任人 | 否 | 指派 |
| `world_assign_strategy_round` | `assign` | 战略 | 全部 world 角色 | Strategy 的责任人（CEO） | 否 | 指派 |
| `world_relate` | `relate` | 周期目标、Mission、Task | 全部 world 角色 | 该对象或其主干上某一级的责任人 | 否 | 否 |
| `world_mark_core_battle` | `core_battle.marked` | Mission | CEO | CEO | 否 | 门 |
| `world_raise_issue` | `issue.raised` | — | 全部 world 角色 | MF（主受影响对象所在域持 AGENT 的 Agent）或主受影响对象主干上的责任人 | 是 | 否 |
| `world_route_issue` | `issue.routed` | — | 全部 world 角色 | 同提出 | 是 | 否 |
| `world_own_issue` | `issue.owned` | — | 全部 world 角色 | 路由指定的承接人本人（人） | 否 | 否 |
| `world_dispose_issue` | `issue.disposed` | — | 全部 world 角色 | 承接人本人（人） | 否 | 否 |
| `world_return_issue` | `issue.returned` | — | 全部 world 角色 | 路由者或承接人 | 是：作为路由者 | 否 |
| `world_grant_delegation` | `delegation.granted` | — | 按 scope | 委托人本人（人） | 否 | 否 |
| `world_revoke_delegation` | `delegation.revoked` | — | 按 scope | 委托人本人（人） | 否 | 否 |
| `world_commit_period_goal` | `commit` | 周期目标 | DOMAIN_DRI | 单元 DRI | 否 | 门 |
| `world_confirm_period_goal` | `confirm` | 周期目标 | CEO | CEO | 否 | 门 |
| `world_reconfirm_period_goal` | `reconfirm` | 周期目标 | CEO | CEO | 否 | 门 |
| `world_commit_mission` | `commit` | Mission | OWNER | 该 Mission 的 Owner 本人 | 否 | 门 |
| `world_confirm_mission` | `confirm` | Mission | DOMAIN_DRI | 单元 DRI | 否 | 门 |
| `world_confirm_long_term_goal` | `confirm` | 长期目标 | CEO | CEO | 否 | 门 |
| `world_reconfirm_long_term_goal` | `reconfirm` | 长期目标 | CEO | CEO | 否 | 门 |
| `world_agree_strategy` | `agreement` | 战略 | 按 scope 与本轮指定 | 本轮被指定的责任人 | 否 | 门 |
| `world_confirm_strategy` | `confirm` | 战略 | CEO | CEO | 否 | 门 |
| `world_reconfirm_strategy` | `reconfirm` | 战略 | CEO | CEO | 否 | 门 |
| `world_confirm_review` | `review.confirmed` | 状态快照 | CEO | CEO | 否 | 门 |
| `world_start` | `start` | Mission、Task、Activity | 全部 world 角色 | 按状态表 | 是：Mission 作为 Owner 的 Agent；Activity 作为其责任人 | 生命周期 |
| `world_deliver` | `deliver` | Mission、Task、Activity | 全部 world 角色 | 按状态表 | 是：Activity 作为其责任人 | 生命周期 |
| `world_accept` | `accept` | Mission、Task、Activity | 全部 world 角色 | 按状态表 | 否 | 生命周期 |
| `world_reject` | `reject` | Mission、Task、Activity | 全部 world 角色 | 按状态表 | 否 | 生命周期 |
| `world_reopen` | `reopen` | Mission、Task、Activity | 全部 world 角色 | 按状态表 | 否 | 生命周期 |
| `world_cancel` | `cancel` | 长期目标、周期目标、Mission、Task、Activity | 全部 world 角色 | 按状态表 | 否 | 生命周期 |

- 参数：
  - 门动作：可选 `content`（块值形状，写进事件，例如退回理由、候选稿的链接）。承诺与长期目标的确认可以带候选 `payload`（第 12 节）；确认必带 `outcome`；撤回带 `outcome: withdrawn` 与 `supersedes_event_id`。复盘确认的目标是被确认的快照。
  - 生命周期动作：目标是对象；可选 `content`；撤回同上。
  - 指派：`{principal_id}`；指定本轮责任人：`{principal_ids, payload}`，`payload` 只在已生效时开轮才带（第 10.1 节）。
  - Issue 动作不带目标，以 `issue_ref`（问题组件的组件引用）指明问题；路由另带承接人 `to_principal_id`；处置另带 `disposition`，且 `content` 必带，写最低理由。
  - 委托：见第 14 节。可代记的动作另带 `on_behalf_of`。

### 9.2 判权

- 顺序不变：先认证与 scope 授权，再按激活策略判动作的角色（404 与 403 先于协议错误），再暴露协议错误，最后按生命周期与责任关系判这条事件现在能不能记：记录者或责任关系不符（不是该转移的「谁记」、越级、不是主干上的责任人）返回 `FORBIDDEN`，状态表或守卫不允许返回 `INVALID_STATE`，同 0.1。
- 状态表里「谁记」的取值（登记 `recorders`）：
  - `self`：对象的责任人。
  - `self_or_agent`：对象的责任人，或在对象所在域持 AGENT 的 Agent，用于 Mission 的「Owner 的 Agent」[补 15]。
  - `parent`：上一级责任人。
  - `gate_role`：持激活策略为该门动作列出的角色的人；Mission 的承诺另须是该 Mission 的 Owner 本人（同 0.1）。
  - `designated`：本轮被 Strategy 的责任人指定的人（第 10.1 节）。
  - `raiser`、`router`、`route_target`、`owner`、`router_or_owner`：Issue 事件，见第 13 节。
- 门事件（含 Agreement）与 Issue 的承接、处置只由人记；代记时按被代记的人判（第 14 节）。Agent 即使持有对应角色也不能记这些事件。
- Agreement 不按激活策略的角色表判权：记录者须在 scope 内有生效指派，且是本轮被指定的责任人 [决 13]。这与 ADR-0005「门的角色写在策略里」不同，原因是被指定的人通常在自己的单元域持角色，不在 Strategy 所在的公司域。
- 指派沿用 0.1 的规则：只记业务责任、不授权限；被指派者须已在该域持对应角色（否则 `INVALID_REQUEST`）；逐级指派、不越级。Activity 的责任人改由其 Task 的责任人指派 [决 12]。已指派之后可以再指派，状态不变；已关闭、已取消的对象不能再指派 [补 16]。

### 9.3 写入声明与 Agent 面

- 写入声明三项（场景、触发事件、是否人工验收及验收人）只对 Agent 身份的写入强制；人写入不强制，带了按同样规则校验。门动作不带写入声明。
- 场景放宽到任一业务对象：`scene` 是任一业务对象的对象形式引用，0.1 只认 Mission 或 Task。
- **Agent 面**是 Agent 身份能做的全部事情，也就是 MCP 与 CLI 暴露的全部 [补 17]：
  - 读：取对象、取上下文、取事件、取状态、列对象。
  - 写：记外部事件（含更正）、写状态快照、修订、提出问题、路由问题、退回形成、开始（Mission 作为 Owner 的 Agent，Activity 作为其责任人）、交付（Activity 作为其责任人）。
- Agent 修订：无门对象可以修订；有门对象只能修订活动块与活动属性。修订触及正式块或正式属性时，声明必须要求人工验收并给出验收人；只触及活动块与活动属性时可以不要求 [补 18]。
- Agent 记的 Activity 交付必须由人验收：上一级责任人是 Task 的责任人，只能是人。
- 代记只走 HTTP，不属于 Agent 面（第 14 节）。
- 接入面三种：HTTP 是唯一的真面，MCP 与 CLI 都是它上面的薄壳，暴露同一个 Agent 面（ADR-0004）。CLI 第一版已按 0.1 的 Agent 面收口（PR #45，工作项 G），随 0.2 扩到上面的清单。

## 10. 生命周期与状态表

- 不存状态枚举：读侧按对象的事件推导生命周期，并给出推出它的事件（ADR-0002）。推导是确定性的纯函数，按记录顺序逐条处理，初始段由建对象推出。状态表以机器可读形式登记在 `lifecycles`。
- 表里没有列出的（状态，动作）组合一律拒绝（`INVALID_STATE`）。重走（第 12 节）与撤回（第 11 节）按各自的规则处理，不列在表里。
- 进入状态与起始状态相同的转移（自环）不换段，也不换推出它的事件。
- 上层对象的关闭、取消不自动改变下层对象的生命周期；Task 全部关闭也不自动关闭 Mission；战略的新版生效不自动重开下游 [补 19]。
- 第一次进入正式段（Strategy 已生效；长期目标、周期目标已确认；Mission 已成立）的那条门事件让对象有了正式内容（第 12 节）。
- Company、ResponsibilityUnit 没有生命周期，只有版本。
- 状态名与《TKOS 企业上下文业务建模方案与落地计划》（飞书 docx `IXIGdaHqYo3ICgxwvrRcHzUKnnd`，rev 48）的「状态口径」一致：待验收即已交付，已完成即已关闭，退回进入调整中、调整后再次交付。它的「未开始」对应本稿 Task、Activity 的未指派与已指派。

### 10.1 Strategy

| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |
|-|-|-|-|-|
| 指定本轮责任人 `world_assign_strategy_round` | 草稿、已生效 | 不变 | Strategy 的责任人（CEO） |  |
| 记 Agreement `world_agree_strategy` | 草稿 | 不变 | 本轮被指定的责任人各记一条 | 本轮未齐 |
| 记 Agreement `world_agree_strategy` | 草稿 | 已达成判断 | 本轮被指定的责任人各记一条 | 本轮补齐 |
| 确认 Strategy 生效（接受） `world_confirm_strategy` | 已达成判断 | 已生效 | CEO |  |
| 确认 Strategy 生效（退回） `world_confirm_strategy` | 已达成判断 | 草稿 | CEO |  |
| 再确认 Strategy（继续有效） `world_reconfirm_strategy` | 已生效 | 不变 | CEO |  |

- 一轮：Strategy 的责任人（CEO）以 `world_assign_strategy_round` 指定本轮责任人，至少一人、各不相同，须是 scope 内有效的人。重新指定即开新一轮，此前的 Agreement 作废。草稿与已生效时可以指定；已达成判断时不能，先由 CEO 确认或退回 [补 20]。
- Agreement：本轮每位被指定的人各记一条，内容是他的判断。事件钉住本轮的指派事件与所同意的内容：草稿时钉住记录时的最新修订，已生效时钉住本轮候选。同一人对同一内容只记一条。
- 草稿时，本轮补齐的那一条 Agreement 推出「已达成判断」：全体被指定的人都有钉住当前最新修订的 Agreement。草稿在补齐之前又被修订，钉住旧修订的 Agreement 不计。
- 已生效时改战略：开轮的指派带候选（合并补丁，只含正式块与正式属性），状态不变；本轮补齐后，CEO 确认生效把候选写回为新修订；CEO 退回，本轮作废。
- 结论是不改：本轮不带候选，补齐后由 CEO 以再确认「继续有效」结束本轮，不出新修订 [补 21]。CEO 也可以不开轮，直接再确认。
- 第一次确认生效让 Strategy 有了正式内容。战略的新版生效不自动重开下游：下游对象仍钉住当时的版本，是否调整由各自的责任人判断。

### 10.2 LongTermGoal

| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |
|-|-|-|-|-|
| 确认长期目标（接受） `world_confirm_long_term_goal` | 草稿 | 已确认 | CEO |  |
| 确认长期目标（退回） `world_confirm_long_term_goal` | 草稿 | 不变 | CEO |  |
| 再确认长期目标（保持） `world_reconfirm_long_term_goal` | 已确认 | 不变 | CEO |  |
| 取消 `world_cancel` | 草稿、已确认 | 已终止 | CEO |  |

- 确认可以带候选。草稿时带候选的确认接受，先写回候选再进入已确认；已确认时带候选的确认接受是一轮重走，直接写回新修订，状态不变（第 12 节）。不带候选的确认，接受的是当时的最新修订。
- 终止记为取消事件，进入「已终止」[补 22]。已终止的长期目标不再是有效的长期目标。
- 确认与再确认可以带「返回 M1-A」的路由结果：事件的 `detail.returns_to` 为 `strategy`，只作记录，不自动提出问题 [补 23]。需要战略处理的，由 MF 或责任人以 Strategy 为主受影响对象提出问题，并引用这条事件。

### 10.3 PeriodGoal

| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |
|-|-|-|-|-|
| 承诺周期目标 `world_commit_period_goal` | 草稿 | 已承诺 | DRI | 锚定有效长期目标与已确认复盘 |
| 确认周期目标（接受） `world_confirm_period_goal` | 已承诺 | 已确认 | CEO | 锚定有效长期目标与已确认复盘 |
| 确认周期目标（退回） `world_confirm_period_goal` | 已承诺 | 草稿 | CEO |  |
| 再确认周期目标 `world_reconfirm_period_goal` | 已确认 | 不变 | CEO |  |
| 复盘确认 `world_confirm_review` | 已确认 | 已关闭 | CEO | 快照以本目标为主体 |
| 取消 `world_cancel` | 草稿、已承诺、已确认 | 已取消 | CEO |  |

- 形成锚定（守卫）：承诺与确认接受时，`goal_ref` 指向的长期目标须处于已确认，`review_ref` 须指向已确认的公司复盘快照。scope 内还没有任何已确认的公司复盘时（第一个周期），`review_ref` 可以空 [补 24]。
- 再确认不带候选、不出新修订。改正式块走一轮：DRI 带候选承诺，CEO 确认接受时写回。
- 复盘确认：CEO 确认以该周期目标为主体的快照（通常是期末快照），周期目标进入已关闭，被钉住的快照成为它的已确认复盘。
- 取消由 CEO 记（上一级责任人）。

### 10.4 Mission

| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |
|-|-|-|-|-|
| 承诺 Mission（立项） `world_commit_mission` | 草稿 | 已承诺 | Owner | 父周期目标已确认 |
| 确认 Mission（立项，接受） `world_confirm_mission` | 已承诺 | 已成立 | DRI | 父周期目标已确认 |
| 确认 Mission（立项，退回） `world_confirm_mission` | 已承诺 | 草稿 | DRI |  |
| 开始 `world_start` | 已成立 | 进行中 | Owner 或其 Agent |  |
| 交付 `world_deliver` | 进行中、调整中 | 已交付 | Owner |  |
| 验收通过 `world_accept` | 已交付 | 已关闭 | DRI |  |
| 退回 `world_reject` | 已交付 | 调整中 | DRI |  |
| 重开 `world_reopen` | 已关闭 | 进行中 | DRI |  |
| 取消 `world_cancel` | 草稿、已承诺、已成立、进行中、已交付、调整中 | 已取消 | DRI |  |
| 标记核心战役（关注） `world_mark_core_battle` | 草稿、已承诺、已成立、进行中、已交付、调整中 | 不变 | CEO | 只标一次 |

- 立项的承诺与确认接受都要求父周期目标处于已确认 [补 25]。
- 已成立之后改正式块走一轮：Owner 带候选承诺，DRI 确认接受时写回，状态不变。这就是 M2 说的必要确认，不是重走整个形成。
- 开始由 Owner 记，或由在 Mission 所在域持 AGENT 的 Agent 作为 Owner 的 Agent 记，带写入声明。交付由 Owner 记；验收通过、退回、重开、取消由 DRI 记 [决 10][决 11]。
- 核心战役按方案 A [决 1]：CEO 记一次关注标记，只影响可见性与沟通空间，不改变状态、不改变决定权。已关闭、已取消的 Mission 不再标记 [补 26]。方案 B 的差异见附录 A。

### 10.5 Task

| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |
|-|-|-|-|-|
| 指派 `world_assign` | 未指派 | 已指派 | Mission Owner |  |
| 指派 `world_assign` | 已指派、进行中、已交付、调整中 | 不变 | Mission Owner |  |
| 开始 `world_start` | 已指派 | 进行中 | Task 责任人 |  |
| 交付 `world_deliver` | 进行中、调整中 | 已交付 | Task 责任人 |  |
| 验收通过 `world_accept` | 已交付 | 已关闭 | Mission Owner |  |
| 退回 `world_reject` | 已交付 | 调整中 | Mission Owner |  |
| 重开 `world_reopen` | 已关闭 | 进行中 | Mission Owner |  |
| 取消 `world_cancel` | 未指派、已指派、进行中、已交付、调整中 | 已取消 | Mission Owner |  |

- 指派由 Mission 的 Owner 记；开始、交付由 Task 责任人记；验收通过、退回、重开、取消由 Mission 的 Owner 记。
- 相对 0.1：进行中由开始推出，不再由此后第一条状态快照推出；已交付、已关闭由交付、验收通过推出，不再由交付类、验收类外部事件推出；新增调整中、重开与取消。

### 10.6 Activity（候选）

| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |
|-|-|-|-|-|
| 指派 `world_assign` | 未指派 | 已指派 | Task 责任人 |  |
| 指派 `world_assign` | 已指派、进行中、已交付、调整中 | 不变 | Task 责任人 |  |
| 开始 `world_start` | 已指派 | 进行中 | Activity 责任人（人或 Agent） |  |
| 交付 `world_deliver` | 进行中、调整中 | 已交付 | Activity 责任人（人或 Agent） |  |
| 验收通过 `world_accept` | 已交付 | 已关闭 | Task 责任人 |  |
| 退回 `world_reject` | 已交付 | 调整中 | Task 责任人 |  |
| 重开 `world_reopen` | 已关闭 | 进行中 | Task 责任人 |  |
| 取消 `world_cancel` | 未指派、已指派、进行中、已交付、调整中 | 已取消 | Task 责任人 |  |

- 与 Task 同一张表，责任人可以是 Agent。指派与验收改由 Task 的责任人记；0.1 是 Mission 的 Owner 指派。
- Agent 记的交付必须由人验收：上一级责任人是 Task 的责任人，只能是人。
- 对照实验的结论若是 Task-only，本表作废。

## 11. 重复、乱序、撤回、更正

- 重复：
  - 门事件与生命周期事件与当前状态不匹配即拒绝，所以同一事件重复记录不会产生第二次状态变化。
  - 同一记录者带同一幂等键重复提交同一请求，返回原回执、不写第二条；同键不同请求返回 `IDEMPOTENCY_CONFLICT`（内核现有行为）。
  - 幂等键不同的记录事件照记，读取时按发生时刻并列。同一主体同一时点的快照仍只有一条（第 7 节）。
- 乱序 [决 9]：
  - 门事件、生命周期事件与其余记录事件的发生时刻就是记录时刻，不接受补记。
  - 外部事件与状态刷新可以补记过去的时刻：外部事件取 `occurred_at`，状态刷新的发生时刻取快照的 `as_of` [补 27]；发生时刻不得晚于记录时刻。
  - 推导按记录顺序；读取按发生时刻，并标「迟记」：一条事件记下时，同一主体已有发生时刻晚于它的事件。
- 撤回：
  - 只能撤回推出对象当前状态的那条门事件或生命周期事件。由同一动作、同一角色记，`outcome` 为 `withdrawn`，以 `supersedes_event_id` 引用原事件；状态回到原事件之前，这一段改由撤回事件推出，被撤回的事件保留。
  - 让内容成为正式的那条确认被撤回时，正式内容一起收回。一轮重走写回过之后，这条确认不能再撤回：正式内容已换成写回的新修订，要改就再走一轮 [补 28]。
  - 撤回事件本身、再确认、没有推出状态的 Agreement、公司复盘确认、一轮重走中的承诺与确认都不能撤回，因为它们不推出生命周期状态 [补 28]。记错的公司复盘确认以更新的已确认复盘为准。
  - 被代记的人可以本人撤回代记的事件（第 14 节）。
- 更正：只对记录事件。记一条 `category` 为 `correction` 的外部事件，以 `supersedes_event_id` 引用被更正的外部事件或 Issue 事件 [补 29]；不改变生命周期；读取时给出被更正关系。快照、指派、修订各用新快照、再指派、新修订更正。

## 12. 修改规则、正式内容与候选内容

- 有门类型：Strategy、LongTermGoal、PeriodGoal、Mission。
- 正式块与正式属性：只在初始段（草稿）可以直接修订。草稿时的承诺（周期目标、Mission）与长期目标的确认可以带候选，也可以不带：带了，确认接受时写回候选；不带，确认接受的是当时的最新修订 [补 30]。已承诺、已达成判断这类还没有正式内容的段不能改，要改先由确认人退回草稿。有正式内容以后只能经门改：一轮重走从初始段起按同一张门表走，不改变生命周期段；候选在确认接受时写回为新修订，旧版成为历史依据；被退回则该轮结束、候选作废。
- 活动块与活动属性：已关闭、已取消之外的任一状态都可以直接修订，不开轮、不走门（第 3.2 节）[补 31]。
- 无门类型随时可以直接修订，同 0.1。
- 各类型的一轮（登记 `lifecycles.<类型>.rounds`）：

| 类型 | 可以开轮的状态 | 开轮 | 写回 |
|-|-|-|-|
| PeriodGoal | 已确认 | DRI 带候选承诺 | CEO 确认接受 |
| Mission | 已成立、进行中、已交付、调整中 [补 32] | Owner 带候选承诺，例如 Play 的核心路径变化 | DRI 确认接受 |
| LongTermGoal | 已确认 | 不单独开轮 | CEO 带候选确认，直接写回 |
| Strategy | 已生效 | Strategy 的责任人以指派开轮并附候选 | 本轮 Agreement 补齐后 CEO 确认生效 |

- 候选的格式同修订的合并补丁，只能含正式块与正式属性；写回时，活动块、活动属性与只由服务写的字段取写回时的当前值 [补 33]。
- 再确认：长期目标的「保持」、周期目标的再确认、Strategy 的「继续有效」都用不带候选的再确认，一条事件，不出新修订，状态不变。
- 一轮未结束前不能再开一轮；记错的重走承诺由确认人退回。
- 修订按合并：只改请求里给出的字段与块。块补丁按字段合并：给出的 `text`、`refs`、`artifacts` 整体替换；`components` 按 id 合并，给出的 id 新增或改写，`{"id": …, "removed": true}` 删除，未提到的保留，新组件追加在后；块给 null 即清空，其中的组件在台账里记为删除 [补 34]。
- 修订与建关系者同 0.1 第 11 节：该对象本身或其主干上某一级的责任人。有门对象的活动块，下级责任人与有权限的 Agent 也可以直接修订。
- 内核对象行的状态列与生效修订指针只承担正式内容指针（ADR-0002），规则同 0.1 第 11 节末条：有门类型建对象为 `draft`，第一次进入正式段时改为 `confirmed` 并把生效指针挪到被确认的修订，撤回这条确认时回到 `draft`、生效指针清空；无门类型一律 `recorded`。

## 13. Issue：问题组件

按 [决 2]，Issue 是主受影响对象快照里的问题组件（方案 2.6），不是对象类型。

- 身份：`(主受影响对象, 组件 id)`。问题组件放在主受影响对象某条快照的 issues 块里，带核心判断问题与可选的最低充分责任主体。同一主受影响对象同一核心问题沿用原 id，不重复提出；后续快照带同一个 id，表示同一问题的新情况。
- Issue 事件以 `issue_ref` 指向该组件，事件的 `subject_refs` 含这条组件引用与主受影响对象的对象引用。
- 状态表（登记 `issue.lifecycle`）：

| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |
|-|-|-|-|-|
| 提出问题 `world_raise_issue` | 未提出、形成中 | 待路由 | MF（Co-Agent）或主受影响对象主干上的责任人 |  |
| 路由问题 `world_route_issue` | 待路由 | 已路由 | 路由者（同提出） |  |
| 路由问题 `world_route_issue` | 已路由 | 不变 | 路由者（同提出） |  |
| 承接问题 `world_own_issue` | 已路由 | 已承接 | 承接人本人 |  |
| 处置问题（不处理并关闭） `world_dispose_issue` | 已承接 | 已处置 | 承接人本人 |  |
| 处置问题（本层处理） `world_dispose_issue` | 已承接 | 已处置 | 承接人本人 |  |
| 处置问题（带入下次形成） `world_dispose_issue` | 已承接 | 已处置 | 承接人本人 |  |
| 处置问题（立即重开） `world_dispose_issue` | 已承接 | 已处置 | 承接人本人 |  |
| 处置问题（转交或上报） `world_dispose_issue` | 已承接 | 待路由 | 承接人本人 |  |
| 处置问题（退回补齐） `world_dispose_issue` | 已承接 | 形成中 | 承接人本人 |  |
| 退回形成 `world_return_issue` | 已路由、已承接 | 形成中 | 路由者或承接人 |  |

- 处置六类，用 M1-B 与 M2 的口径；M1-A、M3 的叫法按下表对照，对照关系待方法侧确认：

| 处置 | 含义 | 进入状态 | M1-A 叫法 | M3 叫法 |
|-|-|-|-|-|
| `no_action_close` | 不处理并关闭 | 已处置 | Close | — |
| `current_layer_action` | 本层处理 | 已处置 | Change | Adjust |
| `roll_forward` | 带入下次形成 | 已处置 | — | — |
| `immediate_reopen` | 立即重开 | 已处置 | — | Return Plan |
| `route_escalate` | 转交或上报 | 待路由 | Route | Route |
| `pushback` | 退回补齐 | 形成中 | Pushback | Pushback |

- 处置必带最低理由，写在 `content`。处置本身不改变业务对象的生命周期 [补 35]：Roll Forward 与 Immediate Reopen 的问题由取上下文带入下一次相关的形成（第 15.3 节），必须被看到、不必须采用；需要重开或调整的，由相应责任人另记门事件或生命周期事件，并在内容里引用处置事件。
- 谁记 [补 36]：
  - 提出（`raiser`）与路由（`router`）：MF，即在主受影响对象所在域持 AGENT 的 Agent（Co-Agent）；或主受影响对象主干上的责任人（人）。路由指定一名承接人（人）；已路由时可以改路由。
  - 承接（`route_target`）：路由指定的承接人本人。
  - 处置（`owner`）：已承接的承接人本人。
  - 退回形成（`router_or_owner`）：路由者或承接人。
- 已处置的问题不再提出；同一问题复发用新 id，并在内容里引用原问题 [补 37]。
- 备选：若 [决 2] 定为升业务对象，Issue 成为业务对象类型，状态表不变，身份改由对象 id 承载。

## 14. 代记

方案 2.9 的机制：人向外部系统的服务主体登记委托，外部系统以自己的凭证写入，并声明代谁记。

- 委托：`world_grant_delegation` 由委托人本人记（人）。参数：受托的服务主体（本 scope 的 Agent 主体）、动作族、域列表、有效期 `valid_until`（必填）。动作族第一版是门、指派与生命周期 [补 38]，建对象待决 [决 6]。`world_revoke_delegation` 由委托人本人记，引用登记事件，撤销即时生效。两者都是记录事件，以 Company 为主体 [补 39]。
- 当前有效的委托属于身份投影：已登记、未撤销、未过期，且委托人仍是 scope 内有效的人。不可转委托。
- 代记写入：受托的服务主体以自己的凭证调用可代记的动作，另带 `on_behalf_of`，写明被代记的人、外部系统里的记录 id、外部确认时刻（不晚于记录时刻）。运行时按被代记的人判权：用他当前的角色指派对照激活策略，规则与他本人记时相同；同时要求委托有效，动作族与目标所在域在委托范围内。任一不满足返回 `FORBIDDEN`。
- 效果：事件与回执同时记录记录者（服务主体）与被代记的人；生命周期与决定权按被代记的人算；事件的发生时刻是记录时刻，外部确认时刻另存 [补 40]。
- 被代记的人可以本人撤回代记的事件，规则同第 11 节。
- 边界：不代持人的凭证；服务主体本身不获得任何决定权；代记写入不带写入声明，改带 `on_behalf_of`；代记只走 HTTP；Agent 自主起草仍走候选与快照，不经代记写回。

## 15. 读取

### 15.1 读投影与按层组织

- scope 内有任一生效角色指派的责任主体，可读该 scope 的全部 world 对象、事件与快照；scope 外 404。不做单元级读隔离。
- 读投影的输出按三层分组，键为 `business`、`identity`、`records` [补 41]：
  - `business`：对象 id、类型、类别、是否候选类型、版本与修订 id、属性、关系引用、块与组件（空块给标准句）、组件台账、正式内容指针、进行中的一轮。
  - `identity`：责任人（按第 3.3 节解析，注明来自属性还是角色）、与该对象有关的当前有效委托。
  - `records`：生命周期与推出它的事件、最新快照（标明未经确认）、最近的已确认复盘、主受影响对象是它且未处置的问题。
- 取对象：输出如上；`version` 取指定修订。
- 取状态：按主体与时点，返回 `as_of` 不晚于该时点的最新快照。
- 取事件：按主体与起始时间，返回 `subject_refs` 含该对象或其组件的事件，按发生时刻升序，带迟记标记、被更正与被撤回关系、代记信息；每条事件带产生它的动作。
- 取子对象：同 0.1。

### 15.2 列对象与按外部引用查找

- `GET /v1/world/objects`：按 `unit_id`（责任单元）或 `domain_id`、`type`、`period`、`external_system` 与 `external_id` 筛选，返回对象头（id、类型、类别、标题、最新版本、生命周期、域、外部引用），分页。
- 按周期筛选的口径：周期目标按自己的 `period`；Mission 按其周期目标；Task、Activity 按其 Mission；快照按自己的 `period` [补 42]。

### 15.3 取上下文

沿用 0.1 的取上下文（`POST /v1/world/objects/{id}/context`，预算默认 12000 字符、每个对象 10 条事件、近期 30 天，可以在请求里给）[决 18]，改动如下：

- 引用按组件返回。
- Why 沿单元长期目标到公司级长期目标、Strategy 与 Company 各多取一跳。
- Markdown 按六问组织；事件行写出记录者与被代记的人。
- 形成周期目标时带入已确认的公司复盘与有效的长期目标；形成任何有门对象时带入待带入的问题 [补 43]。
- Agent 起草走服务端组装的固定上下文；自由遍历只作对照。
- 上下文包是时间记录，只追加，沿用 0.1 的表。

### 15.4 读 0.1 对象

- 0.2 读侧兼容读 0.1 对象：按 0.1 契约与登记解释，生命周期按 0.1 的状态机，按第 15.1 节分组输出。0.1 的引用是 0.2 的对象或块形式，照常解析。0.1 快照按只读的 `legacy_0_1` payload 给出（progress、issue、artifacts 三块）；0.1 事件按 0.1 的种类给出。

## 16. 与 0.1 的差异

1. 类型分三层；状态快照归时间记录；Activity 成为候选类型；Strategy 成为有门对象；Company 与 Strategy 不再是存根。
2. 块值加组件列表；引用加组件与事件两种形式；责任单元的 `architecture_ref` 指到责任单元条目。
3. 块分正式块与活动块；Mission 加执行计划块，周期目标加实现逻辑块；业务对象加外部引用。
4. 事件分三类；新增 `reconfirm`、`agreement`、`review.confirmed`、`start`、`deliver`、`accept`、`reject`、`reopen`、`cancel`、`issue.*`、`delegation.*`；取消 `phase`。
5. Mission 的交付从「承诺（交付）加确认（交付）」改为交付、验收通过与退回；进行中由开始推出；新增重开与取消。
6. Task 与 Activity 的交付与验收从外部事件的类别改为生命周期事件；新增调整中、重开与取消；Activity 的指派与验收改由 Task 的责任人记。
7. 周期目标新增再确认、复盘确认（关闭）与取消，形成须锚定有效长期目标与已确认复盘；长期目标新增再确认与终止；草稿时的承诺也可以带候选（0.1 不带）。
8. 核心战役默认改为关注标记（方案 A），不再推出「等 CEO 确认」。
9. 快照外壳加生成者、来源事件与 payload 类型，payload 按主体类型定义；状态刷新的发生时刻取 `as_of`。
10. 写入声明的场景放宽到任一业务对象；Agent 面扩大（第 9.3 节）。
11. 新增列对象与按外部引用查找；读投影按三层分组；读取标迟记。
12. 新增代记。

## 17. 与 ADR 的关系

- ADR-0001：沿用。0.2 是 tkos.world 的新版本，与冻结的 0.1 和 tkos.method 并存；内核复用，只在协议支持集合里加一行。
- ADR-0002：沿用并扩大。生命周期仍由事件推导、不存状态枚举；0.1 由状态快照与外部事件类别推出的段，改由显式的生命周期事件推出。
- ADR-0003：扩展。快照写入仍无人确认；新增的复盘确认是一条门事件，钉住某条快照、赋予它「已确认复盘」的效力，并可以关闭周期目标。快照本身不因此变成确认式状态。
- ADR-0004：沿用。MCP 与 CLI 都是 HTTP 的薄壳，暴露同一个 Agent 面。
- ADR-0005：门动作沿用。两处不同：生命周期动作是通用动作（补 14）；Agreement 不按策略角色表判权（决 13）。
- ADR-0009：本稿的实现路径：长期分支、可重写的单个迁移、实验库每次重建、第 5 步锁版。

## 18. 补充规则

方案之外、落稿必须定下的细则，是 E&O 的提议，按正文出现的顺序编号：

1. 打法块只写核心路径、关键逻辑与关键取舍，局部做法写进执行计划（活动块），局部变化因此不触发重走。
2. 外部引用在 scope 内按 `(system, id)` 唯一，按各对象的最新修订判定，冲突拒绝；外部引用是活动属性，有正式内容后也可以直接修订。
3. 组件带 `scope`（可选，指向一个业务对象，缺省为所在对象）与 `attributes`（类型属性，由登记给出，可以为空）。方案只列了「稳定 id、类型、scope、正文、引用、材料」，没说 scope 的取值。
4. 组件 id 在所属对象内唯一；对象维护组件台账，删除后 id 不复用；组件不跨块移动。这样「删除留痕」与「空块存 null」两条可以同时成立。
5. 方案第一批之外加两种组件：计划条目（Mission 执行计划块与 Task 计划块，Task-only 线要用）；进展条目（执行状态的进展块，字段取对接说明 11.2 的天枢草案，三方会上定稿）。
6. 事件引用写作 `event:<事件 id>`；事件不可变，不带版本。
7. `review_ref` 在草稿期可以改指另一条已确认的公司复盘，例如形成期间又确认了更新的复盘；这是 0.1「关系字段不能换挂」的一个例外。
8. 周期目标的 `depends_on[]` 可以指向周期目标或 Mission（方案只写「依赖字段」），只经 `world_relate` 写。
9. 快照的生成者由服务端按凭证填，客户端不写；内容实际由别的 Agent 生成的，写进来源事件或材料。
10. 快照的来源事件至少一条。天枢的每周进展快照因此要先记一条外部事件（例如 `category` 为 `other` 的「每周同步」），再在快照里引用它。E&O 已定（2026-09-28）。
11. payload 第一批在方案之外补了三点：责任单元与 Strategy 也有 payload，问题要能以单元或战略为主受影响对象；每种 payload 都带材料块，候选稿放在这里；公司复盘不带问题块，战略层面的问题以 Strategy 为主受影响对象。
12. 复盘确认有两种作用：确认公司复盘快照，它成为已确认的公司复盘，不改变任何生命周期；确认以周期目标为主体的快照，同时关闭该周期目标。方案 2.4 说复盘确认「不改生命周期」，2.3 的周期目标表又用它关闭目标，本稿按主体区分。
13. 方案 2.3「除指派与处置外不推动生命周期」按此解释：记录事件不推动业务对象的生命周期，指派除外；Issue 事件（含处置）只推动 Issue 自己的状态，处置后要改业务对象的，由责任人另记门事件或生命周期事件。
14. 门动作继续按目标类型拆名、角色写在激活策略里（ADR-0005）；六个生命周期动作是通用动作，每条转移由登记的 `by` 按责任关系判定，策略里列角色的并集。否则同一类事件要按目标类型拆成十几个动作名。E&O 已定（2026-09-28）。
15. Mission 的「Owner 的 Agent」指在 Mission 所在域持 AGENT 的 Agent，记开始时带写入声明。
16. 再指派不改变状态；已关闭、已取消的对象不能再指派。
17. Agent 面在 0.1 的三个写动作之外，加提出问题、路由问题、退回形成、开始（Mission 作为 Owner 的 Agent，Activity 作为其责任人）与交付（Activity 作为其责任人），读加列对象。方案 2.8 写的「七个操作」因此不再够用。E&O 已定（2026-09-28）。
18. Agent 修订只触及活动块与活动属性时，声明可以不要求人工验收（M3：计划的日常更新不逐项审批）；触及正式块或正式属性时（只可能是无门对象），必须要求人工验收并给出验收人，同 0.1。
19. 上层对象的关闭、取消不自动改变下层对象的生命周期；Task 全部关闭也不自动关闭 Mission。
20. Strategy 一轮的开法：Strategy 的责任人以指派事件列出本轮责任人，已生效时附候选；Agreement 钉住本轮与所同意的内容；重新指定即开新一轮；已达成判断时不能开轮。
21. 结论为不改的一轮，由 CEO 的再确认结束，不出新修订。
22. 长期目标的终止记为取消事件（`cancel`），进入「已终止」，不另设事件种类。
23. 长期目标确认上的「返回 M1-A」记在事件的 `detail.returns_to`，不自动提出问题。
24. scope 内还没有已确认的公司复盘时（第一个周期），周期目标的 `review_ref` 可以空。
25. Mission 立项的承诺与确认接受都查父周期目标已确认；方案只写「立项检查」。
26. 关注标记在已关闭、已取消之外的任一状态都可以记，每个 Mission 一次。
27. 状态刷新事件的发生时刻取快照的 `as_of`（0.1 取记录时刻），补写的快照在按发生时刻读时落在它描述的时点，并按规则标迟记。
28. 按「只能撤回推出当前状态的事件」推出，在此列明：撤回事件本身、再确认、没有推出状态的 Agreement、公司复盘确认、一轮重走中的承诺与确认都不能撤回。另加一条：一轮重走写回过之后，让对象成为正式的那条确认不能再撤回（登记 `formal_confirm_after_write_back`），否则一次撤回会把写回的正式内容整个收回。E&O 已定（2026-09-28）。
29. 更正可以指向外部事件与 Issue 事件（0.1 只有外部事件）；快照、指派、修订各用新快照、再指派、新修订更正。
30. 草稿时的承诺可以带候选、也可以不带：方案写周期目标的承诺「带候选」、Mission 的「可带候选」，本稿两者都作「可带」；0.1 草稿期的承诺不带候选。
31. 活动块与活动属性在已关闭、已取消之外的任一状态都可以直接修订，包括已承诺这类还没有正式内容的段；方案只写「活动块直接修订、不走门」。
32. Mission 的一轮只在已成立、进行中、已交付、调整中开；方案写「已成立及之后」，本稿把已关闭、已取消排除在外。
33. 候选只含正式块与正式属性；写回时活动块、活动属性与只由服务写的字段取写回时的当前值，一轮进行中对执行计划的修订不会被写回覆盖。
34. 块补丁按字段合并（0.1 是整块替换）：给出的 `text`、`refs`、`artifacts` 整体替换，`components` 按 id 合并，`{"id": …, "removed": true}` 删除，新组件追加在后；块给 null 即清空，其中的组件在台账里记删除。
35. 处置本身不改变业务对象的生命周期；Roll Forward 与 Immediate Reopen 由取上下文带入下一次相关的形成，需要重开或调整的由责任人另记门事件或生命周期事件，并引用处置事件。
36. Issue 事件谁记：提出与路由由 MF（在主受影响对象所在域持 AGENT 的 Agent）或主受影响对象主干上的责任人记；承接与处置只由人记，承接人须是路由指定的人。方案只写了「MF 或 Co-Agent」「路由者」「承接人」。
37. Pushback 回到形成中；已路由时可以改路由（具体人选错在路由层纠正）；已处置的问题 id 不再提出，复发用新 id 并在内容里引用原问题。
38. 代记的动作族在方案的门与指派之外加生命周期：天枢的「执行事项完成」在 0.2 要记成 Task 的交付，由执行人记。E&O 已定（2026-09-28）。
39. 委托的登记与撤销以 Company 为主体，因为事件至少要有一个主体。
40. 代记写入不带写入声明，改带 `on_behalf_of`（被代记的人、外部记录 id、外部确认时刻）；代记只走 HTTP，不进 Agent 面；事件的发生时刻是记录时刻，外部确认时刻另存。
41. 读投影按三层分组输出，键为 `business`、`identity`、`records`，满足执行计划 D「读投影的输出按类别组织」。
42. 列对象按周期筛选时，周期目标按自己的 `period`，Mission 按其周期目标，Task 与 Activity 按其 Mission，快照按自己的 `period`。
43. 待带入的问题：处置为 Roll Forward 或 Immediate Reopen、此后主受影响对象还没记过门事件。形成周期目标时，带入主受影响对象是本单元（责任单元、本单元的长期目标或此前的周期目标）的；形成其他有门对象时，带入主受影响对象是它本身的。

## 19. 待决

前 6 项对应方案第五节，第 7 至 12 项是执行计划第四节的 E&O 内部待决，其余来自方案第七节与执行计划。本稿对每项先按「本稿」一栏落稿；第 7 至 13 项 E&O 已于 2026-09-28 确认，按「本稿」一栏执行。

| 编号 | 事项 | 本稿 | 谁定 |
|-|-|-|-|
| 1 | 核心战役：关注标记（方案 A），还是可选的 CEO 门（方案 B） | 方案 A；B 的差异见附录 A。落地计划稿另有「DRI 可以提名」，本稿未收 | CEO |
| 2 | Issue：问题组件，还是升为业务对象 | 问题组件；升对象时状态表不变，身份改由对象 id 承载 | CEO |
| 3 | 验证范围：一个责任单元回放，还是按方法自己的最低验收跑一个真实周期 | 不影响本契约 | CEO |
| 4 | Activity 去留 | 登记为候选类型；按对照实验的结论在第 4 步定 | CEO，依据实验 |
| 5 | 真实战略材料由谁提供、能否放进实验环境 | 不影响本契约 | 待定 |
| 6 | 代记是否含建对象 | 不含；登记里列为待决的动作族 | CEO |
| 7 | 组件 id 由写入者给还是系统生成 | 写入者可以给，不给由服务生成 | E&O 已定（2026-09-28） |
| 8 | 组件能否带自己的责任人 | 计划条目可以带，只作记录、不产生权限 | E&O 已定（2026-09-28） |
| 9 | 乱序是拒绝还是接受并标记 | 外部事件与状态刷新接受并标记迟记；门事件与生命周期事件不接受补记 | E&O 已定（2026-09-28） |
| 10 | 取消是否设门 | 不设门；取消是生命周期事件，由上一级责任人记，长期目标的终止由 CEO 记 | E&O 已定（2026-09-28） |
| 11 | 重开与退回由谁记 | 上一级责任人 | E&O 已定（2026-09-28） |
| 12 | Activity 的指派者层级 | Task 的责任人（按方案 2.3） | E&O 已定（2026-09-28） |
| 13 | Agreement 的签字范围如何指定、怎样判权 | Strategy 的责任人以指派事件列出本轮责任人；Agreement 按 scope 与本轮指定判权 | E&O 已定（2026-09-28） |
| 14 | 方法侧输入：正式块清单与质量标准、组件类型清单、各类对象的 State 内容项 | 用临时清单 | 方法侧 |
| 15 | 状态快照是否改为独立的记录存储 | 不改存储，语义按时间记录 | 第 4 步 |
| 16 | 上下文包是否归入时间记录 | 归入（执行计划的建议） | E&O |
| 17 | 已有 0.1 数据的 scope 怎样接 0.2：新建 scope，还是按域切换默认协议 | 实验环境只用新 scope | 第 5 步 |
| 18 | 预算默认值与实验用模型 | 沿用 0.1 的默认值 | E&O，依据实验 |

## 20. 未交付边界

- 证据上传仍不在本版：artifacts 只是 URL，登记里 `evidence_upload` 关闭。看板与工作台不在本版，所以人记门事件、生命周期事件目前只经 HTTP 或代记。单元级读隔离不在本版。
- 迁移、profile 与钉定在第 5 步定稿（ADR-0009）；在此之前本稿与登记可以改，改了就重钉、重建实验库。
- 方法侧清单到位前，块清单、组件类型与 payload 都是临时的。

## 附录 A　方案 B：核心战役为可选的 CEO 门

[决 1] 若选方案 B，Mission 的状态表在方案 A 的基础上改动如下（登记不收，选定后再补）：

| 事件（动作） | 起始状态 | 进入状态 | 谁记 |
|-|-|-|-|
| 确认 Mission（立项，接受）`world_confirm_mission` | 已承诺 | 未标核心战役时进入已成立；已标时进入等 CEO 确认 | DRI |
| CEO 确认核心战役立项（接受）`world_confirm_mission_core_battle` | 等 CEO 确认 | 已成立 | CEO |
| CEO 确认核心战役立项（退回）`world_confirm_mission_core_battle` | 等 CEO 确认 | 草稿 | CEO |
| 标记核心战役 `world_mark_core_battle` | 草稿、已承诺 | 不变 | CEO |
| 标记核心战役 `world_mark_core_battle` | 已成立 | 等 CEO 确认 | CEO |

- 进行中及之后不能标记；每个 Mission 只标一次；CEO 只确认立项。门默认关闭：CEO 标记某个 Mission 之后，门才对它生效。
- 已成立后被标记、又被 CEO 退回草稿时，正式内容一并收回，这条退回不能撤回（同 0.1）。

## 附录 B　两个决策点的对比

执行计划工作项 F 要求的「两个决策点的方案对比」：

| 决策点 | 方案 | 对契约的影响 | 代价与风险 |
|-|-|-|-|
| 核心战役 [决 1] | A 关注标记（推荐，按 M1-B v6） | 一条记录事件，只置 `core_battle`；Mission 状态表不含「等 CEO 确认」 | CEO 对个别 Mission 没有否决门，要介入只能经问题或沟通 |
| 核心战役 | B 可选的 CEO 门，默认关闭 | 附录 A 的五条转移，多一个门动作与一个段 | 与 M1-B v6「关注不改变决定权」冲突；被标记的 Mission 立项多一步 |
| Issue [决 2] | 问题组件（推荐） | 不加类型；身份是（主受影响对象，组件 id）；事件指向组件 | 问题跨多个主体、脱离主体快照独立演进时表达别扭；实验若常见这种情况再升对象 |
| Issue | 升为业务对象 | 多一个类型：建、改、读、状态表与验收都要补；身份是对象 id | 与方法「Issue 属于 State」的口径不同；多一类对象的存储与维护 |

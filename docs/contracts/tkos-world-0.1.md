# tkos.world/0.1 — 业务世界模型契约

状态：**契约文字按规格 #17 与 2026-09-24 的补充决定定稿**。0.1 只有在协议登记显式登记、且支持状态为本进程编译支持后才可调用；启用前，通过授权的 world 请求返回 `PROTOCOL_NOT_SUPPORTED`（授权先于协议错误），不产生业务成功。tkos.method/0.4、0.5 冻结，其解释、绑定、历史回执与回归保持不变（ADR-0001）。

来源：《TKOS 语义模型与运行时重构方案 v0.1》、CEO《企业业务世界建模框架 内部讨论稿 v0.1》与 9/23 会议结论、规格 #17、2026-09-24 访谈与补充决定、术语表 `CONTEXT.md`、ADR-0001 至 0005。本契约第 2 至 10 节的清单以机器可读形式登记在 `docs/contracts/world-registry-0.1.json`（`tkos.world-registry` 0.1.2，规范 JSON：键排序、缩进 1、UTF-8、结尾换行）。world profile 同时钉定本契约与该登记的原始字节，任一改动都产生新修订并重新钉定。

## 1. 版本、启用与隔离

- 协议 `tkos.world`，契约版本 `tkos.world/0.1`，profile 结构版本 `tkos.world-profile/0.1`。
- 一个 scope（tenant + organization）就是一家公司，Company 是 scope 内唯一的根对象。world 在独立的新 scope 启用。
- 每个责任单元对应一条域记录；单元级角色（CEO、DOMAIN_DRI、OWNER、IC、AGENT）记在该域的角色指派里。Company 所在的域即公司域。对象所在的域：Company、Strategy、公司级长期目标在公司域；每个责任单元在自己的域（不是公司域，一个域只有一个责任单元）；单元级长期目标、周期目标、Mission、Task、Activity 与主干上一级在同一个域。角色映射：CEO→CEO，DRI→DOMAIN_DRI，Owner→OWNER，Task 的责任人→IC；Activity 的责任人→IC（人）或 AGENT（Agent）。
- 不从 tkos.method 迁移数据、不建桥。同名类型（Strategy、Mission）的含义由对象绑定的协议决定。

## 2. 一级对象

| 类型 | 中文名 | 责任人 | 门 | 第一版 |
|-|-|-|-|-|
| Company | 公司 | CEO | 无 | 存根 |
| Strategy | 战略 | CEO | 无 | 存根 |
| ResponsibilityUnit | 责任单元 | 该单元的 DRI | 无 | 是 |
| LongTermGoal | 长期目标 | CEO | 确认：CEO | 是 |
| PeriodGoal | 周期目标 | 该单元的 DRI | 承诺：DRI；确认：CEO | 是 |
| Mission | Mission | Owner | 承诺：Owner；确认：DRI；立项与交付各一次；核心战役立项再加 CEO | 是 |
| Task | Task | 人 | 无 | 是 |
| Activity | Activity | 人或 Agent | 无 | 是 |
| StateSnapshot | 状态快照 | 写入者 | 无 | 是 |

责任主体（人与 Agent）由身份与角色指派投影，不是对象类型。

## 3. 内容块

- 块值是三件套 `{text, refs[], artifacts[]}`：text 为 Markdown，refs 为引用（第 5 节），artifacts 为文档链接（URL）。
- 块路径只一层。块 id 为英文 snake_case，中文显示名在登记里。
- 空块存 null，读取时渲染标准句「当前没有〈块名〉」，句子在投影层配置。
- 非空块至少含文字、引用或文档链接之一；文字只有空白且没有引用与链接的块视为空块，必须存 null，不能以空内容冒充有内容。
- 块随对象出版，改块即对象出新修订；块没有自己的版本。运行时只校验块的结构，不校验内容质量。
- 除状态快照外，每类对象固定「定义类块 + constraint」。constraint 是每个对象自己的块，不继承、不汇总。状态快照固定三块：progress（进展）、issue（问题）、artifacts（产物）。
- 临时定义类块清单（方法侧正式清单到了，整体替换登记并重新钉定）：

| 类型 | 定义类块 |
|-|-|
| Company | identity（身份） |
| Strategy | choices（战略选择）、path（路径）、assumptions（关键假设）、capabilities（能力）、responsibility_structure（责任结构） |
| ResponsibilityUnit | definition（定义）、boundary（边界） |
| LongTermGoal | outcome（结果）、measures（衡量） |
| PeriodGoal | outcome（结果）、acceptance（验收标准） |
| Mission | definition（定义）、acceptance（验收标准）、play（打法） |
| Task | definition（定义）、acceptance（验收标准）、plan（计划） |
| Activity | instruction（执行指令） |

## 4. 属性与责任人

- 属性不进块，可索引。所有类型都有必填的 `title`（标题）。
  - ResponsibilityUnit `unit_kind`：`battlefield`（战场）或 `domain`（域）。
  - LongTermGoal `scope`：`company`（公司级）或 `unit`（责任单元级）；`horizon`（期限，文字）。
  - PeriodGoal `period`：月份，形如 `YYYY-MM`。
  - Mission `core_battle`：只由 `core_battle.marked` 事件置为真。
  - StateSnapshot `subject_ref`（主体引用）、`as_of`（时间戳），可选 `period`（`YYYY-MM`）。
- 对象级 `responsible` 只在 Mission、Task、Activity 上，只由 assign 事件写入。其余类型的责任人按角色解析：Company、Strategy、LongTermGoal 为 CEO；ResponsibilityUnit、PeriodGoal 为该单元的 DOMAIN_DRI；StateSnapshot 为写入者。

## 5. 引用

- 业务形式 `<对象 id>@<版本号>#<块路径>`，版本号为整数修订序号。写入时引用一律用业务形式的字符串；读回时同时给出业务形式与钉定后的结构化对象。
- 关系引用字段按登记带或不带块路径：`architecture_ref` 必须带 `#responsibility_structure`，其余关系引用字段指向对象本身、不带块路径。块内引用可指向本 scope 任一 world 对象的任一已有版本，块路径须是该类型登记的块（空块也可引用）。
- 存储为结构化对象：对象 id、版本号、修订 id、块路径。服务端在写入时把版本号解析成修订 id 并钉住；引用固定在版本上，不随对方更新而漂移。
- 指向不存在的对象、版本或块的引用被拒绝；指向非 world 记录或 scope 外对象的引用按不存在处理。

## 6. 关系与主干

| 关系 | 主语到宾语 | 承载 |
|-|-|-|
| has / contains | Company 到 Strategy；Company 或责任单元到其长期目标 | Strategy、LongTermGoal 的 `parent_ref` |
| defines | Strategy 的责任结构块到责任单元 | 责任单元的 `architecture_ref`：Strategy 版本加块路径 `responsibility_structure` |
| decomposes_to / allocates_to | 公司级长期目标到责任单元级长期目标 | 责任单元级长期目标的 `goal_ref`（可空） |
| advances | 周期目标到长期目标 | 周期目标的 `goal_ref` |
| serves | Mission 到周期目标 | Mission 的 `goal_ref` |
| decomposed_into | Mission 到 Task；Task 到 Activity | Task、Activity 的 `parent_ref` |
| responsible_for | 责任主体到责任单元、Mission、Task、Activity | 责任单元：角色指派；Mission、Task、Activity：`responsible` 属性 |
| depends_on | Mission 到 Mission；Task 到 Task | 引用列表 `depends_on[]` |
| contributes_to | Mission 到另一责任单元的目标 | 引用列表 `contributes_to[]` |
| supersedes | 新版到旧版 | 修订链 |

- 目标约束：公司级长期目标的 `parent_ref` 指向 Company，不带 `goal_ref`；责任单元级长期目标的 `parent_ref` 指向所属责任单元，`goal_ref` 只能指向公司级长期目标；周期目标的 `goal_ref` 指向本单元的长期目标；`contributes_to` 指向另一责任单元的周期目标或长期目标。Strategy 的责任结构块是 Strategy 自身的一部分。
- 主干：Activity → Task → Mission → 周期目标 → 长期目标 → 责任单元 → Strategy → Company。每一层沿登记的主干字段向上：Activity、Task、长期目标、Strategy 用 `parent_ref`；Mission、周期目标用 `goal_ref`；责任单元用 `architecture_ref`。公司级长期目标直接挂在 Company 下。
- `depends_on[]` 与 `contributes_to[]` 只经 `world_relate` 写入，建对象与修订时不接受；其余引用字段在建对象时写入，修订时只能改钉到同一对象的另一个已有版本（例如跟进上一级的新版本），不能换挂到别的对象，换挂要新建对象。
- `world_relate` 以 `{field, refs}` 整体替换该字段的列表（可增可删）：`depends_on` 不能指向对象自己，`contributes_to` 指向另一责任单元的周期目标或单元级长期目标，列表内不重复。关系记在持有该字段的对象上，被指向的对象不出新修订；取对象时，被指向的一端列出指向它的跨链关系。
- 跨链关系只展示不递归。第一版不建关系表。

## 7. 状态快照

- StateSnapshot 是独立对象，属性见第 4 节，三块见第 3 节。写入即生效，无人确认（ADR-0003）；读侧展示时须标明它未经确认。
- 同一主体同一时刻（`subject_ref` 加 `as_of`）唯一。`as_of` 以 UTC 规范文本存储（`YYYY-MM-DDTHH:MM:SS[.ffffff]Z`），同一时刻只有一种写法，唯一性按这一写法判定。只读最新，历史天然保留；错快照用新快照更正。
- issue 是块，不分战略与管理问题，由 Co-Agent 分流，不产生对象。
- Agent 起草的对象内容以 artifacts 链接放在状态快照里，只有经承诺与确认写回对象后才是正式内容。

## 8. 事件

- 只追加，是唯一的触发源。字段：`event_id`、`scope`、`kind`、`phase`、`category`、`outcome`、`subject_refs`（至少一条）、`principal`、`occurred_at`、`recorded_at`（`occurred_at` 可早于 `recorded_at`）、`content`（三件套）、`action_id`（指回回执）、`supersedes_event_id`。
- 每个 world 动作在同一事务里恰好写一条事件。
- 结构化属性的取值：
  - phase（承诺与确认用）：`initiation`（立项）、`delivery`（交付）。只有 Mission 的门带 phase。
  - category（外部事件必填）：`meeting`（会议）、`review`（评审）、`delivery`（交付）、`acceptance`（验收）、`other`（其他）、`correction`（更正）。
  - outcome：确认必填，取 `accepted`（接受）、`returned`（退回）、`withdrawn`（撤回）；承诺只在撤回时带 `withdrawn`。
- `supersedes_event_id` 在更正（category 为 `correction`）与撤回（outcome 为 `withdrawn`）时必填。

| kind | 含义 | 谁能记 |
|-|-|-|
| object.created | 建对象，subject_refs 钉到第一个修订 | 新对象主干上某一级的责任人（第 9 节） |
| object.revised | 对象或块变化，subject_refs 钉到新修订 | 该对象或其主干上某一级的责任人（第 11 节）；确认写回时由服务记 |
| state.refreshed | 状态快照写入 | Co-Agent、执行 Agent、责任人 |
| event.recorded | 外部发生的事，带 category 与 artifact 链接 | 有 scope 权限的人或 Agent |
| commit | 下级承诺 | 周期目标由 DRI；Mission 由 Owner |
| confirm | 上级签字 | 长期目标、周期目标由 CEO；Mission 由 DRI；核心战役立项再加 CEO |
| assign | 责任指派，生效时间即 `occurred_at` | CEO 指派 DRI，DRI 指派 Owner，Owner 指派 Task 与 Activity 的责任人 |
| relate | 建立跨链关系，subject_refs 钉到新修订与列表里的对象 | 该对象或其主干上某一级的责任人（第 11 节） |
| core_battle.marked | Mission 标为核心战役 | CEO |

- 0.1 不做未来才生效的指派：assign 的生效时间就是该事件的 `occurred_at`。
- 事件不删不改：错快照用新快照；错外部事件记 category 为 `correction` 的新事件，以 `supersedes_event_id` 引用原事件；错承诺或确认按第 10 节撤回。

## 9. 动作、门与写入声明

- 协议动作即事件类型。每个动作走 HTTP 的 prepare／commit 两段式请求（与承诺事件 `commit` 无关）、乐观并发（期望版本）、幂等键与回执；world 动作不入队、不外发。
- 建对象：`world_create_object` 建除状态快照外的八类对象；状态快照只经 `world_refresh_state` 写入。建对象者须是新对象主干上某一级对象的责任人（按第 4 节解析；按角色解析的责任人须是人，Agent 只经 `responsible` 属性成为责任人）；Company 由 CEO 本人建。即：Strategy、公司级长期目标、责任单元由 CEO 建；单元级长期目标、周期目标、Mission、Task、Activity 由 CEO 或该单元的 DRI 建；Mission 有 Owner 后，Owner 可建其下 Task 与 Activity；Task 有责任人后，责任人可建其下 Activity。
- 通用动作：`world_create_object`（object.created）、`world_revise_object`（object.revised）、`world_refresh_state`（state.refreshed）、`world_record_event`（event.recorded）、`world_assign`（assign）、`world_relate`（relate）。「只有责任人能改」一类规则写在服务代码里。`world_relate` 为对象产生新修订并只记一条 relate 事件；跨链关系不属于正式内容块，有门对象成立后建关系也不重走承诺与确认。
- 门动作按目标类型拆名，角色全部写在激活策略的 `action_roles` 里，与下表一一对应（ADR-0005）：

| 动作 | 事件 | 目标类型 | 角色 | phase | outcome |
|-|-|-|-|-|-|
| `world_commit_period_goal` | commit | PeriodGoal | DOMAIN_DRI | 无 | 撤回 |
| `world_commit_mission` | commit | Mission | OWNER | 立项、交付 | 撤回 |
| `world_confirm_long_term_goal` | confirm | LongTermGoal | CEO | 无 | 接受、撤回 |
| `world_confirm_period_goal` | confirm | PeriodGoal | CEO | 无 | 接受、退回、撤回 |
| `world_confirm_mission` | confirm | Mission | DOMAIN_DRI | 立项、交付 | 接受、退回、撤回 |
| `world_confirm_mission_core_battle` | confirm | Mission | CEO | 立项 | 接受、退回、撤回 |
| `world_mark_core_battle` | core_battle.marked | Mission | CEO | 无 | 无 |

- 写入声明三项：场景（属于哪个 Mission 或 Task）、触发事件、是否人工验收及验收人。以请求参数 `declaration` 提交：`scene` 为 Mission 或 Task 的引用 `<对象 id>@<版本号>`，写入时钉定；`trigger` 为触发事件的文字说明；`human_acceptance` 为 `{required, acceptor}`，需要人工验收时 `acceptor` 必须是本 scope 内有效的人，不需要时不带验收人。声明随回执留存。只对 Agent 身份的写入强制，缺一拒绝；人经工作台或 HTTP 写入不强制，带了按同样规则校验。三项在 HTTP 服务端校验。
- MCP 只开放 `world_revise_object`、`world_refresh_state`、`world_record_event`；其余动作只走工作台或 HTTP。

## 10. 生命周期

- 不存状态枚举。读侧按对象的事件推导 lifecycle，并给出推出它的事件 id（ADR-0002）；推导是确定性的纯函数。
- 下文未列出的（状态，动作）组合：门动作被拒绝，撤回按本节末条规则处理；非门事件不改变生命周期。
- 「以某对象为主体的 state.refreshed」指 subject_refs 含该对象的状态快照写入事件；进入某一段之前的同类事件不计。
- Company、Strategy、ResponsibilityUnit、StateSnapshot 没有生命周期，只有版本。
- LongTermGoal：草稿 →（`world_confirm_long_term_goal` 接受）已确认；草稿时被退回仍为草稿，留下 CEO 退回的记录。
- PeriodGoal：草稿 →（`world_commit_period_goal`）已承诺 →（`world_confirm_period_goal` 接受）已确认；已承诺时被退回 → 草稿。
- Mission：
  - 草稿 →（`world_commit_mission` 立项）已承诺。
  - 已承诺 →（`world_confirm_mission` 立项 接受）已成立；若此时已标为核心战役，改为进入等 CEO 确认。已承诺时被退回 → 草稿。
  - 等 CEO 确认 →（`world_confirm_mission_core_battle` 立项 接受）已成立；被退回 → 草稿。
  - 已成立 →（此后第一条以该 Mission 为主体的 state.refreshed）进行中。
  - 进行中 →（`world_commit_mission` 交付）已交付 →（`world_confirm_mission` 交付 接受）已关闭。
  - 已交付时被退回 → 调整。调整 →（此后第一条以该 Mission 为主体的 state.refreshed）进行中；调整中也可直接再承诺交付（→ 已交付）。
  - 核心战役（`world_mark_core_battle`）只能在草稿、已承诺、已成立时标记：草稿、已承诺时标记不改变所处的段，已成立时被标记转入等 CEO 确认。CEO 只确认立项，交付只由 DRI 确认。
- Task 与 Activity（无门）：未指派 →（`world_assign`）已指派 →（此后第一条以该对象为主体的 state.refreshed）进行中 →（event.recorded，category 为 `delivery`）已交付 →（event.recorded，category 为 `acceptance`）已关闭。验收事件由上一级对象的责任人记：Activity 由其 Task 的责任人，Task 由其 Mission 的 Owner。
- 撤回：只能撤回推出对象当前生命周期段的那条承诺或确认；此后若有任何事件改变过生命周期（包括 state.refreshed 这类非门事件），就不能再撤回。由与原事件相同的角色记同类事件，outcome 为 `withdrawn`，以 `supersedes_event_id` 引用原事件；生命周期回到原事件之前的那一段，被撤回的事件保留。例：Mission 已因 state.refreshed 进入进行中，就不能再撤回立项确认。

## 11. 修改规则、正式内容与候选内容

- 有门类型（长期目标、周期目标、Mission）只在草稿态允许责任人直接 object.revised；已成立或已确认后，改动必须重走承诺与确认，新正式版在确认时写回，旧版成为历史依据。确认前的新内容是候选内容，以状态快照 artifacts 链接的形式存在；已有正式内容不因候选出现而失效。
- 修订与建关系者须是该对象本身或其主干上某一级的责任人，与建对象同一规则（第 9 节）：无门类型随时可改；Mission 的 Owner 可改其下 Task 与 Activity，Task 的责任人可改其下 Activity，单元 DRI 与 CEO 可改其下的对象。
- 修订按合并：只改请求里给出的字段与块，块给 null 即清空，其余沿用当前版本。状态快照不修订，错快照用新快照更正。
- Agent 身份的 object.revised 只允许无门类型，且声明里必须需要人工验收并给出验收人。
- 内核对象的状态列与生效修订指针只承担正式内容指针：有门类型建对象为 `draft`，确认接受时改为 `confirmed` 并把生效指针挪到被确认的修订；无门类型一律 `recorded`，生效指针等于最新。修订与建关系产生新修订时，生效指针原先等于最新修订的随之移动，否则不动。这不是业务生命周期（ADR-0002）。

## 12. 读写权限

- scope 内有任一生效角色指派的责任主体，可读该 scope 全部 world 对象、事件与状态快照；scope 外不可读。0.1 不做单元级读隔离。
- 写入按角色与门判权；Agent 以 Agent 身份写入。
- 四个读投影（取对象、取上下文、取事件、取状态）的输出形状见规格 #17；本契约只约定其中的空块标准句、引用形式与 lifecycle。

## 13. 未交付边界与未决

- 证据上传不在 0.1：artifacts 只是 URL，登记里 `evidence_upload` 关闭。看板与工作台展示、飞书接入、单元级读隔离不在 0.1。
- 未决：有门对象在已成立或已确认后重走承诺与确认时，对生命周期的影响（在门的实现前定）；方法侧定义类块正式清单与内容质量标准；预算默认值。

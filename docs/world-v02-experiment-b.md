# tkos.world/0.2 对照实验 B：Task-only 与 Task+Activity

票 #66（试用回放的转写是 #75），规格 #46 的用户故事 77、78 与第十节。立项时契约把 Activity 登记为候选类型，去留由这个对照实验决定：同一场真实 Mission 分两条线执行到关闭，看 Activity 是否需要独立的指派、执行、重试、验收与管理；结论是 Task-only 时，Activity 类型作废，它的内容改由执行计划块里带责任人的计划条目表达（第 4 节，决 8）。2026-09-30 锁版前用户定 Activity 留，是正式类型（最小任务单元，契约第 3.1 节，决 4）：本实验照做，结论不再决定 0.2 里 Activity 的去留，只作以后版本调整对象结构的依据（见 `docs/world-v02-freeze-checkpoint.md`「划出去的事」）。

2026-09-30 按方法侧 Content Pact 改写（#83，依据 `docs/world-v02-content-pact-mapping.md`）：Task 的块是任务定义 `definition`、Task 计划 `task_plan`（正式）与 Activity 全景 `plan`（活动块），Mission 的是战役定义 `definition`、Mission 计划 `mission_plan`（正式）与 Task 全景 `execution_plan`（活动块），Activity 的是执行目的与要求 `instruction`。计划条目 `plan_item` 在责任人之外多了四个可选属性：`expected_output` 预期产出、`quality_standard` 质量标准、`executor` 执行主体（人或 Agent 名，只作记录）、`division` 人 + Agent 分工。Task-only 线就用这四个属性承载「一段工作」的内容，与 Task+Activity 线 Activity 的 `instruction` 组件对照（第 3 节）。

实验代码在 `experiments/world_v02/`（本票的文件带前缀 `b_`），做法按 E&O 在票上定的：两条线各用一个角色名身份的独立 scope，不动真实 scope 的数据，也不要试用参与者多记一遍；两条线从同一份 Mission 内容播种；执行用一份与线无关的执行脚本描述，驱动器按各线的原生写法落下去，记下某条线表达不了或只能粗粒度表达的地方；五项观测由统计代码从两条线的事件、快照与驱动器记录机械算出。

## 1. 同一场 Mission

**试用回放**的对象（#75）是 E&O 十月起点（`deploy/world-02/seed-eo-2026-10.json`，按天枢个人任务重播的那份）里的 `mission_context`「可信 Context / Memory 与真实 Agent 读写闭环」：这条 Mission 的 Owner 就是 E&O DRI 本人，10/12–16 试用，10/16 联调验收之后由开发会话转写、E&O DRI 审阅，再在两条线上回放（第 8 节）。仓库里只用步骤键 `mission_context` 指它，不写天枢的任务编号。

**冒烟**用的仍是 9 月 29 日最初选定的「天枢 × 本体 0.2 试用」：`experiments/world_v02/b_source-2026-10.json`（E&O 十月起点换任务卡之前的原计划，正文与顺序原样另存，#73）里的 `mission_trial`，下面三个 Task：`task_trial_integration`（联调与委托登记）、`task_trial_week`（试用周每周快照与问题流转）、`task_trial_acceptance`（联调验收）。冒烟内容是合成的，只证明代码与口径跑得通。

播什么由 lines 文件定（格式 `tkos-world-02-experiment-b-lines`），`b_seed` 按 `--lines` 选：

| | 冒烟 | 试用回放 |
|-|-|-|
| lines 文件 | `b_smoke_lines.json`（0.1，`b_seed` 的默认） | 转写产物 `b-lines.json`（0.2，`b_transcribe` 生成，私有、不入库） |
| 主干 | 原计划里 `mission_trial` 到 Company，含三个 Task 的建立与门 | `b_lines.json`：十月起点里 `mission_context` 到 Company 的 10 步（Mission 建成草稿并指派 Owner），另外两个 Mission 与三条委托不带 |
| 门、建 Task | 在主干里 | 转写从真实记录得出，写进 lines 的 `steps`（周期目标与这条 Mission 的形成门、建 Task） |
| Task 的责任人与段 | 预设（下表） | 转写得出：首次指派定责任人；Task 建立时计划块里的计划条目是初始段，之后的段在脚本里划出（Task 可以不划段） |

两条线的播种分两段，第一段完全相同（`b_seed.py`）：

1. **共同播种**：照原计划播主干——步骤、正文与顺序原样取自原计划（`source.steps` 列出要的步骤），接着是 lines 的 `steps`（前置由 `seed_eo.check_plan` 一起核对）。然后 Mission Owner 把各 Task 指派给 `tasks[].responsible`，把 Mission 的执行计划块（Task 全景）写成每个 Task 一条带责任人的计划条目（引用该 Task）；Task 的工作结果与验收标准不另写进条目，读取时由 Mission 的投影项从 Task 的任务定义块给出（映射表第 1 节第 7 条，不存第二份）。播完读回核对，Mission 的生命周期要是播种里它的门推出的那个（冒烟是已成立）。
2. **划段**：每个 Task 下「谁做哪一段」的初始划分（`tasks[].segments`），由 Task 的责任人按线的写法落下。段有键、标题、正文（`text`）与责任人，另可带四个可选属性 `expected_output`、`quality_standard`、`executor`、`division`（同 `plan_item` 的四个属性，写了就是非空文本）：

| | Task-only 线 | Task+Activity 线 |
|-|-|-|
| 段 | Task 的 Activity 全景块（`plan`）里的计划条目（组件 id 即段键）：带责任人，段写了的四个可选属性写成计划条目的同名属性 | Task 下的 Activity，由 Task 的责任人建并指派；`instruction` 块写执行事项 `work_definition`（段的正文）、预期产出 `expected_output` 与成功 / 验收标准 `acceptance_criterion`（段的质量标准）组件 |
| 执行主体、分工 | 计划条目的 `executor`、`division` 属性 | 执行主体由 Activity 的指派（责任人）承担；一个 Activity 里没有「人 + Agent 分工」的组件，分工由各 Activity 的责任人表达 |
| Task 的计划块 | 各段的计划条目 | 空 |
| Task 之上 | 相同：Mission 执行计划块每个 Task 一条带责任人的计划条目 | 相同 |

Task 之上的对象在两条线上同形（验收逐块核对）；两条线只差在 Task 以下。冒烟的初始划分（`b_smoke_lines.json`）按各 Task 的定义拆，是实验用的提议。段的四个可选属性只取段正文里原样写着的那一截、正文不改，没有原文的留空，不编造（#83）：

| Task（责任人） | 段 | 责任人 | 段带的属性 |
|-|-|-|-|
| 联调与委托登记（eo-owner） | `integration` 联调 | eo-ic | 质量标准「按接口清单逐项跑通」（正文冒号后那句） |
| | `delegation_setup` 委托登记 | eo-owner | 执行主体「CEO、E&O DRI 与 Mission Owner」（正文句首点名的三方） |
| 试用周（eo-ic） | `weekly_snapshot` 每周快照 | eo-ic（冒烟里改指派给 exec-agent） | 预期产出「试用周每张任务卡的每周快照」 |
| | `issue_flow` 问题流转 | eo-ic | — |
| 联调验收（eo-owner） | `acceptance_run` 联调验收 | eo-owner | — |

五段都没有写分工。

身份一律是角色键（`b_spec.json`，`deploy/world-02/provision.py` 的格式）：`ceo`（公司域与 E&O 域的 CEO）、`eo-dri`（DOMAIN_DRI、OWNER、IC；OWNER 是试用回放的 Mission 要的，同 world-02 实例的名单，冒烟的结果不因此变）、`eo-owner`（Mission Owner：OWNER、IC）、`eo-ic`（IC）、`eo-coagent`（E&O 的 Co-Agent，AGENT）、`exec-agent`（执行 Agent，AGENT）。显示名也是角色名，仓库里不出现真名与真实账号。

## 2. 执行脚本

执行脚本（`format` 为 `tkos-world-02-experiment-b-script/0.1` 或 `0.2`，例子是冒烟的 `b_smoke.json`）是与线无关的一串步骤，按出现顺序从 1 编号。每步：

- `do`：`plan`（划出一段并定责任人）、`assign`（指派）、`start`、`deliver`、`accept`、`reject`、`reopen`（生命周期）、`progress`（在某一段上写进展）、`refresh`（状态刷新）、`raise_issue`、`route_issue`、`own_issue`、`dispose_issue`（问题流转）。脚本 0.2 另有 `cancel`（取消，生命周期）与 `return_issue`（退回形成，问题流转，可带 `text`），是转写真实记录要的（#75）；冒烟脚本仍是 0.1。
- `by`：行动者的主体键。
- 目标四选一：`segment`（段键）、`task`（Task 的步骤键）、`mission`（`true`）、`issue`（问题键，路由、承接、处置、退回形成用）。
- 按 `do` 另带：`to`（`plan`、`assign`、`route_issue`），`task` 与 `title`（`plan`），`text`（正文：进展、状态、问题、理由；`reject`、`progress`、`refresh`、`dispose_issue` 必带），`question`（`raise_issue` 的核心判断问题），`disposition`（`dispose_issue`，六类处置之一）。
- 可选：`as_of`（`progress`、`refresh`、`raise_issue` 的时点，回放真实记录时给），`declaration`（Agent 写入声明的 `trigger` 与 `acceptor`），`note`（说明，不写入）；`plan` 另可带段的四个可选属性 `expected_output`、`quality_standard`、`executor`、`division`（#83，别的步骤不带）。

`b_drive.check_script` 在驱动前整份校验，列出全部问题：目标与 `do` 相配、段已划出、问题已提出、主体键都在 `ids.json` 里、正文非空、处置是登记的六类之一。

## 3. 两条线的写法

驱动器（`b_drive.py`）把每一步按这条线的原生写法落下，逐个发动作（prepare 再 commit），记下动作、回执与事件 id、表达结果。**行动者一律是脚本写的那个人**：某种写法要别人来记才成立时，结果就是协议的拒绝，不换人。

| 步骤 | 目标 | Task+Activity 线 | Task-only 线 | Task-only 的表达 |
|-|-|-|-|-|
| `plan` | 段 | 建 Activity（`instruction` 块写段的执行事项、预期产出与质量标准组件），再指派给 `to` | 修订 Task：计划块加一条计划条目，`responsible` 是 `to`，带段的四个可选属性 | 粗粒度 |
| `assign` | 段 | 再指派 Activity | 修订 Task：改这条计划条目的 `responsible`（组件整条替换，段的属性照带，下同） | 粗粒度 |
| 生命周期 | 段，Task 有两段以上 | 这个 Activity 的同名生命周期动作 | 修订 Task：这条计划条目的正文加一行「状态：…（执行脚本第 n 步）：理由」 | 粗粒度 |
| 生命周期 | 段，Task 只有这一段 | 同上 | 这个 Task 的同名生命周期动作；被拒则同一人改计划条目的状态（退路） | 原生；走退路为粗粒度 |
| `progress`、`refresh` | 段 | 外部事件（主体是 Activity）＋ Activity 的执行状态快照 | 外部事件（主体是计划条目的组件引用）＋ Task 的执行状态快照，内容引用计划条目 | 只有一段为原生，两段以上为粗粒度 |
| `raise_issue` | 段 | 外部事件＋ Activity 快照 issues 块里的问题组件＋提出 | 外部事件＋ Task 快照里的问题组件（引用计划条目）＋提出 | 同上 |
| 路由、承接、处置、退回形成 | 问题 | 同一动作，`issue_ref` 取这条线上最近提出它的那一步 | 同左 | 原生 |
| 任一 | Task、Mission | 同一动作 | 同一动作（见下面的配对） | 原生 |

- **配对**：Task-only 线里只有一段的 Task，这一段与 Task 本身是同一个单位。段上的生命周期步骤与 Task 一级的同名步骤按次序配对（第 i 条段的 `deliver` 配第 i 条 Task 的 `deliver`），先到的写成 Task 的生命周期动作，后到的并入它（记 `merged_into`、不发动作，表达为原生）。先到的被拒（例如段的验收人不是 Mission Owner），后到的照常写。Task+Activity 线不配对：Activity 与 Task 是两个对象，各记各的。
- **快照的来源事件**：快照至少一条来源事件（契约补 10），所以进展、状态刷新与提出问题都先记一条 `category` 为 `other` 的外部事件，正文是这一步的 `text`，再以它为来源写快照。外部事件的发生时刻取这条线最晚的回执时刻（服务端时钟，不早于这条线上任何事件，又已经过去），快照的时点取外部事件回执的时刻，所以都不迟记、同一主体的时点不重复；脚本给了 `as_of` 就都用它。
- **写入声明**：Agent 的每个写入都带声明，场景是这一步在这条线上的主体（Activity 或 Task）；脚本没给就用默认的触发说明、不要求人工验收。承接与处置只由人记，不带声明。
- **表达结果**：一步在一条线上是**原生**（同粒度表达）、**粗粒度**（只能在 Task 一级表达：只能改计划条目，或只能以 Task 为主体记下）或**被拒**（协议拒绝，记下每个被拒动作的错误码）。判定：并入配对的另一半为原生；配套动作（快照前的外部事件、提出前的快照）被拒即被拒；主写法全部提交按这一步写法的粒度；主写法被拒而退路提交为粗粒度；其余为被拒。前提在这条线上没成的（段没划成、问题没提出成）记为被拒，错误码 `SEGMENT_NOT_PLANNED`、`ISSUE_NOT_RAISED`，不发动作。

## 4. 运行日志

每条线一个运行日志 `<目录>/b-run.json`（`format` 为 `tkos-world-02-experiment-b-run/0.1`），播种、划段、脚本的每一步都记在里面，是统计的唯一输入；不含凭证与显示名：

- `line`、`scope_id`、`lines_sha256`（lines 文件与原计划合在一起的哈希）、`script`（脚本 id 与哈希）；`principals`：主体 id → 角色键与类型。
- `objects`：播种建出的对象与 Task+Activity 线的 Activity（`segment:<段>`）；`seed`：共同播种每一步的回执、事件与对象。
- `steps`：划段（`phase` 为 `plan`，键 `plan:<段>`）与脚本（`phase` 为 `script`，键是步号）的每一步：`do`、`by`、目标（段所在的 Task 与这个 Task 有几段）、`writing`（`activity`、`plan_item`、`task_level`、`task_subject`、`task`、`mission`、`issue`）、`attempts`（每个动作：`primary` 主写法、`fallback` 退路、`support` 配套；动作名、主体、行动者、幂等键，提交了的带回执、事件、回执时刻，被拒的带入口、状态码、错误码）、`expression`、`error_codes`、`merged_into`。
- `pending`：已提交、还没记进步骤的请求体，给中断后重发用，跑完为空。
- `evidence`（取证后）：这条线碰过的每个对象（含快照）经取事件读到的事件（种类、主体、记录者与类型、detail、action_id、时刻、迟记），业务对象的生命周期、责任人与计划条目（每条计划条目读回的非空属性：责任人与写了的四个可选属性，#83 起；此前只记责任人），快照的主体、时点与组件。

两条线按步骤键一一配对；一个 scope 只跑一份脚本（换脚本要新开 scope）。播种内容改了（`lines_sha256` 不同）也要新开 scope。

## 5. 五项观测

统计在 `b_observe.py`，纯函数。每项观测先在 Task+Activity 线的事件上找发生，判它是不是**独立发生**（Task 一级承载不了），再按运行日志找到产生它的那一步，取 Task-only 线同一步的表达结果。事件顺序、当时的责任人与生命周期按运行日志的顺序由事件推出（指派事件的 `detail.principal_id`、生命周期事件的种类）。

| 观测 | 问题 | 记录来源（Task+Activity 线的事件） | 独立发生 |
|-|-|-|-|
| 指派 | 是否要把一段单独指派给别人或 Agent | Activity 的 `assign`（划段与脚本的指派） | 被指派者不是当时这个 Task 的责任人 |
| 执行 | Agent 是否需要以 Activity 为主体写入 | Agent 记的 `start`、`deliver`、`event.recorded`、`state.refreshed`，主体是这个 Agent 当时负责的 Activity | 都算：Agent 不能是 Task 的责任人，Task 一级承载不了 |
| 重试 | 失败后是否需要 Activity 自己的退回与重开 | Activity 的 `reject`、`reopen` | 不与 Task 的同名事件配对 |
| 验收 | 是否需要在 Activity 一级验收 | Activity 的 `accept` | 不配对，且验收人不是这一段的责任人（自己验自己不算） |
| 管理 | Issue 与 State 是否需要指向 Activity | 主受影响对象是 Activity 的 `issue.raised`，主体是 Activity 的 `state.refreshed` | 这个 Task 有两段以上（只有一段时以 Task 为主体是同一粒度） |

- **配对**（重试、验收）：Task 只有这一个 Activity，且 Task 有第 i 条同名事件配这个 Activity 的第 i 条——两者是同一单位的同一次转移。
- 发问题的 Co-Agent 不是这一段的责任人，它的外部事件与快照算管理、不算执行。
- 每项输出：`occurred`（Task+Activity 线上是否独立发生）；`task_only`（独立发生的那些步在 Task-only 线上最差的表达：表达不了 > 粗粒度 > 原生，没有独立发生为空）与逐类计数；`evidence`（Task+Activity 线的事件 id 与日志步键）；`coarse`（粗粒度的那些步，写法与事件）；`occurrences`（每条发生、是否独立与理由，连同 Task-only 线那一步）。
- **Agent 写入需要以谁为主体**（`agent_subject`）：逐条列出行动者是 Agent 的步骤在两条线上写到哪类主体、表达结果。有 Agent 的写入在 Task+Activity 线以 Activity 为主体写成、在 Task-only 线表达不了，为 Activity；否则取 Task-only 线写成的主体类型；没有 Agent 的步骤为空。
- 另给两条线的表达结果计数、逐步对照表，以及 Task+Activity 线上被拒的步骤（脚本本身写错了的，单列，不进观测）。

**Content Pact 的新形状不改五项观测的口径（#83）**：统计只读两条线的事件、每步的表达结果与运行日志的顺序，不读块 id，也不读计划条目的属性，所以旧块换成新块、计划条目多出四个可选属性，都不改变哪一项算发生、哪一步算独立、Task-only 线那一步是原生、粗粒度还是被拒。新形状改变的是 Task-only 线「一段」写下了什么：计划条目除了责任人，还带这一段的预期产出、质量标准、执行主体与分工，与 Task+Activity 线 Activity 的执行事项、预期产出、成功 / 验收标准对得上；但这四个属性与责任人一样只作记录、不判权，Activity 一级的生命周期、验收与写入权限仍然没有，所以段上的指派、生命周期与验收照旧是粗粒度，执行 Agent 仍改不了 Task 的计划条目。冒烟在新库上按新块重录后与旧日志逐步对照：表达结果、五项观测的发生与独立、依据步骤、`coarse` 清单、Agent 写入的主体与结论完全相同（第 7 节）。

## 6. 结论规则与粗粒度

**结论**：五项中有独立发生、且 Task-only 线表达不了的，Activity 留作对象；否则降为组件。输出 `conclusion.activity`（`object` 或 `component`）与 `because`（让它留作对象的观测）。

**表达不了 = 被拒**：Task-only 线同一步被协议拒绝，一条记录都落不下。**粗粒度算能表达**：它把这一步写成计划条目的修改，或以 Task 为主体的外部事件、快照与问题，谁做了哪一段、这一段要交什么、按什么标准（计划条目的四个可选属性，#83）、什么时候、说了什么都在，损失的是 Activity 一级的生命周期与权限（计划条目的责任人与四个可选属性只作记录，契约第 4 节）。这部分损失逐条列在每项观测的 `coarse` 里，给报告复核，不改结论。理由：票上把「表达不了」与「只能粗粒度表达」分开记，结论规则只点名前者；Task-only 的设计本来就是用计划条目承载「谁做哪一段」。

报告若要按更严的口径（段的生命周期只能写成计划条目的文字也算表达不了），用 `coarse` 清单与逐步对照表复核即可，统计不必改。

## 7. 冒烟脚本与它的结果

`b_smoke.json`（44 步，内容合成）把两条线都推到 Mission 关闭：联调段退回一次再交付，每周快照改指派给执行 Agent，Agent 开始、写进展、交付，被退回、重交、验收，之后重开再交；问题流转段上 Co-Agent 提出问题，路由给 Owner，Owner 承接、本层处理；联调验收只有一段；最后 Mission 状态刷新、交付，DRI 验收通过。隔离库上跑出的结果（录成 `tests/fixtures/world_v02_experiment_b/`；2026-09-30 按新块在新库 `tkos_a1_method_f8bb68e5e02b46a8` 上重录，#83，两条线读回核对无问题，下面的数字与重录前逐步相同）：

- Task+Activity 线 49 步（划段 5、脚本 44）全部原生；Task-only 线原生 20、粗粒度 21、被拒 8，被拒的都是 `FORBIDDEN`：eo-ic 不在「联调与委托登记」的主干上，改不了它的计划条目、写不了它的快照；执行 Agent 不能修订 Task（无门对象只由主干上的责任人修订），它开始、交付自己那一段连计划条目都改不了。
- 指派：独立发生（联调段指派给 eo-ic，每周快照改指派给执行 Agent），Task-only 粗粒度。
- 执行：独立发生，Task-only 表达不了（Agent 的开始与交付被拒；它的进展以 Task 为主体写成，粗粒度）。
- 重试：独立发生（两次退回、一次重开），Task-only 粗粒度。
- 验收：独立发生（Task 责任人验收别人负责的段），Task-only 粗粒度；自己验自己的两段、只有一段且与 Task 验收配对的一段不算。
- 管理：独立发生，Task-only 表达不了（eo-ic 写不了联调段所在 Task 的快照）；只有一段的联调验收的快照不算。
- 结论：Activity 留作对象（执行、管理）；Agent 写入需要以 Activity 为主体。

这是冒烟脚本的结果，证明代码与口径跑得通，不是实验结论；结论看试用期间转写的真实记录（票 #68 的报告）。

## 8. 试用期间的做法：转写与回放

1. 试用在真实 scope 里照常进行，参与者只记一遍。
2. 10/16 联调验收之后，开发会话用 `b_transcribe` 把真实 scope 里 `mission_context` 这场 Mission 的记录转写成两条线共用的 lines（`b-lines.json`）与执行脚本（`b-script.json`），另出一份审阅稿（`b-review.md`）交 E&O DRI 审（#75，命令见第 9 节）。
3. 审过之后，两条线各新开一个 scope（按 `b_spec.json` 供给；冒烟用过的 scope 不再用：一个 scope 只跑一份脚本），用转写的 lines 播种、转写的脚本驱动，取证，算观测，结果写进票 #68 的报告。

隔离库上有一条可重跑的预演（`b_rehearse`，第 9 节）：仿一个真实 scope、按天枢的写法在 `mission_context` 上造一段试用记录，转写，再两条线回放。

### 8.1 读什么

只读：只发 GET（取对象、取事件、列对象），不写真实 scope 的任何东西；凭证从一个文件读，不打印。读出：这条 Mission 与它的周期目标；列对象后按 `parent_ref` 筛出挂在它下面的 Task 与 Task 下的 Activity，Task 与 Activity 另取每个修订；这些对象的全部事件；事件里出现的快照（状态刷新的快照、问题所在的快照）。读到的原样（含显示名）存成私有的 `bundle.json`，之后可以不连服务重写。

### 8.2 主体换成角色键

对照表私有、不进仓库：真实 scope 的 `ids.json` 的 principals 照抄，每个主体加 `role`。人是 `ceo`、`eo-dri`、`eo-owner`、`eo-ic`（CEO → `ceo`，E&O DRI → `eo-dri`，另一位 Mission Owner → `eo-owner`，其余执行的人 → `eo-ic`）；Agent 写 `agent`（按写入转写），或钉成 `exec-agent`、`eo-coagent`。

- 天枢代记的写入（带 `on_behalf_of`）按被代记的人转写；被代记的只能是人。
- 人以自己身份的写入按对照表；对照表的 `role` 与事件里的主体类型（人或 Agent）要一致。
- Agent 以自己身份的写入：`role` 为 `agent` 的按写入转写——提出、路由、退回形成问题是 `eo-coagent`（Co-Agent 的事），其余（开始、交付、快照、外部事件）是 `exec-agent`（执行）；钉了角色键的照钉的。被指派者、计划条目的责任人是按写入转写的 Agent 时当 `exec-agent`。
- 记录里出现、对照表里没有的主体，报错。

### 8.3 共同播种：门、Task 与段

- **门**进共同播种，不扩展脚本动词：周期目标与这条 Mission 的形成门（月度计划签发 = DRI 承诺 + CEO 确认；任务卡确认 = Owner 承诺 + DRI 确认），按记录顺序、到第一次确认接受为止（中间的退回也照记），写进 lines 的 `steps`。门在两条线上相同、不进五项观测；`b_seed` 本来就会播门（冒烟的主干里就有），`b_drive` 与 `b_observe` 为此一行不改；提前到脚本之前也不改变回放：Task 的生命周期不看 Mission 的状态，Mission 的开始要它已成立，本来就在门之后。形成之后的门（一轮重走带候选，播种写不了候选）与再确认不转写，列进审阅稿。
- **Task**：挂在这条 Mission 下、指派过的 Task 按建立顺序编为 `task_1`、`task_2`……，建立写进 lines 的 `steps`（由建它的人记；正文取第 1 版的任务定义 `definition`、Task 计划 `task_plan` 等块，不带 Activity 全景块 `plan` 与外部引用，组件只留 id、类型、文字与指回主干的引用，例如指回 Mission 战役定义块验收标准的 `@mission_context#definition/ac-3`）。首次指派定 `tasks[].responsible`，由 Mission Owner 在共同播种里记（真实记录里不是 Mission Owner 记的，审阅稿提醒）；之后的再指派写 `assign` 步骤。从没指派过的 Task 与它下面的 Activity 不进回放，列进审阅稿。真实记录里这条 Mission 的 Owner 与主干不一致时报错。
- **段**：Task 建立时（或首次指派之前）计划块里的计划条目是初始段（`tasks[].segments`，键 `<task>.s<n>`），由 Task 的责任人划，同冒烟。首次指派之后新加的计划条目写 `plan` 步骤、改了责任人的写 `assign` 步骤，都由 Task 当时的责任人记：Task+Activity 线上 Activity 由 Task 的责任人建并指派，由别人记会被拒，那是转写造成的，不是协议。Activity 的首次指派即划段（`plan`，由指派的人记），之后的指派是 `assign`。计划条目改文字、改四个可选属性、删掉条目不转写。Task 下没有计划条目也没有 Activity 的，不划段。
- **段带的属性**（#83）：计划条目写了的预期产出、质量标准与分工照抄进段（初始段写进 `tasks[].segments`，之后的写进 `plan` 步骤）；执行主体整个正是对照表或真实 scope 里某个主体的显示名时换成它的角色键（按写入转写的 Agent 当 `exec-agent`），不是就照抄——夹着显示名的仍由第 8.5 节的拦截拦下。Activity 划出的段：`instruction` 块的文字与执行事项 `work_definition` 组件是段的正文，预期产出 `expected_output`、成功 / 验收标准 `acceptance_criterion` 组件是段的预期产出与质量标准；其余组件（贡献、时间边界、执行边界、执行约束）计划条目没有对应属性，回放不带，审阅稿「请审的判断」里逐条提醒。审阅稿的段表列出每段带的属性。

### 8.4 执行脚本

按记录顺序（事件的记录时刻），格式 `tkos-world-02-experiment-b-script/0.2`，每步的 `note` 是它所转写的真实事件。

- **生命周期**：Mission、Task 与段（Activity）的开始、交付、验收通过、退回、重开、取消，写同名步骤。退回没写理由时写「（真实记录没有写理由）」，审阅稿提醒。生命周期与指派不能补记，回放时按原顺序在回放时刻记下：保留顺序、不保留时间。
- **快照**：进展条目（`progress_item`）按组件 id 对上段（计划条目 id、Activity 的外部引用）或 Task（Task 的外部引用，天枢写 `todo:<uuid>`）的，写成那一处的 `progress`；对不上的与块里的文字（进展块的文字、阻碍、没提出的问题）合成快照主体上的一步（有对不上的进展条目为 `progress`，否则 `refresh`）。时点取快照真实的 `as_of`（补记的快照读侧会标迟记）；同一张快照拆出的第 i 步加 i 微秒，因为同一主体同一时点只能有一条快照，拆出的几步在 Task-only 线上可能落到同一个 Task。只带问题、问题都提出了的快照并入提出；快照的来源外部事件并入这一步（驱动器另记一条）。
- **问题**：提出、路由、承接、处置、退回形成写 `raise_issue`、`route_issue`、`own_issue`、`dispose_issue`、`return_issue`；问题键取原问题组件的 id（不同主受影响对象用了同一个 id 时依次加 `.2`、`.3`）；提出的时点取提出事件的发生时刻，核心判断问题取问题组件的 `core_question`。
- **不转写**，列进审阅稿：外部事件（会议、交付类、验收类记录等）、Mission 与 Activity 的修订、Task 计划条目以外的修订、计划条目改文字或四个可选属性、Mission 的再指派、形成之后的门与再确认、关注标记、建立跨链关系、更正；撤回的事件连同撤回本身一起略去（两者抵消）。

### 8.5 输出与显示名拦截

三份产物只有角色键：`b-lines.json`（lines 0.2：主干照抄 `b_lines.json`，加门与建 Task、Task 的责任人与初始段）、`b-script.json`、`b-review.md`（审阅稿：共同播种、逐步的执行脚本与来源事件、段的来源、未转写清单、请审的判断）。写之前先核对产物能直接用（`b_seed.derive`、`b_drive.check_script`），再逐份查显示名：对照表里的显示名，以及真实 scope 给这些主体的显示名，出现在任一产物里就报出现在哪（文件、步骤与字段或行号、是谁的——只写角色键），以 1 退出，什么也不写。文字照真实记录抄、不替换；进展条目写入时的姓名与主体 id 不抄。

## 9. 命令

### 隔离库上的冒烟

前提同 0.2 验收：隔离验收栈在跑（`python3 acceptance/runtime/infra.py status`），基础 env 是仅本人可读的 `.runtime-acceptance/env.json`；不连 54350/54351 的 Clark 联动栈，不对共享库跑迁移。不打印 env、DSN 或凭证。

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.method_v05.database create --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/world-v02-db-$STAMP --output artifacts/runtime-acceptance/world-v02-db-$STAMP
.venv/bin/python -m acceptance.method_v05.database upgrade --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --output artifacts/runtime-acceptance/world-v02-db-$STAMP-upgrade
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m experiments.world_v02.b_seed smoke --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --private .runtime-acceptance/world-v02-b-$RUN --output artifacts/runtime-acceptance/world-v02-b-$RUN
```

`smoke` 新开两个随机 tenant 的 scope（owner SQL 播种身份与凭证，控制面 CLI 装 0.2），起真 API，两条线播种（默认 `b_smoke_lines.json`，`--lines` 换）、读回核对、按脚本（默认 `b_smoke.json`，`--script` 换）驱动、取证，再算观测。`--private` 下每条线一个目录（`ids.json`、凭证、运行日志），`--output` 下是两条线的运行日志、`observations.json` 与 `summary.json`。两条线的 Mission 都关闭、读回核对没有问题时退出码为 0。同一个库可以反复跑，每次都是新的 scope。

### 隔离库上的试用回放预演（#75）

前提同上，库同上（`$STAMP` 是 method_v05 database create/upgrade 用的那次）：

```sh
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m experiments.world_v02.b_rehearse --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --private .runtime-acceptance/world-v02-b-rehearse-$RUN --output artifacts/runtime-acceptance/world-v02-b-rehearse-$RUN
```

- 仿真实 scope：按 `deploy/world-02/spec.eo.example.json` 的名单（键与显示名都是角色名）供给一个随机 tenant 的 scope，用 `deploy/world-02/seed_eo.py` 按人分段播十月起点（委托的到期时刻已过或不到一天时顺延一周）。
- 在 `mission_context` 上照天枢的写法造一段试用记录（请求形状同 `deploy/world-02/examples.py`，块按 Content Pact）：外部引用与执行计划的 `todo:` 计划条目、代记的门（任务卡确认第一次退回）、三个带外部引用的 Task（任务定义块三种写法；一个带 Task 计划块与 Activity 全景块，计划条目带可选属性，一条的执行主体正是名单上的显示名，一个取消）、代记的指派与生命周期（打回、重开）、Owner 的 Agent 开始 Mission、Task 下交给执行 Agent 的 Activity（执行事项、预期产出与验收标准组件）、周快照（三条进展条目、一个问题）与会议事件、代记的议题承接、退回形成与处置、Co-Agent 以自己身份在 Task 上提问题、Task 责任人本人改计划条目与写进展、Co-Agent 再加一条执行计划，最后代记 Mission 交付与验收。
- 以 ceo 的凭证经 `b_transcribe` 只读取回并转写（对照表按名单生成，天枢按写入转写），再按 `b_spec.json` 另开两个 scope，用转写的 lines 播种、转写的脚本驱动、取证、算观测。
- 通过：转写成功（含显示名拦截）；两条线播种核对无问题、Mission 都关闭；Task+Activity 线没有被拒的步骤（仿真实 scope 里记得下的，这条线都记得下）；仿真实 scope 里 Mission、每个 Task 与 Activity 的生命周期与两条线读回一致。`--private` 下是三个 scope 的凭证、读取原样与对照表；`--output` 下是转写产物（`transcript/`）、两条线的运行日志、`observations.json` 与 `summary.json`。全部通过退出码为 0，同一个库可以反复跑。

### 实例 scope

变量同 `deploy/world-02/README.md`：`SRC`（主机上的源码，构建提交要含 `experiments/world_v02/`）、`LAB`（env 与输出）；`U` 是实例地址，`B` 是本机放两条线凭证与运行日志的目录（例如 tkos-secrets 克隆里的一个目录）。

在主机上各供给一个 scope（第 4–5 节的脚本，每条线一遍，输出目录先 `chown` 给 10001，跑完收回）：

```bash
SRC=$SRC LAB=$LAB SPEC=$SRC/experiments/world_v02/b_spec.json OUT=$LAB/out-b-task-only bash $SRC/deploy/world-02/provision-and-install.sh
SRC=$SRC LAB=$LAB SPEC=$SRC/experiments/world_v02/b_spec.json OUT=$LAB/out-b-task-activity bash $SRC/deploy/world-02/provision-and-install.sh
```

把两个输出目录里的 `ids.json` 与 `*.token` 取回本机的 `$B/task-only`、`$B/task-activity`（同第 10 节的归档方式），然后在本机仓库检出里经域名跑（脚本只用标准库；凭证只从文件读，不上命令行、不打印）：

```bash
python3 -m experiments.world_v02.b_seed seed $U $B/task-only --line task_only
python3 -m experiments.world_v02.b_seed seed $U $B/task-activity --line task_activity
python3 -m experiments.world_v02.b_drive drive $U $B/task-only          # 默认冒烟脚本；回放用 --script <转写的脚本>
python3 -m experiments.world_v02.b_drive drive $U $B/task-activity
python3 -m experiments.world_v02.b_drive collect $U $B/task-only
python3 -m experiments.world_v02.b_drive collect $U $B/task-activity
python3 -m experiments.world_v02.b_observe $B/task-only/b-run.json $B/task-activity/b-run.json --output $B/observations.json
python3 -m experiments.world_v02.b_drive status $B/task-only           # 只看运行日志，不连服务
```

### 试用回放（10/16 联调验收之后）

在本机仓库检出里经域名跑，只用标准库；`R` 是真实 scope（world-02 实验 scope）的清单与凭证目录，即 tkos-secrets 克隆里的 `ontology-runtime/world-02/`（`deploy/world-02/README.md` 第 10、11 节，`ids.json`、`<键>.token` 与 `seed-eo-state.json` 都在这里），`B` 同上。

1. 对照表（私有，放 `$B`）：照抄 `$R/ids.json` 的 principals，每个主体加 `role`（第 8.2 节）。
2. 转写：凭证用在 scope 里有生效角色的任一主体的（转写只发 GET）；`--mission` 是 `mission_context` 在实例上的 object_id（`$R/seed-eo-state.json` 的 `steps.mission_context.object_id`）。

   ```bash
   python3 -m experiments.world_v02.b_transcribe transcribe $U --token-file $R/eo-dri.token --mission <object_id> \
     --principals $B/principals.json --private $B/read --output $B/transcript
   # 改了对照表，或只想重出产物：从存下的读取原样重写，不连服务
   python3 -m experiments.world_v02.b_transcribe write $B/read/bundle.json --principals $B/principals.json --output $B/transcript
   ```

3. E&O DRI 审 `$B/transcript/b-review.md`；要改的是转写规则或个案，改了重跑第 2 步。
4. 两条线另开新 scope（同上面「实例 scope」的供给），用转写产物播种、驱动：

   ```bash
   python3 -m experiments.world_v02.b_seed seed $U $B/task-only --line task_only --lines $B/transcript/b-lines.json
   python3 -m experiments.world_v02.b_seed seed $U $B/task-activity --line task_activity --lines $B/transcript/b-lines.json
   python3 -m experiments.world_v02.b_drive drive $U $B/task-only --script $B/transcript/b-script.json
   python3 -m experiments.world_v02.b_drive drive $U $B/task-activity --script $B/transcript/b-script.json
   ```

   之后 `collect`、`b_observe` 同上；`observations.json` 经 `experiment summarize --b-observations` 进 #68 报告的 B 一节。

`seed` 播完即读回核对（`check` 可以单独再跑；驱动过之后两者都跳过读回核对，只打印状态）。每个命令都可以重跑：记过的步骤不再做；中途断了（连不上、服务端 5xx），同一个幂等键原样重发，已提交的拿回原回执。运行日志与凭证放在同一个目录，照 E&O 十月起点播种的状态文件那样归档。

## 10. 验证

- 无库测试 `tests/test_world_v02_experiment_b.py`：共同播种就是原计划里 mission_trial 的主干、名单只有角色名且过 `provision.check_spec`、段与角色合登记；冒烟的段只带正文里原样写着的可选属性、写了的不能为空；脚本校验（只有 `plan` 带四个可选属性）；驱动器按线的写法（假 HTTP）：Activity 与它的 `instruction` 组件、带可选属性的计划条目（每次改都整条带上）、只有一段的 Task 与配对、退路、快照前的外部事件、问题引用、行动者不换人；取证里 Task-only 线的计划条目带段的属性、Task+Activity 线的 Task 计划块为空；三种表达结果；传输（假 HTTP）：被拒是结果不是错误，提交中途断掉原样重发拿回原回执，重发被拒则重新 prepare，服务端错误留着请求；用录好的冒烟运行日志核对五项观测的发生与未发生（去掉对应步骤）、独立的判定、Task-only 线的三种表达、结论规则（粗粒度不留对象、不独立的不留对象、五项都没发生）、Agent 写入的主体、两份日志必须是同一次对照。
- 独立验收 `acceptance/world_v02/` 的场景 `experiment_b`：隔离库上两条线播种、读回、按冒烟脚本推到 Mission 关闭，经读投影与只读 SQL 核对（见该目录 README）。按新块（#83，2026-09-30，分支 `world/0.2-pact-83`）：新库 `tkos_a1_method_f2b0d9f281cd4ba6` 上整跑 871 项全过（25 组都跑完，其中 `experiment_b` 11 项，矩阵 445 格覆盖 440、不适用 5）；同库回归库 `tkos_a1_world_f2063232ec014d29` 上 0.1 的 248 项与四个门槛全过，其后 0.2 同样 871 项全过（库里原有的 125 条 0.1 事件行只被校验）。
- 实例上的冒烟（2026-09-29，world-02 跑 c29e7d5 镜像，实验代码取 d82d176）：
  - 按上面「实例 scope」的命令，在主机上各供给一个 scope：Task-only 线 `885495de`，Task+Activity 线 `1816d844`，tenant `tokenking-world-02-experiment-b`。
  - 在本机经域名跑完 seed、drive、collect、observe、status，全部退出码 0，两条线的 Mission 都读回已关闭。
  - 结果与隔离库上录的一致：Task+Activity 线 49 步全部原生；Task-only 线原生 20、粗粒度 21、被拒 8；结论为 Activity 留作对象（执行、管理）。
  - 两条线的清单、凭证、运行日志与观测曾归档在 tkos-secrets 1c24e00 的 `ontology-runtime/world-02/experiment-b-smoke/`，运行日志与观测可从那个提交取回。
  - 同日按 55c46d3 重建（#70）后，这两个 scope 连同凭证作废，归档目录在 tkos-secrets 7bb3023 删除；试用回放要另开两个新 scope。
- 试用回放的转写（#75）：
  - 无库测试 `tests/test_world_v02_transcribe.py`：读取原样 fixture（`tests/fixtures/world_v02_transcribe/`，隔离库上 `b_rehearse` 仿真实 scope 造的试用记录经 `b_transcribe` 只读取回；2026-09-30 按新块在新库 `tkos_a1_method_f8bb68e5e02b46a8` 上重录，#83）核对读取只有 GET、按 parent_ref 筛出这场 Mission、凭证不进读取原样；逐步的脚本（行动者与目标）、门与 Task 的共同播种（Task 的任务定义块与 Task 计划块，指回 `@mission_context#definition/ac-3`）、段带的可选属性（执行主体是显示名的换成角色键，夹着显示名的被拦下）、Activity 组件对应段的正文与属性、属性改动与 Activity 其余组件列进审阅稿、代记还原、快照的真实时点与拆分、问题流转（含代记的承接、退回形成与处置）、未转写清单、撤回一起略去、Owner 与主干不一致报错、对照表缺主体或角色不合报错、显示名拦截（对照表的与真实 scope 给的，报出位置、不写文件）；转写产物能被 `b_seed.derive` 与 `b_drive.check_script` 直接用。`tests/test_world_v02_experiment_b.py` 另核对回放的主干就是十月起点里 `mission_context` 到 Company 的 10 步、lines 0.2 的门与 Task、播种后 Mission 的生命周期、脚本 0.2 的取消与退回形成。
  - 隔离库预演（2026-09-29，本票分支）：`b_rehearse` 全过：转写出执行脚本 38 步、Task 3 个（初始段 2 段，脚本里划段 2 步）、未转写 4 条；Task+Activity 线 40 步全部原生；Task-only 线原生 33、粗粒度 6、被拒 1（执行 Agent 交付自己那一段，`FORBIDDEN`）；两条线的 Mission、Task、Activity 的生命周期与仿真实 scope 一致。这是预演的合成记录，不是实验结论。
  - 加了 eo-dri 的 OWNER 之后在同一个库上重跑冒烟，结果不变（Task-only 线原生 20、粗粒度 21、被拒 8，结论同第 7 节）。
  - 按 Content Pact 的新块（#83，2026-09-30，分支 `world/0.2-pact-83`，隔离栈新库 `tkos_a1_method_f8bb68e5e02b46a8`）：`b_rehearse` 全过，四项判定（播种读回、Mission 关闭、Task+Activity 线无被拒、生命周期与仿真实 scope 一致）都为真；转写出执行脚本 38 步、Task 3 个（初始段 2 段，脚本里划段 2 步）、未转写 4 条、请审 0 条；Task+Activity 线 40 步全部原生；Task-only 线原生 33、粗粒度 6、被拒 1，与改块前相同。段带的属性在两条线上都落下了（初始段 `task_1.s1` 的执行主体由显示名换成 `eo-owner`，`task_1.s2` 带齐四项；Activity 划出的段带预期产出与质量标准）。同一个库上冒烟也重跑并重录（第 7 节）。这仍是预演的合成记录，不是实验结论。
- 未验证：真实记录的转写与回放，要等 10/16 之后；实例上的对照表、读凭证与 Mission id 由转写时填。

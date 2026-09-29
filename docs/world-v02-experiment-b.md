# tkos.world/0.2 对照实验 B：Task-only 与 Task+Activity

票 #66，规格 #46 的用户故事 77、78 与第十节。契约把 Activity 登记为候选类型（第 3.1 节，决 4），去留由这个对照实验决定：同一场真实 Mission 分两条线执行到关闭，看 Activity 是否需要独立的指派、执行、重试、验收与管理。结论是 Task-only 时，Activity 类型作废，它的内容改由执行计划块里带责任人的计划条目表达（第 4 节，决 8）。

实验代码在 `experiments/world_v02/`（本票的文件带前缀 `b_`），做法按 E&O 在票上定的：两条线各用一个角色名身份的独立 scope，不动真实 scope 的数据，也不要试用参与者多记一遍；两条线从同一份 Mission 内容播种；执行用一份与线无关的执行脚本描述，驱动器按各线的原生写法落下去，记下某条线表达不了或只能粗粒度表达的地方；五项观测由统计代码从两条线的事件、快照与驱动器记录机械算出。

## 1. 同一场 Mission

对照对象是 9 月 29 日选定的「天枢 × 本体 0.2 试用」：`deploy/world-02/seed-eo-2026-10.json` 里的 `mission_trial`，10/9–16 联调与试用，10/16 联调验收即关闭。它下面三个 Task：`task_trial_integration`（联调与委托登记，10/9–11）、`task_trial_week`（试用周每周快照与问题流转，10/12–16）、`task_trial_acceptance`（10/16 联调验收）。

两条线的播种分两段，第一段完全相同（`b_seed.py`，定义在 `b_lines.json`）：

1. **共同播种**：照原计划播 `mission_trial` 到 Company 的主干——Company、Strategy（存根）、E&O 责任单元、公司级与 E&O 长期目标（确认）、十月周期目标（承诺、确认）、Mission（指派 Owner、承诺、确认，已成立）与三个 Task。步骤、正文与顺序原样取自原计划（`source.steps` 列出要的步骤，前置由 `seed_eo.check_plan` 核对），另外两个 Mission、它们的 Task 与三条委托不带。然后 Mission Owner 把三个 Task 指派给 `tasks[].responsible`，把 Mission 的执行计划块写成每个 Task 一条带责任人的计划条目（引用该 Task）。
2. **划段**：每个 Task 下「谁做哪一段」的初始划分（`tasks[].segments`），按线的写法落下：

| | Task-only 线 | Task+Activity 线 |
|-|-|-|
| 段 | Task 计划块里带责任人的计划条目（组件 id 即段键） | Task 下的 Activity，由 Task 的责任人建并指派 |
| Task 的计划块 | 各段的计划条目 | 空 |
| Task 之上 | 相同：Mission 执行计划块每个 Task 一条带责任人的计划条目 | 相同 |

Task 之上的对象在两条线上同形（验收逐块核对）；两条线只差在 Task 以下。段的初始划分按各 Task 的定义拆，是实验用的提议，试用期间以转写的真实记录为准：

| Task（责任人） | 段 | 责任人 |
|-|-|-|
| 联调与委托登记（eo-owner） | `integration` 联调 | eo-ic |
| | `delegation_setup` 委托登记 | eo-owner |
| 试用周（eo-ic） | `weekly_snapshot` 每周快照 | eo-ic（冒烟里改指派给 exec-agent） |
| | `issue_flow` 问题流转 | eo-ic |
| 联调验收（eo-owner） | `acceptance_run` 联调验收 | eo-owner |

身份一律是角色键（`b_spec.json`，`deploy/world-02/provision.py` 的格式）：`ceo`（公司域与 E&O 域的 CEO）、`eo-dri`（DOMAIN_DRI、IC）、`eo-owner`（Mission Owner：OWNER、IC）、`eo-ic`（IC）、`eo-coagent`（E&O 的 Co-Agent，AGENT）、`exec-agent`（执行 Agent，AGENT）。显示名也是角色名，仓库里不出现真名与真实账号。

## 2. 执行脚本

执行脚本（`format` 为 `tkos-world-02-experiment-b-script/0.1`，例子是 `b_smoke.json`）是与线无关的一串步骤，按出现顺序从 1 编号。每步：

- `do`：`plan`（划出一段并定责任人）、`assign`（指派）、`start`、`deliver`、`accept`、`reject`、`reopen`（生命周期）、`progress`（在某一段上写进展）、`refresh`（状态刷新）、`raise_issue`、`route_issue`、`own_issue`、`dispose_issue`（问题流转）。
- `by`：行动者的主体键。
- 目标四选一：`segment`（段键）、`task`（Task 的步骤键）、`mission`（`true`）、`issue`（问题键，路由、承接、处置用）。
- 按 `do` 另带：`to`（`plan`、`assign`、`route_issue`），`task` 与 `title`（`plan`），`text`（正文：进展、状态、问题、理由；`reject`、`progress`、`refresh`、`dispose_issue` 必带），`question`（`raise_issue` 的核心判断问题），`disposition`（`dispose_issue`，六类处置之一）。
- 可选：`as_of`（`progress`、`refresh`、`raise_issue` 的时点，回放真实记录时给），`declaration`（Agent 写入声明的 `trigger` 与 `acceptor`），`note`（说明，不写入）。

`b_drive.check_script` 在驱动前整份校验，列出全部问题：目标与 `do` 相配、段已划出、问题已提出、主体键都在 `ids.json` 里、正文非空、处置是登记的六类之一。

## 3. 两条线的写法

驱动器（`b_drive.py`）把每一步按这条线的原生写法落下，逐个发动作（prepare 再 commit），记下动作、回执与事件 id、表达结果。**行动者一律是脚本写的那个人**：某种写法要别人来记才成立时，结果就是协议的拒绝，不换人。

| 步骤 | 目标 | Task+Activity 线 | Task-only 线 | Task-only 的表达 |
|-|-|-|-|-|
| `plan` | 段 | 建 Activity（`instruction` 块写段的正文），再指派给 `to` | 修订 Task：计划块加一条计划条目，`responsible` 是 `to` | 粗粒度 |
| `assign` | 段 | 再指派 Activity | 修订 Task：改这条计划条目的 `responsible` | 粗粒度 |
| 生命周期 | 段，Task 有两段以上 | 这个 Activity 的同名生命周期动作 | 修订 Task：这条计划条目的正文加一行「状态：…（执行脚本第 n 步）：理由」 | 粗粒度 |
| 生命周期 | 段，Task 只有这一段 | 同上 | 这个 Task 的同名生命周期动作；被拒则同一人改计划条目的状态（退路） | 原生；走退路为粗粒度 |
| `progress`、`refresh` | 段 | 外部事件（主体是 Activity）＋ Activity 的执行状态快照 | 外部事件（主体是计划条目的组件引用）＋ Task 的执行状态快照，内容引用计划条目 | 只有一段为原生，两段以上为粗粒度 |
| `raise_issue` | 段 | 外部事件＋ Activity 快照 issues 块里的问题组件＋提出 | 外部事件＋ Task 快照里的问题组件（引用计划条目）＋提出 | 同上 |
| 路由、承接、处置 | 问题 | 同一动作，`issue_ref` 取这条线上提出它的那一步 | 同左 | 原生 |
| 任一 | Task、Mission | 同一动作 | 同一动作（见下面的配对） | 原生 |

- **配对**：Task-only 线里只有一段的 Task，这一段与 Task 本身是同一个单位。段上的生命周期步骤与 Task 一级的同名步骤按次序配对（第 i 条段的 `deliver` 配第 i 条 Task 的 `deliver`），先到的写成 Task 的生命周期动作，后到的并入它（记 `merged_into`、不发动作，表达为原生）。先到的被拒（例如段的验收人不是 Mission Owner），后到的照常写。Task+Activity 线不配对：Activity 与 Task 是两个对象，各记各的。
- **快照的来源事件**：快照至少一条来源事件（契约补 10），所以进展、状态刷新与提出问题都先记一条 `category` 为 `other` 的外部事件，正文是这一步的 `text`，再以它为来源写快照。外部事件的发生时刻取这条线最晚的回执时刻（服务端时钟，不早于这条线上任何事件，又已经过去），快照的时点取外部事件回执的时刻，所以都不迟记、同一主体的时点不重复；脚本给了 `as_of` 就都用它。
- **写入声明**：Agent 的每个写入都带声明，场景是这一步在这条线上的主体（Activity 或 Task）；脚本没给就用默认的触发说明、不要求人工验收。承接与处置只由人记，不带声明。
- **表达结果**：一步在一条线上是**原生**（同粒度表达）、**粗粒度**（只能在 Task 一级表达：只能改计划条目，或只能以 Task 为主体记下）或**被拒**（协议拒绝，记下每个被拒动作的错误码）。判定：并入配对的另一半为原生；配套动作（快照前的外部事件、提出前的快照）被拒即被拒；主写法全部提交按这一步写法的粒度；主写法被拒而退路提交为粗粒度；其余为被拒。前提在这条线上没成的（段没划成、问题没提出成）记为被拒，错误码 `SEGMENT_NOT_PLANNED`、`ISSUE_NOT_RAISED`，不发动作。

## 4. 运行日志

每条线一个运行日志 `<目录>/b-run.json`（`format` 为 `tkos-world-02-experiment-b-run/0.1`），播种、划段、脚本的每一步都记在里面，是统计的唯一输入；不含凭证与显示名：

- `line`、`scope_id`、`lines_sha256`（`b_lines.json` 与原计划合在一起的哈希）、`script`（脚本 id 与哈希）；`principals`：主体 id → 角色键与类型。
- `objects`：播种建出的对象与 Task+Activity 线的 Activity（`segment:<段>`）；`seed`：共同播种每一步的回执、事件与对象。
- `steps`：划段（`phase` 为 `plan`，键 `plan:<段>`）与脚本（`phase` 为 `script`，键是步号）的每一步：`do`、`by`、目标（段所在的 Task 与这个 Task 有几段）、`writing`（`activity`、`plan_item`、`task_level`、`task_subject`、`task`、`mission`、`issue`）、`attempts`（每个动作：`primary` 主写法、`fallback` 退路、`support` 配套；动作名、主体、行动者、幂等键，提交了的带回执、事件、回执时刻，被拒的带入口、状态码、错误码）、`expression`、`error_codes`、`merged_into`。
- `pending`：已提交、还没记进步骤的请求体，给中断后重发用，跑完为空。
- `evidence`（取证后）：这条线碰过的每个对象（含快照）经取事件读到的事件（种类、主体、记录者与类型、detail、action_id、时刻、迟记），业务对象的生命周期、责任人与计划条目，快照的主体、时点与组件。

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

## 6. 结论规则与粗粒度

**结论**：五项中有独立发生、且 Task-only 线表达不了的，Activity 留作对象；否则降为组件。输出 `conclusion.activity`（`object` 或 `component`）与 `because`（让它留作对象的观测）。

**表达不了 = 被拒**：Task-only 线同一步被协议拒绝，一条记录都落不下。**粗粒度算能表达**：它把这一步写成计划条目的修改，或以 Task 为主体的外部事件、快照与问题，谁做了哪一段、什么时候、说了什么都在，损失的是 Activity 一级的生命周期与权限（计划条目的责任人只作记录，契约第 4 节）。这部分损失逐条列在每项观测的 `coarse` 里，给报告复核，不改结论。理由：票上把「表达不了」与「只能粗粒度表达」分开记，结论规则只点名前者；Task-only 的设计本来就是用计划条目承载「谁做哪一段」。

报告若要按更严的口径（段的生命周期只能写成计划条目的文字也算表达不了），用 `coarse` 清单与逐步对照表复核即可，统计不必改。

## 7. 冒烟脚本与它的结果

`b_smoke.json`（44 步，内容合成）把两条线都推到 Mission 关闭：联调段退回一次再交付，每周快照改指派给执行 Agent，Agent 开始、写进展、交付，被退回、重交、验收，之后重开再交；问题流转段上 Co-Agent 提出问题，路由给 Owner，Owner 承接、本层处理；联调验收只有一段；最后 Mission 状态刷新、交付，DRI 验收通过。隔离库上跑出的结果（录成 `tests/fixtures/world_v02_experiment_b/`）：

- Task+Activity 线 49 步（划段 5、脚本 44）全部原生；Task-only 线原生 20、粗粒度 21、被拒 8，被拒的都是 `FORBIDDEN`：eo-ic 不在「联调与委托登记」的主干上，改不了它的计划条目、写不了它的快照；执行 Agent 不能修订 Task（无门对象只由主干上的责任人修订），它开始、交付自己那一段连计划条目都改不了。
- 指派：独立发生（联调段指派给 eo-ic，每周快照改指派给执行 Agent），Task-only 粗粒度。
- 执行：独立发生，Task-only 表达不了（Agent 的开始与交付被拒；它的进展以 Task 为主体写成，粗粒度）。
- 重试：独立发生（两次退回、一次重开），Task-only 粗粒度。
- 验收：独立发生（Task 责任人验收别人负责的段），Task-only 粗粒度；自己验自己的两段、只有一段且与 Task 验收配对的一段不算。
- 管理：独立发生，Task-only 表达不了（eo-ic 写不了联调段所在 Task 的快照）；只有一段的联调验收的快照不算。
- 结论：Activity 留作对象（执行、管理）；Agent 写入需要以 Activity 为主体。

这是冒烟脚本的结果，证明代码与口径跑得通，不是实验结论；结论看试用期间转写的真实记录（票 #68 的报告）。

## 8. 试用期间的做法

1. 两条线各新开一个 scope（按 `b_spec.json` 供给），播种。冒烟用过的 scope 不再用于回放：一个 scope 只跑一份脚本。
2. 试用在真实 scope 里照常进行，参与者只记一遍。
3. E&O 把真实 scope 里这场 Mission 的记录（取对象、取事件、列对象）转写成执行脚本：
   - 真人与服务主体换成角色键：CEO → `ceo`，E&O DRI → `eo-dri`，Mission Owner → `eo-owner`，其余执行的人 → `eo-ic`；天枢代记的写入按被代记的人转写；Agent 以自己身份的写入按角色转写成 `exec-agent`（执行）或 `eo-coagent`（Co-Agent 提出、路由问题）。
   - Task 一级的指派与生命周期写 `task` 步骤，Mission 一级写 `mission` 步骤；Task 以下的工作（天枢的执行事项、计划条目、Activity）归到段上：`b_lines.json` 的初始划分不合用就先改它（再新开 scope 播种），试用中新出现的段在脚本里用 `plan` 划出。
   - 快照写 `refresh` 或 `progress`，带真实的 `as_of`（补记的快照读侧会标迟记）；生命周期与指派不能补记，按原顺序在回放时刻记下，回放保留顺序、不保留时间。
   - 问题写 `raise_issue`、`route_issue`、`own_issue`、`dispose_issue`，问题键取原问题组件的 id。
4. 用同一个驱动器把转写的脚本回放到两条线，取证，算观测，结果写进票 #68 的报告。

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

`smoke` 新开两个随机 tenant 的 scope（owner SQL 播种身份与凭证，控制面 CLI 装 0.2），起真 API，两条线播种、读回核对、按脚本（默认 `b_smoke.json`，`--script` 换）驱动、取证，再算观测。`--private` 下每条线一个目录（`ids.json`、凭证、运行日志），`--output` 下是两条线的运行日志、`observations.json` 与 `summary.json`。两条线的 Mission 都关闭、读回核对没有问题时退出码为 0。同一个库可以反复跑，每次都是新的 scope。

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

`seed` 播完即读回核对（`check` 可以单独再跑；驱动过之后两者都跳过读回核对，只打印状态）。每个命令都可以重跑：记过的步骤不再做；中途断了（连不上、服务端 5xx），同一个幂等键原样重发，已提交的拿回原回执。运行日志与凭证放在同一个目录，照 E&O 十月起点播种的状态文件那样归档。

## 10. 验证

- 无库测试 `tests/test_world_v02_experiment_b.py`：共同播种就是原计划里 mission_trial 的主干、名单只有角色名且过 `provision.check_spec`、段与角色合登记；脚本校验；驱动器按线的写法（假 HTTP）：Activity、计划条目、只有一段的 Task 与配对、退路、快照前的外部事件、问题引用、行动者不换人；三种表达结果；传输（假 HTTP）：被拒是结果不是错误，提交中途断掉原样重发拿回原回执，重发被拒则重新 prepare，服务端错误留着请求；用录好的冒烟运行日志核对五项观测的发生与未发生（去掉对应步骤）、独立的判定、Task-only 线的三种表达、结论规则（粗粒度不留对象、不独立的不留对象、五项都没发生）、Agent 写入的主体、两份日志必须是同一次对照。
- 独立验收 `acceptance/world_v02/` 的场景 `experiment_b`：隔离库上两条线播种、读回、按冒烟脚本推到 Mission 关闭，经读投影与只读 SQL 核对（见该目录 README）。
- 实例上的冒烟（2026-09-29，world-02 跑 c29e7d5 镜像，实验代码取 d82d176）：
  - 按上面「实例 scope」的命令，在主机上各供给一个 scope：Task-only 线 `885495de`，Task+Activity 线 `1816d844`，tenant `tokenking-world-02-experiment-b`。
  - 在本机经域名跑完 seed、drive、collect、observe、status，全部退出码 0，两条线的 Mission 都读回已关闭。
  - 结果与隔离库上录的一致：Task+Activity 线 49 步全部原生；Task-only 线原生 20、粗粒度 21、被拒 8；结论为 Activity 留作对象（执行、管理）。
  - 两条线的清单、凭证、运行日志与观测曾归档在 tkos-secrets 1c24e00 的 `ontology-runtime/world-02/experiment-b-smoke/`，运行日志与观测可从那个提交取回。
  - 同日按 55c46d3 重建（#70）后，这两个 scope 连同凭证作废，归档目录在 tkos-secrets 7bb3023 删除；试用回放要另开两个新 scope。
- 未验证：真实记录的转写与回放，要等试用。

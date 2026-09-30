## 实验 E：五个场景、标准答案与反例判定

票 #67（规格 #46 第十节，用户故事 79、80）。五个场景让实验能证伪对象结构：

- **跨责任单元**：Agents 单元的目标与约束不得混入 E&O 的回答；
- **约束冲突**：E&O 单元的关键约束与本层 Task 计划里的执行上下文（及照它执行的计划条目）相抵，回答要指出冲突并引上下两处；
- **版本变化**：十月周期目标出了新修订，回答引生效版本，不引旧版本；
- **验收失败重启**：Task 退回、再交付、验收通过又被重开，回答反映当前状态与退回原因；
- **无 Activity 的 Task**：Task-only 写法，Activity 全景块里是带责任人的计划条目，六问仍可恢复。

五个场景共用 E&O 十月主干，主干取自换任务卡之前的十月起点原计划（原样存为 `b_source-2026-10.json`），公司层正文取自 0.1 审过的材料。真实战略材料未到，Company 与 Strategy 先用存根。块与组件的位置已按真实材料预留，在场景文件的 `pending_material` 与审阅稿里标「待真实战略材料」。业务真实性由用户审；标准答案须经 E&O DRI 批准后，才能交给实验跑器（票 #68）。

2026-09-30 按方法侧 Content Pact 改写（#82，依据 `docs/world-v02-content-pact-mapping.md`）：`b_source-2026-10.json`、主干与场景换成新块，旧块的正文按意思放进新组件，只在分号、冒号处拆开；约束不再单独成块，成了带约束角色的组件；标准答案的引用全部落到新块与组件，约束冲突场景按新形状改写。内容哈希随之改变，**标准答案未批准，需要 E&O DRI 按重新生成的审阅稿重新审阅并批准**（审阅稿「请你确认」第一条），批准前跑器拒用。

| 文件 | 内容 |
| --- | --- |
| `scenarios.json` | 身份（角色名）、待真实战略材料的位置、共用主干的播种、五个场景各自的播种。每步附说明，推断的写「推」 |
| `gold.json` | 各场景的起点、六问（why、what、who、now、happened、basis）的标准答案与应引用的对象版本、块、组件或事件，以及反例的类别、规则与诱饵；请用户确认的事项；批准段 |
| `spec.py` | 两份文件的格式与自洽校验、占位解析、按步骤推演各对象播种结束时的版本与组件（纯函数） |
| `counterexamples.py` | 反例判定（纯函数，供 #68 的指标代码调用） |
| `gold.py` | 批准、取用与审阅稿生成 |
| `seed.py` | 经 HTTP 播种，输出清单，再做回放检查 |
| `replay.py` | 回放检查 |

占位写法：

- `@键`：该对象此刻的最新版本；`@键@N`：第 N 版。两者后面都可带 `#块` 或 `#块/组件`。
- `$身份键`：该身份的 principal id。
- `event:键`：那一步记下的事件。
- 快照的 `as_of` 与外部事件的 `occurred_at` 写 `now`：取播种那一步的数据库时刻。这样按记录顺序就是按发生顺序，不出迟记，也不会落到近期窗口之外。场景写的是十月的事，在十月之前播种也照此写，所以正文不写具体日期。

场景只建、只改自己的对象：

- 修订、指派、门与生命周期的目标，快照与外部事件的主体，要么是本场景建的，要么是它在 `owns` 里认领的主干对象；
- 一个对象只归一个场景；
- 共用对象的改动一律写在主干里。十月周期目标的一轮重走就在主干末尾，所以五个场景的 Why 都引它的第 2 版。

`spec.validate` 按这些规则拒绝不自洽的场景文件。

### 回答的形状与反例判定

回答是断言的列表，沿用 0.1 的形状 `{"claim": 文字, "kind": 种类, "refs": [引用]}`。种类有三种：

- `fact`：陈述内容；
- `gap`：说明为空或取不到，要引出显示为空的块或对象；
- `conflict`：指出冲突。`refs` 同时引冲突的两处，即上层的约束与本层与之相抵的计划或执行上下文；`claim` 写冲突是什么。

引用是 0.2 的四种业务形式，不带 `@` 的裸 UUID 当作事件。

`counterexamples.resolve(场景的标准答案, 播种清单)` 先把占位换成这次播种的引用，`counterexamples.judge(断言, 换好的标准答案, question=None)` 再判定。输出只列这个场景要判的类别，每类给出：

- `occurred`：是否出现；
- `claims`：依据的断言序号；
- `refs`：落在反例上的引用；
- `reason`：只有冲突类用，`one_sided` 或 `not_stated`，其余类别为 `None`。

诱饵落在哪里算命中：

- 诱饵是事件：引用要完全相同。
- 诱饵是对象：它的任何块与组件都算。
- 诱饵是块：这一块与其中的组件都算（`content_as_empty` 例外，只算块级引用）。
- 诱饵是组件：只算这一条。
- 诱饵写了版本（`@键@N`）只算那一版，没写版本的任何版本都算。只引对象本身，不落在块或组件诱饵上。

各类的判法：

| 类别 | 场景 | 出现的条件 |
| --- | --- | --- |
| `other_unit` | 跨责任单元 | 任何种类的断言引了另一单元的目标或约束 |
| `conflict_missed` | 约束冲突 | 一条 `conflict` 断言只引到一侧（`one_sided`，哪一问都算）；或判「做什么」「凭什么」时，没有一条 `conflict` 断言同时引到两侧（`not_stated`，没有依据的断言）。冲突的两侧不看版本，侧是块时其中的组件也算；现在的标准答案两侧都写到组件（上层是单元的关键约束 `no-graph-db`，本层是执行上下文 `graph-db` 或计划条目 `rag-graph` 之一），只引责任定义块或单元的另一条关键约束不算引到上层。不给问题即按整份回答判 |
| `stale_version` | 版本变化 | 任何种类的断言引了诱饵，或引了比这次播种结束时更旧的任一对象版本 |
| `stale_state` | 验收失败重启 | 任何种类的断言引了已过时的快照 |
| `phantom_activity` | 无 Activity 的 Task | 任何种类的断言引了 Activity 类型的对象，或引了 scope 里不存在的对象 |
| `content_as_empty` | 无 Activity 的 Task | `gap` 断言以块级引用引了有内容的块；引块里的某个组件（以某条计划或验收标准为依据说它还没满足）不算 |

`tests/test_world_v02_experiment_e.py` 按 `tests/fixtures/world_v02_experiment_e/answers.json` 逐场景核对出现与不出现，不连库。

### 审阅与批准

- 审阅稿 `docs/world-v02-scenarios-review.md` 由两份文件生成。改了 JSON 要重新生成：`python -m experiments.world_v02.gold render`。测试会核对审阅稿与两份文件一致。
- E&O DRI 本人审过后运行：

  ```sh
  .venv/bin/python -m experiments.world_v02.gold approve --by "E&O DRI"   # 写角色名，不写真实姓名
  .venv/bin/python -m experiments.world_v02.gold status   # approved / not approved
  ```

- 批准记下批准人、时间，以及场景文件与标准答案（批准段除外）合在一起的内容哈希：
  - 批准人只能是标准答案里写明的角色名；
  - 两份文件任何改动都让批准失效，要重新批准。
- 实验跑器只能经 `gold.load_approved()` 取用，未批准或已失效时抛 `NotApproved`。传入某次播种的 `manifest.json` 时，还核对那次播种用的正是批准的内容。
- 播种与回放不经批准也能跑，用来在批准前核对内容。

### 真实战略材料到位后

1. 按 `pending_material` 列的位置替换 Company 与 Strategy 的正文。组件 id 不变，这样各场景 Why 的应引用项不用改；材料要是带来新的块或组件，同时改标准答案。
2. 重新生成审阅稿，播种并回放一次。
3. 请 E&O DRI 重新批准。旧批准会因内容哈希变化自动失效。

### 播种与回放检查

前提与 0.2 独立验收相同：隔离验收栈在运行（`python3 acceptance/runtime/infra.py status`），基础 env 是仅本人可读的 `.runtime-acceptance/env.json`。不要打印 env、DSN 或凭据。

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.method_v05.database create --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/world-e-db-$STAMP --output artifacts/runtime-acceptance/world-e-db-$STAMP
.venv/bin/python -m acceptance.method_v05.database upgrade --env-file .runtime-acceptance/world-e-db-$STAMP/env.json \
  --output artifacts/runtime-acceptance/world-e-db-$STAMP-upgrade
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m experiments.world_v02.seed --env-file .runtime-acceptance/world-e-db-$STAMP/env.json \
  --private .runtime-acceptance/world-e-$RUN --output artifacts/runtime-acceptance/world-e-$RUN
```

- 每次运行新开一个随机 tenant 的 scope，在同一个库里可以重复运行。
- 身份经 owner SQL 播种，沿用 0.1 实验的 `seed_scope`；控制面经维护 CLI 装各域激活策略、0.2 的 profile、默认策略与支持登记；业务记录一律经 HTTP 的 prepare 与 commit 写入。
- 播完核对每个对象的版本与 `spec.final_versions` 推演的一致。
- 输出 `manifest.json`：scope、域、身份的 principal id、每个对象键的 object_id、类型与最新版本、每个事件键的 event_id、写 `now` 的时刻、内容哈希。不含凭据。
- 输出 `replay.json`：回放检查的结果。只有全部通过，退出码才为 0。

回放检查从各场景的起点出发，只经 HTTP 读投影、取上下文与只读 SQL。取上下文按读接口的既定行为落一行上下文包，它是时间记录。

- **六问**：应引用的每一项都读得到，并且从起点可达：
  - 对象、块与组件按版本读得到，块非空，组件在那一版里；
  - 对象在主干上，或是主干上某个对象的关系所指的对象；
  - 快照的主体、事件的某个主体在主干上。

  另有三条要求：

  - Who 问的对象已有责任人；
  - 现状问的快照仍是其主体的最新一条，且起点当前的生命周期由现状问所引的某条事件推出；
  - 有门对象引到的是它的生效修订，还没有正式内容的（草稿的 Strategy）引最新版。
- **取上下文可恢复**：从起点取一次上下文，用默认预算，近期窗口显式给 30 天。应引用的每一项都在包里带着内容回来，口径与 MCP 运行日志的 `read_refs` 相同；这一问在包的六问覆盖里答得了。
- **诱饵都在，且各有该有的样子**：

  | 类别 | 该有的样子 |
  | --- | --- |
  | 另一单元的目标与约束 | 非空，不在主干上，在另一个域；经依赖主干对象的对象引得到 |
  | 冲突的两处 | 上层那处在主干上，本层那处在起点上，都非空 |
  | 旧版本 | 不是最新；最新版就是生效修订；主干上有对象钉着这个旧版本 |
  | 过时的状态 | 是起点的快照，但已不是最新一条 |
  | 不存在的 Activity | 诱饵 Activity 在，但不在起点下面；起点下面没有任何 Activity |
  | 有内容的块 | 在起点上，非空 |

独立验收 `acceptance/world_v02` 的场景 `experiment_e` 在验收库上跑同样的播种与回放。

## 对照实验 B：Task-only 与 Task+Activity

票 #66、#75。同一场真实 Mission 分 Task-only 与 Task+Activity 两条线执行到关闭，对照 Activity 是否需要独立的指派、执行、重试、验收与管理。试用回放的对象是 E&O 十月起点（`deploy/world-02/seed-eo-2026-10.json`）里的 `mission_context`（Owner 是 E&O DRI 本人）：10/16 联调验收之后把真实 scope 里它的记录转写成执行脚本，E&O DRI 审过再回放。冒烟仍用换任务卡之前的合成源数据（`b_source-2026-10.json` 的 `mission_trial`）。设计、执行脚本格式、五项观测的记录来源与判断口径、结论规则、转写规则与命令见 `docs/world-v02-experiment-b.md`（转写在第 8 节）。

2026-09-30 按方法侧 Content Pact 改写（#83）：Task、Mission、Activity 用新块；Task-only 线的段是 Task 的 Activity 全景块里的计划条目，除责任人外带 `plan_item` 的四个可选属性（预期产出、质量标准、执行主体、人 + Agent 分工），Task+Activity 线的 Activity 在 `instruction` 块里写执行事项、预期产出与成功 / 验收标准组件；转写把这些属性与组件带进段。五项观测的口径不变（该文档第 5 节末）。冒烟的运行日志与转写的读取原样都在隔离栈新库上按新块重录。

| 文件 | 内容 |
|-|-|
| `b_source-2026-10.json` | 源数据：E&O 十月起点换任务卡之前的原计划，另存（#73；正文与顺序原样，块形状 9/30 按 Content Pact 改过，#82）；实例用的 `deploy/world-02/seed-eo-2026-10.json` 已改为按天枢个人任务重播 |
| `b_smoke_lines.json` | 冒烟的播种（lines 0.1，`b_seed` 的默认）：原计划里要的步骤（mission_trial 到 Company 的主干）、三个 Task 的责任人、每个 Task 下「谁做哪一段」的初始划分（段只带正文里原样写着的可选属性，#83） |
| `b_lines.json` | 试用回放的主干（lines 0.2）：十月起点里 `mission_context` 到 Company 的 10 步；门、Task 与段由转写填进转写产物 `b-lines.json` |
| `b_spec.json` | 两条线各自 scope 的名单（角色名，`deploy/world-02/provision.py` 的格式；eo-dri 另持 OWNER） |
| `b_smoke.json` | 预置的冒烟执行脚本（0.1）：两条线都推到 Mission 关闭 |
| `b_http.py` | HTTP 传输：一条线就是一个目录（`ids.json` 与 `<键>.token`），prepare 再 commit，中断后原样重发；只用标准库 |
| `b_seed.py` | 播种（共同播种加划段）、读回核对；隔离库上的一键冒烟（owner SQL 供给、控制面 CLI 装 0.2、起真 API） |
| `b_drive.py` | 执行脚本的校验、驱动器（按各线的写法落下，记下动作、回执、事件与表达结果）、取证 |
| `b_observe.py` | 五项观测与结论（纯函数，输入是两条线的运行日志） |
| `b_transcribe.py` | 试用回放的转写：只读（GET）取回真实 scope 里一场 Mission 的记录，按私有的对照表把主体换成角色键，写出转写产物 `b-lines.json`（lines 0.2）、`b-script.json`（脚本 0.2）与审阅稿 `b-review.md`；任一产物里出现显示名就报错、不写 |
| `b_rehearse.py` | 隔离库上的试用回放预演：仿一个真实 scope、按天枢的写法造一段试用记录，转写，再两条线回放 |

```sh
# 隔离库上的冒烟（建库同 0.2 验收，STAMP 为 method_v05 database create/upgrade 用的那次）
.venv/bin/python -m experiments.world_v02.b_seed smoke --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --private .runtime-acceptance/world-v02-b-$RUN --output artifacts/runtime-acceptance/world-v02-b-$RUN
# 隔离库上的试用回放预演：仿真实 scope → 转写 → 两条线播种、驱动、取证、观测
.venv/bin/python -m experiments.world_v02.b_rehearse --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --private .runtime-acceptance/world-v02-b-rehearse-$RUN --output artifacts/runtime-acceptance/world-v02-b-rehearse-$RUN
# 试用回放的转写：只读取回真实 scope 里这场 Mission 的记录（凭证与对照表都私有），产物交 E&O DRI 审
python3 -m experiments.world_v02.b_transcribe transcribe $U --token-file <凭证文件> --mission <object_id> \
  --principals $B/principals.json --private $B/read --output $B/transcript
python3 -m experiments.world_v02.b_transcribe write $B/read/bundle.json --principals $B/principals.json --output $B/transcript
# 已供给的实例 scope：播种、驱动、取证、观测（每条线一个目录，凭证只从文件读；回放带 --lines 与 --script）
python3 -m experiments.world_v02.b_seed seed $U $B/task-only --line task_only [--lines $B/transcript/b-lines.json]
python3 -m experiments.world_v02.b_drive drive $U $B/task-only [--script $B/transcript/b-script.json]
python3 -m experiments.world_v02.b_drive collect $U $B/task-only
python3 -m experiments.world_v02.b_observe $B/task-only/b-run.json $B/task-activity/b-run.json --output $B/observations.json
```

无库测试是 `tests/test_world_v02_experiment_b.py`（录好的运行日志在 `tests/fixtures/world_v02_experiment_b/`）与 `tests/test_world_v02_transcribe.py`（录好的读取原样在 `tests/fixtures/world_v02_transcribe/`，是预演的仿真实 scope 取回的），独立验收是 `acceptance/world_v02/` 的场景 `experiment_b`。输出目录、`--private` 目录、对照表与转写产物是本地产物，不入库。

## 四种取法对照（含 RAG）

票 #68（规格 #46 第十节，用户故事 81、82）。结构沿用 0.1 的 `experiments/world_v01/`，0.1 的模块一行不改。同一批问题——上面实验 E 五个场景的六问——四组都作答：同一模型、同一推理档、同一回答形状（断言列表 `{claim, kind: fact|gap|conflict, refs}`），每问三次（`--attempts` 可改，彩排按实测耗时减次数），只差模型拿到上下文的方式。

| 组 | 模型拿到什么 | 工具 |
| --- | --- | --- |
| `full` 全量塞入 | scope 内每个对象的最新版加每条事件，经读投影取、按分块写进提示词 | 无 |
| `fixed` 固定路径 | 跑器调一次取上下文（不给预算，用服务端默认值），把 MCP 交给调用方的那段返回写进提示词，同 0.1 的 F 组 | 无 |
| `traverse` 模型遍历 | 模型经 tkos-world-mcp（契约 0.2）自己走，同 0.1 的 A0 组 | 取对象、取事件、取状态、列对象（不给取上下文） |
| `rag` RAG | 按问题检索分块写进提示词 | 无 |

隔离、污染判定与重试照 0.1：每次作答在空目录里起一次 codex exec，不读用户配置，关掉 shell、记忆、联网、插件、apps 与子代理；事件流里出现本组工具之外的动作记为污染，污染与失败的运行不计入指标，同一次重跑到有效为止，每一次尝试都留在输出目录里。四组都以 E&O Agent 的身份只读。

| 文件 | 内容 |
| --- | --- |
| `retrieval.py` | 经读投影取 scope 的全部内容、切分、全量文本、字符二元组 BM25 与装入 |
| `experiment.py` | 准备（不调模型）、跑、重算指标 |
| `metrics.py` | 召回、可追溯、确定性与反例四项门，另报所引集合一致率、回答覆盖与成本 |
| `triggers.py` | 读投影扫描与四个触发条件的机械检查 |
| `report.py` | 只由 `summary.json` 生成报告，默认写到 `docs/world-v02-retrieval-report.md` |

### 切分、检索与装入

- **切分**：每个分块只带一条自己的引用，就是它「取到」的那一项。
  - 对象表头：业务对象与状态快照各一块，引用 `对象@版本`。「谁负责」「现在怎样」的标准答案引对象本身，没有这一块按构造就答不了。Mission 与责任单元的投影项（读取时从下级对象投影，#79）只在表头里列下级对象的引用，内容在下级对象自己的分块里。
  - 块：一个块一块，空块写标准句，不含块里的组件；组件：一个组件一块，引用 `对象@版本#块/组件`。
  - 事件：每条一块，引用 `event:<事件 id>`。否则「发生了什么」这一问按构造就答不了。状态快照同样按表头、块与组件切。
  - 正文里照读投影写出钉着的别处引用（关系、块内引用、事件的主体），它们只是引用，不算取到。
- **取到的口径**：用 `tkos_world_mcp.server._content` 判，与 MCP 运行日志和实验 E 的回放一致；这是对一个私有函数的依赖。切分时逐块核对 `_content(来源)` 恰好是这个分块的引用。
- **检索**：纯 Python 的字符二元组 BM25（k1=1.2，b=0.75），不接任何外部服务或 embedding。检索文本去掉业务引用与时刻（两者每次播种都不同，留着会让同样的内容在不同的播种上排出不同的名次），查询是起点对象的标题加问题（有的问题不带标题）。按分数从高到低、同分按文档顺序排，分数为 0 的不取。
- **装入：同成本对照**。每一问的上限等于固定路径组这一问实际交给模型的字符数，也就是写进提示词的那段取上下文返回（MCP 交给调用方的 JSON）的长度。两组按同一口径（交给模型的字符数）量成本。按名次逐块装，装不下的跳过、接着看下一块。
  - 理由：取上下文的默认预算（#70 定为 100000 字符）大到几乎装下整个 scope，RAG 按它装就退化成全量塞入；按固定路径实际交给模型的字符装，比的是同样的成本下谁取得准。
  - 不用 Markdown 的 `used_chars`：它比交给模型的文本少一层 JSON 外壳，拿它作上限，RAG 的成本就按另一个口径量了。
  - 代码里不写死预算：固定路径的预算读自取上下文返回的 `budget.max_chars`，RAG 的上限是固定路径那段文本的长度。
- **固定路径交给模型的文本**：与 MCP 交给调用方的完全相同，由 `tkos_world_mcp.server._shown` 生成（第二个私有函数依赖），含包 id、Markdown、六问覆盖与按原因计的裁剪条数，所以比 `used_chars` 大一层 JSON 外壳。
- **空块**留在索引里：它写的是标准句（「Activity 全景：暂无」），gap 回答要引到它。模型遍历组保留列对象。

### 命令

前提与「播种与回放检查」相同：隔离验收栈在运行，库用 method_v05 的工具新建并升级到工作区源码，先用 `experiments.world_v02.seed` 播种一次（下面的 `$STAMP` 与 `$RUN` 是那两步的）。不要打印 env、DSN 或凭据。

```sh
# 准备：不调模型，批准前也能跑。生成三组的上下文，核对引用，做读投影扫描；核对全部通过退出码才为 0
.venv/bin/python -m experiments.world_v02.experiment prepare \
  --env-file .runtime-acceptance/world-e-db-$STAMP/env.json \
  --seeded-private .runtime-acceptance/world-e-$RUN --seeded-output artifacts/runtime-acceptance/world-e-$RUN \
  --private .runtime-acceptance/world-r-prepare-$RUN --output artifacts/runtime-acceptance/world-r-prepare-$RUN
# 跑：标准答案经 E&O DRI 批准后才能跑（gold.load_approved），会先在同一个输出目录里再准备一次
R=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m experiments.world_v02.experiment run \
  --env-file .runtime-acceptance/world-e-db-$STAMP/env.json \
  --seeded-private .runtime-acceptance/world-e-$RUN --seeded-output artifacts/runtime-acceptance/world-e-$RUN \
  --private .runtime-acceptance/world-r-$R --output artifacts/runtime-acceptance/world-r-$R \
  --model <模型> --effort <推理档> [--groups full fixed traverse rag] [--attempts N]
# 彩排（票 #74）：同上再加 --rehearsal。标准答案不要求批准，只核对播种用的正是当前内容；setup.json 记 rehearsal，
# summarize 自动按彩排汇总，报告开头写明结果不作数。彩排的报告用 --output 写到 artifacts/，不写 docs/
# 只重算指标；有了 #66 的观测结论就带上
.venv/bin/python -m experiments.world_v02.experiment summarize \
  --seeded-output artifacts/runtime-acceptance/world-e-$RUN --output artifacts/runtime-acceptance/world-r-$R \
  [--b-observations <#66 的 observations.json>]
# 报告：只由 summary.json 生成
.venv/bin/python -m experiments.world_v02.report --summary artifacts/runtime-acceptance/world-r-$R/summary.json
```

输出目录（本地产物，不入库）：

| 路径 | 内容 |
| --- | --- |
| `prepared.json` | scope 规模与分块数；每问三组的字符数、取到召回与缺的应引项；固定路径的预算、六问覆盖与检索计划里被裁的项；RAG 的上限与装入块数；读投影扫描；引用核对（`verified`） |
| `contexts/` | 全量文本（`full.json`），每问的固定路径返回与 RAG 结果（`<场景>.<问>.fixed.json`、`.rag.json`） |
| `setup.json` | 模型、推理档、Codex 版本、各组工具、关掉的功能、每问次数、是否彩排、内容哈希、提交 |
| `<组>/<场景>.<问>-<次>[-retryN]/` | `run.json`、`answer.json`、`codex.jsonl`、`codex.stderr`；全量、固定路径、RAG 另有 `context.json`，模型遍历有 `mcp/` 运行日志 |
| `summary.json` | 四组逐问、逐场景与合计的指标和判定，准备结果，对照实验 B 的摘要（`b`，带了 `--b-observations` 才有），四个触发检查与阈值 |

报告只由 `summary.json` 生成，默认写到 `docs/world-v02-retrieval-report.md`；`docs/` 下不提交占位报告。

### 指标口径

四组各自判定；没有有效运行的组或场景，各项门都不算达标。

| 指标 | 全量塞入 | 固定路径 | 模型遍历 | RAG |
| --- | --- | --- | --- | --- |
| 取到的集合 | 全部分块（按构造最大） | 包里带着内容回来的（`_content`） | MCP 运行日志的 `read_refs` 与 `read_event_ids` | 装入的分块 |
| 召回（门 ≥ 0.9） | 应引项里被取到的比例，所有有效运行合计 | 同左 | 同左 | 同左 |
| 可追溯（门 100%） | 断言所引都在这次运行交给模型的材料里出现过（带内容或只以引用形式）的比例；材料是写进提示词的文本 | 同左 | 材料是 MCP 运行日志的 refs 与 event_ids | 同左 |
| 确定性（门 ≥ 0.9） | 取到集合两两 Jaccard，按构造为 1 | 按构造为 1 | 随运行变 | 按构造为 1 |
| 反例（门：五个场景零出现） | 断言所引，`counterexamples.judge` 逐问判，每次运行每问每类记一次 | 同左 | 同左 | 同左 |
| 所引集合一致率（另报） | 同一问各次运行所引集合的 Jaccard | 同左 | 同左 | 同左 |
| 回答覆盖（另报） | 应引项里被回答引到的比例 | 同左 | 同左 | 同左 |
| 成本（只报告） | 写进提示词的全量文本字符数 | 写进提示词的取上下文返回字符数 | 工具返回给模型的字符数之和 | 写进提示词的检索结果字符数，上限是固定路径那一问的成本 |

- 有效运行不足三次的问列为 short，确定性不算达标。
- 全量、固定路径、RAG 三组的上下文每问生成一次，三次作答用同一份，确定性按构造为 1；全量组的召回按构造最大。报告写明这两点，不当成实验发现。回答的稳定性看所引集合一致率。
- 回答覆盖：完全相同；应引项是对象时，引了同一版本该对象的块或组件也算；是块时，引了同一版本这一块里的组件也算。

### 四个触发条件

`triggers.py` 各做成一个有名字的检查，输出触发、未触发或无数据。已有数据足以判定触发就判触发；判未触发要该有的数据都在；否则无数据，并列出缺什么。

| 检查 | 条件 → 调整 | 数据源 | 判法 |
| --- | --- | --- | --- |
| `activity_to_component` | Task-only 线达标 → Activity 降为组件 | #66 的观测结论（`summary.json` 的 `b`，按 #66 口径的 `conclusion.activity`）；固定路径组在 `task_only` 场景的四项门 | B 为 component 且 task_only 四项门全过则触发；B 为 object 或有门没过则未触发 |
| `component_ref_instability` | 组件引用跨修订不稳 → 拆块或升对象 | 读投影扫描：钉着的组件引用，对照目标对象最新版的组件台账 | 跨修订的引用至少 10 条；悬空比例超过 0.1 则触发 |
| `why_coverage_low` | Why 覆盖持续偏低 → 改主干关系或取法 | 固定路径组 Why 问的取到召回（准备结果）与回答覆盖（要模型运行） | 五个场景里至少 3 个，Why 召回或 Why 回答覆盖低于 0.9，则触发 |
| `issue_detached` | Issue 常脱离主体快照演进 → Issue 升为对象 | 读投影扫描：提出之后的问题事件，与主受影响对象在事件时刻的最新快照 | 问题事件至少 5 条；不在那条快照 issues 块里的比例不低于 0.5 则触发 |

阈值是 `triggers.py` 的模块常量，`THRESHOLDS` 列出全部，写进 `summary.json` 与报告：

- `MIN_COMPONENT_REFS = 10`：第 2 条至少要这么多跨修订的组件引用才判；
- `MAX_DANGLING = 0.1`：悬空的比例超过它就触发第 2 条；
- `WHY_RECALL = WHY_COVERAGE = 0.9`：与召回门一致，Why 召回或 Why 回答覆盖低于它，这个场景算偏低；
- `WHY_SCENARIOS = 3`：五个场景里偏低的至少这么多，触发第 3 条；
- `MIN_ISSUE_EVENTS = 5`：第 4 条至少要这么多提出之后的问题事件才判；
- `DETACHED_SHARE = 0.5`：脱离快照的比例不低于它就触发第 4 条；
- `REFERENCE = fixed`：第 1、3 条以固定路径组为参照。

实验 E 的播种里跨修订的组件引用只有两条，也没有问题流转，第二、四条在这里是无数据；要的数据是试用期间（10/12–16）#66 两条线与实验实例上的真实修订与问题事件，用同一扫描读回。

### 报告里的对照实验 B

`summarize --b-observations` 读 #66 的 `observations.json`（`b_observe` 的输出），经 `triggers.b_conclusions` 摘成 `summary.json` 的 `b`，报告的「对照实验 B」一节与第 1 条检查都读它：

- 五项观测各自是否独立发生、Task-only 线的表达（原生、粗粒度、表达不了）与逐类计数、依据的步骤，Agent 写入需要以谁为主体；
- **按 #66 的口径**（粗粒度算能表达）的结论：五项中有独立发生、且 Task-only 线表达不了（被拒）的，Activity 留作对象，否则降为组件。第 1 条检查按这个口径判；
- **按更严的口径**（粗粒度也算表达不了）的结论，由 `coarse` 清单机械算出：一项观测在 #66 口径下留对象，或它的 `coarse` 清单不为空，就让 Activity 留作对象。两种都列，粗粒度清单逐条写出步骤与写法；
- 观测来自 #66 预置的冒烟脚本（`smoke`）时，报告注明那是合成内容，不是实验结论。

无库测试是 `tests/test_world_v02_retrieval.py`，fixture 在 `tests/fixtures/world_v02_retrieval/`：一次真实播种的读投影子集与准备结果（id 换成假播种清单的；#82 按新块改写、没有在新库上重跑，准备结果里的字符数与预算沿用 9/29 那次，口径见 `prepared.json` 的 note），以及录好的运行；对照实验 B 一节与第 1 条检查用 `tests/fixtures/world_v02_experiment_b/` 的冒烟日志经 `b_observe` 算出的观测。独立验收 `acceptance/world_v02` 的场景 `experiment_r` 在验收库上另开 scope 播种，跑准备并核对交给模型的文本，不调模型。

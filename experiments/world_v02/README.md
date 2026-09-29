## 实验 E：五个场景、标准答案与反例判定

票 #67（规格 #46 第十节，用户故事 79、80）。五个场景让实验能证伪对象结构：

- **跨责任单元**：Agents 单元的目标与约束不得混入 E&O 的回答；
- **约束冲突**：E&O 单元的约束与本层计划相抵，回答要指出冲突并引两处；
- **版本变化**：十月周期目标出了新修订，回答引生效版本，不引旧版本；
- **验收失败重启**：Task 退回、再交付、验收通过又被重开，回答反映当前状态与退回原因；
- **无 Activity 的 Task**：Task-only 写法，计划块里是带责任人的计划条目，六问仍可恢复。

五个场景共用 E&O 十月主干，主干取自 `deploy/world-02/seed-eo-2026-10.json`，公司层正文取自 0.1 审过的材料。真实战略材料未到，Company 与 Strategy 先用存根。块与组件的位置已按真实材料预留，在场景文件的 `pending_material` 与审阅稿里标「待真实战略材料」。业务真实性由用户审；标准答案须经 E&O DRI 批准后，才能交给实验跑器（票 #68）。

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
- `conflict`：指出冲突。`refs` 同时引冲突的两处，即上层的约束与本层的计划；`claim` 写冲突是什么。

引用是 0.2 的四种业务形式，不带 `@` 的裸 UUID 当作事件。

`counterexamples.resolve(场景的标准答案, 播种清单)` 先把占位换成这次播种的引用，`counterexamples.judge(断言, 换好的标准答案, question=None)` 再判定。输出只列这个场景要判的类别，每类给出：

- `occurred`：是否出现；
- `claims`：依据的断言序号；
- `refs`：落在反例上的引用；
- `reason`：只有冲突类用，`one_sided` 或 `not_stated`，其余类别为 `None`。

诱饵落在哪里算命中：

- 诱饵是事件：引用要完全相同。
- 诱饵是对象：它的任何块与组件都算。
- 诱饵是块：这一块与其中的组件都算。
- 诱饵是组件：只算这一条。
- 诱饵写了版本（`@键@N`）只算那一版，没写版本的任何版本都算。只引对象本身，不落在块或组件诱饵上。

各类的判法：

| 类别 | 场景 | 出现的条件 |
| --- | --- | --- |
| `other_unit` | 跨责任单元 | 任何种类的断言引了另一单元的目标或约束 |
| `conflict_missed` | 约束冲突 | 一条 `conflict` 断言只引到一侧（`one_sided`，哪一问都算）；或判「做什么」「凭什么」时，没有一条 `conflict` 断言同时引到两侧（`not_stated`，没有依据的断言）。冲突的两侧不看版本，侧是块时其中的组件也算。不给问题即按整份回答判 |
| `stale_version` | 版本变化 | 任何种类的断言引了诱饵，或引了比这次播种结束时更旧的任一对象版本 |
| `stale_state` | 验收失败重启 | 任何种类的断言引了已过时的快照 |
| `phantom_activity` | 无 Activity 的 Task | 任何种类的断言引了 Activity 类型的对象，或引了 scope 里不存在的对象 |
| `content_as_empty` | 无 Activity 的 Task | `gap` 断言引了有内容的块或其中的组件 |

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

票 #66。同一场真实 Mission（E&O 十月起点的「天枢 × 本体 0.2 试用」，`deploy/world-02/seed-eo-2026-10.json` 的 `mission_trial`）分 Task-only 与 Task+Activity 两条线执行到关闭，对照 Activity 是否需要独立的指派、执行、重试、验收与管理。设计、执行脚本格式、五项观测的记录来源与判断口径、结论规则、试用期间的做法与命令见 `docs/world-v02-experiment-b.md`。

| 文件 | 内容 |
|-|-|
| `b_lines.json` | 两条线的播种：原计划里要的步骤（mission_trial 到 Company 的主干）、三个 Task 的责任人、每个 Task 下「谁做哪一段」的初始划分 |
| `b_spec.json` | 两条线各自 scope 的名单（角色名，`deploy/world-02/provision.py` 的格式） |
| `b_smoke.json` | 预置的冒烟执行脚本：两条线都推到 Mission 关闭 |
| `b_http.py` | HTTP 传输：一条线就是一个目录（`ids.json` 与 `<键>.token`），prepare 再 commit，中断后原样重发；只用标准库 |
| `b_seed.py` | 播种（共同播种加划段）、读回核对；隔离库上的一键冒烟（owner SQL 供给、控制面 CLI 装 0.2、起真 API） |
| `b_drive.py` | 执行脚本的校验、驱动器（按各线的写法落下，记下动作、回执、事件与表达结果）、取证 |
| `b_observe.py` | 五项观测与结论（纯函数，输入是两条线的运行日志） |

```sh
# 隔离库上的冒烟（建库同 0.2 验收，STAMP 为 method_v05 database create/upgrade 用的那次）
.venv/bin/python -m experiments.world_v02.b_seed smoke --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --private .runtime-acceptance/world-v02-b-$RUN --output artifacts/runtime-acceptance/world-v02-b-$RUN
# 已供给的实例 scope：播种、驱动、取证、观测（每条线一个目录，凭证只从文件读）
python3 -m experiments.world_v02.b_seed seed $U $B/task-only --line task_only
python3 -m experiments.world_v02.b_drive drive $U $B/task-only [--script <脚本>]
python3 -m experiments.world_v02.b_drive collect $U $B/task-only
python3 -m experiments.world_v02.b_observe $B/task-only/b-run.json $B/task-activity/b-run.json --output $B/observations.json
```

无库测试是 `tests/test_world_v02_experiment_b.py`（录好的运行日志在 `tests/fixtures/world_v02_experiment_b/`），独立验收是 `acceptance/world_v02/` 的场景 `experiment_b`。输出目录与 `--private` 目录是本地产物，不入库。

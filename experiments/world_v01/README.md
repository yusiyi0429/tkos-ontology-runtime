# E&O 九月回放：播种与标准答案

tkos.world/0.1 的静态截面实验：票 #28 准备基准，票 #29 跑实验、算指标。基准包括：

- 把 E&O 九月的真实业务世界经治理路径播种到可清的实验库；
- 写好从 Activity「改建模材料」出发的六问标准答案，以及八类反例的诱饵。

业务真实性由用户审，标准答案须经 E&O DRI 批准后才能用于实验。

| 文件 | 内容 |
| --- | --- |
| `seed.json` | 播种步骤：身份（角色名）、建对象、修订、指派、门、状态快照、外部事件。步骤之间用稳定键互相引用，每步附来源与「确/推」说明 |
| `gold.json` | 六问（why、what、who、now、happened、basis）的标准答案与应引用的对象、块或事件；八类反例的规则与诱饵；请用户确认的事项；批准段 |
| `spec.py` | 两份文件的格式与自洽校验、占位引用解析（纯函数） |
| `gold.py` | 批准、取用与审阅稿生成 |
| `seed.py` | 建实验库、播种（含回放检查）、清理 |
| `replay.py` | 回放检查 |
| `experiment.py` | 实验跑器：B 组取上下文、A 与 A0 组 Codex CLI、全量塞入的长度（票 #29、#32 D） |
| `metrics.py` | 指标：召回、可追溯、预算、确定性与八类反例，模型遍历组各自判定（纯函数，票 #29、#32 D） |

占位写法：

- `@键`：该对象此刻的最新版本；`@键@N`：第 N 版；两者后面都可带 `#块`。
- `$身份键`：该身份的 principal id。
- 标准答案里 `event:键`：某一步记下的事件。

## 审阅与批准

- 审阅稿 `docs/world-v01-eo-september-review.md` 由两份文件生成，改了 JSON 要重新生成：`python -m experiments.world_v01.gold render`。
- 测试会核对审阅稿与两份文件一致。
- E&O DRI 本人审过后运行：

  ```sh
  .venv/bin/python -m experiments.world_v01.gold approve --by "E&O DRI"   # 写角色名，不写真实姓名
  .venv/bin/python -m experiments.world_v01.gold status   # approved / not approved
  ```

- 批准记下批准人、时间，以及播种文件与标准答案（批准段除外）合在一起的内容哈希：
  - 批准人只能是标准答案里写明的角色名；
  - 批准同时也是你审过播种内容的记录；
  - 两份文件任何改动都让批准失效，要重新批准。
- 实验跑器只能经 `gold.load_approved()` 取用，未批准或已失效时抛 `NotApproved`。
- 传入某次播种的 `manifest.json` 时，还核对那次播种用的正是批准的内容。

## 播种、回放检查与清理

前提与验收相同：

- 隔离验收栈在运行（`python3 acceptance/runtime/infra.py status`），Docker 上下文为 `desktop-linux`；
- 基础 env 是仅本人可读的 `.runtime-acceptance/env.json`。

不要打印 env、DSN 或凭据。

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m experiments.world_v01.seed create --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/world-exp-db-$STAMP --output artifacts/runtime-acceptance/world-exp-db-$STAMP
.venv/bin/python -m experiments.world_v01.seed run --env-file .runtime-acceptance/world-exp-db-$STAMP/env.json \
  --private .runtime-acceptance/world-exp-$STAMP-run1 --output artifacts/runtime-acceptance/world-exp-$STAMP-run1
.venv/bin/python -m experiments.world_v01.seed clean --env-file .runtime-acceptance/world-exp-db-$STAMP/env.json
```

- **create**：用验收的建库工具新建 `tkos_a1_world_*` 库，从基线迁移，再把工作区 `src` 的 world 迁移作为升级应用。
- **run**：
  - 播种过程：
    - 每次新开两个随机 tenant 的 scope，本家公司与「scope 外」的另一家公司；
    - 身份与指派经 owner SQL 播种；
    - 控制面经维护 CLI 装各域激活策略、world profile、默认策略与支持登记；
    - 起真 API 进程，按步骤经 prepare 与 commit 写入。
  - 在同一个实验库里可以重复运行，每次都是互不相干的新 scope。
  - 输出 `manifest.json`：scope、身份、每个对象键的 object_id 与最新版本、每个事件键的 event_id、播种内容的哈希，不含凭据。
  - 输出 `replay.json`：回放检查结果。只有检查全部通过，退出码才为 0。
- **clean**：删掉整个实验库（库里每次运行的 scope 一起删），并核对它已不存在。只删建库工具生成的 `tkos_a1_world_*` 库。`--private` 下的凭据文件随库失效，可以一并删掉。

建对象、修订、指派与门事件记的是播种时刻：契约里指派的生效时间就是记录时刻，0.1 不能补记过去的指派。外部事件与状态快照按真实时间写。

## 回放检查

从起点 Activity 出发，只经 HTTP 读投影与只读 SQL，逐项核对：

- **六问**：应引用的每一项都经读投影取得到，也就是对象的那个版本、非空的块、在主体的取事件里列得出的事件。并且从起点可达：
  - 对象在主干上，或是主干上某个对象的关系引用所指的对象（例如单元长期目标经 `goal_ref` 指到的公司级目标）；
  - 快照的主体、事件的某个主体在主干上。

  另外两条附加要求：Who 问的对象要已有责任人；现状问的快照要仍是其主体的最新一条。
- **八类诱饵**：都在，且各有该有的样子：
  - 旧版本确实不是最新；
  - 别的单元的约束不在主干上；
  - 无关 Mission 的快照主体不在主干上；
  - 已处置的 issue 与过期 artifact 所在的快照都已有更新的快照；
  - 无关事件没有落在起点到 Mission 这段执行链上；
  - 空块确实为空；
  - scope 外的对象在本 scope 读不到（404）。
- **诱饵可达**：
  - Agents 单元的 Mission 经 `depends_on` 指向本 Mission，从本 Mission 取对象时 `referenced_by` 会列出它，所以别的单元的约束、无关 Mission 的状态、无关事件都引得到。
  - scope 外的对象按 id 读不到，它检验的是取法有没有越过 scope。

## 静态截面实验（票 #29、#32 D）

同一组六问、同一模型、同一数据、同一口径，三组只差给模型的工具，都以 E&O Agent 的身份只读，都不给写工具：

- **B 组，固定路径**：经 `tkos-world-mcp` 调一次取上下文，预算与近期窗口用默认值。
- **A 组，模型遍历**：Codex CLI 经 `tkos-world-mcp` 自己走，每问三次。开四个读工具：取对象、取上下文、取事件、取状态。
- **A0 组，纯模型遍历**：同 A，但不给取上下文，只开取对象、取事件、取状态。上一轮 A 组几乎只用取上下文，纯遍历与八类诱饵都没有经受检验（实验报告建议 6）。
- **全量塞入**：scope 内每个对象最新版经取对象返回的字符数，加上每条事件（去重）的事件视图字符数。

A 与 A0 的提示词只差列出的工具，A 组的与上一轮逐字相同。`--groups` 可以只跑其中几组，默认三组都跑，顺序是 B、A、A0。

模型遍历组每次运行都隔离：

- 在空的临时目录里跑，`--ignore-user-config`，不带你的 Codex 配置和其他 MCP server；
- 只读沙盒，关掉 shell、记忆、联网搜索、插件、apps、浏览器与子代理；
- 凭证只经环境变量 `TKOS_WORLD_AGENT_TOKEN` 透传给 MCP server，不上命令行；
- 这一版 Codex 的 MCP 工具经代码模式调用，它的宿主程序在 codex 真实路径旁边，所以跑器按解析后的路径调用 codex；
- MCP server 只开本组的读工具（`enabled_tools`），并设为免批准，因为 exec 下没人能批。

事件流里只要出现本组读工具之外的动作（A0 调了取上下文也算），这次就记为污染。污染的、失败的、没有一次工具调用到达 MCP server 的运行都不计入指标，同一次重跑到有效为止，最多再试 3 次；每次尝试都留在输出目录。

回答按 JSON Schema 写成断言：`claim`、`kind`（fact 陈述内容，gap 说明为空或取不到）、`refs`。指标全部机械计算，不靠模型打分也不靠人读：

| 指标 | 算法 |
|-|-|
| 取到 | 运行日志的 `read_refs` 与 `read_event_ids`：带着内容回来的对象版本、块与事件（取对象会顺带返回最新快照，也算）。只以引用形式出现过的不算 |
| 召回 | 标准答案应引用的项里被取到的比例（所有有效运行合计） |
| 可追溯 | `refs` 非空、且每条引用都被这次运行取到的断言的比例。gap 断言也要引出显示为空的块或对象 |
| 预算 | 模型遍历组：一次运行里工具返回给模型的字符数。B 组：渲染后 Markdown 的字符数（规格的预算口径），另记经 MCP 返回的 JSON 字符数；#32 D 起取上下文经 MCP 只返回包 id、Markdown、覆盖与预算摘要，这个 JSON 比上一轮小得多。全量塞入：每个对象、每条事件各算一次。都先逐问平均再对各问平均；逐问比较，任一问超过全量塞入的一半或高于 B，即为不达标 |
| 确定性 | 同一问三次在运行日志里取到的集合，两两 Jaccard 的平均，再对各问取平均；另记所引集合的一致率。有效运行不足三次的问列为 short，确定性就不算达标 |
| 反例 | 断言引了该类诱饵：诱饵不带块时，指整个对象的任何版本与块。引了比这次播种更旧的版本，一律记为旧版本对象。fact 断言引空块一律记为「空块被当作有内容」，gap 断言引空块不算。另记各组取到的诱饵；B 组没有回答，只看这一项 |

通过标准写进报告，但不作为 0.1 验收门，A 与 A0 各自对照：召回 ≥ 0.9，可追溯 100%，长度不超过全量塞入的一半且不高于 B，确定性 ≥ 0.9，反例零出现。「不高于 B」只对 A0 有意义：A 用的就是 B 的包，只要再多读任何东西就会高于 B。

```sh
# 先照上一节 create 与 run 播种（SEED 为那次 run 的 --private 与 --output 名）；标准答案须已批准
.venv/bin/python -m experiments.world_v01.experiment run --env-file .runtime-acceptance/world-exp-db-$STAMP/env.json \
  --seeded-private .runtime-acceptance/world-exp-$SEED --seeded-output artifacts/runtime-acceptance/world-exp-$SEED \
  --private .runtime-acceptance/world-exp-$STAMP-exp --output artifacts/runtime-acceptance/world-exp-$STAMP-exp \
  --model gpt-6-sol --effort medium   # 只跑其中几组时加 --groups，例如 --groups b a0
.venv/bin/python -m experiments.world_v01.experiment summarize \
  --seeded-output artifacts/runtime-acceptance/world-exp-$SEED --output artifacts/runtime-acceptance/world-exp-$STAMP-exp
```

`summarize` 只按输出目录重算 `summary.json`，有哪个组的目录就算哪个组。输出目录的内容：

- `setup.json`：模型、推理档、Codex 版本、各组的工具、关掉的功能、内容哈希、源码提交；
- `world.json`：全量塞入的长度与空块；
- `b/`、`a/`、`a0/`：每次运行的 `run.json`、`mcp/` 运行日志，模型遍历组另有 `answer.json` 与 `codex.jsonl` 事件流；
- `summary.json`：`a`、`a0` 各自的逐问与合计指标、预算比值（`budget`：`of_full`、`of_b`、`of_b_json`、`over`）与对照通过标准的结论（`verdict`）；`b` 组作对照；`full_chars`。

输出目录是本地产物，不入库；报告与摘要另行写在 `docs/`。

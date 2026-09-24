# E&O 九月回放：播种与标准答案

给 tkos.world/0.1 的静态截面实验（票 #29）准备基准（票 #28）：

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

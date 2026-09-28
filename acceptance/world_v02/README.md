# tkos.world/0.2 独立 API 验收（骨架）

合成身份的真实 HTTP／PostgreSQL 验收：真 API 进程、隔离库、控制面 CLI 安装、prepare 与 commit 驱动每个动作。业务成功只来自 `/v1/actions/prepare` 与 `/v1/actions`，SQL 只用于播种身份与独立核对；每个拒绝用例都在它能到达的入口上核对 scope 的库快照不变。不运行真实模型。

这是骨架：票 #49 立起来，之后每张 0.2 票往 `run.py` 里追加自己的检查，票 #65 汇总成验收矩阵。锁版前不冻结（ADR-0009），所以报告只列每条检查与场景是否跑完，`world_v02_accepted` 一律为 false。全部检查通过、全部场景跑完、源码运行中不变时退出码为 0，否则非 0。

## 实际驱动的路径

- 迁移：0039 作为升级的一步应用、重复为空，它是最新的迁移；事件表带上 0.2 的全部字段；库里已有的事件（同库回归时是 0.1 验收留下的）都是 0.1 行，且没有任何 0.2 字段。
- 控制面：0.2 scope 的每个域装激活策略；0.2 profile 的登记字节与钉定不符时整体拒绝、什么都不留；装上的 profile 钉住契约与登记字节，scope 默认契约为 `tkos.world/0.2`，支持登记只列已实现的动作与类型。另一 scope 装 0.1 作对照。
- #49 Company：
  - CEO 经 HTTP 以 0.2 建 Company，回执写明契约版本。
  - 取对象按 `business`、`identity`、`records` 三组读回：类别、块类别、空块标准句、外部引用、正式内容指针；责任人按角色 CEO 解析；Company 的记录层为空。解释状态为 `world_v0_2`。
  - 恰好一条 0.2 的 `object.created`（记录事件，不带阶段）指回回执，恰好一张回执，绑定经 0.2 闸门钉定 0.2 profile。
  - scope 级读规则、另一 scope 404、回执按 world 规则可读；尚未接 0.2 的读端点（取事件）返回 409，而不是按 0.1 解释。
  - 同键重放返回原回执，同键换命令被拒。
  - owner 回滚事务里的三个探针：伪造 profile 的 0.2 绑定被绑定门拒绝；事件表拒绝带阶段的 0.2 事件、带 0.2 字段的 0.1 事件、代记的记录事件。
- 拒绝（错误码与库快照）：IC 与 Agent 建 Company、授权先于协议错误、多余字段、空内容块、块内组件（随票 #50 开放）、缺 id 的外部引用、0.2 还没实现的类型与动作、第二个 Company、错期望版本、0.2 请求打到默认 0.1 的 scope。
- 并存：同名动作带 0.1 版本由 0.1 处理，0.1 对象读回仍是 0.1 形状，事件写成 0.1 行；0.1 请求打到默认 0.2 的 scope 被拒（`PROTOCOL_BINDING_CONFLICT`）。
- 撤掉 CEO 指派后，原 0.2 命令不能重放成成功（放在最后跑）。

## 没有驱动的路径

其余七类对象、组件与四种引用、修订、快照、事件读取、生命周期、门、Issue、代记、取上下文、MCP 与 CLI 的 0.2 面，各随自己的票加入。以下几项与 0.1 验收一样没有驱动：并发写、进程重启、故障注入、工作台与看板、证据上传、部署。

## 前提

- 隔离验收栈正在运行：`python3 acceptance/runtime/infra.py status`（项目 `tkos-ontology-runtime-acceptance`）。不连 54350/54351 的 Clark 联动栈。
- 基础 env 为仅本人可读的 `.runtime-acceptance/env.json`。不要打印 env、DSN 或令牌，也不要提交 `.runtime-acceptance/`。
- 0039 在锁版前可以重写（ADR-0009）。改写后，已应用旧 0039 的库再迁移会报「已应用的迁移文件被改动过」，每次都用新建的库。`infra.py up` 会把隔离栈的共享库 `tkos_runtime_acceptance` 迁移到当前检出的源码，它同样受这条限制。

## 建库并运行

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.method_v05.database create \
  --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/world-v02-db-$STAMP \
  --output artifacts/runtime-acceptance/world-v02-db-$STAMP
.venv/bin/python -m acceptance.method_v05.database upgrade \
  --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --output artifacts/runtime-acceptance/world-v02-db-$STAMP-upgrade
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.world_v02.run \
  --env-file .runtime-acceptance/world-v02-db-$STAMP/env.json \
  --upgrade-evidence artifacts/runtime-acceptance/world-v02-db-$STAMP-upgrade/upgrade.json \
  --private .runtime-acceptance/world-v02-$RUN \
  --output artifacts/runtime-acceptance/world-v02-$RUN
```

## 同库回归

0.1 验收要求播种前库里没有任何 scope，所以 0.1 先跑：

1. 用 `acceptance.world_v01.database create` 新建库。
2. 用 `upgrade` 迁移到本分支源码（含 0039）。
3. 按 `acceptance/world_v01/README.md` 跑 0.1 独立验收。不给 `--commit` 是开发运行：矩阵全过，但报告不写通过、退出码为 2，这是它的设计。
4. 以同一个库的 env 与 `upgrade.json` 跑上面的 `acceptance.world_v02.run`。

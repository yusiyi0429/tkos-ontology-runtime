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
- #50 业务对象、组件与引用：沿主干建出 Strategy、责任单元、公司级与单元级长期目标、周期目标、Mission、Task、Activity，每类读回块、组件、组件台账与钉定关系，恰好一条 0.2 的 `object.created`。
  - 组件 id 由写入者给或由服务生成，台账从建对象起记下每个组件的类型、所在块与出现的版本。
  - 四种引用：对象（关系字段、组件适用范围）、块（0.1 的整块写法）、组件（责任单元的架构引用、结果逐条引用上层）、事件，都读回业务形式与钉定结构。
  - 人带的写入声明按 Agent 的规则校验，场景可以是责任单元或周期目标；计划条目的责任人只作记录；Activity 标候选类型；按属性解析的责任人在指派前为空。
- 拒绝（错误码与库快照）：IC 与 Agent 建 Company、授权先于协议错误、多余字段、空内容块、不带组件的块里的组件、缺 id 的外部引用、建对象写状态快照、0.2 还没实现的动作、第二个 Company、错期望版本、0.2 请求打到默认 0.1 的 scope。
- #50 拒绝（放在 0.1 对照之后跑，要用到另一 scope 的对象与事件）：指向不存在的对象、版本、块、组件、事件，指向 scope 外的对象与事件；架构引用不指向责任单元条目；同一对象内组件 id 重复、块不允许的组件类型、计划条目的责任人不是本 scope 的主体、组件适用范围不是对象形式、周期目标的依据复盘（票 #60）、场景不是对象形式；Agent 建对象；IC 建 Task；放错域；一个域第二个责任单元。
- #51 修订与建关系（放在 #50 拒绝之后跑）：
  - 合并修订：Mission 改写验收条件与计划条目不换 id、未提到的字段与块保留，追加带 id 的计划条目；旧版本按版本号仍读回原样；有门草稿没有生效修订，无门对象的生效修订随修订移动。
  - 引用稳定：Task 里指向 Mission 版本 1 的组件引用仍钉在版本 1 的修订，新写的引用在版本 2 里按同一 id 找到。
  - 台账：删除一条计划条目记删除版本号；块给 null 清空，其中的组件记删除。拒绝：复用已删 id、组件换块（含同一次修订里删了再在别的块加）、删除本块里不存在的组件。
  - 每次修订恰好一条 0.2 的 `object.revised` 与一张回执，事件钉到新修订；同键重放返回原回执。
  - 周期目标的 `depends_on` 指向周期目标与 Mission，`relate` 事件钉住新修订与列表里的对象，之后的修订保留这条关系。拒绝：指向自己、同一对象两次、指向 Task、周期目标没有 `contributes_to`。
  - 权限：非责任人修订与建关系、持 DRI 角色的 Agent 修订周期目标被拒；Agent 建关系不在 Agent 面上。改挂建对象时写的关系、写只由服务写的字段、错期望版本、目标不是最新修订，各一条拒绝。
  - Agent 修订的声明：不带声明被拒；触及正式块或正式属性而不要求人工验收，在判责任人之前就被拒；只触及活动块与活动属性、不要求人工验收的，过了声明规则，因为不是责任人被拒。
- #52 状态快照、外部事件、迟记与更正（放在 #51 之后跑，时点都取在 Mission 已有事件之后）：
  - 先由单元里的 Agent 记一条外部事件作来源（带写入声明），再写五种 payload 各一条：Mission 的执行状态（进展条目带本期条目、问题组件）、周期目标的目标状态、责任单元的单元状态（均由单元 Agent 写，场景分别是周期目标与责任单元），Strategy 的战略状态（公司域的 Agent），Company 的公司复盘（CEO）；单元 DRI 为 Task 写快照不带声明。每条读回时点、payload 类型、来源事件、生成者（等于写入凭证的主体）并标明未经确认；每条恰好一条 `state.refreshed`，发生时刻等于 `as_of`，钉住快照与主体。
  - 拒绝：payload 类型与主体不符、缺来源事件或为空、来源事件在 scope 外、`as_of` 在将来、请求里给生成者、问题组件缺核心判断问题（HTTP 上补验 #50）、Agent 不带声明、不在主干上的人、在主体所在域不持 AGENT 的 Agent、以快照为主体、同一主体同一时刻换一种时区写法的第二条（`INVALID_STATE`）。同键重放返回原回执。
  - 补记：scope 里另一个域的人补记一条早一天的外部事件，单元 DRI 补写一条时点更早的快照；取事件按发生时刻升序，两条都标迟记，其余不标；按起始时间过滤；每条带类、记录者、产生它的动作与回执 id。取状态按时点返回对应那条，读投影 `records` 给按 `as_of` 最新的一条。
  - 更正：更正外部事件，原事件行不变，读取给出被更正关系；更正建对象事件、状态刷新事件、另一 scope 的事件被拒。外部事件在将来、缺类别、Agent 不带声明、没有主体，各一条拒绝。快照里的问题组件以组件形式作事件主体。
  - 库约束（owner 回滚事务探针）：外部事件的发生时刻晚于记录时刻被拒，补记的外部事件可以写入，其余种类补记被拒；本 scope 已写的全部 0.2 事件都满足这两条。
- 并存：同名动作带 0.1 版本由 0.1 处理，0.1 对象读回仍是 0.1 形状，事件写成 0.1 行；0.1 请求打到默认 0.2 的 scope 被拒（`PROTOCOL_BINDING_CONFLICT`）。
- 撤掉 CEO 指派后，原 0.2 命令不能重放成成功（放在最后跑）。

## 没有驱动的路径

生命周期、门、Issue、代记、取上下文、MCP 与 CLI 的 0.2 面，各随自己的票加入。#51 里 Agent 修订成功的路径没有在 HTTP 上驱动：Agent 只有被指派成 Activity 的责任人才能修订，指派在票 #53，等指派接入后补验（#50 转来的「Agent 写入声明的场景指向周期目标或责任单元」已在 #52 的 Agent 快照上验过）。取事件里的被撤回关系与代记信息（记录者之外的被代记人、外部确认记录）随撤回与代记的票驱动，#52 只验了它们为空。#50 里另有一处没有在 HTTP 上驱动：同 scope 内的非 world 对象（0.2 scope 里建不出来，以另一 scope 的 0.1 对象代替）。以下几项与 0.1 验收一样没有驱动：并发写、进程重启、故障注入、工作台与看板、证据上传、部署。

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

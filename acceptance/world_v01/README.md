# tkos.world/0.1 独立 API 验收（骨架）

合成身份的真实 HTTP／PostgreSQL 验收。业务成功只来自 `/v1/actions/prepare` 与 `/v1/actions`，SQL 只用于播种身份与独立核对；每个拒绝用例都在它能到达的入口上核对整个 scope 的库快照不变。不运行真实模型。

本骨架对应票 #19 到 #22，经 HTTP 驱动的是：

- #19：world 迁移作为升级步骤单独应用且重复为空；控制面 CLI 安装 world profile（同时核对契约与 world 登记字节）、scope 默认策略与支持登记；CEO 建 Company 并经 `GET /v1/world/objects/{id}` 读回（块、引用形式、空块标准句、正式内容指针、`world_v0_1` 解释状态）；恰好一条 `object.created` 事件并指回回执；绑定经 world 闸门钉定 world profile；scope 内任一指派可读、另一 scope 的身份读不到；同键重放返回原回执、同键异命令被拒；IC 与 Agent 建 Company 被拒、载荷多余字段与空内容块被拒、第二个 Company 被拒、错期望版本被拒；撤掉 CEO 指派后原命令不能重放成成功（放在最后跑）。
- #20：CEO 与单元 DRI 沿主干建出 Strategy、责任单元、公司级与单元级长期目标、周期目标、Mission、Task、Activity，取对象读回的属性、关系引用与块内引用都钉到被引用的修订（含修订 id 与业务形式），空块可被引用；有门类型建为 draft、其余 recorded 且生效指针等于最新；每次建对象一条钉定事件、绑定经重钉后的 world 闸门；人不带写入声明被接受，带了的声明随回执钉定留存。拒绝用例（均核对错误信息与库快照不变）：引用指向不存在的对象、版本或块，多余字段与只经 world_relate 写的字段；对象放错域、目标约束不符、关系引用指错类型、一个域第二个责任单元、经建对象写状态快照；非主干责任人建对象；Agent 不带声明或缺任一项、带齐声明但不是责任人；声明的场景不是 Mission 或 Task、验收人不是本 scope 的人。
- #21：合并修订（只改给出的字段与块，块给 null 即清空）与版本链：新版本读到 supersedes 指向上一版本，旧版本按 `?version=` 仍可取到，不存在的版本 404，每次修订一条钉到新版本的 `object.revised` 事件，同键重放返回原回执、同键换补丁被拒；被引用对象出新修订后下级读回的引用仍钉在原版本；建对象时写的引用可改钉到同一对象的新版本；有门类型草稿期修订不动正式内容指针，Task 由单元 DRI 沿主干修订；`world_relate` 整体替换跨链关系列表（可清空，重放不再出修订），关系记在持有字段的对象上、另一端不出新版本但取对象时在 `referenced_by` 列出指向它的关系，每次一条 relate 事件、subject_refs 含两端；`GET /v1/world/objects/{id}/children` 只列最新修订 parent_ref 指向该对象的对象，另一 scope 的身份 404。拒绝用例：非主干责任人修订与建关系、Agent 修订有门类型、Agent 不带声明或声明不要人工验收、错期望版本、修订换挂或去掉建对象时的引用、修订写只由服务写的字段、依赖自己、贡献给本单元的目标或公司级目标、关系指错类型、类型没有的关系字段。
- #22：单元 DRI 与单元里持 AGENT 角色的 Agent（带写入声明）为同一 Mission 写两条不同时刻的快照，快照与主体同域、`as_of` 存 UTC 规范文本、写入即生效；`GET …/state?as_of=` 返回不晚于该时点的最新一条（之前为空、恰在该时刻取到该条），不给时点即最新；取对象附最新一条，直接取快照也标明未经确认；每次写快照一条 `state.refreshed`，subject_refs 依次为快照与主体；同一主体同一时刻（另一种写法）的第二条被拒；写快照同键重放返回原回执、同键换快照被拒。外部事件：occurred_at 早于记录时刻的会议事件按自己的 occurred_at 存；别的单元的 DRI、以及所在域策略不列外部事件的身份都凭 scope 权限记事件；单元 Agent 带声明记事件；更正引用原事件、原事件逐字段不变；`GET …/events?since=` 列出以该对象为主体的全部事件，按 occurred_at 升序、时刻一律 UTC 规范文本、被更正的事件列出更正它的事件、按起始时间过滤；记事件同键重放返回原回执、同键换事件被拒；另一 scope 的身份读不到事件与状态。拒绝用例：没有主体的事件、scope 内已无生效指派的身份（不能记、不能把原命令重放成成功、读不到）、Agent 不带声明写事件或快照、非主干责任人写快照、不持 AGENT 角色的 Agent 写快照、以快照为主体、未发生的事件与未来时点的快照、更正非外部事件。

尚未经 HTTP 驱动（随后续票补齐）：指派与生命周期、承诺与确认的门（含成立后的修订规则）、取上下文、MCP。`summary.json` 的 `world_api_accepted` 固定为 false，只由最终矩阵（票 #27）写 true。

## 前提

- 隔离验收栈正在运行：`python3 acceptance/runtime/infra.py status`（项目 `tkos-ontology-runtime-acceptance`）。本目录工具不启动、停止或重建容器。
- 建库要求 Docker 上下文为 `desktop-linux`。
- 基础 env 为仅本人可读的 `.runtime-acceptance/env.json`。不要打印 env、DSN 或令牌，也不要提交 `.runtime-acceptance/`。

## 建库：基线迁移到 0029，再单独应用 world 迁移

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.world_v01.database create \
  --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/world-v01-db-$STAMP \
  --output artifacts/runtime-acceptance/world-v01-db-$STAMP
.venv/bin/python -m acceptance.world_v01.database upgrade \
  --env-file .runtime-acceptance/world-v01-db-$STAMP/env.json \
  --source src \
  --output artifacts/runtime-acceptance/world-v01-db-$STAMP-upgrade
```

`create` 用基线提交 `ef31b02`（含 0.5）的源码迁移到 `0029_method_v05.sql`，重复迁移为空。`upgrade` 必须恰好应用给定源码中 0029 之后的全部迁移（world 迁移与其后的契约重钉迁移），重复迁移为空；核对迁移已授予应用角色事件表的查询与追加权限，再授予应用角色运行时表权限。

## 运行

```sh
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.world_v01.run \
  --env-file .runtime-acceptance/world-v01-db-$STAMP/env.json \
  --private .runtime-acceptance/world-v01-$RUN \
  --output artifacts/runtime-acceptance/world-v01-$RUN
```

每次运行都用新的 `--private`／`--output` 路径；运行期间不得改动 `src/`。

# tkos.world/0.1 独立 API 验收矩阵

合成身份的真实 HTTP／PostgreSQL 验收：真 API 进程、从含 0.5 的基线新建的隔离库、控制面 CLI 安装、prepare 与 commit 驱动每个动作。业务成功只来自 `/v1/actions/prepare` 与 `/v1/actions`，SQL 只用于播种身份与独立核对；每个拒绝用例都在它能到达的入口上核对错误码与整个 scope 的库快照不变。不运行真实模型。

## 判定规则

- 冻结矩阵在 `matrix.py`：按规格 #17「测试决定」主缝清单分十二组（迁移升级与幂等、控制面安装四步、13 个动作的通过与拒绝、门按目标类型判权、退回与撤回、确认写回与生效指针、快照唯一性、引用钉定、四个读投影、上下文包落表、读权限、MCP 端到端），共 248 项检查，每项只属于一组。
- `COVERAGE` 把 13 个动作逐格对照通过与五类拒绝（错角色、错状态、缺声明、错期望版本、错引用）；不适用的格子在 `NOT_APPLICABLE` 写明理由（建关系、外部事件、指派不看生命周期；门与指派只由人记，没有写入声明）。规格写的「错引用哈希」在 world 0.1 里对应两类：钉不住的引用（world 的引用是 `<对象 id>@<版本号>[#块]`，不带哈希），以及带目标的动作把目标修订写成不是最新的那个。
- 四个环境门槛：运行期间源码不变；应用库角色是普通身份（不是超级用户、不能绕过 RLS，gov_ 表都强制 RLS、不给 DELETE，world 事件表与上下文包表只查询与追加）；不可变历史（owner 打开控制面也改不了 world 事件与上下文包，不声明写能力的应用连接追加不进去）；无外部效果（回执不带效果任务，任务队列里没有这家公司的任务）。
- `report.json` 的 `world_api_accepted` 只有在 248 项全部通过、四个门槛全部通过、十二个场景都跑完且没有运行错误、源码钉在一个提交上（`--commit`）时才为 true。缺脚本、没跑、失败、部分运行、只在工作区上跑通，都不写 true，退出码为 2。判定逻辑由 `tests/test_world_v01_acceptance_matrix.py` 守着。
- 场景日期按运行日换算（9/24 是运行前一天），「近期」窗口与「不能在未来」的规则在哪天重跑都成立。

## 实际驱动的路径

- #19：world 迁移作为升级步骤单独应用且重复为空；控制面 CLI 安装各域激活策略（#24）、world profile（同时核对契约与 world 登记字节）、scope 默认策略与支持登记；CEO 建 Company 并经 `GET /v1/world/objects/{id}` 读回（块、引用形式、空块标准句、正式内容指针、`world_v0_1` 解释状态）；恰好一条 `object.created` 事件并指回回执；绑定经 world 闸门钉定 world profile；scope 内任一指派可读、另一 scope 的身份读不到；同键重放返回原回执、同键异命令被拒；IC 与 Agent 建 Company 被拒、载荷多余字段与空内容块被拒、第二个 Company 被拒、错期望版本被拒；撤掉 CEO 指派后原命令不能重放成成功（放在最后跑）。
- #20：CEO 与单元 DRI 沿主干建出 Strategy、责任单元、公司级与单元级长期目标、周期目标、Mission、Task、Activity，取对象读回的属性、关系引用与块内引用都钉到被引用的修订（含修订 id 与业务形式），空块可被引用；有门类型建为 draft、其余 recorded 且生效指针等于最新；每次建对象一条钉定事件、绑定经重钉后的 world 闸门；人不带写入声明被接受，带了的声明随回执钉定留存。拒绝用例（均核对错误信息与库快照不变）：引用指向不存在的对象、版本或块，多余字段与只经 world_relate 写的字段；对象放错域、目标约束不符、关系引用指错类型、一个域第二个责任单元、经建对象写状态快照；非主干责任人建对象；Agent 不带声明或缺任一项、带齐声明但不是责任人；声明的场景不是 Mission 或 Task、验收人不是本 scope 的人。
- #21：合并修订（只改给出的字段与块，块给 null 即清空）与版本链：新版本读到 supersedes 指向上一版本，旧版本按 `?version=` 仍可取到，不存在的版本 404，每次修订一条钉到新版本的 `object.revised` 事件，同键重放返回原回执、同键换补丁被拒；被引用对象出新修订后下级读回的引用仍钉在原版本；建对象时写的引用可改钉到同一对象的新版本；有门类型草稿期修订不动正式内容指针，Task 由单元 DRI 沿主干修订；`world_relate` 整体替换跨链关系列表（可清空，重放不再出修订），关系记在持有字段的对象上、另一端不出新版本但取对象时在 `referenced_by` 列出指向它的关系，每次一条 relate 事件、subject_refs 含两端；`GET /v1/world/objects/{id}/children` 只列最新修订 parent_ref 指向该对象的对象，另一 scope 的身份 404。拒绝用例：非主干责任人修订与建关系、Agent 修订有门类型、Agent 不带声明或声明不要人工验收、错期望版本、修订换挂或去掉建对象时的引用、修订写只由服务写的字段、依赖自己、贡献给本单元的目标或公司级目标、关系指错类型、类型没有的关系字段。
- #22：单元 DRI 与单元里持 AGENT 角色的 Agent（带写入声明）为同一 Mission 写两条不同时刻的快照，快照与主体同域、`as_of` 存 UTC 规范文本、写入即生效；`GET …/state?as_of=` 返回不晚于该时点的最新一条（之前为空、恰在该时刻取到该条），不给时点即最新；取对象附最新一条，直接取快照也标明未经确认；每次写快照一条 `state.refreshed`，subject_refs 依次为快照与主体；同一主体同一时刻（另一种写法）的第二条被拒；写快照同键重放返回原回执、同键换快照被拒。外部事件：occurred_at 早于记录时刻的会议事件按自己的 occurred_at 存；别的单元的 DRI、以及所在域策略不列外部事件的身份都凭 scope 权限记事件；单元 Agent 带声明记事件；更正引用原事件、原事件逐字段不变；`GET …/events?since=` 列出以该对象为主体的全部事件，按 occurred_at 升序、时刻一律 UTC 规范文本、被更正的事件列出更正它的事件、按起始时间过滤；记事件同键重放返回原回执、同键换事件被拒；另一 scope 的身份读不到事件与状态。拒绝用例：没有主体的事件、scope 内已无生效指派的身份（不能记、不能把原命令重放成成功、读不到）、Agent 不带声明写事件或快照、非主干责任人写快照、不持 AGENT 角色的 Agent 写快照、以快照为主体、未发生的事件与未来时点的快照、更正非外部事件。
- #23：取对象带 lifecycle（段、中文名、推出它的事件 id），公司、战略、责任单元没有生命周期，有门对象建出即草稿；逐级指派：CEO 指派责任单元 DRI（只记事件、不出新版本，被指派者记在回执与取事件里），DRI 指派 Mission Owner（出新版本写 responsible，生命周期不动），Owner 指派 Task（此前写的快照不算，指派后为已指派）与 Activity；Activity 由单元 Agent 写快照进入进行中、记交付进入已交付，Owner 记的验收不能关闭它，Task 责任人记的验收关闭它，每一步取对象的段与事件 id 都对；Owner 凭 responsible 在 Mission 下建 Task、写快照、建关系，Task 责任人修订 Task 并写快照、记交付把它推到已交付，Activity 的 Agent 修订 Activity；改派 Task 后原责任人不能把旧修订重放成成功；指派同键重放返回原回执、同键换人被拒、旧版本冲突。拒绝用例：DRI 越过 Owner 指派 Task、CEO 越级指派 Activity、Task 责任人指派 Activity、Mission Owner 不持 OWNER、Task 责任人是 Agent、不存在的被指派者、指派目标；Owner 被撤掉 OWNER 角色后（仍有 IC 角色）不能再建、不能把原命令重放成成功，他记的验收也不算、Task 仍是已交付。
- #24：各域激活策略经控制面 `install-activation-policy` 安装（门动作的角色取登记门表，通用动作对全部角色开放），每个域一条控制事件；未知角色、空角色列表、另一 scope 的域、数据库在授权纪元推进后才拒绝的内容，四种失败都整体回滚（策略、控制事件与授权纪元都不变）。完整 Mission：Owner 承诺立项（内容尚非正式）→ DRI 接受（被确认的修订成为正式内容）→ CEO 标核心战役（等 CEO 确认、出新版本）→ CEO 确认立项、撤回这条确认（它没有让内容成为正式，正式内容指针不动）、再确认 → 首条快照进入进行中；已成立后直接修订被拒；重走立项门：Owner 承诺带候选、DRI 接受后正式内容仍不变、CEO 接受时写回成新修订（最新与生效指针一起移，确认事件钉它，不另记 object.revised，生命周期仍是进行中）；承诺交付 → DRI 退回（带理由）→ 调整 → 再承诺交付 → DRI 确认关闭，每一步核对段、推出它的事件与正式内容指针，取事件按序列出每条门事件的动作、phase 与 outcome。非核心战役的 Mission 重走在 DRI 接受时写回；重走中被标为核心战役的 Mission，CEO 的一次接受既让它回到已成立也写回候选；已成立后被标记、被 CEO 退回草稿的 Mission 正式内容一并收回，这条退回不能撤回，草稿修订不碰正式内容指针。长期目标：草稿时被退回仍为草稿、修订后 CEO 确认、撤回确认连同正式内容指针一起收回、再确认、CEO 带候选的确认直接写回。周期目标：记错的承诺由 DRI 撤回（原事件保留）、CEO 退回、草稿修订、再承诺、CEO 确认；已确认后直接修订被拒，重走承诺带候选、候选未决时不能再开一轮、CEO 接受时写回；门的重放返回原回执、同键换候选被拒；CEO 退回候选后正式内容不变、再没有可确认的候选。拒绝用例（均核对库快照不变）：七个门动作各一条错角色、错状态、错期望版本；持 OWNER 但不是该 Mission 的 Owner、持 DRI 角色的 Agent、草稿带候选、重走承诺不带候选、门落在不对应的类型、重复标核心战役、换动作撤回、撤回不是推出当前段的门事件。
- #25：`POST /v1/world/objects/{id}/context` 从 Activity 出发沿主干读到 Company（八层，每层取最新修订，检索计划逐跳记下钉定版本与实际读的版本），每个块、快照与对象都带钉定引用，空块用标准句；快照与近期事件只取 Activity、Task、Mission 三层，同一事件只出现一次且都在窗口内；跨链关系只列引用不展开；六问覆盖全部答了且依据都在包里；不再持对应角色的 responsible 不列为责任人；每次调用恰好落一行、行内容与返回一致；两次调用引用集合相同。预算：紧预算下先按时间从旧到新裁事件、再从最远层起裁块，覆盖随之变化（发生了什么成为缺口）；预算小到只剩当前对象时标超预算、当前对象的块与快照照放；每对象事件上限保留最新的一条；近期天数决定窗口。另从状态快照出发（主体为当前对象、状态取这条快照）与从 Company 出发各一条；未指派、没有执行指令的 Activity 报出 What 与 Who 缺口。拒绝用例：另一 scope 的身份、scope 内已无指派的身份、空白问题、超上限的天数、不存在的对象，都不落行（两个 scope 都核对）。
- #26：`tkos-world-mcp` 子进程（mcp SDK 的 stdio 客户端拉起）以单元 Agent 的凭证打真 API：工具清单恰为四读三写、没有门动作、指派与建关系；取对象、取上下文（落一行）、取事件、取状态各一条；修订 Activity、写快照、记外部事件各一条经 prepare 与 commit 提交、回执的调用者是该 Agent；经安装后的 `tkos-world-mcp` 命令入口启动；三个写工具不带声明或记事件缺任一项声明时被 HTTP 面拒绝，经 MCP 拿到的错误体与直接打 HTTP 的逐字相同，库快照不变；取上下文落的行记在该 Agent 名下；运行日志每次调用一行、带引用集合、上下文包 id 与渲染字符数、写入的幂等键，不含凭证。依赖：验收环境需装开发依赖组（含 mcp）。

- #27：建库与升级证据逐项核对（基线提交与 0029、播种前没有任何 scope、升级恰好应用这份源码 0029 之后的迁移且文件 SHA256 一致、两次重复都为空、最新迁移是 world 的）；控制面前三步各自的落库与控制事件（profile 钉住契约与登记字节、scope 默认协议是 world、支持登记列出 13 个动作与九类对象），profile 的契约或登记字节与钉定不符时整体拒绝、什么都不留；补齐动作逐格拒绝：修订引用不存在的版本，建关系缺声明、错期望版本、引用不存在的版本，写快照错期望版本、主体版本不存在，记外部事件错期望版本、主体版本不存在，七个门动作各一条内容引用不存在的版本；十个带目标的动作（修订、建关系、指派与七个门）各一条目标修订不是最新（期望版本对、修订旧）被拒；持 DRI 角色的 Agent 指派被拒；四个环境门槛（钉提交时，运行结束再核对一次工作区验收代码与契约文件）。

## 没有驱动的路径

- 真实模型与 Codex CLI：MCP 由 mcp SDK 的 stdio 客户端拉起，不经 Codex。上下文包的召回、可追溯、预算与确定性等质量指标属于票 #29 的实验；预算默认值（12000 字符、每对象 10 条事件、30 天）是暂定的。
- 并发写（per-scope 授权栅栏下的竞争）、API 进程重启恢复、事务中途故障注入：本矩阵没有驱动，沿用内核在方法矩阵与 v0.2 验收里的结论，未对 world 动作重跑。
- 工作台与看板：world 0.1 没有工作台页面，`/v1/governance/*`、`/v1/dashboard/*` 读 world 对象未驱动。
- 证据上传：world 0.1 的支持登记 `evidence_upload` 为 false，块内 artifacts 只是链接；原始证据存储不作为门槛。
- 既有容器与部署文件保留：不作为门槛（运行期间 54350 那套 Clark 联动栈自己会变状态），本工具不启动、停止或改动容器。
- 部署、生产身份、生产数据迁移。

## 前提

- 隔离验收栈正在运行：`python3 acceptance/runtime/infra.py status`（项目 `tkos-ontology-runtime-acceptance`）。本目录工具不启动、停止或重建容器。
- 建库要求 Docker 上下文为 `desktop-linux`。
- 基础 env 为仅本人可读的 `.runtime-acceptance/env.json`。不要打印 env、DSN 或令牌，也不要提交 `.runtime-acceptance/`。

## 建库：基线迁移到 0029，再单独应用 world 迁移

```sh
COMMIT=$(git rev-parse HEAD)   # 要冻结的提交；acceptance/ 与 world 契约文件的工作区内容必须与它一致
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.world_v01.database create \
  --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/world-v01-db-$STAMP \
  --output artifacts/runtime-acceptance/world-v01-db-$STAMP
.venv/bin/python -m acceptance.world_v01.database upgrade \
  --env-file .runtime-acceptance/world-v01-db-$STAMP/env.json \
  --commit $COMMIT \
  --output artifacts/runtime-acceptance/world-v01-db-$STAMP-upgrade
```

`create` 用基线提交 `ef31b02`（含 0.5）的源码迁移到 `0029_method_v05.sql`，重复迁移为空。`upgrade --commit` 用该提交 `git archive` 出的源码迁移（开发时可改用 `--source src`），必须恰好应用 0029 之后的全部迁移（world 迁移与其后的契约重钉迁移），重复迁移为空，并记下每个迁移文件的 SHA256；核对迁移已授予应用角色事件表的查询与追加权限，再授予应用角色运行时表权限。每次冻结验收都用新建的库。

## 运行

```sh
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.world_v01.run \
  --env-file .runtime-acceptance/world-v01-db-$STAMP/env.json \
  --commit $COMMIT \
  --database-evidence artifacts/runtime-acceptance/world-v01-db-$STAMP/database.json \
  --upgrade-evidence artifacts/runtime-acceptance/world-v01-db-$STAMP-upgrade/upgrade.json \
  --private .runtime-acceptance/world-v01-$RUN \
  --output artifacts/runtime-acceptance/world-v01-$RUN
```

`--commit` 让 API 进程、控制面 CLI 与 MCP server 都跑该提交取出的 `src`（放在 `--private` 下），工作区里未提交的改动不进验收；验收代码（整个 `acceptance/`，含 world 验收导入的共享助手）与它读的 world 契约文件在工作区运行，开跑前核对它们与该提交一致。不给 `--commit` 就是开发运行：用工作区 `src`，矩阵可以跑通，但报告不写通过。每次运行都用新的 `--private`／`--output` 路径。验收环境需装开发依赖组（含 mcp）。

## 冻结检查点

只有通过的报告才能生成公开摘要与冻结检查点：

```sh
.venv/bin/python -m acceptance.world_v01.summarize \
  --report artifacts/runtime-acceptance/world-v01-$RUN/report.json \
  --summary docs/acceptance/world-v01-summary.json \
  --checkpoint docs/world-v01-freeze-checkpoint.md
```

报告没通过、源码在运行中变过、矩阵不是冻结的那份，都拒绝生成。检查点列出提交、源码清单 SHA256，以及相对基线改动过的 `src/` 文件与 world 契约、登记、profile、支持登记的逐个 SHA256。

# TKOS Ontology Runtime

TKOS 企业本体与记忆系统的治理运行内核。PostgreSQL 保存权威状态与审计记录，版本化 S3/MinIO 保存原始证据，API 接收有权限、带版本和幂等键的动作，Worker 执行已授权的外部效果。

当前支持 M1A 战略研究与更新、M1B 经营目标核对与定稿，并保留 Contract-A 的协议治理、公司组合与 DRI–IC 执行交接，以及既有 v0.2 交付能力。不同协议分别保留对象含义、角色和生效规则。

Runtime 提供 Clark 现有月度核对、周进展和会议工作面的 Runtime 场景接口：当前身份、聚合读取、字段定位、本人核对与确认、来源材料、会议发布及分流记录。新增 `tkos.workspace/0.1` 保持 Method 正式生效规则不变。见 [伙伴接入契约](docs/clark-workspace-integration.md)、[场景 OpenAPI](docs/runtime-workspace-openapi.json) 和 [本地验收报告](docs/runtime-workspace-acceptance.md)。Runtime 接口已通过本地验收；Clark 由伙伴维护，接线、浏览器与真实模型验收分别待完成，本扩展纳入 v0.3.0。

已合并的 `tkos.method/0.3` 提供 Anchor 与 CEO Agent 立项能力：整体版本化 Architecture、Operating State、Problem 及原子移交。见 [伙伴接口包](docs/runtime-anchors-v03-integration.md) 和 [隔离验收](docs/runtime-anchors-v03-acceptance.md)。该增量纳入 v0.3.0；本机 API/Worker 已随看板镜像更新，旧对象仍保留原协议绑定。

已合并的 `tkos.method/0.4` 把 CEO Agent 立项、Agreement 全体精确确认与多 PCO 候选整组激活纳入正式链，并新增 `tkos.workspace/0.2` 独立来源场景（无业务锚点的会议／文档／本人选定对话、来源版本与更正、精确分享、默认私有）。同期新增本机**治理工作台**：有权本人以个人会话办理白名单动作，Runtime 只负责权限、版本、正式效力与回执。该增量纳入 v0.4.0，迁移头到 0028；0.1/0.2/0.3 对象保留原协议绑定与原生效规则。

已合并的 `tkos.world/0.1`（[PR #30](https://github.com/yusiyi0429/tkos-ontology-runtime/pull/30)）是独立的业务世界模型协议，按 CEO《企业业务世界建模框架》把公司表达为九类一级对象、内容块、少量关系，以及所有对象共有的状态快照与事件；同时提供按主干组装上下文的 Context Runtime 和给 Agent 的 MCP 入口。它与 `tkos.method` 并存，语义互不改写。迁移头到 0036；0037 是随后追加的授权修复，收回只读角色在三张只增表上的写权。该协议已纳入 v0.5.0；Clark 尚未接线，未做生产部署。

## 当前交付状态

当前功能 Release 为 **[v0.6.0](https://github.com/yusiyi0429/tkos-ontology-runtime/releases/tag/v0.6.0)**，在 v0.5.0 之上纳入 2026-09-30 锁版的 `tkos.world/0.2`（块与组件按方法侧 Content Pact 首版替换，Activity 转为正式类型，只在新建 scope 启用；钉定哈希见[锁版检查点](docs/world-v02-freeze-checkpoint.md)），`tkos-world-mcp` 与新增的 `tkos-world` 命令行都可选 0.2。迁移头到 0039。

本次交付 wheel、源码包、**amd64/arm64 完整五镜像离线包**及 SHA256 校验和，详见[发布说明](docs/releases/v0.6.0.md)与[离线验收](docs/releases/v0.6.0-offline-acceptance.md)。tag、包版本、离线包文件名与镜像标签统一用同一个版本号（`v0.6.0`，镜像为 `<仓库>:v0.6.0-<架构>`）。v0.5.0 的[发布说明](docs/releases/v0.5.0.md)与[离线验收](docs/releases/v0.5.0-offline-acceptance.md)作为上一版证据保留。

| 验证范围 | 结果 |
| --- | --- |
| Python 回归 | 无库全量 **4538 passed**（本机）；数据库测试（应用角色与迁移所有者）、看板与构建以 #89 的 CI 为准，全部通过 |
| 双架构离线包 | arm64 **25/25**（原生）、amd64 **25/25**（仿真），各自空卷启动：迁移至 0039、FORCE RLS、证据版本化与保留、真实治理动作、world 0.1 与 0.2 的控制面安装与 HTTP 冒烟、Worker 任务 |
| v0.5.0 → v0.6.0 升级（arm64） | **11/11**：只补上 0039，升级前的数据、证据与回执仍可读，world 0.1 数据仍按 0.1 读，新 scope 可启用 0.2 |
| 协议独立验收（沿用最近一次运行） | world 0.2 **871/871**（`46fb876`）、world 0.1 **248/248**（`d261eb9`）、Method 0.5 **27/27**、Method 0.4 **68/68**、v0.2 运行时 **20/20**（`a6d1efa`） |
| 伙伴接线／Clark 浏览器／真实模型／生产部署 | **尚未验证／尚未验证／未运行／未进行** |

验收使用隔离数据库、独立合成身份和受控 Agent 输入。详见 [0.3 验收报告](docs/runtime-anchors-v03-acceptance.md)、[机器检查清单](docs/acceptance/anchors-v03-summary.json) 和 [复跑入口](acceptance/anchors_v03/README.md)。企业身份、历史对象跨版本接续、生产迁移与部署另行安排。

原 Method 0.1 的 [PR #3](https://github.com/yusiyi0429/tkos-ontology-runtime/pull/3) 验收作为历史基线保留：98/98 项、8/8 环境门槛及旧协议兼容，见 [原验收报告](docs/runtime-method-acceptance-report.md)。历史验收结果不计作本轮重新执行。

## World 0.1：业务世界模型与 Context Runtime

`tkos.world/0.1` 已纳入 v0.5.0；Clark 尚未接线，未做生产部署。给 Agent 侧联调的独立实例按 [deploy/world-lab/](deploy/world-lab/README.md) 部署，不计作生产部署。

- **对象**：九类一级对象。
  - 公司、战略、责任单元、长期目标、周期目标、Mission、Task、Activity、状态快照。
  - 每类固定「定义类块 + 约束 + 状态快照 + 关系引用」。块值是文字、引用与文档链接三件；空块存空，读时渲染标准句。
  - 引用一律钉到对象的某个版本、某个块。
- **动作**：13 个，经 prepare 与 commit 提交。记外部事件按 scope 判权，其余由激活策略判权。
  - 建对象、修订对象、建立跨链关系、指派。
  - 写状态快照、记外部事件。
  - 承诺与确认的门：承诺周期目标或 Mission；确认长期目标、周期目标或 Mission；标记核心战役，以及 CEO 确认核心战役立项。
  - 生命周期由事件推导，每类一张状态机写在契约里。
- **读投影**：scope 内任一生效指派都可读。
  - 取对象：`GET /v1/world/objects/{id}`，可带 `?version=`。
  - 取状态：`…/state?as_of=`。
  - 取事件：`…/events?since=`。
  - 取上下文：`POST …/context`。沿主干组装分层上下文包，同时给出检索计划、覆盖与预算裁剪记录，并落表留存。
- **MCP**：`tkos-world-mcp` 是 stdio MCP server，作为 HTTP 面的薄壳，四读三写，先接 Codex CLI。它在可选依赖组 `mcp` 里，基础安装不带。配置与运行日志见 [Codex 接入说明](docs/world-mcp-codex.md)。
- **CLI**：`tkos-world` 是给人和脚本用的命令行，作为 HTTP 面的薄封装，第一版只做 Agent 面：四个读投影、取上下文，以及记外部事件、写状态快照、修订无门对象三个写动作（prepare 再 commit）；门动作、指派、建关系与建对象不暴露，允许的动作之内权限由 HTTP 面按凭证判定。它在基础安装里，用法见 [CLI 说明](docs/world-cli.md)。

契约见 [tkos-world-0.1.md](docs/contracts/tkos-world-0.1.md)，登记与 profile 钉定见 [world 登记](docs/contracts/world-registry-0.1.json) 与 [world profile](docs/contracts/world-profile-0.1.json)，支持登记见 [runtime-world-support-0.1.json](docs/runtime-world-support-0.1.json)。契约、登记或 profile 以后再改，都要追加重钉迁移。

| 验证范围 | 结果 |
| --- | --- |
| 独立验收矩阵 `acceptance/world_v01/` | **248/248** 项、12/12 组、4/4 环境门槛，钉在提交 `0a6736a` 上运行，`world_api_accepted: true`。见 [验收报告](docs/world-v01-acceptance-report.md)、[冻结检查点](docs/world-v01-freeze-checkpoint.md) |
| 冻结后差异复验（#32） | 在 `d261eb9` 上重跑同一矩阵：**248/248**、12/12、4/4。覆盖冻结后的 MCP 与取上下文调整、迁移 0037 与 0038 及 #32 各批次的内核改动，见 [复验检查点](docs/world-v01-recheck-checkpoint.md) |
| Python 回归 | 验收时（`0a6736a`）应用角色 **1329 passed、2 skipped**，迁移所有者 **15 passed**，见验收报告。此后每个提交由 [CI](.github/workflows/ci.yml) 按[同一组命令](#测试)执行 |
| E&O 九月回放静态截面实验（不作为验收门） | 第二轮（#32，gpt-6-sol）四组对照：F（固定上下文作答）召回 1.00、回答引用的覆盖 0.67，每次 1.3 万字符，是全量塞入的 0.16 倍；A（遍历 + 取上下文）召回 1.00、覆盖 0.57，每次 2.9 万字符（第一轮 11.8 万）；A0（纯遍历）召回只有 0.56；B（固定取上下文，不作答）经 MCP 的返回由 5.6 万字符降到 1.3 万。各组可追溯 100%、反例 0。「A 不高于 B」按现有定义谁都过不了，口径待定；Why 各组仍弱。见 [第二轮报告](docs/world-v01-experiment-round2.md)、[第一轮报告](docs/world-v01-experiment-report.md) 与 [播种与标准答案审阅稿](docs/world-v01-eo-september-review.md) |
| Clark 接线、生产部署 | **尚未进行**（Agent 侧联调实例不计） |

实验报告里的调整建议 1–4 与 6 已在 #32 批次 D 落到代码并重跑：取上下文经 MCP 只给包 id、Markdown、覆盖与预算摘要，Why 沿 `goal_ref` 多取一跳，Markdown 开头按问题给出处，事件行写出人名，跑器分 B、A、A0 三组。关键回答的内容对不对，由 E&O DRI 人工核验，结果另补。播种、实验跑器与指标在 `experiments/world_v01/`。main 上是压缩合并的 `7e42004`；文档里引用的逐票提交（`0a6736a`、`d5b158e`、`3fdbe83`、`2d2dca7`、`fdaeeab`）在标签 `world-v0.1-tickets`，也就是 PR #30 的分支头。

## 评估整改（#32）

2026-09-24 的两份外部评估（外部专家、Codex）逐条核实后，按批次 A–E 整改，追踪票 [#32](https://github.com/yusiyi0429/tkos-ontology-runtime/issues/32)。已纳入 v0.5.0；未做生产部署。

| 验证范围 | 结果 |
| --- | --- |
| 试点链 Method 0.5 × 治理工作台（`acceptance/pilot_workbench_v05/`） | **29/29**（`206b7de`）：登录、待办、0.5 门动作的正式回执、业务拒绝、CSRF、幂等与响应丢失、API 重启恢复、会话面边界。它找出的三处缺陷都已修。登录后的浏览器复核尚未进行，见[摘要](docs/acceptance/pilot-workbench-v05-summary.json) |
| 运行与恢复基线（`acceptance/runtime_baseline/`） | 阈值 T1–T8 先定后测，改动后 **8/8**，见[容量基线](docs/runtime-capacity-baseline.md) |
| 交付候选的干净环境安装（`acceptance/delivery_candidate/`） | **10/10**（`cf5edb6`）。从干净检出构建 wheel 与镜像；全新离线栈空库迁移到 0038；只用镜像里的材料经控制面装好 Method 0.4、0.5 与 world 0.1，并经 HTTP 办真实动作；v0.4.0 的迁移文件逐字节未变，旧库升级到迁移头。见[交付清单](docs/acceptance/delivery-candidate-cf5edb6.json) |
| 独立验收重跑 | Method 0.5 **27/27**、Method 0.4 **68/68**、world 复验 **248/248**（`d261eb9`）；v0.2 运行时 **20/20**（`a6d1efa`） |
| 尚待人或目标环境完成 | 登录后的浏览器复核；第二轮实验关键回答的人工核验；企业身份与正式 profile 的裁决（[ADR-0008](docs/adr/0008-enterprise-identity-and-formal-profile-path.md)）；目标环境的部署与恢复演练 |

## Runtime 经营看板：`tkos.dashboard/0.1`

面向业务读者的本机只读看板（React + TypeScript + Vite + Tailwind + 官方 shadcn/ui，编译进 wheel），
按 `当前正式 Strategy → Architecture → LTCO/PCO → Mission → 经营状态/事实/复盘/问题` 的类型化精确引用层级展示。
默认关闭；启用后由 FastAPI 提供 `/dashboard/` 静态资源与显式 GET 只读 `/dashboard/api` facade，
浏览器不接触任何凭据。浏览不产生业务、回执、复盘、Context 快照或任务写入。

- 本体可视化增量：默认“本体地图”，按五个业务区域展开类型；可切换 0.1/0.2/0.3 规则，沿“类型说明 → 实际记录 → 业务关系图”查看精确版本和引用。详见[设计与交互](docs/runtime-ontology-views-design.md)、[本轮独立验收](docs/runtime-ontology-views-acceptance.md)。原有本机容器仍是此前版本。
- 新版本地验收：前端 **94**、后端 **101**、新增本体 HTTP/SQL **46**、0.3 场景 **58**、0.1 副本 **53** 项通过；真实浏览器、只读数据库核对和 wheel/sdist 资源校验通过。[本轮源码预览](http://127.0.0.1:58806/dashboard/)使用隔离合成数据，已纳入 v0.3.0，尚未部署新版容器。
- 契约与运行：[看板读取契约](docs/runtime-dashboard.md)、[字段来源映射](docs/runtime-dashboard-field-mapping.md)、[实际 OpenAPI](docs/runtime-dashboard-openapi.json)。
- 构建：`./scripts/build_dashboard.sh`（只接受 `npm ci` + typecheck + vitest + vite build + 构建清单）；`uv build` 的 Hatch hook 与 Dockerfile 都会按清单校验资源，源码改动未重建会使打包失败；`scripts/verify_dashboard_assets.py --archive` 逐文件 hash 校验 wheel/sdist。
- 原列表看板验收基线：0.3 隔离 HTTP/PG/MinIO **58/58**（[复跑](acceptance/dashboard_0_3/README.md)）、真实 0.1 数据只读 **49/49**（[复跑](acceptance/dashboard_0_1/README.md)）、Python dashboard 回归 76、Node 前端回归 56。
- 原容器访问：[Runtime 经营看板](http://127.0.0.1:58802/dashboard/)。API/Worker 已按 `eccc346` 重建，保留现有 0.1 数据、Clark 及 PostgreSQL/MinIO 数据卷；当前仍为五容器。
- 原列表看板独立浏览器与本机部署通过，见[验收报告与截图](docs/runtime-dashboard-acceptance.md)；[复跑与回滚](docs/runtime-dashboard-deployment.md)。业务同事理解度、Clark 接线/浏览器及真实模型另行验证；本增量已纳入 v0.3.0。`/docs` 保留不变。

## Method 0.5：本体 v0.7 对齐

`tkos.method/0.5` 按《TKOS 本体结构 v0.7（M1 范围）》把本期 M1 对象落进正式链：Constraint 一级对象与按范围确认、LTCO 审视结论、CEO 确认复盘并由下期 PCO 承接、Mission 的贡献／依赖／资源与责任域 DRI 唯一承诺、生成即正式的带起止时间状态，以及公司集合视图与确认记录投影。契约见 [tkos-method-0.5.md](docs/contracts/tkos-method-0.5.md)，profile 同时钉定 [本体登记 0.7.1](docs/contracts/ontology-registry-0.7.json)；动作注册表见 [0.5 注册表](docs/runtime-method-registry-0.5.json)；实施与验收状态见 [Method 0.5 状态](docs/method-05-implementation-status.md)；复跑入口在 `acceptance/method_v05/`。迁移头到 0029；0.1–0.4 对象保留原协议绑定与原生效规则。

## Method 0.4：正式链收口与独立来源

- **CEO Agent 直接立项**：绑定 Agent 可直接提出 issue、reframe 或关联既有议题；立项本身不授予战略确认权，CEO 本人仍独立指派研究。
- **Agreement 全体精确确认**：被提名的每位当事人各自对同一精确 revision 确认，全部到齐后 Agreement 转 formal；`no_change=true` 的共识不驱动战略更新。
- **多 PCO 候选与整组激活**：窗口收拢产出候选组，责任 DRI 与 Mission Owner 各自按「责任」承诺（`gov_method_commitments`，同一候选版本下每个责任对象只承诺一次），CEO 只能整组确认，不能代替任何人承诺。
- **Battlefield ＋ Domain 双主责范围**：LTCO/PCO 的 `primary_scope_id` 可指向战场或责任域。
- **`tkos.workspace/0.2` 独立来源**：无业务锚点的会议／文档／本人选定对话场景，来源身份与版本、更正与撤回、精确分享无遍历、默认私有与来源围栏、不可变 Context 快照并在读取时重查当前授权；撤权后不恢复正文与自由文本。

契约与接线材料见 [Method 0.4 冻结契约](docs/contracts/tkos-method-0.4.md)、[workspace 0.2 契约](docs/contracts/tkos-workspace-0.2.md)、[动作注册表](docs/runtime-method-registry-0.4.json)、[工作台 OpenAPI](docs/runtime-governance-openapi.json)、[workspace OpenAPI](docs/runtime-workspace-v02-openapi.json)、[伙伴事件映射](docs/partner-ui-event-mapping.md)与[workspace 接入说明](docs/workspace-v02-integration.md)。验收范围见[验收矩阵](docs/method-04-acceptance-matrix.md)、[交付报告](docs/method-04-delivery-report.md)与[合并前复核](docs/acceptance/method04-premerge.md)；复跑入口在 `acceptance/method_v04/` 与 `acceptance/workspace_v02/`。真实业务模型与 Clark 未运行。

## 本机治理工作台

有权本人的个人会话入口：Runtime 负责权限、版本、正式动作与回执，Clark 继续负责业务交互、模型与 Agent 编排。**没有数据库任意编辑器，也没有 Agent 身份选择器。** 默认关闭（`TKOS_GOVERNANCE_WORKBENCH_ENABLED`）。

按「你要做的事」分四组：处理核对与确认（我的待办、方法事项）、查看业务与依据（业务主线、正式 Mission、业务关系图）、理解本体与规则（本体地图、业务定义）、管理来源与追溯（独立来源、我的提交）。

- **方法事项**：全部人工白名单动作的 typed 表单——参与人提名、Agreement 本人确认、正式更新最终确认、LTCO 确认、候选本人承诺与 CEO 整组激活、候选/窗口重开、经营状态确认、问题登记/修订/关闭。精确引用由已授权对象选择器派生，提交前展示完整预览。
- **业务主线**：`战略 → 长期目标 → 阶段目标 → 任务` 四栏，选中任一卡片后其下游按该卡片的**精确 revision** 收窄。收窄复用服务端语义——战略走 `basis=current` 的递归依据解析，LTCO/PCO 走 `/objects/{id}/downstream?revision_id=`（只返回记录 `payload_hash` 指向该精确版本的子对象）。Mission 不记录 `ltco_ref`，因此只选 LTCO 时任务栏如实提示需再选阶段目标，不伪造过滤。某栏缺某状态时显示「暂无」，不以另一状态填补。
- **会话边界**：同源 ＋ CSRF ＋ `no-store`，登录限流，重绑/重置与过期处理；浏览器不接触任何治理凭据。

详见 [启用与分工](docs/runtime-governance-workbench.md)、[隔离验收](docs/acceptance/runtime-governance-workbench.md)与[复跑入口](acceptance/governance_workbench/README.md)。本轮不代表 Clark 接线、真实模型或远程部署完成。

## Method 0.3：Anchor 与 Agent 立项

- **Architecture**：整体独立版本化，Battlefield／Capability 为稳定 ID 定义项；相关 DRI／Agent 提出、CEO 确认，Strategy 同步变化时原子确认一致版本。目标明确引用结构，历史依据保留。
- **Operating State**：覆盖 Mission、LTCO、PCO 及 Outcome，保存推荐、确认、人工修正、观察时点、基准与证据。正式状态由对应当前责任人确认，Mission Owner 不必兼任 DRI。
- **Problem → M1A**：复盘发现与经营问题进入统一候选池；CEO Agent 立项或关联已有议题，成功后原 Problem 原子移交并停止原跟踪。CEO 本人随后独立指派研究；立项不授予战略确认权。
- **版本与效力**：新对象显式绑定 `tkos.method/0.3`，0.1／0.2 保留原规则与回执。周复盘确认、会议发布等场景记录不自动产生战略更新、目标生效或执行授权。

伙伴从 [0.3 接口包](docs/runtime-anchors-v03-integration.md)、[冻结契约](docs/contracts/tkos-method-0.3.md)、[OpenAPI](docs/runtime-anchors-v03-openapi.json) 和 [请求模板](docs/runtime-anchors-v03-examples.json) 接线；0.2 生命周期对照见 [接入说明](docs/runtime-lifecycle-v02-integration.md)。Clark 页面、BFF、模型调用与 Agent 编排由伙伴维护，本仓库提供 Runtime 能力与验收材料。

## Method 0.1 基线：战略研究与经营目标定稿

独立协议 `tkos.method/0.1` 采用 M1A L4 revision 21、M1B L5 revision 837，提供完整底座 API：

`战略议题 → 研究澄清与预审 → 会议纪要 → Agreement → 战略更新 → 事实与周期复盘 → LTCO 审视 → PCO＋Mission 共同核对 → CEO 整组确认`

49 个动作共用现有身份、精确版本、原始证据和治理事务。纪要、Agreement、战略更新分别确认；PeriodReview 无需审批；新版 CEO 确认不继承旧 A2 全体 DRI 签认条件。战略改版保留已确认目标的基线和效力，待确认方案须复核变化后的依据。新 Mission 提供明确的下游承接投影，执行授权仍需独立约定。

详见 [Method API 与调用说明](docs/runtime-method-api.md)、[完整 OpenAPI](docs/runtime-method-openapi.json)、[Clark 接入契约](docs/method-clark-contract.md)、[独立验收报告](docs/runtime-method-acceptance-report.md) 和 [可复跑矩阵](acceptance/method_independent/README.md)。本轮范围是本地合成身份下的 API 验收，Clark 页面、真实身份、生产迁移和部署另行安排。旧 Contract-A 与 legacy 保持各自原义。

## A3：正常 DRI–IC 执行交接

Contract-A 在 A1 协议治理、A2 公司组合之上增加正常执行交接：

`公司组合生效 → 指定 DRI 与 IC 同版签认 → 释放执行授权／独立验收任命 → 下达任务 → IC 接收 → 发布计划 → v1 → 退回 → v2 → 独立验收`

What、IC 的 How、执行授权和验收任命分别记录。交付通过、Outcome 达成与 MF 关闭仍分别判断；IC 撤权或执行到期后禁止新执行，已提交内容可由仍有权的独立验收人评审。只覆盖实验 Profile 下的正常交接和合成结果证据；不含替岗、临时授权、正式调整、Clark A3 界面或部署。

详见 [A3 API](docs/runtime-a3-api.md)、[工程映射](docs/runtime-a3-engineering.md)、[验收记录](docs/runtime-a3-acceptance-report.md) 与 [可复跑矩阵](acceptance/execution_a3_independent/README.md)。下述 v0.2 闭环保留其原有角色和对象含义。

## v0.2：真实 DRI 交付闭环

`派单 → DRI 承接 → 提交 v1 → 有权人退回补充 → 提交 v2 → 有权人验收`

WorkItem 固定承诺版本、指定 DRI、指定验收人及标准。Deliverable 每次提交生成不可变版本，v2 显式回应上轮退回记录；交付验收、Outcome 达成判断和 MF 关闭使用独立记录与动作。交付通过不会自动判定经营目标达成，也不会自动关闭反馈。详见 [v0.2 契约](docs/runtime-v0.2-dri-delivery-contract.md)。

此协议的 DRI 为生效 ExecutionCommitment 的 MISSION_DRI 签认人；Outcome 评估先覆盖 CompanyOutcome，由当前 CEO 权限判断。该 v0.2 交付入口已完成 Clark 本地联调，见[联调报告](docs/clark-v0.2-acceptance-report.md)。改派、任务重基线和完整企业身份接入后续单独完成。

## 当前能力

- Method 独立协议、严格对象 schema、角色动作及不可变版本；战略更新与经营目标基线之间的影响追溯。
- M1A 研究、预审、会议和正式更新循环；M1B 独立事实、无需审批的复盘、共同核对窗口与 CEO 整组确认。
- 既有 Contract-A／legacy 的精确版本签认、承诺激活、管理调整、执行交接与交付验收。
- 数据库中的身份与域权限、强制 RLS、撤权后旧回执和快照的读取校验。
- 乐观版本检查、不可变 revision/ActionReceipt、请求重放和冲突拒绝。
- 原始证据上传与 hash/version 校验，独立人员验收后显式关闭反馈。
- 按身份、阶段、用途、有效时间和记录时间生成 Context Pack，保留采用版本与排除原因。
- 轻量运行关联、步骤尝试、暂停和恢复查询，事务回滚与幂等重放。
- 持久任务队列、Worker 崩溃重试，以及接收端持久幂等账本验证。

服务负责执行治理规则与保留证据；经营判断的正确性仍由有权的人确认。应用负责页面与流程编排，生产身份由后续身份系统对接提供。

## 接口与代码

当前源码 API 快照见 [工作台 OpenAPI](docs/runtime-governance-openapi.json) 与 [workspace 0.2 OpenAPI](docs/runtime-workspace-v02-openapi.json)，Method 0.4 动作的注册信息见 [0.4 注册表](docs/runtime-method-registry-0.4.json)。上一版 [0.3 OpenAPI](docs/runtime-anchors-v03-openapi.json) 与 [0.3 注册表](docs/runtime-method-registry-0.3.json) 作为对应版本文档保留。各 OpenAPI 快照内嵌的是导出当时的应用版本，不随发布号改写。运行中服务的实际版本以其 `/openapi.json` 为准。原 Method 0.1 的 [API 指南](docs/runtime-method-api.md)、[49 个动作 schema](docs/runtime-method-actions.json) 和 [接入契约](docs/method-clark-contract.md) 保留用于对应版本，不作为 0.3 的权限规则。

既有 v0.2 的 [OpenAPI 快照](contracts/openapi.json)、[Runtime 契约](docs/runtime-independent-contract.md)和[实现说明](docs/runtime-implementation-notes.md)作为对应版本文档保留。

| 目录 | 用途 |
| --- | --- |
| `src/memory_service_runtime/governed/` | 本体对象、Action、授权、证据与回执 |
| `src/memory_service_runtime/` | 持久队列与 Worker |
| `src/memory_service_app/` | FastAPI、迁移与配置 |
| `src/memory_service/` | 原有 Memory 核心与数据库迁移 |
| `src/adapter/` | 保留的 Clark 只读兼容接口 |
| `acceptance/anchors_v03/`、`acceptance/lifecycle_v02/`、`acceptance/workspace_scenes/` | 0.3 Anchor、0.2 生命周期和 Clark 场景接口隔离验收 |
| `acceptance/method_v04/`、`acceptance/workspace_v02/` | Method 0.4 正式链与 workspace 0.2 独立来源隔离验收 |
| `acceptance/method_v05/` | Method 0.5 本体对齐链隔离验收 |
| `acceptance/world_v01/` | World 0.1 独立验收矩阵与冻结检查点 |
| `src/tkos_world_mcp/` | World 0.1 的 stdio MCP server（`tkos-world-mcp`） |
| `src/tkos_world_cli/` | World 0.1 的命令行（`tkos-world`） |
| `experiments/world_v01/` | E&O 九月回放的播种、标准答案与静态截面实验 |
| `acceptance/governance_workbench/` | 本机治理工作台会话、权限与真实 HTTP/数据库验收 |
| `acceptance/method_independent/` | M1A＋M1B 完整 API、权限、并发、恢复与历史兼容验收 |
| `acceptance/composition_a2_independent/`、`acceptance/execution_a3_independent/` | A2 公司组合与 A3 执行交接独立验收 |
| `acceptance/runtime/` | 既有 v0.2 HTTP、数据库、S3、故障和恢复验收 |
| `acceptance/pilot_workbench_v05/` | 试点链 Method 0.5 × 治理工作台的 HTTP／数据库验收 |
| `acceptance/runtime_baseline/` | 容量与恢复基线（阈值 T1–T8） |
| `acceptance/delivery_candidate/` | 交付候选的干净环境安装验收与交付清单 |
| `docs/contracts/` | Method 与 World 的冻结规则、登记与 Profile |
| `tests/` | Method、旧协议、叙述与 Worker 回归测试 |

Python 包名 `tkos-memory-service`、模块名和 CLI 保持兼容。Clark 应用由伙伴维护，不在本仓库中；当前兼容读取接口不能代替新的 Clark 联调验收。长任务续租不属于当前实现。WorkItem/Deliverable 的 API 与 Clark 独立交付入口已完成本地联调。

## 本体叙述与记忆收敛

新增 `POST /v1/context-graph/narrative`，保持 Clark 默认 NarrativeClient 的请求、Bearer 鉴权与返回字段。启用 `TKOS_NARRATIVE_ENABLED=1` 后，读取当前权限下的精确版本及历史交付、Outcome、MF 状态，返回带来源的确定性叙述；不创建业务记录、ActionReceipt 或 Context 快照。

历史语义记忆可在单独的 `read_legacy_context` 授权下参与检索，模型只压缩历史背景，三项治理结论独立保留。详见 [叙述接口契约](docs/narrative-convergence-contract.md)、[本轮验收记录](docs/narrative-convergence-acceptance.md)、[可复跑验收](acceptance/narrative/README.md) 和 [迁移工具](deploy/convergence/README.md)。代码接入、真实数据迁移演练及生产切换分别记录，不因兼容接口存在就宣称旧服务已替换。

## 测试

[CI](.github/workflows/ci.yml) 在每次推送 main 与每个 PR 上跑下面这组检查，本地命令相同：

```bash
uv sync --frozen --extra s3
uv run pytest tests -q -m "not db"          # 无库测试，不需要 DATABASE_URL
uvx ruff@0.16.7 check --select E9,F63,F7,F82 src tests acceptance scripts deploy experiments hatch_build.py
python3 acceptance/runtime/infra.py up       # 本机隔离 PostgreSQL＋MinIO；CI 用 attach 接一次性服务容器
python3 acceptance/runtime/infra.py run -- .venv/bin/python -m pytest tests -q -m "db and not owner"
python3 acceptance/runtime/infra.py run --migration -- .venv/bin/python -m pytest tests -q -m owner
(cd workbench/dashboard && npm ci && npm run typecheck && npm test) && python3 scripts/verify_dashboard_assets.py
uv build
```

- 要数据库的测试都标了 `db`。没有 `DATABASE_URL` 时它们直接失败，不会退回本机默认库。
- 应用角色那一轮，在 RLS 生效下跑全部数据库测试。迁移所有者那一轮，只跑标了 `owner` 的测试：它们要建临时库或角色，或经 bootstrap 播种。
- 这些测试不代替各协议的独立验收矩阵。

## 本地独立验收

需要 Python 3.12+、uv、Docker Engine 与 Compose。新建隔离库统一用 [`acceptance/method_v05/database.py`](acceptance/method_v05/README.md)（只接受 `acceptance/runtime/infra.py` 起的隔离验收栈，不连 54350/54351 的 Clark 联动栈），再按各验收目录的 README 运行当前源码 API；若进入容器联调，须重新构建镜像并记录源码、镜像和契约版本。

原 M1A＋M1B 基线请按 [Method 独立验收说明](acceptance/method_independent/README.md)执行（该矩阵固定在 2026-09-11 那次运行，只升级 0021，HEAD 上不能原样复跑）：保留环境基线 → 创建隔离库 → 捕获真实旧历史 → 应用 0021 并验证重放 → 旧能力回归 → 完整 Method HTTP 场景及历史复核。只有 98 条检查与 8 个环境门槛全部通过，报告才设 `method_api_accepted: true`。API 使用普通应用数据库角色，控制面与迁移使用独立身份。

以下命令用于**既有 v0.2 验收**，不能替代 Method／A2／A3 独立矩阵。先按 [v0.2 验收说明](acceptance/runtime/README.md)缓存固定镜像，再在本仓库目录执行：

```bash
uv sync --frozen --extra s3
python3 acceptance/runtime/infra.py up
.venv/bin/python acceptance/runtime/run.py
```

该脚本创建独立 PostgreSQL/MinIO 数据卷，生成私有测试凭据，并以本机子进程启动 API/Worker。其业务矩阵包含原有 15 组检查和 5 组 v0.2 交付检查，并执行回归、迁移重放、打包和恢复验证。它会短暂停启自己的数据服务，不应并行执行多个操作相同数据环境的完整 runner。

本地凭据在 `.runtime-acceptance/`，运行证据在 `artifacts/`，均不提交。保留凭据目录与数据卷作为一组；脚本不提供删除数据卷的命令。

## 历史验收证据

后续 **Clark v0.2 本地业务联调已通过**：真实页面的 DRI v1/v2 交付、独立 Outcome 评估与 MF 关闭，以及响应丢失后刷新重试、服务不可用和重启检查。详见 [联调报告](docs/clark-v0.2-acceptance-report.md) 与 [联调启动方法](acceptance/clark_v02/README.md)。本次为 Clark 新增独立交付入口，使用合成个人测试身份，未发布或部署远程。以下独立验收报告仍作为各自时间点的历史证据保留。

2026-09-07，v0.2 完整独立验收 `runtime-a15a50851637` 通过 **20 组检查、164 passed、1 skipped**，迁移重放、sdist/wheel 构建、数据卷重启及独立备份恢复均通过。跳过项仍是缺少 `agent_service` fixture 的原有非 human viewer 检查。

详见 [v0.2 验收报告](docs/runtime-v0.2-acceptance-report.md)、[脱敏摘要](docs/acceptance/runtime-v0.2-summary.json) 与[当时的源码清单](docs/acceptance/runtime-v0.2-source-sha256.json)。该报告记录的是 Clark v0.2 联调之前的本地 API/Worker 验收；后续本地联调结论见上方独立报告。

以下是 v0.1 的历史验收基线：

2026-09-07，导出前的独立验收运行 `runtime-2e941f404803` 通过全部 15 组检查，原有测试为 **137 passed, 1 skipped**，sdist/wheel 构建通过。跳过项是缺少 `agent_service` fixture 的原有非 human viewer 检查。

公开保留 [脱敏验收摘要](docs/acceptance/runtime-independent-summary.json) 和 [87 个生产源文件 SHA256](docs/acceptance/production-source-sha256.json)。v0.1 导出时生产源文件逐字节一致；后续 v0.2 修改使用新的源码清单；原始数据库、对象存储内容、令牌及原始运行产物未公开。该结果是本地 Runtime 验收，不表示 Clark 集成、容器目标架构或远程部署已经验收。

导出后的独立仓库再次执行完整验收：`runtime-246ed4a1bc03` 同样通过 **15 组、137 passed, 1 skipped**，包含构建和恢复验证。详见 [独立仓库复验摘要](docs/acceptance/standalone-export-summary.json) 与 [本次源码清单](docs/acceptance/standalone-source-sha256.json)。本次复验使用 Python 3.13.14，本地 API/Worker 为 Python 进程；Dockerfile 的 Python 3.12 容器运行另需目标环境验证。

## 部署与来源

离线发布说明见 [v0.6.0 发布说明](docs/releases/v0.6.0.md) 与[离线验收](docs/releases/v0.6.0-offline-acceptance.md)；历史版本见 [v0.5.0](docs/releases/v0.5.0.md)、[v0.4.0](docs/releases/v0.4.0.md)、[v0.3.0](docs/releases/v0.3.0.md) 与 [v0.2.1](docs/releases/v0.2.1.md)。AMD64 与 ARM64 分包均包含 Runtime API、Worker、PostgreSQL 17＋pgvector、MinIO Server 和 MinIO Client 五类镜像；Clark 与宿主机 Nginx 不在包内。镜像中的 Method 控制面输入位于 `/opt/tkos/docs/`；发布版本与生产切换分别验收。v0.2.0 只包含 AMD64 API／Worker，已由 v0.2.1 替代。

代码可构建 API/Worker 镜像，远程试点还需要环境初始化和部署验收，见 [部署边界](docs/deployment.md)。根目录没有可直接投产的 Compose；历史 Memory 模板仅保留在 `deploy/legacy-memory/`。

本仓库以独立源代码快照初始化，来源及基线见 [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)。

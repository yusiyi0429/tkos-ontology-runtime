# TKOS Ontology Runtime

TKOS 企业本体与记忆系统的治理运行内核。PostgreSQL 保存权威状态与审计记录，版本化 S3/MinIO 保存原始证据，API 接收有权限、带版本和幂等键的动作，Worker 执行已授权的外部效果。

当前支持 M1A 战略研究与更新、M1B 经营目标核对与定稿，并保留 Contract-A 的协议治理、公司组合与 DRI–IC 执行交接，以及既有 v0.2 交付能力。不同协议分别保留对象含义、角色和生效规则。

Runtime 提供 Clark 现有月度核对、周进展和会议工作面的 Runtime 场景接口：当前身份、聚合读取、字段定位、本人核对与确认、来源材料、会议发布及分流记录。新增 `tkos.workspace/0.1` 保持 Method 正式生效规则不变。见 [伙伴接入契约](docs/clark-workspace-integration.md)、[场景 OpenAPI](docs/runtime-workspace-openapi.json) 和 [本地验收报告](docs/runtime-workspace-acceptance.md)。Runtime 接口已通过本地验收；Clark 由伙伴维护，接线、浏览器与真实模型验收分别待完成，本扩展纳入 v0.3.0。

已合并的 `tkos.method/0.3` 提供 Anchor 与 CEO Agent 立项能力：整体版本化 Architecture、Operating State、Problem 及原子移交。见 [伙伴接口包](docs/runtime-anchors-v03-integration.md) 和 [隔离验收](docs/runtime-anchors-v03-acceptance.md)。该增量纳入 v0.3.0；本机 API/Worker 已随看板镜像更新，旧对象仍保留原协议绑定。

## 当前交付状态

当前功能 Release 为 **[v0.3.0](https://github.com/yusiyi0429/tkos-ontology-runtime/releases/tag/v0.3.0)**，包含 Clark 场景接口、Method 0.2 生命周期、Method 0.3 Anchor，以及经 [PR #6](https://github.com/yusiyi0429/tkos-ontology-runtime/pull/6) 合并的只读看板、本体地图与业务关系图。

本次交付 wheel、源码包、**amd64/arm64 完整五镜像离线包**及 SHA256 校验和，详见[发布说明](docs/releases/v0.3.0.md)和[离线验收](docs/releases/v0.3.0-offline-acceptance.md)。两架构均通过业务/看板 58 项、本体接口/数据库 46 项检查；amd64 使用 Docker 仿真验收。本机原 API/Worker 仍运行 `eccc346` 镜像，新界面为 58806 源码预览；尚未部署新版容器或远程生产环境。

| 验证范围 | 结果 |
| --- | --- |
| Method 0.3 实际 HTTP／PostgreSQL／MinIO 增量 | **35/35**：Architecture、State、Problem、Agent 立项、原子移交、权限与恢复、M1B 回归 |
| Method 0.2 独立回归 | **22/22**：生命周期、关窗竞争、候选原子性与旧版共存 |
| Python 回归 | **699 passed、1 既有 skipped**；legacy 集成另 **14 passed** |
| 迁移与构建 | 空库迁移至 0025 及重复执行 **1 passed**；wheel／sdist 构建通过 |
| Workbench 模块回归 | **61 passed**；不代表 Clark 浏览器验收 |
| 伙伴接线／Clark 浏览器／真实模型 | **尚未验证／尚未验证／未运行** |

验收使用隔离数据库、独立合成身份和受控 Agent 输入。详见 [0.3 验收报告](docs/runtime-anchors-v03-acceptance.md)、[机器检查清单](docs/acceptance/anchors-v03-summary.json) 和 [复跑入口](acceptance/anchors_v03/README.md)。企业身份、历史对象跨版本接续、生产迁移与部署另行安排。

原 Method 0.1 的 [PR #3](https://github.com/yusiyi0429/tkos-ontology-runtime/pull/3) 验收作为历史基线保留：98/98 项、8/8 环境门槛及旧协议兼容，见 [原验收报告](docs/runtime-method-acceptance-report.md)。历史验收结果不计作本轮重新执行。

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
- 原列表看板独立浏览器与本机部署通过，见[验收报告与截图](docs/runtime-dashboard-acceptance.md)；[复跑与回滚](docs/runtime-dashboard-deployment.md)。业务同事理解度、Clark 接线/浏览器及真实模型另行验证；本增量已纳入 v0.3.0。旧的 `workbench/` 四页原型与 `/docs` 保留不变。

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

当前源码 API 快照见 [0.3 OpenAPI](docs/runtime-anchors-v03-openapi.json)，61 个 Method 0.3 动作的注册信息见 [注册表](docs/runtime-method-registry-0.3.json)。运行中服务的实际版本以其 `/openapi.json` 为准。原 Method 0.1 的 [API 指南](docs/runtime-method-api.md)、[49 个动作 schema](docs/runtime-method-actions.json) 和 [接入契约](docs/method-clark-contract.md) 保留用于对应版本，不作为 0.3 的权限规则。

既有 v0.2 的 [OpenAPI 快照](contracts/openapi.json)、[Runtime 契约](docs/runtime-independent-contract.md)和[实现说明](docs/runtime-implementation-notes.md)作为对应版本文档保留。

| 目录 | 用途 |
| --- | --- |
| `src/memory_service_runtime/governed/` | 本体对象、Action、授权、证据与回执 |
| `src/memory_service_runtime/` | 持久队列与 Worker |
| `src/memory_service_app/` | FastAPI、迁移与配置 |
| `src/memory_service/` | 原有 Memory 核心与数据库迁移 |
| `src/adapter/` | 保留的 Clark 只读兼容接口 |
| `acceptance/anchors_v03/`、`acceptance/lifecycle_v02/`、`acceptance/workspace_scenes/` | 0.3 Anchor、0.2 生命周期和 Clark 场景接口隔离验收 |
| `acceptance/method_independent/` | M1A＋M1B 完整 API、权限、并发、恢复与历史兼容验收 |
| `acceptance/composition_a2_independent/`、`acceptance/execution_a3_independent/` | A2 公司组合与 A3 执行交接独立验收 |
| `acceptance/runtime/` | 既有 v0.2 HTTP、数据库、S3、故障和恢复验收 |
| `docs/contracts/` | Method 冻结规则与实验 Profile |
| `tests/` | Method、旧协议、叙述与 Worker 回归测试 |

Python 包名 `tkos-memory-service`、模块名和 CLI 保持兼容。Clark 应用由伙伴维护，不在本仓库中；当前兼容读取接口不能代替新的 Clark 联调验收。长任务续租不属于当前实现。WorkItem/Deliverable 的 API 与 Clark 独立交付入口已完成本地联调。

## 本体叙述与记忆收敛

新增 `POST /v1/context-graph/narrative`，保持 Clark 默认 NarrativeClient 的请求、Bearer 鉴权与返回字段。启用 `TKOS_NARRATIVE_ENABLED=1` 后，读取当前权限下的精确版本及历史交付、Outcome、MF 状态，返回带来源的确定性叙述；不创建业务记录、ActionReceipt 或 Context 快照。

历史语义记忆可在单独的 `read_legacy_context` 授权下参与检索，模型只压缩历史背景，三项治理结论独立保留。详见 [叙述接口契约](docs/narrative-convergence-contract.md)、[本轮验收记录](docs/narrative-convergence-acceptance.md)、[可复跑验收](acceptance/narrative/README.md) 和 [迁移工具](deploy/convergence/README.md)。代码接入、真实数据迁移演练及生产切换分别记录，不因兼容接口存在就宣称旧服务已替换。

## 本地独立验收

需要 Python 3.12+、uv、Docker Engine 与 Compose。当前 0.3 增量先按 [Anchor 隔离验收说明](acceptance/anchors_v03/README.md) 创建新库并运行当前源码 API；若进入容器联调，须重新构建镜像并记录源码、镜像和契约版本。

原 M1A＋M1B 基线请按 [Method 独立验收说明](acceptance/method_independent/README.md)执行：保留环境基线 → 创建隔离库 → 捕获真实旧历史 → 应用 0021 并验证重放 → 旧能力回归 → 完整 Method HTTP 场景及历史复核。只有 98 条检查与 8 个环境门槛全部通过，报告才设 `method_api_accepted: true`。API 使用普通应用数据库角色，控制面与迁移使用独立身份。

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

离线发布说明见 [v0.2.1 Release 指南](docs/releases/v0.2.1.md)。AMD64 与 ARM64 分包均包含 Runtime API、Worker、PostgreSQL 17＋pgvector、MinIO Server 和 MinIO Client 五类镜像；Clark 与宿主机 Nginx 不在包内。镜像中的 Method 控制面输入位于 `/opt/tkos/docs/`；发布版本与生产切换分别验收。v0.2.0 只包含 AMD64 API／Worker，已由 v0.2.1 替代。

代码可构建 API/Worker 镜像，远程试点还需要环境初始化和部署验收，见 [部署边界](docs/deployment.md)。根目录没有可直接投产的 Compose；历史 Memory 模板仅保留在 `deploy/legacy-memory/`。

本仓库以独立源代码快照初始化，来源及基线见 [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)。

### 本机 M1B 治理工作台（开发分支）

个人身份办理评论、替代/撤回、差异核对、CEO 整组确认及重开，查看正式 Mission 和提交回执。默认关闭；保留本体地图、业务关系图和只读模式。详见 [启用与分工](docs/runtime-governance-workbench.md)及[隔离验收入口](acceptance/governance_workbench/README.md)。本轮不代表 Clark 接线、真实模型或远程部署完成。

# tkos.method/0.4 ＋ tkos.workspace/0.2 验收矩阵

本矩阵把用户确认的 0.4／workspace 0.2 规则映射到可执行验收项，并记录**当前实际状态**。最终交付报告见 [method-04-delivery-report.md](method-04-delivery-report.md)。契约文字与编译支持仍分开：注册表按 scope 显式启用，方法地图按实际支持状态诚实报告；矩阵不再声称“仅 B0+B1”或“0.4 未编译”。

独立验收使用隔离 PostgreSQL＋对象存储与真实 HTTP；单元测试不替代独立验收。受控 Agent 的 HTTP/DB 独立验收**计入 Runtime API 验收**（例如 qa-final-v04），只不代表真实模型验收；仅 mock/fixture 断言不构成独立验收。pi 开发所用的模型选择（deepseek-flash thinking=max）与业务/受控 Agent 的 `model` 元数据无关。

## 当前状态（root 校准）

| 批次 | 状态 | 独立证据（仅引用 root 授权编号） |
|---|---|---|
| B0 契约／基线 | 已交付 | 见 `docs/contracts/tkos-method-0.4.md`、`tkos-workspace-0.2.md`；基线回归由 root 记录 |
| B1 方法地图／业务定义视图 | 已交付 | B1 独立 HTTP/UI 证据在 root QA 记录中，本矩阵不重复计数 |
| B2 workspace/0.2 独立来源后端 | 已交付并独立验收 | `qa-final-workspace` = **64 checks passed**（2 真实私有来源、来源 ACL 内部 Method 引用、Agent 自建 Context/run、creator-purpose 撤销、通用路径、当前 Agent binding、drop-response、重启、receipt 修复、SQL 无副作用） |
| B3+B4 正式 0.4 后端 | 已交付并独立验收 | `qa-final-v04` = **29 正向 + 39 负向 passed** |
| 旧协议 v0.3 | 已交付并独立验收 | `qa-final-v03` = **35 passed**（含 `source_unchanged`） |
| B5 人类工作台 | 实现中；facade 读写缺口与 journal 隐私测试已产出 | 本仓库 facade 验收（见 `acceptance/workspace_v02/facade_sessions.py` 与私有 `facade-findings.md`）：prepare/commit/get/list/retry 与撤权 journal 场景；新增 facade 读取 URL 仍缺注册 |
| 浏览器（root 独立） | **本地受控链已完成** | DRI A publish+replace+second opinion；DRI B publish+withdraw+new；controlled Co-agent HTTP close/resolve 3 份有效意见恰好一次（unresolved、正文不变）；4 承诺→CEO 整组激活→Owner 正式 Mission；final DB check：confirmed、2 正式 Mission、4 承诺、5 评论含 1 撤回、0 执行/0 work receipt；map04 定义→2 授权记录 PASS。截图见交付报告 |
| 回归（root final） | 套件 Python **919 pass / 16 skip** + 独立迁移 replay **1 pass** = **920 total**；UI **165 pass / 21 files**，typecheck、uv build、manifest 72 inputs/8 outputs PASS | 仅引用 root 已执行结果；skip 原因见交付报告（1 health env、1 0.4 fail-closed by design、14 legacy 隔离守卫） |
| 迁移 / 冻结 | 0026、0027 与两个不同文件名的 0028 已应用；**fresh create→all migrations→replay 0→drop PASS**；root 标记 source frozen（`index-CvwLgGIZ.js`） | 不做发布/部署；旧服务未动 |
| 真实模型／伙伴 | **未运行／未验证** | 不声称真实模型或伙伴接线通过 |

边界声明：本轮源码已完成评审冻结和本地验收；未提交、发布或部署。旧协议 HTTP 回归结果见上表。

## A. B1 方法地图／业务定义视图验收项（已交付）

| ID | 验收项 | 期望 |
|---|---|---|
| A01 | 44 项清单映射 | `GET /v1/dashboard/ontology/method-map` 返回 I01–I44 恰好 44 条，字段完整；条目数量不随数据变化 |
| A02 | 来源与版本 | 每条含 B01 表/记录定位；快照含 B01–B10 来源、版本/指纹与检查日期；不复制完整来源文档 |
| A03 | 未实现标记 | `reusable_partial`／`pending_business_close`／`requires_contract_change`／`not_implemented` 与实现支持分离；无运行时对象的概念保留条目 |
| A04 | 业务成熟度 vs 实现支持 vs 数据可得性 | 三个字段分别返回；默认 `not_queried`，不返回可读记录条数 |
| A05 | 0.4 支持状态诚实标记 | 方法地图按编译与 scope 登记分别报告 0.4 状态（`documented_not_compiled`／`compiled_not_enabled_in_scope`／`enabled_in_scope`）；0.4 已在隔离验收 scope 编译并启用（qa-final-v04），状态由真实注册表推导；未登记 scope 仍失败关闭 |
| A06 | 运行时链接 | 只为已编译对象类型提供目录链接；0.4 专属概念不提供数据链接 |
| A07 | 授权 | 无当前读取授权身份返回 403；不存在的类型不进入计数；`availability=query` 只返回 visible/none_visible/failed |
| A08 | 旧视图不受影响 | 旧版本特定解释与本数据图保持；最新业务地图不自动更新服务器规则 |
| A09 | UI | 类型卡片显示来源、分类、成熟度与实现支持；运行时链接只在支持时出现；未启用契约明确标注 |
| A10 | 业务定义视图 | 独立视图按四类业务身份显示全部 44 项概念（含未实现的 Play／Human+AI Plan 与 To Define 参考项），每项含目的与简明定义；实现支持、本 scope 启用、授权数据分别标注，互不替代；旧协议视图保持独立 |
| A11 | 部分可见语义 | 一个对象类型可见、另一个未读到时报 `partially_visible` 与逐类型状态，不把 `none_visible` 说成“不可读” |

## B. 0.4 与 workspace 0.2 门槛验收项（已交付，状态见上）

| ID | 验收项 | 期望 |
|---|---|---|
| B01 | 初始空 scope | 通过 0.4 Issue → Agreement → 已复核提案 → CEO 确认创建初始 Strategy＋Architecture；无预插业务成功；无正式初始对时初始议题可明确缺少现有 Strategy |
| B02 | 参与人全体确认 | CEO 提名必要当前人类参与人（可含 CEO）；每人确认同一精确版本后才正式；Agent 不能冒充确认 |
| B03 | 失效与重查 | 正文/证据/必要参与人变化使待确认全部失效；正式化重查全部必要签署人当前任职；历史正式记录不可变 |
| B04 | 正式更新链 | CEO Agent 依 Agreement 提确切变更；Co-agent 复核；CEO 最终确认原子更新；无独立 should-adjust 审批；无变化仅记录共识；仅 Architecture 变更也需 Agreement 但不强制研究/会议 |
| B05 | 陈旧基线 | 提案锁定确切 Agreement 与依据；Agreement/依据/对象版本变化时拒绝并重新准备；回滚无残留 |
| B06 | 直接立项 | CEO Agent 用本人 MethodRun/Context 直接创建、重新界定、关联议题；无 PotentialIssue Gate；研究/证据可选；无 Judgment/Decision/StrategicMission/Close 平行正式链 |
| B07 | 多 PCO 整组 | 支持多个 PCO；窗口冻结完整 PCO＋Mission 集合；收拢恰好覆盖全部冻结成员与全部有效意见一次；Runtime 分配未来版本号 |
| B08 | 承诺与激活 | 具名 DRI/Owner 仅能提交本人责任承诺；集合变化使承诺失效；CEO 最后整组 CAS 原子激活；关键未决依赖阻止激活；无执行/验收副作用 |
| B09 | State | LTCO→CEO、PCO→Scope DRI、Mission→唯一 Owner；确认重查当前任职（含 Owner≠DRI、撤权）；无证据→Unknown＋缺口；无公司/域聚合 State、无自动 RAG 平均；PeriodReview 无审批 |
| B10 | Architecture | Battlefield＋Domain 稳定 ID 独立版本；Required Capability 为 Strategy 语义并分配 Domain 主要责任；Business Scope 显式映射、不隐式授权；旧引用保留 |
| B11 | 问题移交 | 议题与来源关联后同事务 `transferred`＋`tracking=false`；失败保持原跟踪；移交≠解决；无 Strategic Signal |
| B12 | 幂等与恢复 | 丢响应重放同一信封返回原回执；并发写、进程重启、权限变化、旧协议历史回归 |
| B13 | 人类工作台 | 提名、Agreement 审阅/确认、变更复核/最终确认、承诺/候选、State 推荐/更正/确认、问题移交历史均可在工作台 preview/submit/receipt/retry；浏览器不持有 Agent 凭据。状态：0.4 后端 qa-final-v04 通过；同一案例浏览器+controlled Co-agent 全链（评论/收拢/承诺/整组激活/正式 Mission）已由 root 独立验证 |
| B14 | workspace 0.2 独立来源 | 无业务锚点场景；来源系统/ID/版本/指纹/acquired_at/分段；更正不可变；两份会议时间线分离；Context 创建/读取与恢复重查；精确分享不扩散；Scene Agent 运行记录（不伪造 MethodRun）；草稿仅引用确切片段并区分事实/请求/建议/已接受；无正式效力；来源不可读时撤销派生正文；审计元数据按当前授权。状态：**独立验收通过**（qa-final-workspace = 64）；浏览器 2 会话分享/撤销与 Context 撤权扣留已验证 |
| B15 | 伙伴与真实模型分离报告 | OpenAPI、UI 事件映射、身份示例、拒绝/陈旧/未知/重试用例；Runtime 浏览器与 Clark 浏览器/真实模型状态分开报告。状态：**部分**；OpenAPI/示例/事件映射已交付；本地受控链（浏览器+controlled Agent HTTP/DB）已完成；迁移 replay PASS、source frozen；真实业务模型与 Clark 未运行；伙伴未验证 |

## 记录要求

每次验收报告必须分别列出：已执行检查、跳过项、失败项、未验证的部署边界。仅 mock/fixture 的断言不构成独立验收；**受控 Agent 的 HTTP/DB 独立验收计入 Runtime API 证据**，只不代表真实模型验收。仅引用 root 最终独立证据编号（qa-final-workspace=64、qa-final-v03=35、qa-final-v04=29+39、qa-facade02=47、Python 919 通过/16 跳过，另迁移重放 1 通过；UI 165 通过）；不得把 pi 开发模型当作业务真实模型，不得把源码评审冻结等同于发布。

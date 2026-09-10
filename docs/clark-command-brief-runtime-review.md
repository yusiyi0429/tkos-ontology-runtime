# Clark 批准并下达与 Runtime 衔接核对

核对日期：2026-09-09。Clark 主线：`82a94e0a60d6fc946029710288eec77f84bcd3e3`，应用版本 v0.142.0。Runtime 基线：`c977da9588f48f08b7f76b6828e75e0b02576076`。

## 结论与本轮范围

Clark 主线已快进合并到本地 `codex/runtime-v0.2-clark-integration`，原有本地联调代码保留。版本号、研究界面、行动整理、批准规则和 `CommandBrief` / `DecisionOrder` 类型均遵循 Clark 主线。

**批准流程与 Runtime 交付闭环尚未接通。** Clark 当前把批准结果记入应用内决议账；Runtime 的既有交付链从已激活承诺下的 WorkItem 开始。前者不会自动创建后者。本轮核对与回归不能作为“批准后已通知 DRI”或“决议已经进入 Runtime”的验收证据。

用户确认：Clark 尚未开发这条流程的正式 DRI 使用界面。既有 `/delivery` 是使用合成身份的独立验收工具，正式交互由 Clark 后续开发。本轮通过接口验证 Runtime，未新增 DRI 产品页面。

## 核对结果

| 边界 | Clark 当前行为 | Runtime 当前要求 | 结论 |
|---|---|---|---|
| 批准入口 | 先进入行动整理，报告完整性与行动就绪条件通过后才允许批准；暂缓不产生命令 | 只接受注册的 typed Action；每次检查当前策略和个人权限 | 保留 Clark 的研究与决议规则；它们不能替代 Runtime 授权 |
| 批准身份 | 共享入口解析为 Clark CEO；DRI 无权调用三个决议 RPC | 个人 principal、有效 assignment、domain、action policy | 共享 CEO cookie、DRI 链接、Runtime cookie 不能互相授予权限 |
| 下达产物 | 本地 `DecisionOrder`，包含 `issuedAt`；代码明确尚未通知 DRI、未写回本体 | 创建 WorkItem 返回 committed Receipt；初始状态为 offered | `issuedAt` 只表示 Clark 记账，不能投影为 Runtime 已派单或已承接 |
| 负责人 | `role` 为描述，`driId` 可为空，由人输入 | DRI 必须是上游 ExecutionCommitment 的有效 MISSION_DRI 签认人 | 需要经验证的人员/任职映射，不能按角色文字或名称猜测 |
| 承诺基线 | `DecisionOrder` 没有 Runtime 承诺引用 | 当前有效 ExecutionCommitment 精确 revision、双方签认、激活 | 缺失时保持待交接，不能由服务账号自动补签 |
| 验收 | `acceptance` 为文本数组，尚无具体 Runtime 验收人 | 冻结 criterion IDs、指定有效验收人，DRI 与验收人不同自然人 | 接入前补齐，不能默认“批准人就是交付验收人” |
| 时间 | 日期 `dueOn` 与相对时间 `dueNote` 分开 | `due_at` 为带时区的时间戳，可缺省 | 相对时间保留原文；不能擅自换算为具体日期或时刻 |
| 版本和重试 | `briefAt` 是报告时间；批准 API 没有 Runtime expected_version/幂等信封 | 精确 revision、依赖版本、同请求同幂等键、不可变 Receipt | 衔接层须冻结已批准来源；报告时间不足以替代内容版本 |
| 承接与交付 | 本轮命令尚无正式 DRI 承接/交付入口 | 本人承接 → v1 → 有权人退回 → v2 回应上轮退回 → 指定人验收 | 在正式界面完成前，通过独立接口与合成身份验证 |
| 结束状态 | `Decided` 表示议题已拍板；命令状态由时间戳推导 | 交付验收、OutcomeAssessment、MF closure 三套独立判断 | 任何一个 Clark 时间戳或议题状态都不能自动关闭这三套流程 |

对应源码：Clark `src/lib/data/store.ts` 的 `enterActionStaging`、`approveCommandBrief`、`listDecisionOrders`，`src/lib/data/types.ts` 的 `DecisionOrder`，`src/lib/data/scope.ts`，`src/app/api/data/route.ts`；Runtime `src/memory_service_runtime/governed/models.py`、`delivery.py` 和 [v0.2 契约](runtime-v0.2-dri-delivery-contract.md)。

Clark 当前 BFF 只允许创建 Decision / MetricObservation，不允许直接创建 WorkItem 或 ExecutionCommitment。这个限制继续保留；不能为了让按钮“看起来接通”而扩大代理权限。

## 后续接口适配顺序

以下是后续实施契约，不是本轮已经上线的接口。

1. **冻结 Clark 批准产物。** 来源至少包含应用实例/租户、issueId、decisionId、DecisionOrder.id、briefAt、已批准内容与 hash，以及需要保留的指挥边界、资源约束和证据引用。原报告可被后续研究替换，因此不能只存一个时间戳再回读“最新报告”。Clark 主线继续拥有研究、修改后批准、暂缓等产品语义。
2. **显式绑定 Runtime 执行条件。** 绑定真实批准人、Domain、已激活 ExecutionCommitment 的精确版本、该承诺的 DRI、独立验收人、冻结验收标准、必要的 Outcome/MF 引用。缺人、缺承诺或授权不匹配时返回可说明的待补状态；保持 Clark 决议，不虚构 Runtime WorkItem。
3. **以实际有权人的 typed Action 创建 WorkItem。** 来源与工作项的绑定需要持久、可审计的机制；当前严格 payload 不支持任意附加外部字段，不能把 `DO-*` 当 UUID，也不能只在浏览器保存映射。候选来源本身不授予 Action 权限。
4. **按每条命令对账。** 稳定外部命令 ID + 冻结版本决定幂等请求；记录对应 Receipt、WorkItem 和精确版本。网络结果不明时查原回执或重放原请求，不能重新生成 key。一个批准包可能有多条命令，部分失败应逐条反映，不能仅凭本地批准成功显示全部已送达。
5. **DRI 界面消费 Runtime 状态。** 首次派单只到 offered；承接必须由指定本人执行。后续提交、退回、再提交、验收均以 Receipt 为准。Outcome/MF 的判断与命令、交付状态分别显示。通知投递是另一项效果，不能拿数据库记录代替通知成功。

## 本轮新增回归

- Clark 三个新决议 RPC：DRI 拒绝、只有 Runtime 个人 cookie 时拒绝、Clark CEO 仍可调用。
- Clark BFF：不把 `approveCommandBrief`、`issuedAt`、`driId` 或原始 `DecisionOrder` 当作 Runtime 派单命令。
- 真实 HTTP：共享 Clark CEO cookie 不能派 Runtime 工作项；显式转发 Runtime cookie 也不能取得 Clark 批准权限；现有 BFF 拒绝未经适配的工作项创建。
- 既有真实 HTTP 交付链：指定人承接、提交 v1、退回、提交 v2、有权人验收；版本/幂等/越权负向路径；Outcome 与 MF 独立；数据库事实核对。

本轮检查已完成，详见 [结构化验收记录](acceptance/clark-main-82a94e0-merge-summary.json)。模型、研究数据和测试参与者为合成配置；HTTP、Runtime、PostgreSQL、MinIO 和权限检查实际运行。正式员工授权、Clark 决议自动进入 Runtime、正式 DRI 页面和远程发布分别验收。


## 合并与验证结果

- 快进合并：`d5bddaa` → `82a94e0`；冲突已解决，原有未提交联调代码保留。应用版本保持 Clark v0.142.0，未修改主线研究/行动整理/批准实现。
- 首轮全量测试发现旧工具列表断言未包含已存在的 `report_calculate`；只修正该测试预期及说明，未修改工具实现。
- 最终全量回归：211 个测试文件通过、7 个跳过；2484 项测试通过、12 项跳过。TypeScript、相关 ESLint、Next production build 通过。
- HTTP 联调 run：`clark-main-82a94e0-20260909-r2`，4 组全部通过；真实数据库核对得到 2 个交付版本、2 次评审及对应个人回执。
- 交付通过时 Outcome 仍为 `not_assessed`、MF 仍为 `investigating`；随后单独记录 Outcome `not_achieved`，独立完成 MF 验收并关闭，交付保持通过。
- 暂停本轮 Runtime API 后，Clark 返回 503 `RUNTIME_UNAVAILABLE`，没有模拟成功；恢复后读取通过。
- 首次启动因已有验证服务器占用 `.next-clark-v02` 中止；已在启动器增加显式隔离构建目录选项，第二次使用独立目录成功。原有服务器未停止。
- 本轮复用了既有独立验收 PostgreSQL/MinIO 容器，验收结束后停止本轮进程并恢复这两个容器原先的停止状态。保留验收记录和数据卷。
- 本轮为本地合并与接口核对，未进行本轮浏览器完整验收，未将 Clark 决议自动发送至 Runtime，未提交/推送适配修改或部署远程服务。DRI 产品界面仍由 Clark 后续开发。

## A1 主线兼容复核（2026-09-10）

Runtime 基线 `e9b904a`，Clark 仍为 `82a94e0` 加既有本地联调代码。
本轮没有修改 Clark 产品源码；对 812 个受检文件核对前后 hash，保持原样。
前述 2484 项回归属于 9 月 9 日记录，本轮新增的实际验证如下：

- 在专用 A1 验证库中新建合成 scope，API、Worker、Clark BFF 和 PostgreSQL/MinIO
  实际运行；没有迁移既有 0017 演示数据库，也没有修改部署脚本。
- `clark-a1-20260910-r1` 的 4 组 HTTP 联调全部通过：独立 cookie 与越权拒绝、
  承接→v1→退回→v2→有权人验收、Outcome/MF 分别判断、普通应用角色只读 SQL 核验。
  核对 2 个不可变交付版本、2 次评审和实际个人回执。
- 交付通过时 Outcome 仍未评估、MF 仍跟进中；另行判断后 Outcome 为未达成、MF 关闭，
  交付保持通过。Clark「批准并下达」字段仍不能绕过 Runtime 授权或直接创建 WorkItem。
- Ego 浏览器完成个人 DRI 登录，读取实际 offered 工作项及三项独立状态。
  本轮浏览器只做登录/读取检查；完整交付动作由真实 HTTP 联调验证。
- 暂停本次 Runtime API 后，Clark 返回 503 `RUNTIME_UNAVAILABLE`，没有模拟成功；
  恢复后读取通过。首次故障检查因 Codex 提前关闭测试服务器而中断，未计入通过；
  随后恢复同一 scope、保留既有业务对象，重跑成功。
- 本轮所有测试进程已停止；14 个原有容器和暂缓处理的 19 个部署相关文件保持原样。
  Next 自动添加的本轮类型目录已精确恢复，保留用户原有 TypeScript 配置改动。

见 [A1 联调结构化记录](acceptance/clark-runtime-a1-20260910.json)。
这些结果不表示 Clark 决议已自动派发至 Runtime，也不表示正式 DRI 产品界面或远程部署完成。

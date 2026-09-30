# world 0.2 按方法侧 Content Pact 替换块、组件与状态项：映射表

票 #78（契约第 19 节待决 14）。来源：方法侧《TKOS 企业上下文业务建模方案与落地计划》第 4.1 节 Content Pact 首版（飞书 rev 391，2026-09-30）。本文件是分支 `world/0.2-pact` 上各张实现票的唯一依据：登记、契约正文、读侧、播种、实验、示例都按这里改；这里没写到的，先按「只加不换、保留天枢已写的 id」处理，并在票里记下。

第 7 节八个对齐点已于 2026-09-30 定为一律按括号里的默认做法，不再等方法侧答复；方法侧之后若改 Content Pact，按契约变更另行处理。

## 1. 总的规则

1. **方法侧的「属性」就是块内的组件类型**；引用写 `对象@版本#块/组件`。组件类型 id 取方法侧英文名的 snake_case；方法侧同一中文名在不同对象上出现的，合成一个类型（见第 3 节）。
2. **块 id 是我们的，能留就留**：块还在、作用不变的（Company 的 `identity`，Mission、Task、责任单元的 `definition`，Activity 的 `instruction`，Mission 的 `execution_plan`，Task 的 `plan`，Strategy 的 `responsibility_structure`）只改显示名和可用组件；其余旧块删除，新块用新 id。
3. **约束不再单独成块**：登记里不再有 kind 为 `constraint` 的块；约束成为定义块或计划块里的组件，组件类型带 `context_role: "constraint"`。公司、长期目标、周期目标没有约束。
4. **组件类型加可选字段 `context_role`**，取值 `constraint`、`acceptance`、`contribution`。取上下文时「约束沿关系读」「凭什么」「为什么」改为按组件类型的这个角色取，不再按块 id `acceptance` 或块 kind `constraint` 取。`world_v02_registry_check.py` 校验：角色取值合法；每个带角色的组件类型至少被一个块使用。
5. **对象属性保留**：`title`、`unit_kind`、`scope`、`horizon`、`period`、`core_battle`、`responsible`、`external_refs` 都被列表过滤、守卫、验收或播种读取，继续做属性。方法侧与之重合的行由属性承担，不另加组件（见各表「由属性承担」）。
6. **「责任归属」行不是块**：由现有的 `responsible` 属性或按角色的责任人承担（Company、Strategy 是 CEO，责任单元是 DRI，Mission 是 Mission DRI，Task、Activity 是指派的责任人）。
7. **投影项不存第二份**（对齐点 5）：Mission 计划里的「Task 预期结果与质量标准」、责任单元的「战役引用」、责任主体的「当前责任」都在读取时从下级对象投影。
8. **状态只加不换**：已有快照 payload 类型、块 id、组件 id 全部保留；新增的状态块都可以缺省，缺省时读侧给标准句（「当前无……」）。
9. **天枢已经在写的形状不变**：见第 5 节，由无库测试钉住。
10. 写进仓库的文字只写「方法侧」，不写人名。

## 2. 逐类对象

记法：`块 id`（class）｛组件类型 id …｝。

### Company（公司）

| Content Pact | 0.2 |
|-|-|
| 企业身份与长期意图 { 业务定义、企业使命、愿景、价值观与公司原则 } | `identity`（formal，显示名「企业身份与长期意图」）｛`business_definition` `corporate_purpose` `vision` `values_principles`｝ |
| 责任归属 | CEO（按角色），不是块 |
| 状态 | 见第 4 节 `company_review` |

删除：`constraint`。

### Strategy（战略）

| Content Pact | 0.2 |
|-|-|
| 战略 { 总体战略、目标客户、目标市场、产品与价值主张、核心壁垒、市场进入与获客方式、战略取舍 } | `strategy_core`（formal，「战略」）｛`strategic_thesis` `target_customers` `target_markets` `value_proposition` `competitive_advantage` `go_to_market` `trade_offs`｝ |
| 商业模式与成立逻辑 { 商业模式、价值实现逻辑、关键假设、战略约束 } | `business_logic`（formal，「商业模式与成立逻辑」）｛`business_model` `value_logic` `assumption` `strategy_constraint`｝ |
| 战略责任结构 { 战场引用、责任域引用 } | `responsibility_structure`（formal，「战略责任结构」）｛`unit_entry`｝；战场与责任域由责任单元的 `unit_kind` 区分，责任单元的 `architecture_ref` 目标不变 |
| 状态 | `strategy_state` |

删除：`choices`、`path`、`assumptions`、`capabilities`、`constraint`。

### ResponsibilityUnit（责任单元）

| Content Pact | 0.2 |
|-|-|
| 责任定义 { 战略贡献 / 存在必要性、核心责任、责任边界、关键约束 } | `definition`（formal，「责任定义」）｛`contribution` `mandate` `scope_boundary` `key_constraint`｝ |
| 责任归属 { DRI 引用 } | 单元 DRI（按角色），不是块 |
| 责任结构 { 战役引用 } | 读取时投影：本单元下 Mission 的引用，不存 |
| 状态 | `unit_state` |

删除：`boundary`、`constraint`。

### LongTermGoal（长期目标）

| Content Pact | 0.2 |
|-|-|
| 定位与承接 { 责任范围、承接关系 } | `alignment`（formal，「定位与承接」）｛`responsibility_scope` `alignment_note`｝；公司级还是单元级由属性 `scope` 承担，上级对象由关系 `parent_ref` / `goal_ref` 承担 |
| 目标定义 { 目标结果、成功标准、时间跨度、实现逻辑 } | `target`（formal，「目标定义」）｛`outcome` `success_criterion` `realization_logic`｝；时间跨度由属性 `horizon` 承担 |
| 状态 | `goal_state` |

删除：`outcome`（块）、`measures`、`constraint`。

「承接关系」的组件 id 用 `alignment_note`，避免与块 id `alignment` 同名。

### PeriodGoal（周期目标）

| Content Pact | 0.2 |
|-|-|
| 定位与承接 { 责任范围与周期、上级 LTO 引用、本周期必要性、预计长期目标推进 } | `alignment`（formal，「定位与承接」）｛`responsibility_scope` `why_this_period` `expected_lt_advance`｝；周期由属性 `period` 承担，上级长期目标由关系 `goal_ref` 承担 |
| 目标定义 { 目标结果、成功 / 验收标准、时间边界、实现逻辑 } | `target`（formal，「目标定义」）｛`outcome` `acceptance_criterion` `time_boundary` `realization_logic`｝ |
| 状态 | `goal_state`（含 `variance`） |

删除：`outcome`（块）、`realization_logic`（块）、`acceptance`、`constraint`。`review_ref`、`depends_on` 不变。

### Mission（战役）

| Content Pact | 0.2 |
|-|-|
| 战役定义 { 贡献 / 存在必要性、战役结果、成功 / 验收标准、时间边界、责任边界 } | `definition`（formal，「战役定义」）｛`contribution` `outcome` `acceptance_criterion` `time_boundary` `scope_boundary`｝ |
| Mission 计划 { 核心路径、关键取舍、关键里程碑、关键约束与依赖 } | `mission_plan`（formal，「Mission 计划」）｛`core_path` `key_trade_off` `milestone` `constraint_dependency`｝，随 Mission 过门（对齐点 4） |
| Mission 计划 { Task 全景 } | `execution_plan`（activity，显示名「Task 全景」）｛`plan_item`｝，天枢随时写，不过门（对齐点 4） |
| Mission 计划 { Task 预期结果与质量标准 } | 读取时投影：各下级 Task `definition` 里的 `outcome` 与 `acceptance_criterion`，不存 |
| 责任归属 { 战役 DRI 引用 } | `responsible` 属性；角色显示名改「Mission DRI」，角色码 `OWNER` 与判权不变（对齐点 8） |
| 状态 | `execution_state` |

删除：`acceptance`、`play`、`constraint`。

### Task（任务）

| Content Pact | 0.2 |
|-|-|
| 任务定义 { 贡献 / 存在必要性、工作结果、成功 / 验收标准、时间边界、工作边界 } | `definition`（formal，「任务定义」）｛`contribution` `outcome` `acceptance_criterion` `time_boundary` `scope_boundary`｝ |
| Task 计划 { 执行顺序与依赖、时间安排、执行上下文与约束、治理点、异常与升级规则 } | `task_plan`（formal，「Task 计划」）｛`execution_sequence` `schedule` `execution_context` `governance_point` `escalation_rule`｝ |
| Task 计划 { Activity 全景 } | `plan`（activity，显示名「Activity 全景」）｛`plan_item`｝ |
| 责任归属 { 任务 DRI 引用 } | `responsible` 属性，显示名「Task DRI」 |
| 状态 | `execution_state` |

删除：`acceptance`、`constraint`。

### Activity（活动，候选）

| Content Pact | 0.2 |
|-|-|
| 执行目的与要求 { 贡献 / 存在必要性、执行事项、预期产出、成功 / 验收标准、时间边界、执行边界、执行约束 } | `instruction`（formal，「执行目的与要求」）｛`contribution` `work_definition` `expected_output` `acceptance_criterion` `time_boundary` `scope_boundary` `execution_constraint`｝ |
| 执行责任 { 执行主体引用 } | `responsible` 属性 |
| 状态 | `execution_state` |

删除：`constraint`。

### 责任主体（Responsible Entity）

不变（对齐点 3）：0.2 只做身份投影——类型、显示名、各域角色、由指派投影的当前责任。方法侧的可用状态、当前负荷、当前履责状态、履责记录、结果证据不进 0.2。

## 3. 组件类型清单

已有的 8 个全部保留 id：`outcome`、`success_criterion`、`acceptance_criterion`、`unit_entry`、`assumption`、`issue`、`plan_item`、`progress_item`。

| id | 显示名 | 方法侧英文名 | 用在 | context_role |
|-|-|-|-|-|
| `business_definition` | 业务定义 | Business Definition | Company | |
| `corporate_purpose` | 企业使命 | Corporate Purpose | Company | |
| `vision` | 愿景 | Vision | Company | |
| `values_principles` | 价值观与公司原则 | Values & Company Principles | Company | |
| `strategic_thesis` | 总体战略 | Strategic Thesis | Strategy | |
| `target_customers` | 目标客户 | Target Customers | Strategy | |
| `target_markets` | 目标市场 | Target Markets | Strategy | |
| `value_proposition` | 产品与价值主张 | Offering & Value Proposition | Strategy | |
| `competitive_advantage` | 核心壁垒 | Competitive Advantage | Strategy | |
| `go_to_market` | 市场进入与获客方式 | Go-to-Market | Strategy | |
| `trade_offs` | 战略取舍 | Trade-offs & Exclusions | Strategy | |
| `business_model` | 商业模式 | Business Model | Strategy | |
| `value_logic` | 价值实现逻辑 | Value Logic | Strategy | |
| `assumption` | 关键假设 | Key Assumptions | Strategy | |
| `strategy_constraint` | 战略约束 | Strategy Constraints | Strategy | constraint |
| `unit_entry` | 责任单元条目（战场 / 责任域） | Battlefield / Domain References | Strategy | |
| `contribution` | 贡献 / 存在必要性 | Contribution / Why Necessary | 责任单元、Mission、Task、Activity | contribution |
| `mandate` | 核心责任 | Responsibility Mandate | 责任单元 | |
| `scope_boundary` | 责任 / 工作 / 执行边界 | Scope & Boundary | 责任单元、Mission、Task、Activity | constraint |
| `key_constraint` | 关键约束 | Key Constraints | 责任单元 | constraint |
| `responsibility_scope` | 责任范围 | Responsibility Scope | 长期目标、周期目标 | |
| `alignment_note` | 承接关系 | Alignment | 长期目标 | contribution |
| `why_this_period` | 本周期必要性 | Why This Period | 周期目标 | contribution |
| `expected_lt_advance` | 预计长期目标推进 | Expected LT Progress Advance | 周期目标 | contribution |
| `outcome` | 目标结果 / 战役结果 / 工作结果 | Outcome | 长期目标、周期目标、Mission、Task | |
| `success_criterion` | 成功标准 | Success Criteria | 长期目标 | acceptance |
| `acceptance_criterion` | 成功 / 验收标准 | Success & Acceptance Criteria | 周期目标、Mission、Task、Activity | acceptance |
| `realization_logic` | 实现逻辑 | Realization Logic | 长期目标、周期目标 | |
| `time_boundary` | 时间边界 | Time Boundary | 周期目标、Mission、Task、Activity | constraint |
| `core_path` | 核心路径 | Core Path | Mission | |
| `key_trade_off` | 关键取舍 | Key Trade-offs | Mission | |
| `milestone` | 关键里程碑 | Key Milestones | Mission | |
| `constraint_dependency` | 关键约束与依赖 | Key Constraints & Dependencies | Mission | constraint |
| `plan_item` | 计划条目 | Task / Activity Landscape | Mission、Task（活动块） | |
| `execution_sequence` | 执行顺序与依赖 | Execution Sequence & Dependencies | Task | |
| `schedule` | 时间安排 | Schedule & Timing | Task | |
| `execution_context` | 执行上下文与约束 | Execution Context & Constraints | Task | constraint |
| `governance_point` | 治理点 | Governance Points | Task | |
| `escalation_rule` | 异常与升级规则 | Exception & Escalation Rules | Task | |
| `work_definition` | 执行事项 | Work Definition | Activity | |
| `expected_output` | 预期产出 | Expected Output | Activity | |
| `execution_constraint` | 执行约束 | Execution Constraints | Activity | constraint |
| `variance` | 关键偏差 | Material Variance | 周期目标状态 | |
| `issue` | 问题 | Issues | 各类状态 | |
| `progress_item` | 进展条目 | — | 执行状态 | |

合并规则：方法侧「战略贡献 / 存在必要性」与「贡献 / 存在必要性」合为 `contribution`；「责任边界」「工作边界」「执行边界」合为 `scope_boundary`；「目标结果」「战役结果」「工作结果」合为 `outcome`；周期目标、Mission、Task、Activity 的「成功 / 验收标准」合为 `acceptance_criterion`，长期目标的「成功标准」仍是 `success_criterion`。名字不同的约束类（战略约束、关键约束、关键约束与依赖、执行上下文与约束、执行约束）不合并，各自带 `context_role: "constraint"`。`time_boundary`、`scope_boundary` 带 constraint 角色，是因为它们回答「做到什么程度为止、不做什么」，取上下文时与约束同读。

`plan_item` 新增属性，**全部可选**（天枢现有写入不带它们也必须通过）：

| id | 显示名 | value |
|-|-|-|
| `expected_output` | 预期产出 | text |
| `quality_standard` | 质量标准 | text |
| `executor` | 执行主体 | text（人名或 Agent 名，只作记录，与已有 `responsible` 一样不判权） |
| `division` | 人 + Agent 分工 | text |

## 4. 状态项

| payload 类型 | 用于 | 保留的块 | 新增的块（都可缺省） |
|-|-|-|-|
| `company_review` | Company | `results`、`gaps`（显示名改「关键结果差距」）、`causes`、`key_changes`、`implications`、`materials` | `overall_state`「整体经营状态」、`key_risks`「关键风险」、`issues`「问题」｛`issue`｝ |
| `strategy_state` | Strategy | `issues`、`materials` | `validity`「战略有效性」、`assumption_status`「关键假设状态」、`key_risks`「关键战略风险」 |
| `unit_state` | 责任单元 | `progress`（「进展」）、`issues`、`materials` | `current_state`「当前状态」、`key_risks`「关键风险」 |
| `goal_state` | 长期目标、周期目标 | `progress`（「进展」）、`issues`、`materials` | `current_state`「当前状态」、`key_risks`「关键风险」、`variance`「关键偏差」｛`variance`｝（长期目标留空） |
| `execution_state` | Mission、Task、Activity | `progress`｛`progress_item`｝、`blockers`（显示名改「关键风险与阻塞」）、`issues`、`materials` | `current_state`「当前状态」 |

公司复盘（`company_review`）的 id 与周期目标承诺时的 `formation_anchors` 守卫不变（对齐点 6）。

## 5. 天枢已经在写的形状（不许变）

由一条无库测试钉住，测试从 `deploy/world-02/examples.py`、`deploy/world-02/seed-eo-2026-10.json`、`deploy/world-02/smoke.py` 与接口清单里实际取出这些 id，断言登记里仍然存在、新增属性都可选：

- 快照 payload 类型与块：`execution_state` 的 `progress`、`blockers`、`issues`、`materials`；`goal_state`、`unit_state` 的 `progress`、`issues`、`materials`。
- 组件：`progress_item`、`issue`、`plan_item` 连同它们已有的属性 id。
- Mission 的 `execution_plan` 块、Task 的 `plan` 块、属性 `external_refs`。
- 委托动作族与动作名。

天枢要改的只有：代记 Mission 与周期目标的承诺时带的候选内容块（旧的 `acceptance`、`play`、`outcome`、`realization_logic`、`constraint` 换成新的 `definition` / `mission_plan` / `alignment` / `target` 与组件），以及文档里的叫法。

## 6. 不在本次范围

- 方法侧第 4.3 节事件、第 5.1 节关系仍写「待输入」：按 0.2 现稿（对齐点 7）。
- 方法侧第 7–9 节（CLI 命令名、试点 Agent）。
- 核心战役的正式确认权：仍是 CEO 的待决。

## 7. 与方法侧对齐的八点（2026-09-30 定：按默认做法）

1. 属性即组件类型；引用写 `对象@版本#块/组件`；类型 id 用方法侧英文名（是）。
2. 状态项是快照 payload 的块，块级可寻址；「问题」是组件（是）。
3. 责任主体只做身份投影，可用状态、负荷、履责记录不进 0.2（不进）。
4. Mission 计划拆成正式的 `mission_plan` 与活动块 `execution_plan`（Task 全景）（拆）。
5. 投影项读取时从下级对象投影，不存第二份（投影）。
6. 保留公司复盘与周期目标承诺的守卫（保留）。
7. 事件与关系按 0.2 现稿（按现稿）。
8. Mission DRI、Task DRI、RU DRI 只改显示名（是）。

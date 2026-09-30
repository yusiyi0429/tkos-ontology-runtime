# 实验 E：E&O 十月的五个场景：播种与标准答案审阅稿

> 由 `experiments/world_v02/scenarios.json` 与 `gold.json` 生成（`python -m experiments.world_v02.gold render`），不要手改。
> 业务真实性请你审；标准答案经 E&O DRI 运行 `python -m experiments.world_v02.gold approve --by "E&O DRI"` 批准后，
> 实验跑器（票 #68）才能使用。

- 批准状态：未批准：实验跑器会拒绝使用
- 当前内容哈希：`32c714b56b59d10fdea27ec549825c46f535a9bb32a6f8911f8879a0bd3f9022`
- 占位：`@键` 是该对象播种结束时的最新版本，`@键@N` 是第 N 版，`#块`、`#块/组件` 指到块与组件；`$身份键` 是身份；`event:键` 是那一步记下的事件；时刻写 `now` 的取播种那一步的数据库时刻。

## 一、身份

| 键 | 显示名（角色名） | 类型 | 角色（域） |
|---|---|---|---|
| `ceo` | CEO | human | CEO（公司）；CEO（E&O）；CEO（Agents） |
| `eo_dri` | E&O DRI | human | DOMAIN_DRI（E&O） |
| `eo_owner` | E&O Mission Owner | human | OWNER（E&O） |
| `trial_ic` | 试用 Task 责任人 | human | IC（E&O） |
| `integration_ic` | 联调 Task 责任人 | human | IC（E&O） |
| `retrieval_ic` | 取法对照 Task 责任人 | human | IC（E&O） |
| `lock_ic` | 锁版 Task 责任人 | human | IC（E&O） |
| `experiment_e_ic` | 实验 E Task 责任人 | human | IC（E&O） |
| `experiment_b_ic` | 实验 B Task 责任人 | human | IC（E&O） |
| `eo_agent` | E&O Agent | agent | AGENT（E&O） |
| `agents_dri` | Agents DRI | human | DOMAIN_DRI（Agents） |

## 二、战略材料里没有的组件

Company 与 Strategy 的正文是公司知识库战略材料的原句（票 #67），逐条原句与页码见 `experiments/world_v02/strategy_material.json`，第三节这两步的说明里也逐条写了页码。下面这些块里，材料没有讲到的组件类型留空，没有编。

| 位置 | 留空的组件类型 |
|---|---|
| 公司「词元云集（TokenKing）」（最新版，第 1 版）的「企业身份与长期意图」块（`@company#identity`）　**材料里没有** | 企业使命：材料里没有，留空 |
| 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略」块（`@strategy#strategy_core`）　**材料里没有** | 目标客户、目标市场：材料里没有，留空 |

## 三、共用主干的播种

五个场景共用 E&O 十月主干（base），各场景只建、只改自己的对象（owns 列出它要改的主干对象）。主干取自换任务卡之前的十月计划 experiments/world_v02/b_source-2026-10.json（公司层正文取自 0.1 审过的 experiments/world_v01/seed.json；Company 与 Strategy 的正文 2026-09-30 换成公司知识库《总体战略定位与阶段路径》（2026-07）的原句，#67，逐条原句与页码见 experiments/world_v02/strategy_material.json），只改两处：Strategy 的责任结构块加 Agents 的责任单元条目组件；十月周期目标多走一轮重走（版本变化场景要的上层新修订）。2026-09-30 按方法侧 Content Pact 换成新块（#82，映射表 docs/world-v02-content-pact-mapping.md）：主干与 b_source 一起改，旧块的正文按意思放进新块的组件，只在分号、冒号处拆开，不改写字句；约束不再单独成块，成了带约束角色的组件（责任单元的关键约束、Mission 的关键约束与依赖、Task 的执行上下文与约束等），公司、长期目标与周期目标没有约束。不播委托与天枢主体。外部事件与状态快照的时刻写 now：取播种那一步的数据库时刻，按记录顺序即发生顺序。

1. CEO 建公司「词元云集（TokenKing）」（键 `company`）
  - 企业身份与长期意图：
    - 组件 `long-term-identity`（业务定义）：本轮管理层需要确认的 6 个问题：是否确认“企业经营范式重构与 Token 运营中枢”为词元云集长期身份？
    - 组件 `vision`（愿景）：长期愿景：建设系统性 Token 工厂，推动收入从空间、算力升级到 Token 与数字劳动力。
    - 组件 `token-principle`（价值观与公司原则）：创业公司，不追求 Token 用量最大化：研发按需用模型。
    - 组件 `core-principle`（价值观与公司原则）：核心原则：短期见利见效，长期具有战略意义；不能用长期愿景替代当前商业模式，也不能因短期收入把战略越讲越窄。
  - 说明：正文照搬十月起点（b_source-2026-10.json）。正文是公司知识库《总体战略定位与阶段路径》（2026-07）的原句（#67，逐条原句见 experiments/world_v02/strategy_material.json）：long-term-identity 第 10 页，vision 第 2 页，core-principle 第 2 页。长期身份是第 10 页「本轮管理层需要确认的 6 个问题」的问题 1，组件正文就是带该页标题的问句，不写成已定；标题式短语连同它下面的说明合成一句，写成「标题：说明」。token-principle 出自 9/23 会议，不在材料里，保留 0.1 审过的原文（原是公司约束块，Content Pact 的公司没有约束，放在价值观与公司原则）。企业使命材料里没有，留空。组件 id 沿用十月起点。
2. CEO 建战略「词元云集总体战略（存根）」（键 `strategy`）
  - 战略：
    - 组件 `current-strategy`（总体战略）：当前战略：词元云集承接第 3-6 层，成为连接智能供给与企业价值的运营中枢。
    - 组件 `main-line`（总体战略）：阶段打法：主线做 AI 企业智能决策网络；辅线以 TokenOps 获取高频真实负载并训练底层能力。
    - 组件 `closed-loop`（总体战略）：总体战略：系统性 Token 工厂的关键不是“七层全自建”，而是形成闭环、掌握控制点
    - 组件 `path`（总体战略）：阶段路径：先内部灯塔，再外部复制； 831 是“闭环验证节点”而非“大平台完成节点”
    - 组件 `success-831`（总体战略）：831 成功标准：真实客户、真实使用、真实调用、真实反馈、可复制方案和明确 Owner 同时成立。
    - 组件 `confirm-831`（总体战略）：本轮管理层需要确认的 6 个问题：是否确认 831 以真实使用、真实闭环和可复制资产为主要判断标准？
    - 组件 `layer-6`（产品与价值主张）：第 6 层：FDE / Agent / 数字劳动力产品化
    - 组件 `layer-5`（产品与价值主张）：第 5 层：统一入口、路由、计量与治理
    - 组件 `layer-4-3`（产品与价值主张）：第 4-3 层：用真实调用逐步补足模型运营与推理优化
    - 组件 `foundation`（核心壁垒）：本轮管理层需要确认的 6 个问题：是否确认 Foundry / Ontology、引擎与 TokenHub 是两条业务共同底座？
    - 组件 `control-points`（核心壁垒）：词元云集真正要掌握的不是“四层全部自建”，而是五个关键控制点
    - 组件 `control-customer-entry`（核心壁垒）：客户与场景入口：知道 AI 应进入什么任务，并保持长期客户关系
    - 组件 `control-runtime-entry`（核心壁垒）：企业智能运行入口：掌握 Agent、工作流与企业上下文的持续运行
    - 组件 `control-token-calls`（核心壁垒）：Token 调用关系：掌握统一接入、计量、权限、日志与运营数据
    - 组件 `control-routing`（核心壁垒）：模型选择与路由权：持续优化任务、质量、成本和供应组合
    - 组件 `control-feedback`（核心壁垒）：结果反馈数据：让真实业务结果回流，形成跨项目可复用能力
    - 组件 `stage-now-831`（市场进入与获客方式）：现在 - 831：内部兄弟公司灯塔项目；形成真实议题、LOP、报价、CEO Agent 初版、Foundry / TokenHub 最小能力
    - 组件 `stage-831-spring-festival`（市场进入与获客方式）：831 - 春节前：内部验证 + 少量外部试点；形成 1-2 个可复制模块、管理者 Agent / 数字劳动力样板、TokenOps 真实负载
    - 组件 `stage-after-funding`（市场进入与获客方式）：下一轮注资后：产品线与平台能力扩张；扩大客户复制、持续使用收入和单位经济性，按真实增长点扩组织
    - 组件 `value-chain-boundary`（战略取舍）：战略边界：覆盖完整价值链，不等于一个主体一次性建设全部能力。
    - 组件 `organization`（战略取舍）：组织建设：主体资源围绕主产品组合，辅助业务保持轻量，底层能力共用
    - 组件 `confirm-resource-focus`（战略取舍）：本轮管理层需要确认的 6 个问题：是否确认 AI 企业智能决策网络为主体资源方向，TokenOps 仅保持轻量验证？
  - 商业模式与成立逻辑：
    - 组件 `one-main-one-side`（商业模式）：商业模式：一主一副，不是两个彼此独立的方向
    - 组件 `core-business`（商业模式）：核心业务：AI 企业智能决策网络；建立高价值经营入口与长期公司身份
    - 组件 `side-business`（商业模式）：辅助业务：TokenOps FDE；获取高频真实负载，训练路由、评测和调优能力
    - 组件 `revenue-structure`（商业模式）：收入结构：先用项目进入，再转向系统持续使用与 Token 运营收入
    - 组件 `revenue-short-term`（商业模式）：短期：项目与部署收入；战略诊断、FDE、产品设计、部署实施；先形成现金流和灯塔案例
    - 组件 `revenue-mid-term`（商业模式）：中期：系统持续使用收入；Agent / Foundry 授权、订阅、托管与扩展；把一次项目变成持续运行关系
    - 组件 `revenue-long-term`（商业模式）：长期：Token 与数字劳动力运营收入；模型调用、统一治理、数字劳动力与平台运营；随组织级部署深度持续放大
    - 组件 `confirm-revenue-shift`（商业模式）：本轮管理层需要确认的 6 个问题：是否确认短期以项目与部署收入为主，中长期向系统持续使用和 Token 运营收入迁移？
    - 组件 `shared-capability`（价值实现逻辑）：两类业务分别贡献不同输入，但最终训练同一套核心能力
    - 组件 `scenario-inputs`（价值实现逻辑）：高价值场景输入：企业经营上下文；Agent / 数字劳动力部署经验；长期客户关系与业务结果
    - 组件 `load-inputs`（价值实现逻辑）：高频真实负载输入：模型选择与调用链；成本、质量、稳定性数据；路由与优化实验
    - 组件 `flywheel`（价值实现逻辑）：能力提升反过来降低交付成本、改善客户效果，并推动更多场景部署与更多真实负载。
    - 组件 `token-traction`（关键假设）：关键判断：当前不能把 Token 消纳设为唯一牵引；只有从 CEO 向管理者矩阵和数字劳动力扩展，持续调用才会真正放大。
    - 组件 `capability-rule`（战略约束）：判断标准：任何新增能力，都应强化上述控制点，并沉淀可复用的客户、调用或反馈数据。
    - 组件 `confirm-first-project`（战略约束）：本轮管理层需要确认的 6 个问题：是否确认首个内部项目必须同步产出产品、服务、报价和复制模板？
  - 战略责任结构：3 个战场：01 灯塔项目、02 Token Ops、03 002 客户。7 个能力域：02 Re-architecture、03 Method、04 Agents、05 E&O、07 TokenHub、08 Talent & Organization、09 Finance, Compliance & Operations。
    - 组件 `eo`（责任单元条目（战场 / 责任域））：05 E&O
    - 组件 `agents`（责任单元条目（战场 / 责任域））：04 Agents
  - 字段：parent_ref = `@company`
  - 说明：正文照搬十月起点（b_source-2026-10.json）。正文是公司知识库《总体战略定位与阶段路径》（2026-07）的原句（#67，逐条原句见 experiments/world_v02/strategy_material.json）。战略块：总体战略是 current-strategy 第 2 页、main-line 第 2 页、closed-loop 第 3 页、path 第 8 页、success-831 第 8 页、confirm-831 第 10 页；产品与价值主张是第 3 页「词元云集重点承接」下的三层 layer-6 第 3 页、layer-5 第 3 页、layer-4-3 第 3 页；核心壁垒是 foundation 第 10 页、control-points 第 4 页与五个控制点 control-customer-entry 第 4 页、control-runtime-entry 第 4 页、control-token-calls 第 4 页、control-routing 第 4 页、control-feedback 第 4 页；市场进入与获客方式是阶段路径的三段 stage-now-831 第 8 页、stage-831-spring-festival 第 8 页、stage-after-funding 第 8 页；战略取舍是 value-chain-boundary 第 3 页、organization 第 9 页、confirm-resource-focus 第 10 页。商业模式与成立逻辑块：商业模式是 one-main-one-side 第 5 页、core-business 第 5 页、side-business 第 5 页、revenue-structure 第 7 页、revenue-short-term 第 7 页、revenue-mid-term 第 7 页、revenue-long-term 第 7 页、confirm-revenue-shift 第 10 页；价值实现逻辑是 shared-capability 第 6 页、scenario-inputs 第 6 页、load-inputs 第 6 页、flywheel 第 6 页；关键假设是 token-traction 第 7 页；战略约束是 capability-rule 第 4 页、confirm-first-project 第 10 页。第 10 页「本轮管理层需要确认的 6 个问题」的问题 2–6 待管理层确认，组件正文就是带该页标题的问句，不写成已定；第 7 页收入结构、第 8 页 831 成功标准与第 9 页组织建设分别是问题 6、5、2 要确认的内容。标题式短语连同它下面的说明合成一句，写成「标题：说明」，几条说明以「；」隔开。目标客户、目标市场材料里没有，留空。组件 id 沿用十月起点。责任结构块不在这份材料里（第 9 页组织建设原稿是图），照旧取自 0.1 审过的战场图 V1，另加 Agents 的责任单元条目组件（文字取自同一块的正文）。留在草稿，同十月起点。
3. CEO 建责任单元「E&O」（键 `unit_eo`）
  - 责任定义：
    - 组件 `contribution`（贡献 / 存在必要性）：主要支撑灯塔项目的 Mission 01-2、01-3、01-4。
    - 组件 `mandate`（核心责任）：Engine & Ontology，战场图编号 05 的能力域：主导语义模型（本体）与 Context Runtime。
    - 组件 `boundary`（责任 / 工作 / 执行边界）：业务建模不归 E&O：由 CEO 与 Method 锁定业务语义，E&O 忠实翻译成语义模型、数据承载与运行时。Activity 要不要成为对象由 E&O 自己定。
    - 组件 `method-frozen`（关键约束）：tkos.method/0.4、0.5 冻结不改。
    - 组件 `no-graph-db`（关键约束）：当前量级用 PostgreSQL 加 pgvector，不引入图数据库。
  - 字段：unit_kind = `domain`，architecture_ref = `@strategy#responsibility_structure/eo`
  - 说明：正文照搬十月起点（取自 0.1 seed.json 的 unit_eo，已审）：原定义块拆成核心责任与战略贡献，原边界块是责任边界，原约束块按分号拆成两条关键约束。关键约束 no-graph-db 是约束冲突场景的上层约束。
4. CEO 建长期目标「春节前核心业务第一次被真实证明」（键 `company_goal`）
  - 目标定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：跑通「Re-architecture 进入客户 → TKOS 持续经营」这条主线，形成可复制的产品、交付与商业模式；同时跑通 TokenHub / TokenOps 的商业闭环。
  - 字段：scope = `company`，horizon = `2027 年春节前`，parent_ref = `@company`
  - 说明：正文照搬十月起点（取自 0.1 seed.json，已审），是目标结果；成功标准、实现逻辑与定位与承接块留空。
5. E&O DRI 建长期目标「E&O 六个月目标」（键 `eo_goal`）
  - 目标定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：建立支撑 TKOS 长期运行的 Enterprise Context 与 Agent Infrastructure，让真实经营数据能持续进入、调用、回写和复用。
  - 字段：scope = `unit`，horizon = `六个月`，parent_ref = `@unit_eo`，goal_ref = `@company_goal`
  - 说明：正文照搬十月起点（取自 0.1 seed.json，已审），是目标结果；成功标准、实现逻辑与定位与承接块留空。
6. CEO 确认长期目标 `company_goal`（accepted）（键 `confirm_company_goal`）
7. CEO 确认长期目标 `eo_goal`（accepted）（键 `confirm_eo_goal`）
8. E&O DRI 建周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（键 `october_goal`）　**诱饵：版本变化·引用旧版本**
  - 目标定义：
    - 组件 `tianshu`（目标结果 / 战役结果 / 工作结果）：10 月 16 日天枢联调验收通过
    - 组件 `records`（目标结果 / 战役结果 / 工作结果）：E&O 十月的工作全部经本体记录
    - 组件 `lock`（目标结果 / 战役结果 / 工作结果）：tkos.world 0.2 锁版
    - 组件 `ac-tianshu`（成功 / 验收标准）：10 月 16 日天枢联调验收通过
    - 组件 `ac-records`（成功 / 验收标准）：E&O 十月的工作全部经本体记录（每张任务卡有每周快照，执行事项的完成由交付事件推出）
    - 组件 `ac-lock`（成功 / 验收标准）：tkos.world 0.2 锁版（验收冻结并钉定提交）
  - 字段：period = `2026-10`，goal_ref = `@eo_goal`
  - 说明：照搬十月起点（用户 2026-09-29 给定）：三条结果与三条验收条件都在目标定义块里，组件 id 不变；定位与承接块、时间边界与实现逻辑留空。不带 review_ref：scope 里还没有已确认的公司复盘。
9. E&O DRI 承诺周期目标 `october_goal`（键 `commit_october_goal`）
10. CEO 确认周期目标 `october_goal`（accepted）（键 `confirm_october_goal`）
11. E&O DRI 建 Mission「天枢 × 本体 0.2 试用」（键 `mission_trial`）
  - 战役定义：
    - 组件 `trial`（目标结果 / 战役结果 / 工作结果）：天枢与本体 tkos.world 0.2 联调并试用。
    - 组件 `ac-tianshu`（成功 / 验收标准）：10 月 16 日天枢联调验收通过（引用 `@october_goal#target/ac-tianshu`）
    - 组件 `window`（时间边界）：10 月 9 日至 16 日。
  - Mission 计划：
    - 组件 `ms-integration`（关键里程碑）：10/9–11 联调与委托登记。
    - 组件 `ms-trial-week`（关键里程碑）：10/12–16 试用周每周快照与问题流转。
    - 组件 `ms-acceptance`（关键里程碑）：10/16 联调验收。
  - 字段：goal_ref = `@october_goal`
  - 说明：照搬十月起点：原定义块拆成战役结果、时间边界与三个关键里程碑，验收条件在战役定义块里。
12. E&O DRI 把 `mission_trial` 指派给 E&O Mission Owner（键 `assign_mission_trial`）
13. E&O DRI 建 Mission「0.2 实验与报告」（键 `mission_experiments`）
  - 战役定义：
    - 组件 `report`（目标结果 / 战役结果 / 工作结果）：0.2 的实验与实验报告。
  - Mission 计划：
    - 组件 `path`（核心路径）：对照实验 B（Task-only 与 Task+Activity），实验 E（五个场景、真实战略材料与标准答案），四种取法对照（含 RAG），写成实验报告。
  - 字段：goal_ref = `@october_goal`
  - 说明：照搬十月起点：原定义块按冒号拆成战役结果与核心路径；验收标准没有给定，留空。
14. E&O DRI 把 `mission_experiments` 指派给 E&O Mission Owner（键 `assign_mission_experiments`）
15. E&O DRI 建 Mission「0.2 锁版」（键 `mission_lock`）
  - 战役定义：
    - 组件 `lock`（目标结果 / 战役结果 / 工作结果）：tkos.world 0.2 锁版。
    - 组件 `ac-lock`（成功 / 验收标准）：tkos.world 0.2 锁版（验收冻结并钉定提交）（引用 `@october_goal#target/ac-lock`）
  - Mission 计划：
    - 组件 `path`（核心路径）：验收冻结并钉定提交，然后发版、切换实例。
  - 字段：goal_ref = `@october_goal`
  - 说明：照搬十月起点：原定义块按冒号拆成战役结果与核心路径。验收条件钉在十月周期目标第 1 版；主干末尾的一轮重走之后，它就是版本变化场景的旧版本出处。
16. E&O DRI 把 `mission_lock` 指派给 E&O Mission Owner（键 `assign_mission_lock`）
17. E&O Mission Owner 承诺 Mission（立项） `mission_trial`（键 `commit_mission_trial`）
18. E&O Mission Owner 承诺 Mission（立项） `mission_experiments`（键 `commit_mission_experiments`）
19. E&O Mission Owner 承诺 Mission（立项） `mission_lock`（键 `commit_mission_lock`）
20. E&O DRI 确认 Mission（立项） `mission_trial`（accepted）（键 `confirm_mission_trial`）
21. E&O DRI 确认 Mission（立项） `mission_experiments`（accepted）（键 `confirm_mission_experiments`）
22. E&O DRI 确认 Mission（立项） `mission_lock`（accepted）（键 `confirm_mission_lock`）
23. E&O Mission Owner 建 Task「联调与委托登记（10/9–11）」（键 `task_trial_integration`）
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：天枢与本体 0.2 联调，登记给天枢的委托。
    - 组件 `window`（时间边界）：10 月 9 日至 11 日。
  - 字段：parent_ref = `@mission_trial`
24. E&O Mission Owner 建 Task「试用周每周快照与问题流转（10/12–16）」（键 `task_trial_week`）
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：每周快照与问题流转经本体记录。
    - 组件 `window`（时间边界）：10 月 12 日至 16 日试用周。
  - 字段：parent_ref = `@mission_trial`
25. E&O Mission Owner 建 Task「10/16 联调验收」（键 `task_trial_acceptance`）
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：10 月 16 日天枢联调验收。
  - 字段：parent_ref = `@mission_trial`
26. E&O Mission Owner 建 Task「对照实验 B：Task-only 与 Task+Activity」（键 `task_experiment_b`）
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：对照 Task-only 与 Task+Activity 两种做法。
  - 字段：parent_ref = `@mission_experiments`
27. E&O Mission Owner 建 Task「实验 E：五个场景、真实战略材料与标准答案」（键 `task_experiment_e`）　**诱饵：无 Activity 的 Task·把有内容的块说成空、无 Activity 的 Task·把有内容的块说成空**
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：五个场景，用真实战略材料，写标准答案。
  - 字段：parent_ref = `@mission_experiments`
28. E&O Mission Owner 建 Task「四种取法对照（含 RAG）与实验报告」（键 `task_retrieval_report`）　**诱饵：约束冲突·约束冲突没指出或只引一处、约束冲突·约束冲突没指出或只引一处**
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：对照四种取法（含 RAG），写成实验报告。
  - 字段：parent_ref = `@mission_experiments`
29. E&O Mission Owner 建 Task「验收冻结与钉定提交」（键 `task_freeze`）
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：0.2 验收冻结，钉定提交。
  - 字段：parent_ref = `@mission_lock`
30. E&O Mission Owner 建 Task「发版与实例切换」（键 `task_release`）
  - 任务定义：
    - 组件 `outcome`（目标结果 / 战役结果 / 工作结果）：按钉定的提交发版，实例切换到发版的版本。
  - 字段：parent_ref = `@mission_lock`
31. E&O DRI 承诺周期目标 `october_goal`（带候选）（键 `commit_october_goal_round`）
  - 候选：
    - 目标定义：
      - 组件 `ac-lock`（成功 / 验收标准）：10 月 20 日 CEO 定下对象结构之后，tkos.world 0.2 锁版（验收冻结并钉定提交）
  - 事件内容：一轮重走：锁版放到 10 月 20 日 CEO 定下对象结构之后，改锁版那一条验收条件。
  - 说明：推：版本变化场景要的上层新修订。改哪一条、改成什么，按规格「10 月 20 日 CEO 决定对象结构」推断。候选只给目标定义块里的 ac-lock，其余组件按 id 合并保留。
32. CEO 确认周期目标 `october_goal`（accepted）（键 `confirm_october_goal_round`）
  - 事件内容：接受这一轮的候选。
  - 说明：写回候选，十月周期目标出第 2 版；三个 Mission 的目标引用与验收条件仍钉在第 1 版。

## 四之1、跨责任单元（`cross_unit`）

Agents 单元的 M1-B Mission 依赖试用 Mission，从试用周 Task 取上下文时引得到 Agents 的目标与约束；它们不得混入本 Task 的回答。

- 起点：Task「试用周每周快照与问题流转（10/12–16）」（最新版，第 2 版）（`@task_trial_week`）
- 认领的主干对象：`task_trial_week`

### 播种步骤

1. CEO 建责任单元「Agents」（键 `unit_agents`）
  - 责任定义：
    - 组件 `mandate`（核心责任）：战场图编号 04 的能力域：CEO Agent、DRI Agent 与经营协同 Agent。
  - 字段：unit_kind = `domain`，architecture_ref = `@strategy#responsibility_structure/agents`
  - 说明：正文取自 0.1 seed.json 的 unit_agents（已审），是核心责任。
2. Agents DRI 建长期目标「Agents 六个月目标」（键 `agents_goal`）　**诱饵：跨责任单元·另一责任单元的目标与约束混入**
  - 目标定义：
    - 组件 `agent-usage`（目标结果 / 战役结果 / 工作结果）：CEO Agent 与 DRI / Manager Agent 常态化使用。
  - 字段：scope = `unit`，horizon = `六个月`，parent_ref = `@unit_agents`
  - 说明：正文取自 0.1 seed.json 的 agents_goal（已审），是目标定义块的目标结果组件。留在草稿。
3. Agents DRI 建周期目标「Agents 9 月」（键 `agents_period`）　**诱饵：跨责任单元·另一责任单元的目标与约束混入**
  - 目标定义：
    - 组件 `practice`（目标结果 / 战役结果 / 工作结果）：CEO 与 DRI 进入真实协同实践。
  - 字段：period = `2026-09`，goal_ref = `@agents_goal`
  - 说明：正文取自 0.1 seed.json 的 agents_period（已审），是目标定义块的目标结果组件；没有十月的材料，沿用九月。留在草稿。
4. Agents DRI 建 Mission「M1-B 经营协同 MVP 与真实实践」（键 `agents_mission`）　**诱饵：跨责任单元·另一责任单元的目标与约束混入**
  - 战役定义：
    - 组件 `mvp`（目标结果 / 战役结果 / 工作结果）：M1-B 经营协同的最小可用版本上线，并在真实经营里用起来。
  - Mission 计划：
    - 组件 `manual-fallback`（关键约束与依赖）：允许人工兜底，不要求完整自动化。
  - 字段：goal_ref = `@agents_period`
  - 说明：正文取自 0.1 seed.json 的 agents_mission（已审）：原定义块是战役结果，原约束块是 Mission 计划块的关键约束与依赖，它是别的单元的约束诱饵。
5. Agents DRI 给 `agents_mission` 建关系 depends_on，指向 `@mission_trial`（键 `relate_agents_mission`）
  - 说明：推：0.1 里 M1-B 依赖 E&O 九月的 Mission（依赖栏写「E&O/本体：真实信息、变化更新与读写能力」）；十月改指试用 Mission，因为 CEO 与 DRI 的 Agent 在天枢上跑。有了它，试用 Mission 的 referenced_by 列出 M1-B，诱饵才引得到。
6. E&O Mission Owner 把 `task_trial_week` 指派给 试用 Task 责任人（键 `assign_trial_week`）
7. 试用 Task 责任人 开始 `task_trial_week`（键 `start_trial_week`）
8. 试用 Task 责任人 记外部事件（meeting）（键 `ev_trial_week_plan`）
  - 主体 `@task_trial_week`，发生于 `now`
  - 内容：试用周安排：天枢每周先记一条「每周同步」外部事件，再按任务卡写进展快照并引用它；试用中发现的问题由 Co-Agent 提出，路由给最低充分的责任人。M1-B 经营协同的 CEO Agent 与 DRI Agent 在天枢上跑，靠本 Mission 的读写。
  - 说明：推：安排取自契约补 10 与第 13 节、规格「天枢替 CEO、DRI 跑 Agent」；这次会是合成的。
9. 试用 Task 责任人 写状态快照「试用周 Task 状态」（主体 `@task_trial_week`）（键 `snap_trial_week`）
  - 时点 `now`，来源事件 `event:ev_trial_week_plan`
  - 进展：每周同步事件与进展快照的写法已和天枢对齐，等试用开始。
  - 字段：period = `2026-10`
  - 说明：推：合成的状态。

### 六问标准答案

#### why：为什么要做「试用周每周快照与问题流转」这个 Task？

它是 Mission「天枢 × 本体 0.2 试用」的一段，这个 Mission 的战役结果是天枢与本体 tkos.world 0.2 联调并试用。Mission 服务 E&O 十月周期目标的目标结果「10 月 16 日天枢联调验收通过」；十月周期目标推进 E&O 六个月目标的目标结果（建立 Enterprise Context 与 Agent Infrastructure），再往上是公司长期目标「春节前核心业务第一次被真实证明」的目标结果。E&O 是战略责任结构里的 05 能力域，总体战略的主线是 AI 企业智能决策网络，公司拟定的长期身份（业务定义）是企业经营范式重构与 Token 运营中枢（七月战略材料里还待管理层确认）。

应引用：
- Mission「天枢 × 本体 0.2 试用」（最新版，第 2 版）的「战役定义」块里的组件 `trial`（`@mission_trial#definition/trial`）
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `tianshu`（`@october_goal#target/tianshu`）
- 长期目标「E&O 六个月目标」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@eo_goal#target/outcome`）
- 长期目标「春节前核心业务第一次被真实证明」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@company_goal#target/outcome`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略责任结构」块里的组件 `eo`（`@strategy#responsibility_structure/eo`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略」块里的组件 `main-line`（`@strategy#strategy_core/main-line`）
- 公司「词元云集（TokenKing）」（最新版，第 1 版）的「企业身份与长期意图」块里的组件 `long-term-identity`（`@company#identity/long-term-identity`）

#### what：这个 Task 要做成什么？

工作结果是每周快照与问题流转经本体记录，时间边界是 10 月 12 日至 16 日试用周。任务定义里没有写验收标准，Task 计划与 Activity 全景都是空的。

应引用：
- Task「试用周每周快照与问题流转（10/12–16）」（最新版，第 2 版）的「任务定义」块里的组件 `outcome`（`@task_trial_week#definition/outcome`）
- Task「试用周每周快照与问题流转（10/12–16）」（最新版，第 2 版）的「任务定义」块里的组件 `window`（`@task_trial_week#definition/window`）

#### who：谁负责这个 Task？

试用 Task 责任人，由 E&O Mission Owner 指派。

应引用：
- Task「试用周每周快照与问题流转（10/12–16）」（最新版，第 2 版）（`@task_trial_week`）
- E&O Mission Owner 把 `task_trial_week` 指派给 试用 Task 责任人（`event:assign_trial_week`）

#### now：现在进展到哪了？

进行中，由试用 Task 责任人记的开始推出。最近一条状态：每周同步事件与进展快照的写法已和天枢对齐，等试用开始。

应引用：
- Task「试用周每周快照与问题流转（10/12–16）」（最新版，第 2 版）（`@task_trial_week`）
- 试用 Task 责任人 开始 `task_trial_week`（`event:start_trial_week`）
- 状态快照「试用周 Task 状态」（最新版，第 1 版）（`@snap_trial_week`）

#### happened：最近发生了什么？

责任人记了开始；开过一次试用周安排的会：天枢每周先记一条每周同步事件再写进展快照，问题由 Co-Agent 提出并路由；M1-B 经营协同的 CEO Agent 与 DRI Agent 在天枢上跑，靠本 Mission 的读写。

应引用：
- 试用 Task 责任人 开始 `task_trial_week`（`event:start_trial_week`）
- 试用 Task 责任人 记外部事件（meeting）（`event:ev_trial_week_plan`）

#### basis：凭什么这样做？受什么约束？

依据是 Mission 的验收条件「10 月 16 日天枢联调验收通过」，它引用十月周期目标目标定义块里的同名验收条件。约束：本 Task 的时间边界是 10 月 12 日至 16 日试用周；E&O 单元的两条关键约束是 tkos.method 0.4、0.5 冻结不改，当前量级用 PostgreSQL 加 pgvector、不引入图数据库。Agents 单元 M1-B 的关键约束与依赖「允许人工兜底」不是本 Task 的约束。

应引用：
- Mission「天枢 × 本体 0.2 试用」（最新版，第 2 版）的「战役定义」块里的组件 `ac-tianshu`（`@mission_trial#definition/ac-tianshu`）
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `ac-tianshu`（`@october_goal#target/ac-tianshu`）
- Task「试用周每周快照与问题流转（10/12–16）」（最新版，第 2 版）的「任务定义」块里的组件 `window`（`@task_trial_week#definition/window`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `method-frozen`（`@unit_eo#definition/method-frozen`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `no-graph-db`（`@unit_eo#definition/no-graph-db`）

### 反例

#### 另一责任单元的目标与约束混入（`other_unit`）

把 Agents 单元的目标或约束混进本 Task 的回答：Agents 六个月目标与 9 月周期目标的目标定义块、M1-B Mission 的关键约束与依赖「允许人工兜底，不要求完整自动化」。断言不论哪一种，引了这些块或其中的组件、或这条组件，就算出现。可以说 M1-B 依赖本 Mission，只引 M1-B 对象本身不算。

诱饵：
- 长期目标「Agents 六个月目标」（最新版，第 1 版）的「目标定义」块（`@agents_goal#target`）
- 周期目标「Agents 9 月」（最新版，第 1 版）的「目标定义」块（`@agents_period#target`）
- Mission「M1-B 经营协同 MVP 与真实实践」（最新版，第 2 版）的「Mission 计划」块里的组件 `manual-fallback`（`@agents_mission#mission_plan/manual-fallback`）


## 四之2、约束冲突（`constraint_conflict`）

上下层约束相抵：取法对照 Task 在 Task 计划块的执行上下文与约束里写了用单独部署的图数据库（Activity 全景里的 RAG 计划条目照此执行），与 E&O 单元的关键约束「不引入图数据库」相抵；回答必须指出冲突，同时引上层那条关键约束与本层的执行上下文或计划条目。

- 起点：Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）（`@task_retrieval_report`）
- 认领的主干对象：`task_retrieval_report`

### 播种步骤

1. E&O Mission Owner 把 `task_retrieval_report` 指派给 取法对照 Task 责任人（键 `assign_retrieval`）
2. 取法对照 Task 责任人 修订 `task_retrieval_report`（键 `plan_retrieval`）
  - Task 计划：
    - 组件 `graph-db`（执行上下文与约束）：RAG 组的检索环境：切块与对象关系存进单独部署的图数据库，用邻接检索补向量检索的召回。
  - Activity 全景：
    - 组件 `rag-graph`（计划条目）：RAG 组：把 scope 内的对象与事件切块，建进单独部署的图数据库，按向量与邻接关系检索。（责任人 `$retrieval_ic`）
    - 组件 `cost`（计划条目）：四种取法各按交给模型的字符数记成本，预算只报告、不作门。（责任人 `$retrieval_ic`）
    - 组件 `report`（计划条目）：写实验报告，写明触发调整对象结构的条件。（责任人 `$retrieval_ic`）
  - 说明：推：graph-db（执行上下文与约束，带约束角色）是本场景的下层约束，文字取自下面那次方案评审；rag-graph 是照它执行的计划条目，两者都为本场景合成。cost 与 report 取自规格第十节。
3. 取法对照 Task 责任人 开始 `task_retrieval_report`（键 `start_retrieval`）
4. 取法对照 Task 责任人 记外部事件（review）（键 `ev_rag_design`）
  - 主体 `@task_retrieval_report`，发生于 `now`
  - 内容：RAG 组检索方案评审：切块与对象关系存进图数据库，邻接检索补向量检索的召回；按这个方案搭环境。（引用 `@task_retrieval_report#task_plan/graph-db`）
  - 说明：推：合成的评审。
5. 取法对照 Task 责任人 写状态快照「取法对照 Task 状态」（主体 `@task_retrieval_report`）（键 `snap_retrieval`）
  - 时点 `now`，来源事件 `event:ev_rag_design`
  - 进展：RAG 组的图数据库已在本机起好，切块脚本写了一半；成本记法已定。
  - 字段：period = `2026-10`
  - 说明：推：合成的状态。

### 六问标准答案

#### why：为什么要做「四种取法对照（含 RAG）与实验报告」这个 Task？

它属于 Mission「0.2 实验与报告」，战役结果是 0.2 的实验与实验报告。这个 Mission 服务 E&O 十月周期目标的目标结果「tkos.world 0.2 锁版」：现行的锁版验收条件是 10 月 20 日 CEO 定下对象结构之后锁版，实验结果是定对象结构的依据。再往上是 E&O 六个月目标、公司春节前的长期目标、战略责任结构里的 05 E&O、总体战略的主线与公司拟定、还待管理层确认的长期身份。

应引用：
- Mission「0.2 实验与报告」（最新版，第 2 版）的「战役定义」块里的组件 `report`（`@mission_experiments#definition/report`）
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `lock`（`@october_goal#target/lock`）
- 长期目标「E&O 六个月目标」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@eo_goal#target/outcome`）
- 长期目标「春节前核心业务第一次被真实证明」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@company_goal#target/outcome`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略责任结构」块里的组件 `eo`（`@strategy#responsibility_structure/eo`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略」块里的组件 `main-line`（`@strategy#strategy_core/main-line`）
- 公司「词元云集（TokenKing）」（最新版，第 1 版）的「企业身份与长期意图」块里的组件 `long-term-identity`（`@company#identity/long-term-identity`）

#### what：这个 Task 要做成什么、打算怎么做？

工作结果是对照四种取法（含 RAG），写成实验报告。Task 计划的执行上下文与约束写了 RAG 组的切块与对象关系存进单独部署的图数据库；Activity 全景里三条计划条目，都写的是取法对照 Task 责任人：RAG 组把对象与事件切块，建进单独部署的图数据库；四种取法按交给模型的字符数记成本，预算只报告；写实验报告与触发调整对象结构的条件。用图数据库这一点与 E&O 单元的关键约束「不引入图数据库」冲突，要指出来，不能当成定了的做法。

应引用：
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「任务定义」块里的组件 `outcome`（`@task_retrieval_report#definition/outcome`）
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Task 计划」块里的组件 `graph-db`（`@task_retrieval_report#task_plan/graph-db`）
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Activity 全景」块里的组件 `rag-graph`（`@task_retrieval_report#plan/rag-graph`）
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Activity 全景」块里的组件 `cost`（`@task_retrieval_report#plan/cost`）
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Activity 全景」块里的组件 `report`（`@task_retrieval_report#plan/report`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `no-graph-db`（`@unit_eo#definition/no-graph-db`）

#### who：谁负责这个 Task？

取法对照 Task 责任人，由 E&O Mission Owner 指派；三条计划条目也都写的是他。

应引用：
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）（`@task_retrieval_report`）
- E&O Mission Owner 把 `task_retrieval_report` 指派给 取法对照 Task 责任人（`event:assign_retrieval`）

#### now：现在进展到哪了？

进行中，由开始事件推出。最近一条状态：RAG 组的图数据库已在本机起好，切块脚本写了一半，成本记法已定。也就是说，冲突的那条执行上下文已经在照着做。

应引用：
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）（`@task_retrieval_report`）
- 取法对照 Task 责任人 开始 `task_retrieval_report`（`event:start_retrieval`）
- 状态快照「取法对照 Task 状态」（最新版，第 1 版）（`@snap_retrieval`）

#### happened：最近发生了什么？

责任人写了 Task 计划与计划条目并开始；RAG 组检索方案评审定下用图数据库存切块与对象关系，按这个方案搭环境。

应引用：
- 取法对照 Task 责任人 开始 `task_retrieval_report`（`event:start_retrieval`）
- 取法对照 Task 责任人 记外部事件（review）（`event:ev_rag_design`）

#### basis：凭什么这样做？受什么约束？

上层约束是 E&O 单元的关键约束：当前量级用 PostgreSQL 加 pgvector，不引入图数据库（另一条是 tkos.method 0.4、0.5 冻结不改）。本层的执行上下文与约束 graph-db 和照它执行的计划条目 rag-graph 要单独部署图数据库，与上层那条关键约束冲突，回答要同时引上下两处。Task 自己没有写验收标准与时间边界。

应引用：
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `method-frozen`（`@unit_eo#definition/method-frozen`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `no-graph-db`（`@unit_eo#definition/no-graph-db`）
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Task 计划」块里的组件 `graph-db`（`@task_retrieval_report#task_plan/graph-db`）
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Activity 全景」块里的组件 `rag-graph`（`@task_retrieval_report#plan/rag-graph`）

### 反例

#### 约束冲突没指出或只引一处（`conflict_missed`）

没有指出冲突，或只引一侧：回答「做什么」与「凭什么」时，都要有一条 kind 为 conflict 的断言，同时引到上层 E&O 单元的关键约束 no-graph-db，与本层的执行上下文与约束 graph-db 或计划条目 rag-graph 之一。只引到一侧的 conflict 断言，在哪一问里都算出现；引单元的另一条关键约束、或只以块级引用引责任定义块，不算引到上层那一侧。

诱饵：
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Task 计划」块里的组件 `graph-db`（`@task_retrieval_report#task_plan/graph-db`）
- Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Activity 全景」块里的组件 `rag-graph`（`@task_retrieval_report#plan/rag-graph`）

冲突的两处（conflict 断言要同时引到）：
- 上层：责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `no-graph-db`（`@unit_eo#definition/no-graph-db`）
- 本层：Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Task 计划」块里的组件 `graph-db`（`@task_retrieval_report#task_plan/graph-db`）
- 本层：Task「四种取法对照（含 RAG）与实验报告」（最新版，第 3 版）的「Activity 全景」块里的组件 `rag-graph`（`@task_retrieval_report#plan/rag-graph`）
- 要求指出冲突的问题：what、basis


## 四之3、版本变化（`version_change`）

十月周期目标经一轮重走出了第 2 版（锁版那条验收条件改了），锁版 Mission 的验收条件仍钉在第 1 版；回答引生效的第 2 版，不引旧版本。

- 起点：Task「验收冻结与钉定提交」（最新版，第 2 版）（`@task_freeze`）
- 认领的主干对象：`task_freeze`、`mission_lock`

### 播种步骤

1. E&O Mission Owner 把 `task_freeze` 指派给 锁版 Task 责任人（键 `assign_freeze`）
2. E&O Mission Owner 记外部事件（meeting）（键 `ev_lock_timing`）
  - 主体 `@mission_lock`，发生于 `now`
  - 内容：锁版时间对齐：十月周期目标经一轮重走改了锁版那条验收条件，锁版放到 10 月 20 日 CEO 定下对象结构之后；本 Mission 与下面的 Task 按新条件排期。（引用 `@october_goal#target/ac-lock`）
  - 说明：推：合成的对齐会，内容与主干末尾的一轮重走一致。
3. 锁版 Task 责任人 开始 `task_freeze`（键 `start_freeze`）
4. 锁版 Task 责任人 写状态快照「冻结 Task 状态」（主体 `@task_freeze`）（键 `snap_freeze`）
  - 时点 `now`，来源事件 `event:ev_lock_timing`
  - 进展：冻结清单在整理：独立验收矩阵、契约与登记的钉定、迁移 0039；锁版时点按周期目标的新验收条件排在 10 月 20 日之后。
  - 字段：period = `2026-10`
  - 说明：推：合成的状态。

### 六问标准答案

#### why：为什么要做「验收冻结与钉定提交」这个 Task？

它属于 Mission「0.2 锁版」，战役结果是 tkos.world 0.2 锁版；Mission 的验收条件对应十月周期目标里锁版那一条。十月周期目标经一轮重走出了第 2 版，这一条现在是「10 月 20 日 CEO 定下对象结构之后，tkos.world 0.2 锁版（验收冻结并钉定提交）」；Mission 的验收条件还钉在第 1 版，回答以生效的第 2 版为准。再往上是十月周期目标的目标结果「tkos.world 0.2 锁版」、E&O 六个月目标、公司春节前的长期目标、战略责任结构里的 05 E&O、总体战略的主线与公司拟定、还待管理层确认的长期身份。

应引用：
- Mission「0.2 锁版」（最新版，第 2 版）的「战役定义」块里的组件 `lock`（`@mission_lock#definition/lock`）
- Mission「0.2 锁版」（最新版，第 2 版）的「战役定义」块里的组件 `ac-lock`（`@mission_lock#definition/ac-lock`）
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `ac-lock`（`@october_goal#target/ac-lock`）
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `lock`（`@october_goal#target/lock`）
- 长期目标「E&O 六个月目标」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@eo_goal#target/outcome`）
- 长期目标「春节前核心业务第一次被真实证明」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@company_goal#target/outcome`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略责任结构」块里的组件 `eo`（`@strategy#responsibility_structure/eo`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略」块里的组件 `main-line`（`@strategy#strategy_core/main-line`）
- 公司「词元云集（TokenKing）」（最新版，第 1 版）的「企业身份与长期意图」块里的组件 `long-term-identity`（`@company#identity/long-term-identity`）

#### what：这个 Task 要做成什么？

工作结果是 0.2 验收冻结，钉定提交。任务定义里没有写验收标准与时间边界，Task 计划与 Activity 全景都是空的。

应引用：
- Task「验收冻结与钉定提交」（最新版，第 2 版）的「任务定义」块里的组件 `outcome`（`@task_freeze#definition/outcome`）

#### who：谁负责这个 Task？

锁版 Task 责任人，由 E&O Mission Owner 指派。

应引用：
- Task「验收冻结与钉定提交」（最新版，第 2 版）（`@task_freeze`）
- E&O Mission Owner 把 `task_freeze` 指派给 锁版 Task 责任人（`event:assign_freeze`）

#### now：现在进展到哪了？

进行中，由开始事件推出。最近一条状态：冻结清单在整理（独立验收矩阵、契约与登记的钉定、迁移 0039），锁版时点按周期目标的新验收条件排在 10 月 20 日之后。

应引用：
- Task「验收冻结与钉定提交」（最新版，第 2 版）（`@task_freeze`）
- 锁版 Task 责任人 开始 `task_freeze`（`event:start_freeze`）
- 状态快照「冻结 Task 状态」（最新版，第 1 版）（`@snap_freeze`）

#### happened：最近发生了什么？

Mission Owner 记了一次锁版时间对齐：十月周期目标改了锁版那条验收条件，锁版放到 10 月 20 日 CEO 定下对象结构之后，Mission 与 Task 按新条件排期；之后责任人记了开始。

应引用：
- E&O Mission Owner 记外部事件（meeting）（`event:ev_lock_timing`）
- 锁版 Task 责任人 开始 `task_freeze`（`event:start_freeze`）

#### basis：凭什么这样做？受什么约束？

依据是十月周期目标第 2 版（现行）锁版那条验收条件；Mission 的验收条件仍引第 1 版，只作出处。约束是 E&O 单元的两条关键约束：tkos.method 0.4、0.5 冻结不改，不引入图数据库。

应引用：
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `ac-lock`（`@october_goal#target/ac-lock`）
- Mission「0.2 锁版」（最新版，第 2 版）的「战役定义」块里的组件 `ac-lock`（`@mission_lock#definition/ac-lock`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `method-frozen`（`@unit_eo#definition/method-frozen`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `no-graph-db`（`@unit_eo#definition/no-graph-db`）

### 反例

#### 引用旧版本（`stale_version`）

把旧版本当现行：引十月周期目标第 1 版目标定义块里的锁版验收条件（还没改的一版），例如顺着 Mission 验收条件里钉住的引用引到它。回答引了比这次播种结束时更旧的任何版本，都算出现。

诱饵：
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」第 1 版的「目标定义」块里的组件 `ac-lock`（`@october_goal@1#target/ac-lock`）


## 四之4、验收失败重启（`rework_restart`）

联调 Task 交付后被退回，补齐后再交付、验收通过，之后又被重开；回答反映重开后的当前状态与退回原因，交付后与验收后的状态都已过时。

- 起点：Task「联调与委托登记（10/9–11）」（最新版，第 2 版）（`@task_trial_integration`）
- 认领的主干对象：`task_trial_integration`

### 播种步骤

1. E&O Mission Owner 把 `task_trial_integration` 指派给 联调 Task 责任人（键 `assign_integration`）
2. 联调 Task 责任人 开始 `task_trial_integration`（键 `start_integration`）
3. 联调 Task 责任人 交付 `task_trial_integration`（键 `deliver_integration_first`）
  - 事件内容：联调完成：天枢以服务主体凭证代记门、指派与生命周期事件已跑通，CEO 与 E&O DRI 的委托已登记。
  - 说明：推：合成的交付。
4. 联调 Task 责任人 写状态快照「联调 Task 状态（交付后）」（主体 `@task_trial_integration`）（键 `snap_integration_delivered`）　**诱饵：验收失败重启·引用过时的状态或快照**
  - 时点 `now`，来源事件 `event:deliver_integration_first`
  - 进展：已交付，等 Mission Owner 验收：代记三类动作跑通，两份委托已登记。
  - 字段：period = `2026-10`
  - 说明：推：合成的状态，之后过时（诱饵）。
5. E&O Mission Owner 退回 `task_trial_integration`（键 `reject_integration`）
  - 事件内容：退回：Mission Owner 的委托还没登记，天枢代记 Task 的指派与验收会被拒；三份委托都登记后再交付。
  - 说明：推：合成的退回理由（十月起点按人分段播种时，Owner 的委托是最后一步）。
6. 联调 Task 责任人 交付 `task_trial_integration`（键 `deliver_integration_second`）
  - 事件内容：补登 Mission Owner 的委托，三份委托齐了；代记 Task 的指派与验收跑通。
  - 说明：推：合成的再次交付。
7. E&O Mission Owner 验收通过 `task_trial_integration`（键 `accept_integration`）
  - 事件内容：验收通过。
8. 联调 Task 责任人 写状态快照「联调 Task 状态（验收后）」（主体 `@task_trial_integration`）（键 `snap_integration_closed`）　**诱饵：验收失败重启·引用过时的状态或快照**
  - 时点 `now`，来源事件 `event:accept_integration`
  - 进展：已验收关闭：三份委托齐了，代记跑通。
  - 字段：period = `2026-10`
  - 说明：推：合成的状态，之后过时（诱饵）。
9. E&O Mission Owner 重开 `task_trial_integration`（键 `reopen_integration`）
  - 事件内容：重开：试用周第一天，天枢代记的 Task 交付被拒，外部确认时刻晚于记录时刻（天枢取的是回写时刻）；要改天枢侧的确认时刻取法，再联调一次。
  - 说明：推：合成的重开理由（契约第 14 节：外部确认时刻不晚于记录时刻）。
10. 联调 Task 责任人 写状态快照「联调 Task 状态（重开后）」（主体 `@task_trial_integration`）（键 `snap_integration_reopened`）
  - 时点 `now`，来源事件 `event:reopen_integration`
  - 进展：重开后进行中：和天枢改外部确认时刻的取法，改成页面上确认的那一刻。
  - 关键风险与阻塞：天枢侧改动的排期还没定。
  - 字段：period = `2026-10`
  - 说明：推：合成的状态，是现状。

### 六问标准答案

#### why：为什么要做「联调与委托登记（10/9–11）」这个 Task？

它是 Mission「天枢 × 本体 0.2 试用」的第一段，这个 Mission 的战役结果是天枢与本体 tkos.world 0.2 联调并试用。Mission 的验收条件是「10 月 16 日天枢联调验收通过」，服务十月周期目标的同名目标结果；再往上是 E&O 六个月目标、公司春节前的长期目标、战略责任结构里的 05 E&O、总体战略的主线与公司拟定、还待管理层确认的长期身份。

应引用：
- Mission「天枢 × 本体 0.2 试用」（最新版，第 2 版）的「战役定义」块里的组件 `trial`（`@mission_trial#definition/trial`）
- Mission「天枢 × 本体 0.2 试用」（最新版，第 2 版）的「战役定义」块里的组件 `ac-tianshu`（`@mission_trial#definition/ac-tianshu`）
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `tianshu`（`@october_goal#target/tianshu`）
- 长期目标「E&O 六个月目标」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@eo_goal#target/outcome`）
- 长期目标「春节前核心业务第一次被真实证明」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@company_goal#target/outcome`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略责任结构」块里的组件 `eo`（`@strategy#responsibility_structure/eo`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略」块里的组件 `main-line`（`@strategy#strategy_core/main-line`）
- 公司「词元云集（TokenKing）」（最新版，第 1 版）的「企业身份与长期意图」块里的组件 `long-term-identity`（`@company#identity/long-term-identity`）

#### what：这个 Task 要做成什么？

工作结果是天枢与本体 0.2 联调，登记给天枢的委托；时间边界是 10 月 9 日至 11 日。任务定义里没有写验收标准，Task 计划与 Activity 全景都是空的。

应引用：
- Task「联调与委托登记（10/9–11）」（最新版，第 2 版）的「任务定义」块里的组件 `outcome`（`@task_trial_integration#definition/outcome`）
- Task「联调与委托登记（10/9–11）」（最新版，第 2 版）的「任务定义」块里的组件 `window`（`@task_trial_integration#definition/window`）

#### who：谁负责这个 Task？

联调 Task 责任人，由 E&O Mission Owner 指派；验收、退回与重开由 Mission Owner 记。

应引用：
- Task「联调与委托登记（10/9–11）」（最新版，第 2 版）（`@task_trial_integration`）
- E&O Mission Owner 把 `task_trial_integration` 指派给 联调 Task 责任人（`event:assign_integration`）

#### now：现在进展到哪了？

进行中：Mission Owner 重开了它（生命周期由重开事件推出），原因是试用周第一天天枢代记的交付被拒，外部确认时刻晚于记录时刻，要改天枢侧的确认时刻取法再联调。此前它被退回过一次，原因是 Mission Owner 的委托还没登记。最近一条状态是重开后写的：在和天枢改确认时刻的取法，排期还没定。交付后与验收后的两条状态都已过时。

应引用：
- Task「联调与委托登记（10/9–11）」（最新版，第 2 版）（`@task_trial_integration`）
- E&O Mission Owner 重开 `task_trial_integration`（`event:reopen_integration`）
- E&O Mission Owner 退回 `task_trial_integration`（`event:reject_integration`）
- 状态快照「联调 Task 状态（重开后）」（最新版，第 1 版）（`@snap_integration_reopened`）

#### happened：最近发生了什么？

开始 → 第一次交付（代记跑通、两份委托已登记）→ 被退回（缺 Mission Owner 的委托）→ 补登后第二次交付 → 验收通过 → 重开（天枢的外部确认时刻取法有误）。

应引用：
- 联调 Task 责任人 交付 `task_trial_integration`（`event:deliver_integration_first`）
- E&O Mission Owner 退回 `task_trial_integration`（`event:reject_integration`）
- 联调 Task 责任人 交付 `task_trial_integration`（`event:deliver_integration_second`）
- E&O Mission Owner 验收通过 `task_trial_integration`（`event:accept_integration`）
- E&O Mission Owner 重开 `task_trial_integration`（`event:reopen_integration`）

#### basis：凭什么这样做？受什么约束？

依据是 Mission 的验收条件「10 月 16 日天枢联调验收通过」；退回与重开的理由写在那两条事件里。约束是本 Task 的时间边界（10 月 9 日至 11 日）与 E&O 单元的两条关键约束。

应引用：
- Mission「天枢 × 本体 0.2 试用」（最新版，第 2 版）的「战役定义」块里的组件 `ac-tianshu`（`@mission_trial#definition/ac-tianshu`）
- Task「联调与委托登记（10/9–11）」（最新版，第 2 版）的「任务定义」块里的组件 `window`（`@task_trial_integration#definition/window`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `method-frozen`（`@unit_eo#definition/method-frozen`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `no-graph-db`（`@unit_eo#definition/no-graph-db`）
- E&O Mission Owner 退回 `task_trial_integration`（`event:reject_integration`）
- E&O Mission Owner 重开 `task_trial_integration`（`event:reopen_integration`）

### 反例

#### 引用过时的状态或快照（`stale_state`）

把过时的状态当现状：交付后「已交付，等验收」与验收后「已验收关闭」两条快照，都已被后来的退回、重开与重开后的快照取代。断言不论哪一种，引了它们就算出现。

诱饵：
- 状态快照「联调 Task 状态（交付后）」（最新版，第 1 版）（`@snap_integration_delivered`）
- 状态快照「联调 Task 状态（验收后）」（最新版，第 1 版）（`@snap_integration_closed`）


## 四之5、无 Activity 的 Task（`task_only`）

实验 E 的 Task 按 Task-only 写法：不建 Activity，Activity 全景块里是带责任人的计划条目，Task 计划块为空；兄弟 Task「对照实验 B」下有一条 Activity 作诱饵。

- 起点：Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）（`@task_experiment_e`）
- 认领的主干对象：`task_experiment_e`、`task_experiment_b`

### 播种步骤

1. E&O Mission Owner 把 `task_experiment_e` 指派给 实验 E Task 责任人（键 `assign_experiment_e`）
2. 实验 E Task 责任人 修订 `task_experiment_e`（键 `plan_experiment_e`）
  - 任务定义：
    - 组件 `ac-gold`（成功 / 验收标准）：五个场景各有播种、标准答案与反例判定；标准答案经 E&O DRI 批准，跑器拒用未批准或已改动的标准答案。
  - Activity 全景：按 Task-only 写法：不建 Activity，每一段由计划条目写明责任人。
    - 组件 `scenarios`（计划条目）：写五个场景的播种与标准答案（六问、应引用的块与组件）。（责任人 `$experiment_e_ic`）
    - 组件 `judge`（计划条目）：把反例判定写成纯函数，配无库测试。（责任人 `$eo_agent`）
    - 组件 `review`（计划条目）：审阅稿交 E&O DRI 审，按内容哈希批准。（责任人 `$eo_dri`）
    - 组件 `material`（计划条目）：真实战略材料到位后替换 Company 与 Strategy，重新播种、重新批准。（责任人 `$experiment_e_ic`）
  - 说明：验收条件与四条计划取自票 #67 的正文与 E&O 定范围的评论（确）；每条分给谁是推断。验收条件并进任务定义块（按 id 合并，工作结果保留），计划条目在 Activity 全景块。
3. 实验 E Task 责任人 开始 `task_experiment_e`（键 `start_experiment_e`）
4. 实验 E Task 责任人 记外部事件（other）（键 `ev_scope_e`）
  - 主体 `@task_experiment_e`，发生于 `now`
  - 内容：E&O 定下本 Task 的范围：真实战略材料的提供方仍待 CEO 定，先做不依赖材料的部分；Company 与 Strategy 先用现有存根，块与组件的位置按真实材料预留。
  - 说明：取自票 #67 的评论（确）；时刻取播种时刻。
5. 实验 E Task 责任人 写状态快照「实验 E Task 状态」（主体 `@task_experiment_e`）（键 `snap_experiment_e`）
  - 时点 `now`，来源事件 `event:ev_scope_e`
  - 进展：五个场景的播种、标准答案与反例判定已写完，等 E&O DRI 审阅。
  - 关键风险与阻塞：真实战略材料的提供方待 CEO 定。
  - 字段：period = `2026-10`
  - 说明：推：合成的状态；阻塞取自票 #67 的评论。
6. E&O Mission Owner 把 `task_experiment_b` 指派给 实验 B Task 责任人（键 `assign_experiment_b`）
7. 实验 B Task 责任人 建 Activity「Task+Activity 线：按 Activity 执行对照 Mission」（键 `activity_b_run`）　**诱饵：无 Activity 的 Task·引用不存在的 Activity**
  - 执行目的与要求：
    - 组件 `work`（执行事项）：把对照 Mission 的执行拆成 Activity，指派给执行者，逐条开始、交付与验收。
  - 字段：parent_ref = `@task_experiment_b`
  - 说明：推：对照实验 B 的 Task+Activity 线的写法，是本场景的诱饵：它属于兄弟 Task，不属于实验 E 的 Task。正文是执行目的与要求块的执行事项。
8. 实验 B Task 责任人 把 `activity_b_run` 指派给 E&O Agent（键 `assign_b_run`）

### 六问标准答案

#### why：为什么要做「实验 E：五个场景、真实战略材料与标准答案」这个 Task？

它属于 Mission「0.2 实验与报告」，战役结果是 0.2 的实验与实验报告。这个 Mission 服务 E&O 十月周期目标的目标结果「tkos.world 0.2 锁版」：现行的锁版验收条件是 10 月 20 日 CEO 定下对象结构之后锁版，实验结果是定对象结构的依据。再往上是 E&O 六个月目标、公司春节前的长期目标、战略责任结构里的 05 E&O、总体战略的主线与公司拟定、还待管理层确认的长期身份。

应引用：
- Mission「0.2 实验与报告」（最新版，第 2 版）的「战役定义」块里的组件 `report`（`@mission_experiments#definition/report`）
- 周期目标「E&O 10 月：tkos.world 0.2 在真实经营中跑通」（最新版，第 2 版）的「目标定义」块里的组件 `lock`（`@october_goal#target/lock`）
- 长期目标「E&O 六个月目标」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@eo_goal#target/outcome`）
- 长期目标「春节前核心业务第一次被真实证明」（最新版，第 1 版）的「目标定义」块里的组件 `outcome`（`@company_goal#target/outcome`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略责任结构」块里的组件 `eo`（`@strategy#responsibility_structure/eo`）
- 战略「词元云集总体战略（存根）」（最新版，第 1 版）的「战略」块里的组件 `main-line`（`@strategy#strategy_core/main-line`）
- 公司「词元云集（TokenKing）」（最新版，第 1 版）的「企业身份与长期意图」块里的组件 `long-term-identity`（`@company#identity/long-term-identity`）

#### what：这个 Task 要做成什么、怎么安排？

工作结果是五个场景，用真实战略材料，写标准答案。任务定义里一条验收标准：五个场景各有播种、标准答案与反例判定，标准答案经 E&O DRI 批准，跑器拒用未批准或改过的。按 Task-only 写法，不建 Activity，Activity 全景块里四条计划条目：写五个场景的播种与标准答案；反例判定写成纯函数并配无库测试；审阅稿交 E&O DRI 按内容哈希批准；真实战略材料到位后替换、重新播种与批准。Task 计划块是空的。

应引用：
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「任务定义」块里的组件 `outcome`（`@task_experiment_e#definition/outcome`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「任务定义」块里的组件 `ac-gold`（`@task_experiment_e#definition/ac-gold`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `scenarios`（`@task_experiment_e#plan/scenarios`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `judge`（`@task_experiment_e#plan/judge`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `review`（`@task_experiment_e#plan/review`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `material`（`@task_experiment_e#plan/material`）

#### who：谁负责这个 Task，各段由谁做？

实验 E Task 责任人，由 E&O Mission Owner 指派。分段按计划条目：写场景与材料到位后的重播种是他本人，反例判定是 E&O Agent，审阅批准是 E&O DRI。计划条目的责任人只是记录，不是指派；本 Task 下没有 Activity。

应引用：
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）（`@task_experiment_e`）
- E&O Mission Owner 把 `task_experiment_e` 指派给 实验 E Task 责任人（`event:assign_experiment_e`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `scenarios`（`@task_experiment_e#plan/scenarios`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `judge`（`@task_experiment_e#plan/judge`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `review`（`@task_experiment_e#plan/review`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块里的组件 `material`（`@task_experiment_e#plan/material`）

#### now：现在进展到哪了？

进行中，由开始事件推出。最近一条状态：五个场景的播种、标准答案与反例判定已写完，等 E&O DRI 审阅；阻塞是真实战略材料的提供方待 CEO 定。

应引用：
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）（`@task_experiment_e`）
- 实验 E Task 责任人 开始 `task_experiment_e`（`event:start_experiment_e`）
- 状态快照「实验 E Task 状态」（最新版，第 1 版）（`@snap_experiment_e`）

#### happened：最近发生了什么？

责任人写了验收标准与计划条目并开始；E&O 定下本 Task 的范围：材料未到，先做不依赖材料的部分，Company 与 Strategy 用存根并预留块与组件的位置。

应引用：
- 实验 E Task 责任人 开始 `task_experiment_e`（`event:start_experiment_e`）
- 实验 E Task 责任人 记外部事件（other）（`event:ev_scope_e`）

#### basis：凭什么这样做？受什么约束？

依据是本 Task 任务定义里的验收标准与 E&O 定下范围的那条事件；约束是 E&O 单元的两条关键约束。

应引用：
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「任务定义」块里的组件 `ac-gold`（`@task_experiment_e#definition/ac-gold`）
- 实验 E Task 责任人 记外部事件（other）（`event:ev_scope_e`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `method-frozen`（`@unit_eo#definition/method-frozen`）
- 责任单元「E&O」（最新版，第 1 版）的「责任定义」块里的组件 `no-graph-db`（`@unit_eo#definition/no-graph-db`）

### 反例

#### 引用不存在的 Activity（`phantom_activity`）

引用不存在的 Activity：本 Task 按 Task-only 写法，下面没有 Activity。断言不论哪一种，引了任一 Activity（包括兄弟 Task「对照实验 B」下的那条）或 scope 里不存在的对象，就算出现。

诱饵：
- Activity「Task+Activity 线：按 Activity 执行对照 Mission」（最新版，第 2 版）（`@activity_b_run`）

#### 把有内容的块说成空（`content_as_empty`）

把有内容的块说成空：没有 Activity 不等于没有安排，本 Task 的 Activity 全景块有四条带责任人的计划条目，任务定义块有工作结果与一条验收标准。gap 断言以块级引用引了这两块，就算出现；引块里的某一条（以某条计划或验收标准为依据说它还没满足）不算。Task 计划块确实为空，说它为空不算。

诱饵：
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「Activity 全景」块（`@task_experiment_e#plan`）
- Task「实验 E：五个场景、真实战略材料与标准答案」（最新版，第 3 版）的「任务定义」块（`@task_experiment_e#definition`）

## 五、回答的形状与反例判定

回答是断言的列表 `{claim, kind, refs}`：kind 为 fact（陈述内容）、gap（说明为空或取不到）或 conflict（指出冲突，refs 同时引冲突的两处）。判定是纯函数 `experiments.world_v02.counterexamples.judge`，口径见该模块与 README。

## 六、请你确认

1. 本稿先按方法侧 Content Pact 改写（#82，依据 docs/world-v02-content-pact-mapping.md）：主干、场景与标准答案的引用都换成了新块与组件；再把 Company 与 Strategy 的正文换成公司知识库《总体战略定位与阶段路径》（2026-07）的原句（#67）。内容哈希随之改变，此前的内容也还没有经你批准；请按本稿重新审阅、逐条确认下面各项后再批准，批准之前实验跑器拒用。
2. Company 与 Strategy 的正文是公司知识库《总体战略定位与阶段路径》（2026-07）的原句（#67）：只在标点处拆，不改字句；逐条原句与页码见 experiments/world_v02/strategy_material.json，第三节这两步的说明里也逐条写了页码。只有 token-principle「不追求 Token 用量最大化」出自 9/23 会议，不在材料里，保留 0.1 原文。三份播种文件（本主干、b_source-2026-10.json、deploy/world-02/seed-eo-2026-10.json）的 Company 与 Strategy 相同，只有本主干的责任结构块多一条 Agents。E&O 单元与两条长期目标的正文照搬十月起点，来自 0.1 审过的材料，按 Content Pact 只在分号、冒号处拆进新组件，不改写字句。Strategy 仍留在草稿，标题仍是「词元云集总体战略（存根）」；要不要改标题、要不要走指定本轮、Agreement 与确认生效，请你定。
3. 战略材料的句子放进哪个组件类型是推断，请逐条看：第 10 页「本轮管理层需要确认的 6 个问题」六问都带着该页标题、以问句原样放进组件，不写成已定——问题 1 是业务定义 long-term-identity，问题 3 是核心壁垒 foundation（材料里说「共同底座」的只有这一问，第 6 页图里的三个方框名是图注，没拼成句子），问题 2 放战略取舍，问题 4 放战略约束，问题 5 放总体战略，问题 6 放商业模式；与之对应的第 7 页收入结构、第 8 页 831 成功标准、第 9 页组织建设照材料写成陈述，它们要确认的内容就是问题 6、5、2。第 2 页的「核心原则」放在公司的价值观与公司原则，没放战略取舍；第 3 页「词元云集重点承接」下的三层放产品与价值主张（没带这个标题，免得一句里两个冒号）；第 8 页阶段路径的标题句（path）与 831 成功标准放总体战略，三段放市场进入与获客方式；第 7 页「关键判断」整句放关键假设 token-traction（0.1 的推断句「不能把 Token 消纳设为唯一牵引」出自这里）；第 9 页只有标题一句，放战略取舍。
4. 标题式短语连同它下面的说明合成一句时写成「标题：说明」，几条说明（版面上分行或带圆点，如第 5 页两类业务、第 6 页两组输入、第 7 页三段收入、第 8 页三段路径）以「；」隔开，这是唯一加进去的字符。没放进组件的：第 1 页封面（概括句的三段在第 10 页问题 1、第 2 页阶段打法与第 8 页阶段路径里有对应）、第 2 页标题「结论先行」、第 6 页图里的三个方框名与各页页脚。各场景 Why 的应引用项没变：仍是 main-line（现在是第 2 页「阶段打法」一句）、long-term-identity（第 10 页问题 1）与责任结构的 eo；新加的组件没加进应引用项，引了不算错。
5. 旧块落到哪个新组件是推断：E&O 单元原定义块拆成核心责任与战略贡献，原边界块是责任边界，原约束块拆成两条关键约束；Mission 与 Task 原定义块按冒号拆成结果、时间边界、关键里程碑或核心路径。
6. 材料里没有的新组件都留空，没有编：企业使命；目标客户、目标市场；两条长期目标的定位与承接块、成功标准与实现逻辑；十月周期目标的定位与承接块、时间边界与实现逻辑；各 Mission 的贡献、责任边界、关键取舍与关键约束与依赖（M1-B 除外）；各 Task 的贡献、工作边界与 Task 计划块（取法对照 Task 除外）。
7. Strategy 的责任结构块加了 Agents 的责任单元条目组件，文字「04 Agents」取自同一块的正文。
8. Agents 单元、六个月目标、9 月周期目标与 M1-B Mission 的正文取自 0.1 审过的 seed.json，按 0.2 改了形状：单元正文是核心责任，结果写成目标定义块的目标结果组件，M1-B 原约束块的正文是 Mission 计划块的关键约束与依赖。没有 Agents 十月的材料，周期目标仍是九月；三者都留在草稿。
9. M1-B 依赖「天枢 × 本体 0.2 试用」是推断：0.1 里它依赖 E&O 九月的 Mission，十月改指试用 Mission，理由是 CEO 与 DRI 的 Agent 在天枢上跑。
10. 十月周期目标的一轮重走（目标定义块里锁版那条验收条件改为「10 月 20 日 CEO 定下对象结构之后」）是为版本变化场景推断的，依据是规格里 10 月 20 日 CEO 决定对象结构的安排。
11. 「0.2 实验与报告」服务十月周期目标的锁版那条结果：推断，理由是 CEO 依据实验定对象结构，之后才锁版。
12. 约束冲突场景按新形状改写：约束不再单独成块，上层一侧是 E&O 单元的关键约束组件「不引入图数据库」，本层一侧是取法对照 Task 在 Task 计划块里的执行上下文与约束 graph-db（带约束角色），或照它执行的 Activity 全景计划条目 rag-graph。考点不变：上下层的约束冲突要被取到并指出，同时引两侧。graph-db 与 rag-graph 以及对应的评审与状态都是为反例合成的。
13. 「凭什么」的应引用项：起点链上直接对应的验收条件、起点自己的时间边界与 E&O 单元的两条关键约束。Mission 的时间边界、E&O 单元的责任边界、十月周期目标的其余验收条件与 Strategy 的两条战略约束（第 4 页判断标准、第 10 页问题 4）也带约束或验收角色，取上下文会列出，引了不算错，但不算应引用。「为什么」里 Mission 那一层只要求引战役结果（有验收条件出处的另引验收条件），关键里程碑与核心路径不算应引用。
14. 验收失败重启场景的两次交付、退回理由（缺 Mission Owner 的委托）、重开理由（天枢外部确认时刻的取法）与三条状态都是合成的。
15. 版本变化场景里 Mission Owner 记的「锁版时间对齐」会与冻结 Task 的状态（冻结清单在整理、锁版排在 10 月 20 日之后）是合成的。
16. 三个 Mission 都停在「已成立」，没有记开始，下面的 Task 已在进行中；十月起点没给 Mission 的开始，这里也不补。
17. 试用周安排会、各场景的开始与状态是合成的。各 Task 的责任人用角色名（试用、联调、取法对照、锁版、实验 E、实验 B 各一名 Task 责任人）；十月起点里 Task 不指派，试用中在天枢里指派。
18. 对照实验 B 的 Task 下那条 Activity（Task+Activity 线）是为 Task-only 场景合成的诱饵，正文是执行事项。
19. 实验 E Task 的验收条件、四条计划与范围事件取自票 #67 的正文与评论；计划条目分给谁（E&O Agent 写判定、E&O DRI 审阅）是推断。验收条件放在任务定义块，计划条目放在 Activity 全景块。
20. 外部事件与状态快照的时刻取播种那一步的数据库时刻：场景写的是十月的事，在十月之前播种也照此写，不写具体日期。

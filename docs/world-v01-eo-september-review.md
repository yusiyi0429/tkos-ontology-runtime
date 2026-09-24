# E&O 九月回放：播种与标准答案审阅稿

> 由 `experiments/world_v01/seed.json` 与 `gold.json` 生成（`python -m experiments.world_v01.gold render`），不要手改。
> 业务真实性请你审；标准答案经 E&O DRI 运行 `python -m experiments.world_v01.gold approve --by "E&O DRI"` 批准后，
> 实验（票 #29）才能使用。

- 批准状态：已由 E&O DRI 于 2026-09-24T11:09:58.229681+00:00 批准
- 当前内容哈希：`9ce66139bf61b589d748479cee405581f73a3c29bbdd853c0167aa44f5218be5`

## 一、身份

| 键 | 显示名 | 类型 | 所在 scope | 角色（域） |
|---|---|---|---|---|
| `ceo` | CEO | human | main | CEO（公司）；CEO（E&O）；CEO（Agents）；CEO（Method） |
| `eo_dri` | E&O DRI | human | main | DOMAIN_DRI（E&O） |
| `owner` | E&O Mission Owner | human | main | OWNER, IC（E&O） |
| `design_ic` | 方案 Task 责任人 | human | main | IC（E&O） |
| `data_ic` | 数据 Task 责任人 | human | main | IC（E&O） |
| `eo_agent` | E&O Agent | agent | main | AGENT（E&O） |
| `method_dri` | Method DRI | human | main | DOMAIN_DRI（Method）；IC（E&O） |
| `agents_dri` | Agents DRI | human | main | DOMAIN_DRI（Agents）；IC（E&O） |
| `outside_ceo` | 另一家公司 CEO | human | outside | CEO（另一家公司）；CEO（另一家公司的 E&O） |
| `outside_dri` | 另一家公司 E&O DRI | human | outside | DOMAIN_DRI（另一家公司的 E&O） |

## 二、播种步骤

按顺序经 HTTP 的 prepare 与 commit 写入。建对象、修订、指派与门记播种时刻；外部事件与状态快照按下面写的真实时间。

1. CEO 建 公司「词元云集（TokenKing）」（键 `company`）
  - 身份：长期愿景是建设系统性 Token 工厂；拟定的长期身份是「企业经营范式重构与 Token 运营中枢」（七月稿中待管理层确认的六个问题之一）。（链接 https://feishu.example/docx/strategy-positioning-2026-07）
  - 约束：创业公司，不追求 Token 用量最大化：研发按需用模型。
  - 说明：来源：七月总体战略定位稿 p1、p2、p10（确）；约束出自 9/23 会 02:03（确）。
2. CEO 建 战略「词元云集总体战略（存根）」（键 `strategy`）
  - 战略选择：主线是 AI 企业智能决策网络；辅线 TokenOps 只做轻量验证。（链接 https://feishu.example/docx/strategy-positioning-2026-07）
  - 路径：到 8 月 31 日做内部灯塔；8 月 31 日到春节前做内部验证加少量外部试点；下一轮注资后扩张。
  - 关键假设：不能把 Token 消纳设为唯一牵引。
  - 能力：Foundry / Ontology、引擎与 TokenHub 是两条业务的共同底座。
  - 责任结构：3 个战场：01 灯塔项目、02 Token Ops、03 002 客户。7 个能力域：02 Re-architecture、03 Method、04 Agents、05 E&O、07 TokenHub、08 Talent & Organization、09 Finance, Compliance & Operations。（链接 https://feishu.example/docx/battlefield-map-v1）
  - 字段：parent_ref = `@company`
  - 说明：来源：七月战略稿 p5–p10、战场图 V1（确；assumptions 为推断）。战略约束材料里没有，留空。
3. CEO 建 责任单元「E&O」（键 `unit_eo`）
  - 定义：Engine & Ontology，战场图编号 05 的能力域：主导语义模型（本体）与 Context Runtime，主要支撑灯塔项目的 Mission 01-2、01-3、01-4。（链接 https://feishu.example/docx/battlefield-map-v1）
  - 边界：业务建模不归 E&O：由 CEO 与 Method 锁定业务语义，E&O 忠实翻译成语义模型、数据承载与运行时。Activity 要不要成为对象由 E&O 自己定。（链接 https://feishu.example/docx/tkos-business-world-framework-v01）
  - 约束：tkos.method/0.4、0.5 冻结不改；当前量级用 PostgreSQL 加 pgvector，不引入图数据库。
  - 字段：unit_kind = `domain`，architecture_ref = `@strategy#responsibility_structure`
  - 说明：来源：战场图 V1、框架稿 p4/p14/p15、方案 v0.1（确）；pgvector 一句出自 9/22 17:37 会的讨论（推）。
4. CEO 建 责任单元「Agents」（键 `unit_agents`）
  - 定义：战场图编号 04 的能力域：CEO Agent、DRI Agent 与经营协同 Agent。
  - 字段：unit_kind = `domain`，architecture_ref = `@strategy#responsibility_structure`
  - 说明：诱饵所需的别的责任单元。来源：战场图 V1（确）。
5. CEO 把 `unit_eo` 指派给 E&O DRI（键 `assign_eo_dri`）
  - 说明：真实指派时间与会议材料里没有（请你确认）；这里记播种时刻。
6. CEO 把 `unit_agents` 指派给 Agents DRI（键 `assign_agents_dri`）
7. CEO 建 长期目标「春节前核心业务第一次被真实证明」（键 `company_goal`）
  - 结果：跑通「Re-architecture 进入客户 → TKOS 持续经营」这条主线，形成可复制的产品、交付与商业模式；同时跑通 TokenHub / TokenOps 的商业闭环。（链接 https://feishu.example/docx/battlefield-map-v1）
  - 字段：scope = `company`，horizon = `2027 年春节前`，parent_ref = `@company`
  - 说明：来源：战场图 V1 顶栏（确）；衡量材料里没有，留空。
8. E&O DRI 建 长期目标「E&O 六个月目标」（键 `eo_goal`）
  - 结果：建立支撑 TKOS 长期运行的 Enterprise Context 与 Agent Infrastructure，让真实经营数据能持续进入、调用、回写和复用。（链接 https://feishu.example/docx/battlefield-map-v1）
  - 字段：scope = `unit`，horizon = `六个月`，parent_ref = `@unit_eo`，goal_ref = `@company_goal`
  - 说明：来源：战场图 V1 的 05 栏（确）；衡量材料里没有，留空；指向公司级目标为推断。
9. CEO 确认长期目标 `company_goal`（outcome=accepted）（键 `confirm_company_goal`）
  - 说明：确认记录材料里没有：按战场图已发布视为 CEO 已确认（请你确认）。
10. CEO 确认长期目标 `eo_goal`（outcome=accepted）（键 `confirm_eo_goal`）
11. E&O DRI 建 周期目标「E&O 9 月」（键 `eo_period`）　**诱饵：空块被当作有内容**
  - 结果：战略与经营核心本体逐步稳定；真实数据真正进入；Agent 开始共同消费 Context。（链接 https://feishu.example/docx/battlefield-map-v1）
  - 字段：period = `2026-09`，goal_ref = `@eo_goal`
  - 说明：来源：战场图 V1 的 05「9 月 PCO」（确）；验收标准材料里没有，留空（空块诱饵）。
12. E&O DRI 承诺周期目标 `eo_period`（键 `commit_eo_period`）
13. CEO 确认周期目标 `eo_period`（outcome=accepted）（键 `confirm_eo_period`）
  - 说明：承诺与确认的时间材料里没有（请你确认）。
14. E&O DRI 建 Mission「9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用」（键 `mission`）　**诱饵：空块被当作有内容**
  - 定义：由 E&O 为 CEO Agent、DRI Agent 与 M1-A、M1-B 在 9 月底真实可用，提供本体、上下文与数据读写的底座。
  - 打法：本体建模分几步走：业务建模 → 数据建模与数据环境准备（两者可并行，责任人不同）→ 执行方案；9/23 之后分成语义模型、数据承载、Context Runtime、质量验证四块。打法只写路径、里程碑与质量标准。（链接 https://feishu.example/docx/tkos-business-world-framework-v01）
  - 约束：写入先限得死一点：每次写入声明场景、触发事件、是否人工验收；先做静态截面实验，不以「大家都能用」为指标。
  - 字段：goal_ref = `@eo_period`
  - 说明：标题按规格原文；9/23 会原话是「CU agent 和 DR agent」，CU 可能是 CEO 的误转（请你确认）。定义为推断；打法与约束出自 9/23 会 01:13–01:15、01:29、01:59–02:00（确）；验收标准材料里没有，留空（空块诱饵）。
15. E&O DRI 把 `mission` 指派给 E&O Mission Owner（键 `assign_owner`）
  - 说明：Mission Owner 是谁、何时指派，材料里没有（请你确认）。
16. E&O Mission Owner 承诺 Mission `mission`（phase=initiation）（键 `commit_mission`）
17. E&O DRI 确认 Mission `mission`（phase=initiation, outcome=accepted）（键 `confirm_mission`）
  - 说明：立项的承诺与确认材料里没有记录（请你确认）；是否核心战役材料里没有，不标。
18. E&O Mission Owner 建 Task「TKOS 业务建模方案设计」（键 `task_design`）　**诱饵：旧版本对象、旧版本对象**
  - 定义：按 CEO 对齐稿的口径设计本体：4 个责任范围 × 7 类经营语义、约 20 个一级对象。（链接 https://feishu.example/docx/tkos-ontology-v07-ceo-alignment）
  - 验收标准：9/24 给 CEO 对一版计划方案，节前再对齐。
  - 字段：parent_ref = `@mission`
  - 说明：第 1 版是 9/22 的口径（旧版本诱饵），见下面的修订。名称原话是「TKOS 业务实验建模方案设计」（确 9/23 会 01:06）。
19. E&O Mission Owner 建 Task「数据环境准备」（键 `task_data`）
  - 定义：与业务建模并行，准备并搭建数据环境：数据库随一二级分类重构，出计划；对应方案里的「数据承载」。（链接 https://feishu.example/docx/tkos-world-plan-v01）
  - 约束：method 0.4、0.5 冻结，不迁移、不建桥；迁移只追加；播种经治理路径且可清。（链接 https://repo.example/tkos-ontology-runtime/blob/d5b158e/docs/contracts/tkos-world-0.1.md）
  - 字段：parent_ref = `@mission`
  - 说明：来源：9/23 会 01:14、01:56–01:58（确）；正式验收标准材料里没有，留空。
20. E&O Mission Owner 把 `task_design` 指派给 方案 Task 责任人（键 `assign_design`）
  - 说明：9/23 会上 CEO 举例的方案 Task 责任人是两个人；契约一个 Task 只有一名责任人，这里先写一名（请你确认）。
21. E&O Mission Owner 把 `task_data` 指派给 数据 Task 责任人（键 `assign_data`）
22. E&O Mission Owner 修订 `task_design`
  - 定义：节前完成业务建模与数据架构重构的整体方案输出与对齐：9 类一级对象、内容块、所有对象共有的状态快照与事件机制；四块分别是语义模型、数据承载、Context Runtime、质量验证。（链接 https://feishu.example/docx/tkos-business-world-framework-v01）
  - 计划：下设三条 Activity：改建模材料、写内容块标准、Agent 分解测试。
  - 说明：9/23 会后的新口径（确 9/23 会 AI 总结、方案长版 5.1）；让第 1 版成为旧版本。
23. E&O Mission Owner 建 Activity「改建模材料」（键 `activity_rework`）　**诱饵：空块被当作有内容**
  - 执行指令：会后把结论与上下文交给执行者改材料：给背景、目的与质量标准，先改一版，读后再调第二版，中间处于进行中。（引用 `@task_design#definition`）
  - 字段：parent_ref = `@task_design`
  - 说明：来源：9/23 会 01:38（确）。执行者是 AI（确 01:17）；改的具体是哪份材料为推断（CEO 对齐稿 v0.8 草稿，实际产出为方案 v0.1）。约束留空（空块诱饵）。
24. E&O Mission Owner 建 Activity「写内容块标准」（键 `activity_blocks`）
  - 执行指令：确定每类一级对象有哪些块、每块讲什么、质量标准；先由 Method DRI 定，再与 CEO 对。形式（原则、规格或 good case）未定。
  - 字段：parent_ref = `@task_design`
  - 说明：来源：9/23 会 00:58、01:02（确）。跨单元：Method DRI 在 E&O 域持 IC 角色（请你确认）。
25. E&O Mission Owner 建 Activity「Agent 分解测试」（键 `activity_decompose`）
  - 执行指令：用 Markdown 输入输出做小范围验证，看内容块标准能否支撑 Agent 分解与流转；Agents DRI 负责分解能力与 Agent 选型，Method DRI 配合。
  - 字段：parent_ref = `@task_design`
  - 说明：来源：9/23 会 00:59–01:02（确）。跨单元：Agents DRI 在 E&O 域持 IC 角色（请你确认）。
26. E&O Mission Owner 建 Activity「隔离库迁移与播种」（键 `activity_isolated_db`）
  - 执行指令：在隔离库上按追加迁移升级到 world 0.1 并跑通独立验收；再把 E&O 九月回放经治理路径播种到可清的实验 scope。（链接 https://repo.example/tkos-ontology-runtime/blob/d5b158e/docs/world-v01-acceptance-report.md）
  - 字段：parent_ref = `@task_data`
  - 说明：来源：仓库提交记录（确）。
27. E&O Mission Owner 把 `activity_rework` 指派给 E&O Agent（键 `assign_rework`）
  - 说明：9/23 会上定：改材料由 AI 执行（确 01:17）；方案长版 5.1 把它分给一名 E&O 成员（请你确认责任人是 Agent 还是人）。
28. E&O Mission Owner 把 `activity_blocks` 指派给 Method DRI（键 `assign_blocks`）
29. E&O Mission Owner 把 `activity_decompose` 指派给 Agents DRI（键 `assign_decompose`）
30. E&O Mission Owner 把 `activity_isolated_db` 指派给 E&O Agent（键 `assign_isolated_db`）
31. Agents DRI 建 长期目标「Agents 六个月目标」（键 `agents_goal`）
  - 结果：CEO Agent 与 DRI / Manager Agent 常态化使用。
  - 字段：scope = `unit`，horizon = `六个月`，parent_ref = `@unit_agents`
  - 说明：诱饵的主干。来源：战场图 V1（推）。
32. Agents DRI 建 周期目标「Agents 9 月」（键 `agents_period`）
  - 结果：CEO 与 DRI 进入真实协同实践。
  - 字段：period = `2026-09`，goal_ref = `@agents_goal`
33. Agents DRI 建 Mission「M1-B 经营协同 MVP 与真实实践」（键 `agents_mission`）　**诱饵：别的责任单元的约束**
  - 定义：M1-B 经营协同的最小可用版本上线，并在真实经营里用起来。
  - 约束：允许人工兜底，不要求完整自动化。
  - 字段：goal_ref = `@agents_period`
  - 说明：别的责任单元的约束与无关 Mission 的状态的诱饵。来源：Agents DRI 的 9 月 Mission 卡（确）。
34. Agents DRI 给 `agents_mission` 建关系 depends_on
  - 指向：`@mission`
  - 说明：来源：Agents DRI 的 9 月 Mission 卡依赖栏写「E&O/本体：真实信息、变化更新与读写能力」（确）。有了这条，从 E&O 的 Mission 取对象时 referenced_by 会列出它，诱饵才引得到。
35. 方案 Task 责任人 写状态快照「方案 Task 9/22 状态」，时点 2026-09-22T18:05:00+08:00（键 `snap_design_0922`）　**诱饵：已处置的 issue、状态里的过期 artifact**
  - 进展：v0.7 已过 CEO 首评，未被接受；要按消费与存储方式重新设计。
  - 问题：团队与 CEO 对「业务建模」没有共识，CEO 次日回顾称是黄灯或红灯。
  - 产物：CEO 对齐稿 v0.7（链接 https://feishu.example/docx/tkos-ontology-v07-ceo-alignment）
  - 字段：subject_ref = `@task_design`，as_of = `2026-09-22T18:05:00+08:00`
  - 说明：来源：9/22 17:37 会、9/23 会 00:14（确）。问题在 9/23 会上已处置（已处置 issue 诱饵）；链接后来被替换（过期 artifact 诱饵）。
36. 方案 Task 责任人 写状态快照「方案 Task 9/23 状态」，时点 2026-09-23T12:30:00+08:00（键 `snap_design_0923`）　**诱饵：状态里的过期 artifact**
  - 进展：9 类一级对象、内容块与状态快照、事件框架在 9/23 会上达成共识，分工已定。
  - 问题：内容块清单与质量标准要等方法侧；核心战役由谁标记仍待定。
  - 产物：CEO 对齐稿 v0.8 草稿（此后停止迭代）（链接 https://feishu.example/docx/tkos-ontology-v08-draft）
  - 字段：subject_ref = `@task_design`，as_of = `2026-09-23T12:30:00+08:00`
  - 说明：来源：9/23 会、方案长版七（确；v0.8 停止迭代为推断）。v0.8 链接后来被方案 v0.1 取代（过期 artifact 诱饵）；框架稿记在 9/23 会的事件里。
37. E&O Agent 写状态快照「改建模材料 9/23 状态」，时点 2026-09-23T21:00:00+08:00（键 `snap_rework_0923`）
  - 进展：按 9/23 会的结论改出方案第一版，精简版删去全部时间安排。
  - 产物：方案 v0.1 初稿（链接 https://feishu.example/docx/tkos-world-plan-v01）
  - 字段：subject_ref = `@activity_rework`，as_of = `2026-09-23T21:00:00+08:00`
  - 说明：来源：9/23 晚方案成稿与精简（确）；时点 21:00 为推断（请你确认）。
38. 数据 Task 责任人 写状态快照「数据 Task 9/24 状态」，时点 2026-09-24T12:00:00+08:00（键 `snap_data_0924`）
  - 进展：契约、登记与迁移 0030 起在隔离库跑通，按票推进实现。
  - 字段：subject_ref = `@task_data`，as_of = `2026-09-24T12:00:00+08:00`
  - 说明：来源：仓库提交记录（确；时点为推断）。
39. Agents DRI 写状态快照「M1-B 经营协同 MVP 9/22 状态」，时点 2026-09-22T12:00:00+08:00（键 `snap_agents`）　**诱饵：无关 Mission 的状态**
  - 进展：M1B V1 版本上线进行中（计划 9/22 到 9/28）。
  - 问题：状态列多数未回填。
  - 字段：subject_ref = `@agents_mission`，as_of = `2026-09-22T12:00:00+08:00`
  - 说明：无关 Mission 的状态诱饵。来源：九月经营 Agent Pod 任务表（确；表的更新日期未知，时点为推断）。
40. 方案 Task 责任人 记外部事件（review），发生于 2026-09-22T17:37:00+08:00（键 `ev_0922`）
  - 主体：`@mission`, `@task_design`
  - 内容：CEO 首次看本体结构 v0.7：「跨层」不是业务责任范围，七列的来历没讲清，要求按消费与存储方式重新设计；决定拆回公司、责任域、Mission 三层，业务建模只关心表头，现有框架暂缓，先对齐业务建模的标准。本次没有指派。（链接 https://feishu.example/docx/tkos-ontology-v07-ceo-alignment）
  - 说明：来源：9/22 17:37 妙记 00:05–00:24 与 AI 总结（确）。
41. 方案 Task 责任人 记外部事件（meeting），发生于 2026-09-23T10:23:00+08:00（键 `ev_0923`）
  - 主体：`@mission`, `@task_design`, `@task_data`
  - 内容：业务对象架构与落地规划：一级对象 9 类；constraint 作为块、不继承；issue 放在状态块里；事件是唯一触发源；空块用标准句；目标改为长期目标与周期目标；Activity 成为对象。分工：Method DRI 定内容块标准并与 CEO 对；Method DRI 与 Agents DRI 做 Agent 分解测试；方案 Task 责任人 9/24 交计划方案；改材料由 AI 执行；数据库重构与 MCP 由 E&O 推进。会尾确认达成共识。（链接 https://feishu.example/docx/tkos-business-world-framework-v01）
  - 说明：来源：9/23 妙记各时间戳与 AI 总结（确）。真实的指派发生在这场会上。
42. 方案 Task 责任人 记外部事件（delivery），发生于 2026-09-24T10:00:00+08:00（键 `ev_plan_submitted`）
  - 主体：`@task_design`, `@activity_rework`
  - 内容：方案 v0.1 提交给 CEO 对齐。（链接 https://feishu.example/docx/tkos-world-plan-v01）
  - 说明：材料里没有提交的时间、方式与 CEO 回应，时刻是占位（请你确认）。
43. 数据 Task 责任人 写状态快照「数据 Task 9/24 晚状态」，时点 2026-09-24T17:40:00+08:00（键 `snap_data_0924b`）
  - 进展：tkos.world/0.1 冻结验收通过：12/12 组、248/248 项、4/4 个环境门槛。
  - 产物：world 0.1 验收报告（链接 https://repo.example/tkos-ontology-runtime/blob/d5b158e/docs/world-v01-acceptance-report.md）
  - 字段：subject_ref = `@task_data`，as_of = `2026-09-24T17:40:00+08:00`
  - 说明：来源：仓库提交 d5b158e（确）。
44. 数据 Task 责任人 记外部事件（delivery），发生于 2026-09-24T17:35:00+08:00（键 `ev_acceptance`）
  - 主体：`@task_data`, `@mission`
  - 内容：world 0.1 在隔离库冻结验收通过，world_api_accepted 为 true。（链接 https://repo.example/tkos-ontology-runtime/blob/d5b158e/docs/world-v01-acceptance-report.md）
  - 说明：来源：仓库提交 0a6736a、d5b158e（确）。
45. E&O DRI 记外部事件（other），发生于 2026-09-24T10:04:00+08:00（键 `ev_cleanup`）　**诱饵：无关事件**
  - 主体：`@unit_eo`
  - 内容：清理旧 Memory 核心的零引用模块（PR #16）。
  - 说明：无关事件诱饵：E&O 自己的事，但与本 Mission 无关。来源：仓库提交记录（确）。
46. Agents DRI 记外部事件（other），发生于 2026-09-23T18:00:00+08:00（键 `ev_agents_practice`）　**诱饵：无关事件**
  - 主体：`@agents_mission`
  - 内容：CEO 与 DRI 进入 M1-B 经营协同的真实协同实践。
  - 说明：无关事件诱饵。来源：Agents DRI 的 9 月 Mission 卡里程碑（推：是否按时发生未知）。
47. E&O Mission Owner 写状态快照「Mission 9/24 状态」，时点 2026-09-24T18:00:00+08:00（键 `snap_mission_0924`）
  - 进展：方案 v0.1 已成稿并提交对齐；契约定稿，world 0.1 实现与冻结验收完成。
  - 问题：方案还没有 CEO 反馈记录；内容块正式清单未到。
  - 产物：方案 v0.1；world 0.1 契约；验收报告（链接 https://feishu.example/docx/tkos-world-plan-v01, https://repo.example/tkos-ontology-runtime/blob/d5b158e/docs/contracts/tkos-world-0.1.md, https://repo.example/tkos-ontology-runtime/blob/d5b158e/docs/world-v01-acceptance-report.md）
  - 字段：subject_ref = `@mission`，as_of = `2026-09-24T18:00:00+08:00`
  - 说明：「方案对齐中」。来源：仓库提交记录、契约第 13 节（确）；「没有 CEO 反馈」依据文档无评论（推）。
48. 方案 Task 责任人 写状态快照「方案 Task 9/24 状态：方案对齐中」，时点 2026-09-24T18:00:00+08:00（键 `snap_design_0924`）
  - 进展：方案 v0.1 已成稿并提交，等 CEO 对齐。
  - 问题：方案还没有 CEO 反馈记录；内容块正式清单要等方法侧。
  - 产物：方案 v0.1（链接 https://feishu.example/docx/tkos-world-plan-v01）
  - 字段：subject_ref = `@task_design`，as_of = `2026-09-24T18:00:00+08:00`
  - 说明：来源同上。
49. 另一家公司 CEO 建 公司「另一家公司（合成）」（键 `outside_company`）
  - 身份：scope 外的合成公司，只作诱饵。
50. 另一家公司 CEO 建 战略「另一家公司的战略（合成）」（键 `outside_strategy`）
  - 责任结构：只有一个能力域 E&O。
  - 字段：parent_ref = `@outside_company`
51. 另一家公司 CEO 建 责任单元「E&O」（键 `outside_unit`）
  - 字段：unit_kind = `domain`，architecture_ref = `@outside_strategy#responsibility_structure`
52. 另一家公司 E&O DRI 建 长期目标「E&O 六个月目标（合成）」（键 `outside_goal`）
  - 字段：scope = `unit`，horizon = `六个月`，parent_ref = `@outside_unit`
53. 另一家公司 E&O DRI 建 周期目标「E&O 9 月（合成）」（键 `outside_period`）
  - 字段：period = `2026-09`，goal_ref = `@outside_goal`
54. 另一家公司 E&O DRI 建 Mission「9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用」（键 `outside_mission`）　**诱饵：scope 外的对象**
  - 验收标准：（另一家公司）CEO Agent 每天被使用。
  - 字段：goal_ref = `@outside_period`
  - 说明：scope 外的对象诱饵：与本 scope 的 Mission 同名，但属于另一家公司。

## 三、六问标准答案（从 `activity_rework` 出发）

### why：为什么要做「改建模材料」这条 Activity？

它服务于 Mission「9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用」：E&O 要为这些 Agent 与 M1、M1B 提供本体、上下文与数据读写的底座。这条 Mission 推进 E&O 的 9 月周期目标（核心本体逐步稳定、真实数据进入、Agent 共同消费 Context），后者分解自 E&O 六个月目标，再往上是公司「春节前核心业务第一次被真实证明」。

应引用：
- Mission「9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用」（最新版）的「定义」块（`@mission#definition`）
- 周期目标「E&O 9 月」（最新版）的「结果」块（`@eo_period#outcome`）
- 长期目标「E&O 六个月目标」（最新版）的「结果」块（`@eo_goal#outcome`）
- 长期目标「春节前核心业务第一次被真实证明」（最新版）的「结果」块（`@company_goal#outcome`）

### what：这条 Activity 要做成什么，怎样算做完？

按 9/23 会的结论改建模材料：给背景、目的与质量标准，先改一版，读后再调第二版。它所属的 Task「TKOS 业务建模方案设计」现行定义是节前完成业务建模与数据架构重构的整体方案并对齐（9 类一级对象、内容块、状态快照与事件；四块：语义模型、数据承载、Context Runtime、质量验证），验收是 9/24 给 CEO 对一版计划方案、节前再对齐。

应引用：
- Activity「改建模材料」（最新版）的「执行指令」块（`@activity_rework#instruction`）
- Task「TKOS 业务建模方案设计」（最新版）的「定义」块（`@task_design#definition`）
- Task「TKOS 业务建模方案设计」（最新版）的「验收标准」块（`@task_design#acceptance`）

### who：谁负责这条 Activity？什么时候、由谁指派的？

Activity 的责任人是 E&O Agent，由 E&O Mission Owner 指派（指派事件记在播种时刻）；真实的分工在 9/23 10:23 的会上定：改材料由 AI 执行。它所属 Task 的责任人是方案 Task 责任人。

应引用：
- Activity「改建模材料」（最新版）（`@activity_rework`）
- E&O Mission Owner 把 `activity_rework` 指派给 E&O Agent（`event:assign_rework`）
- Task「TKOS 业务建模方案设计」（最新版）（`@task_design`）
- 事件 2026-09-23T10:23:00+08:00「业务对象架构与落地规划：一级对象 9 类；constraint 作为块、不继承；」（`event:ev_0923`）

### now：现在进展到哪了？

Activity 本身最近一条状态是 9/23 21:00：按会上结论改出方案第一版；此后方案 v0.1 已提交（Activity 已交付）。截至 9/24 18:00，所属 Task 处于方案对齐中，等 CEO 反馈，内容块正式清单还要等方法侧；Mission 这一层，契约定稿，world 0.1 的实现与冻结验收已完成。

应引用：
- 状态快照「改建模材料 9/23 状态」（最新版）（`@snap_rework_0923`）
- 状态快照「方案 Task 9/24 状态：方案对齐中」（最新版）（`@snap_design_0924`）
- 状态快照「Mission 9/24 状态」（最新版）（`@snap_mission_0924`）

### happened：最近发生了什么？

9/22 17:37 CEO 首次看 v0.7，要求按消费与存储方式重新设计；9/23 10:23 的会定下 9 类一级对象、内容块与状态事件框架并完成分工；9/24 方案 v0.1 提交对齐；9/24 17:35 world 0.1 冻结验收通过。

应引用：
- 事件 2026-09-22T17:37:00+08:00「CEO 首次看本体结构 v0.7：「跨层」不是业务责任范围，七列的来历没讲清，要」（`event:ev_0922`）
- 事件 2026-09-23T10:23:00+08:00「业务对象架构与落地规划：一级对象 9 类；constraint 作为块、不继承；」（`event:ev_0923`）
- 事件 2026-09-24T10:00:00+08:00「方案 v0.1 提交给 CEO 对齐。」（`event:ev_plan_submitted`）
- 事件 2026-09-24T17:35:00+08:00「world 0.1 在隔离库冻结验收通过，world_api_accepted 」（`event:ev_acceptance`）

### basis：凭什么这样做？依据的材料在哪？

依据是 9/23 会上的 CEO 建模框架稿 v0.1 与会议结论，以及已提交的方案 v0.1。

应引用：
- 事件 2026-09-23T10:23:00+08:00「业务对象架构与落地规划：一级对象 9 类；constraint 作为块、不继承；」（`event:ev_0923`）
- 状态快照「方案 Task 9/24 状态：方案对齐中」（最新版）的「产物」块（`@snap_design_0924#artifacts`）
- 事件 2026-09-24T10:00:00+08:00「方案 v0.1 提交给 CEO 对齐。」（`event:ev_plan_submitted`）

## 四、八类反例（出现即扣分）

### 旧版本对象

把 Task 第 1 版的定义（4 个责任范围 × 7 类经营语义、约 20 个一级对象）当作现行定义。

诱饵：
- Task「TKOS 业务建模方案设计」第 1 版的「定义」块（`@task_design@1#definition`）
- Task「TKOS 业务建模方案设计」第 2 版的「定义」块（`@task_design@2#definition`）

### 别的责任单元的约束

把 Agents 单元 Mission 的约束（允许人工兜底、不要求完整自动化）当作本 Mission 或 E&O 的约束。

诱饵：
- Mission「M1-B 经营协同 MVP 与真实实践」（最新版）的「约束」块（`@agents_mission#constraint`）

### 无关 Mission 的状态

把 Agents 单元「M1-B 经营协同 MVP」的状态（V1 上线进行中）当作本 Mission 的现状。

诱饵：
- 状态快照「M1-B 经营协同 MVP 9/22 状态」（最新版）（`@snap_agents`）

### 已处置的 issue

把 9/22 状态里「团队与 CEO 对业务建模没有共识」当作现在仍未解决的问题（9/23 会上已达成共识）。

诱饵：
- 状态快照「方案 Task 9/22 状态」（最新版）的「问题」块（`@snap_design_0922#issue`）

### 无关事件

把与本 Mission 无关的事件（旧 Memory 核心清理、Agents 的真实协同实践）当作这条链上发生的事。

诱饵：
- 事件 2026-09-24T10:04:00+08:00「清理旧 Memory 核心的零引用模块（PR #16）。」（`event:ev_cleanup`）
- 事件 2026-09-23T18:00:00+08:00「CEO 与 DRI 进入 M1-B 经营协同的真实协同实践。」（`event:ev_agents_practice`）

### 空块被当作有内容

把空块当作有内容：Mission 与 9 月周期目标都没有验收标准，Activity 没有约束，回答不能编出来。

诱饵：
- Mission「9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用」（最新版）的「验收标准」块（`@mission#acceptance`）
- 周期目标「E&O 9 月」（最新版）的「验收标准」块（`@eo_period#acceptance`）
- Activity「改建模材料」（最新版）的「约束」块（`@activity_rework#constraint`）

### scope 外的对象

引用另一家公司（另一个 scope）里同名的 Mission。按 id 读的取法读不到它，出现即说明取法越过了 scope。

诱饵：
- Mission「9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用」（最新版）（`@outside_mission`）

### 状态里的过期 artifact

把早先状态里的 CEO 对齐稿 v0.7、v0.8 草稿当作现行材料（已被方案 v0.1 取代）。

诱饵：
- 状态快照「方案 Task 9/22 状态」（最新版）的「产物」块（`@snap_design_0922#artifacts`）
- 状态快照「方案 Task 9/23 状态」（最新版）的「产物」块（`@snap_design_0923#artifacts`）

## 五、请你确认

1. E&O DRI 与 E&O Mission Owner 各是谁（可以是同一人），在哪次会上指派的；材料里没有任何指派记录。
2. Mission 标题按规格原文写「CU Agent」；9/23 会原话是「CU agent 和 DR agent」，材料显示 CU 很可能是 CEO 的误转，要不要改。以及这条 Mission 算 E&O 自己的，还是经 contributes_to 支撑灯塔项目 01-2、01-3。
3. Mission 与 9 月周期目标的验收标准、两条长期目标的衡量：材料里没有，现在留空（也当空块诱饵）。要不要补？
4. 长期目标、周期目标与 Mission 立项的承诺与确认：材料里没有记录，我按战场图已发布视为已确认。
5. 方案 Task 的责任人：9/23 会上 CEO 举例是两个人，契约一个 Task 只有一名责任人；数据 Task 的责任人旧分工与 9/24 实际执行者不同。
6. 方案 Task 名称含「业务建模」，方案本身写「业务建模不在本稿」：名称要不要改。
7. 「写内容块标准」「Agent 分解测试」由 Method DRI、Agents DRI 负责，挂在 E&O 的 Task 下，需要在 E&O 域给他们 IC 角色；或者改用依赖或外部事件表达。两条的实际进展材料里没有。
8. 「方案提交」的时间、方式与 CEO 回应材料里没有，现在占位为 9/24 10:00。
9. 「改建模材料」改的是哪份材料、责任人是 Agent 还是人；9/23 21:00 那条状态的时点是推断。
10. 文档链接都用占位域名：飞书文档为 feishu.example，仓库文件为 repo.example（不带个人账号）。真实链接不入库，要不要换。

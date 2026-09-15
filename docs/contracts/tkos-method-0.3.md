# tkos.method/0.3 — Anchor 与 Agent 立项契约

冻结日期：2026-09-15。0.1、0.2 的解释、绑定和历史回执保持不变。仅新隔离 scope 显式启用；跨版本采用不在本轮。

## 正式效力与权限

StrategicArchitecture 整体独立版本化，Battlefield/Capability 是具有稳定 ID 的定义项。定义项含名称、定义、战略依据、边界、接口和承接 Domain。稳定 ID 不得改变 battlefield/capability 类型。Strategy map 是配对 Architecture 的简化投影；其单元 ID 与类型必须一致。Architecture 记录确切 Strategy 引用；Strategy 的版本化治理状态记录确切 Architecture 引用，避免循环内容哈希。旧版本通过事件历史恢复。

Strategy 初始和更新沿用 Issue→研究→纪要→Agreement→调整决定→CEO Agent 提案→Co-agent 审查→CEO 确认。公司级提案同时携带 Architecture 定义，Runtime 在同一事务内分配配对正式版本。独立 Architecture 变更由当前有权 DRI/Agent/CEO 提案、CEO 确认，引用未改变的确切 Strategy。LTCO/PCO/Mission 引用确切 Architecture；Mission 另含一个 Primary Scope ID。归属修改通过已有修订及正式确认路径产生新版本，历史目标不改写。

OperatingState 对 Method Mission、LTCO、PCO 整体及 Outcome 保存 subject_ref、as_of、summary、rag、baseline_refs、evidence_refs、data_gaps、generation_version。同一对象/Outcome/as_of 只有一个 State 身份。新推荐需 previous_state_ref 和完整 CAS。推荐与 canonical 分离；确认前仍可读取上一正式版本。本人确认可附理由修正 summary+rag，保留原推荐和原始证据。无证据只能 Unknown 并列明缺口。Mission 由精确内容中的 Owner 确认；LTCO 由其 CEO Owner；PCO Outcome 由其 DRI；PCO 整体由同域当前唯一 CEO。均重新检查当前任职，不赋予 IC 或周报编写者隐式权限。PeriodReview 必须引用目标的 canonical State，仍是不需审批的分析材料。

OperatingProblem 保存 canonical State 来源、核心问题、管理矛盾、重要性、层级、责任任职和证据。同一主体及规范化核心问题持续复用身份；语义相同的判断由 Agent 作出，Runtime 不做文本相似度合并。责任人可修订状态来源及分流层级；普通关闭由当前绑定责任人提交理由和必要依据，区分 resolved 与 no_further_action。

复盘发现、经营问题及真实原始来源可进入同一 PotentialIssue 候选池。CEO Agent 以个人绑定和同域当前 CEO 身份关系核验其权限，使用本人 M1A MethodRun、授权 Context 正式立项或关联已有同域未完成议题，不需要 CEO 批准立项。立项 Agent 与研究指派 CEO 分别保存。CEO Agent 可创建本人 M1A intake run 并绑定有权读取的来源；CEO 保留暂停、恢复与后续研究指派权。

OperatingProblem 仅 strategic 层可以移交 M1A。议题与来源关联保存成功后，同一事务将原问题标记 transferred、tracking=false，保留历史和目标议题引用；该终态表示移交，不表示解决。后续只在议题跟踪。不新增 Strategic Signal 对象。已有 Signal 保留来源含义。

## 事务、读取与边界

所有动作沿用 prepare/commit、精确引用、CAS、幂等键和原信封重放。版本、权限或证据失效拒绝提交；写入、状态、审计和回执原子提交。查询使用本人当前授权和 no-store，授权投影不替代提交校验。Context 引用同一协议版本，研究 Context 需要正在运行的 MethodRun，恢复时重检身份与权限。无数据明确返回缺失，不生成进度或评级。

工作面新增 architectures、operating-states、operating-problems 集合；对象接口提供内容版本、正式版本和治理状态。精确动作参数与读取格式由本契约绑定的 OpenAPI/注册表导出。场景确认不升级为正式决定。没有执行授权、交付验收、Outcome 达成副作用；Play、Human+AI Plan、WorkPackage 和跨版本接续另行交付。

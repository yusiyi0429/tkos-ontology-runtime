# tkos.method/0.4 — 正式业务规则增量契约（M1A / Architecture / M1B / State）

状态：**契约文字已定稿并经用户逐条确认；Runtime 启用由独立的 0.4 实施增量交付**。0.4 只有在协议注册表显式登记、且支持状态为本进程编译支持后才可调用；未启用前任何 0.4 请求返回 `PROTOCOL_NOT_SUPPORTED`，不产生业务成功。启用进度以 `protocol.SUPPORTED_PROTOCOL_CONTRACTS` 与各 scope 的注册表行为为准，不以文档或 profile 文件存在推断可用。0.1／0.2／0.3 的解释、绑定、历史回执与回归保持不变；不自动迁移旧对象，不做跨版本放宽。

来源与校准：`docs/reviews/2026-09-17-business-data/`（工作版本 r2）与用户 2026-09-17 逐条确认。工作文档快照是方法工作的当前理解，不是公司层面统一批准；本契约只把用户明确确认的规则写成 Runtime 契约。

## 1. 版本、启用与隔离

- 每个隔离 scope 显式选择业务规则版本。0.4 使用独立的新 scope 启用；旧 scope 继续按原绑定解释，不新增跨版本依赖、不就地重解释历史。
- 版本由服务器对象绑定/创建政策决定；请求声明只说明客户端理解的格式，不授予任何权限。
- `tkos.workspace/0.2` 与 `tkos.method/0.4` 同批启用但彼此独立：0.4 的正式动作不依赖 workspace 场景，workspace/0.2 的独立来源场景不产生正式业务效力。
- 新版本新增的正式规则必须通过 Runtime 治理工作台可办理（preview / submit / receipt / retry），不能只提供脚本。浏览器不承担 Agent 凭据，也不提供 Agent 身份选择器。
- 未实现的动作、对象与读取在注册表和目录中明确标注为未启用，不以空对象、空成功或占位动作冒充。

## 2. 正式效力总则

1. **效力按对象与动作的规则确定**：需要有权人类确认的动作，确认完成前只是候选，正式内容不得提前产生；被授权主体在其授权与本人运行范围内依规则直接产生的正式对象（例如 CEO Agent 直接创建 Strategic Issue）保持其规则效力，不为这类动作虚构额外人类 Gate；PeriodReview、评论、场景记录等分析／记录类产出按其自身规则记录，不因此获得正式决定效力。
2. **精确版本**：确认绑定确切对象、确切内容版本与确切依据。对象、依据、参与人集合或证据在确认前发生变化，待确认内容与已收集的确认全部失效，必须按新版本重新确认。
3. **当前任职**：所有确认在提交时重新检查确认主体与全部必要签署人的当前任职与授权；撤权、任职变化或身份不匹配拒绝提交。
4. **不可变历史**：历史正式记录保持不可变。新版本不自动改写旧正式内容，旧对象继续保留其历史依据。
5. **原子性**：业务内容、治理状态、审计与回执在同一事务内提交；失败不留下业务成功。
6. **无证据即 Unknown**：缺少证据不得给出已知评级；必须明确 Unknown 并列明缺口。不自动对下层 RAG 求平均，不生成公司／Battlefield／Domain 聚合 State 对象。
7. **无执行副作用**：0.4 的正式动作不产生执行授权、交付验收或 Outcome 达成；Play、Human+AI Plan、WorkPackage 与跨版本接续另行交付。

## 3. M1A：Strategic Issue → Agreement → 正式更新

### 3.1 直接立项

- CEO Agent 可以使用自己的 MethodRun 与本人授权 Context **直接创建、重新界定或关联** Strategic Issue，不设 PotentialIssue 前置门槛。旧链的 PotentialIssue 保留其历史语义，不强行迁移。
- CEO 指定参与人与研究责任。研究与补证按需发生：不强制 Memo／计划／报告／双纪要链，不以空材料补齐旧门槛；不保留独立正式 Judgment／Decision／StrategicMission／Close 平行链。
- 初始议题在以下条件下可以**明确缺少现有 Strategy**：该 scope 尚无任何正式 Strategy／Architecture 对。存在正式对时，议题必须引用当前依据。

### 3.2 Re-framing 与历史

- 重新界定产生新议题版本或新一轮次，保留历史 Agreement、历史议题版本与当时的依据引用；不在旧版本上就地改写结论。

### 3.3 Agreement

- Agreement 是必要当前人类参与人对**同一确切版本**的共同结论。CEO 提名必要的当前人类参与人，**必须包括 CEO 本人**；Agent 只负责起草，不代替任何人确认。
- **全体确认**：每个被提名且当前任职有效的参与人都必须确认同一精确版本后，Agreement 才正式成立。缺少任一人确认即不成立。
- 参与人名单、Agreement 正文或所附证据发生变化：所有待确认失效，必须按新版本重新收集。
- 正式化提交时重新检查全部必要签署人的当前任职；任一人任职失效则拒绝正式化。
- 未形成变化时，Agreement 只记录共识，不触发正式更新。
- 已有历史正式记录不变。

### 3.4 正式更新

- CEO Agent 依据 Agreement 提出**确切变更内容**（策略字段与／或 Architecture 定义与／或必要责任信息）；Co-agent 复核；CEO 最终确认，Runtime 在同一事务内原子生成正式版本。
- 不设独立的“是否应当调整”审批动作；变化与否由 Agreement 内容承载。
- **仅 Architecture 变更**同样需要 Agreement，但不得强制研究／会议链。
- 提案锁定确切 Agreement 引用与确切当前依据；提交时若 Agreement、依据或对象版本已变化，拒绝并重新准备。

## 4. Architecture：Battlefield + Domain

- Architecture 整体独立版本化。定义项为 **Battlefield（价值创造场域）** 与 **Domain（长期能力建设责任）**，各自具有稳定 ID。稳定 ID 不得改变定义项类型。
- **Required Capability 是 Strategy 的分析语义与字段**，分配给 Domain 的主要责任，不是独立对象、不是 Domain 的机械对应物。
- Business Scope 与既有授权域／当前任职的映射必须显式记录；映射只用于解释责任，**不隐式授予任何权限**。
- 旧 `battlefield/capability` 定义项与其 `domain_id` 继续按 0.3 解释；新增 0.4 结构不改写旧引用。
- **同契约历史**：0.4 对象可保留自身同契约的历史修订作为历史依据（例如上一 0.4 版本）。
- **不做跨契约接续**：0.3 对象不得作为 0.4 动作的依赖或依据，也不得把旧定义项改标签冒充 0.4 结构；旧 0.3 对象继续按 0.3 解释，显式新 scope 启用 0.4。

## 5. M1B：Scope 目标、承诺与整组激活

### 5.1 目标层级

- **LTCO**：Scope 级长期结果，含 scope、结果、标准、边界、horizon、why 与依据。其状态责任人为当前 CEO。
- **PCO**：周期结果，含 scope、period、**确切父级 LTCO**、当前现实、结果、标准与预期 LT 推进。
- **Mission**：独立可承担的必要结果单元，含**唯一 Owner**、**唯一主 Scope**、确切父级 PCO、why、标准、证据与时间。
- 每个 LTCO／PCO 结果具有独立身份与版本，并归属**唯一主业务 Scope**；公司视图是组合投影，不是又一个正式结果。
- **不新增 PDO 实体**；不自动把旧 `outcome_id` 记录转换为新结果身份。旧结构按原绑定解释。

### 5.2 候选、承诺与激活

- 一个评审窗口冻结完整公司周期的 PCO＋Mission 成员集合与版本。
- Co-agent 对冻结成员及其**每一份有效意见**给出取舍，恰好覆盖全部冻结成员与全部意见一次；不遗漏、不重复。未来版本号由 Runtime 分配，模型不自行编号。
- **具名 Scope DRI 与 Mission Owner 在确切候选集合中明确提交本人责任承诺**。评论不是承诺；不需要所有参与人都评论。评论与承诺分别记录。
- 候选集合、参与人或相关依据变化：已收集承诺失效，须重新确认。
- **CEO 最后整组激活**：Runtime 使用集合级 CAS，将整个 PCO＋Mission 集合一次性原子激活。任一项不满足则全部不生效，不允许半套生效，不产生执行或验收副作用。
- 存在**关键未决依赖或分歧**时阻止激活；非阻塞说明随整组确认一并记录。

## 6. State 与 PeriodReview

- State 主体责任人：LTCO 为当前 CEO；PCO 为对应 Scope DRI；Mission 为其唯一 Owner。确认时重新检查当前任职，包括 Owner 与 DRI 不同人、任职被撤销等情况。
- State 保留确切 subject、as_of、summary、canonical 评级、baseline_refs、evidence_refs、data_gaps 与 generation_version。
- 推荐与 canonical 分离：Agent 可给出推荐；只有有权责任人确认后成为正式经营状态。本人确认时可附理由修正 summary 与评级，保留原推荐与原始证据。
- 无证据只能 Unknown 并列明缺口。
- 新增对象（如 0.4 的 Scope 结果与 Mission 新结构）必须补齐基准与有权人，不得自动平均下层 RAG。
- 不新增公司／Battlefield／Domain 聚合 State 对象。
- PeriodReview 仍是**不需要审批**的分析材料，必须引用目标的 canonical State；模板示例不创建 Gate。

## 7. Operating Problem

- 战略移交沿用既有规则：先把议题与来源关联保存成功，同一事务将原问题标记 `transferred`、`tracking=false`；任一步失败则原问题保持原跟踪状态。移交不等于解决。
- 不新增 Strategic Signal 对象；已有 Signal 保留来源含义。
- 一般生命周期描述不推翻上述移交终态。

## 8. 读取、Context 与恢复

- 读取使用本人当前授权与 no-store；授权投影不替代提交校验。
- Context 引用同一协议版本；恢复历史快照时重新检查当前身份与权限。
- 未实现对象在目录中保留概念条目并明确标注未启用，不显示为零条业务数据。
- 工作面新增 0.4 对象集合与动作列表；精确动作参数与读取格式由本契约绑定的 OpenAPI／注册表导出。

## 9. 未交付边界（本契约不承诺）

- Play、Human + AI Plan、WorkPackage、M2/M3 完整执行链。
- PDO／Domain Outcome 最终层级对账与旧 outcome 数据迁移。
- 公司／Battlefield／Domain 聚合 State 与自动 RAG 汇总。
- 跨版本接续、旧对象自动升级。
- Clark 与真实模型编排；伙伴负责连接器、身份同步与真实送达。
- `tkos.workspace/0.2` 独立来源场景的正式业务效力（见 workspace 契约）。

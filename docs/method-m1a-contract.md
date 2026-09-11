# M1A API 运行契约：tkos.method/0.1

本契约实现已核对的 M1A L4 revision 21（运行图 136 节点）。来源：[M1A L4](https://tokenking.feishu.cn/docx/Df6Od9uRgoTk8txv9BGcWKFtnjf)。联合方法对象采用 M1B L5 revision 837 对 Strategy.map 的定义，因此运行图中战略地图的内容保存在 Strategy 的同一个不可变版本中。

本文件说明运行机制。受控 Agent 产物只能验证身份、状态、版本和来源机制；真实研究质量及经营效果须由真实案例检验。

## 1. 对象与权威边界

| 对象 | 内容和版本 | 谁产生效力 |
| --- | --- | --- |
| Signal | 多来源信号及精确 source_refs | 来源记录，不是战略决定 |
| PotentialIssue | 信号集合、议题标题和问题摘要，可迭代 | CEO 确认后另建 StrategicIssue |
| StrategicIssue | 已确认议题及来源 PotentialIssue 精确版本 | CEO；研究阶段另存可变 state |
| ResearchMemo | 问题、边界、预期输出、来源 | CEO Agent 起草，双方 Agent 分别澄清核对 |
| ResearchPlan | Memo、人工工作、Agent 工作、研究方法及期限 | 被指定的研究 DRI 发布 |
| ResearchReport | 计划、发现、结论、局限、证据 | 本议题 DRI 的个人 Agent 或研究 DRI 编写；DRI 提交；CEO Agent 预审 |
| MeetingRound | 会议轮次、目标、报告及原始材料 | 被指定的研究 DRI 记录 |
| MeetingMinutes | CEO / DRI 两份纪要及差异核对后的第三份纪要 | 双 Agent 分别发布；DRI 确认最终纪要 |
| StrategicAgreement | 确认纪要、战略共识及会议目标是否达成 | CEO 独立确认 |
| StrategyUpdateProposal | Agreement、调整理由、精确目标版本及待更新内容 | CEO Agent 起草；Co-agent 审查；CEO 独立确认 |
| Strategy | 战略判断与 map.units 一起版本化、生效 | CEO 确认更新事务 |
| StrategicJudgment | 域内判断，引用战略精确版本与单元 ID | CEO 确认域内更新；不写 M2 经营承诺 |

所有引用使用 `object_id + revision_id + payload_hash`。对象正文的不可变版本与对象治理版本分别存储。澄清、预审、纪要确认、Agreement 确认、影响审查、战略更新确认写入不可变协作记录，推进 StrategicIssue 的治理版本，**不会仅因评论产生正文新版本**。

每个 StrategicIssue 显式绑定 CEO、研究 DRI、CEO Agent、DRI Agent 和 Co-agent。三位 Agent 使用不同主体；个人 Agent 还必须有控制面登记的本人绑定。公司议题允许指定某一业务域的 DRI；由责任绑定开放该议题及必要材料，不为此授予公司级 DRI 或其他域底层资料权限。

域内 StrategicJudgment 按其服务端写入的 Agreement、更新方案双来源精确版本识别来源议题。来源议题的当前参与者可读取对应判断版本，以便提出后续版本；此权限不递归开放战略、原始材料或全域对象。未参与来源议题的新负责人仍需显式域授权。

## 2. 动作、身份和阶段

全部动作经 `POST /v1/actions/prepare` 与 `POST /v1/actions`。`params` 由同一严格模型校验，拒绝未知字段。表中动作均带 `m1a_` 前缀；“双方 Agent”指两个独立身份各自执行一次。

| 动作 | 当前身份 | 前置阶段 → 结果 |
| --- | --- | --- |
| record_signal | CEO / DOMAIN_DRI / 有效方法 Agent | 新建来源记录 |
| open_potential_issue | 同上 | 新建 potential |
| revise_potential_issue | 原创建人或 CEO | potential → 正文新版本 |
| confirm_strategic_issue | CEO 本人 | potential → promoted，另建 issue_confirmed |
| assign_research | 本议题 CEO | issue_confirmed → research_assigned |
| publish_memo | 本议题 CEO Agent | research_assigned / memo_clarifying / memo_ready / report_returned / meeting_ready → memo_clarifying |
| record_clarification | 研究 DRI 或其个人 Agent | memo_clarifying；记录意见并清除先前核对结果 |
| check_memo | 双方 Agent 各自发布 | 同版双方均 clear → memo_ready；否则保留待澄清 |
| direct_clarification | 本议题 CEO 或研究 DRI 本人 | 已有未解决的 Agent 核对 → 记录直接澄清，继续核对 |
| publish_research_plan | 研究 DRI 本人 | memo_ready → plan_published |
| publish_report | 研究 DRI 或其个人 Agent | plan_published / report_drafting / report_returned / meeting_ready → report_drafting |
| submit_report | 研究 DRI 本人 | report_drafting → report_submitted |
| precheck_report | 本议题 CEO Agent | report_submitted → meeting_ready 或 report_returned |
| open_meeting | 研究 DRI 本人 | meeting_ready → meeting_open，新建会议轮次 |
| publish_minutes | 所属 CEO / DRI Agent | meeting_open / minutes_drafting → minutes_drafting |
| reconcile_minutes | 本议题 CEO Agent | 两份当前纪要 → minutes_reconciled |
| confirm_minutes | 研究 DRI 本人 | minutes_reconciled → minutes_confirmed |
| confirm_agreement | 本议题 CEO 本人 | minutes_confirmed → agreement_confirmed；目标未达成则回到 meeting_ready |
| decide_update | 本议题 CEO 本人 | agreement_confirmed → update_requested 或 completed/no_change |
| propose_update | 本议题 CEO Agent | update_requested / update_returned / update_proposed / update_reviewed → update_proposed |
| review_update | 本议题 Co-agent | update_proposed → update_reviewed 或 update_returned |
| confirm_update | 本议题 CEO 本人 | update_reviewed → completed/updated |

新建 Signal、PotentialIssue 的动作不带 target；修改和确认 PotentialIssue 以该对象为 target。后续所有步骤以 **StrategicIssue 为 target**，具体报告、Memo、会议、纪要、Agreement 和方案在 params 中携带精确版本。调用方每一步重新读取治理版本并 prepare；API 不能用报告正文版本代替议题的 expected_version。

## 3. 循环与失效语义

1. Memo 改版清空先前双方核对；新的澄清记录也使已有核对结果不能直接放行。直接澄清不能代替双方 Agent 对明确 Memo 的分别核对。
2. 预审退回或通过后、尚未开始会议时，允许修改报告，也允许重新研究 Memo、重新发布计划后再次提交。报告改版清除之前的预审放行；旧版本预审不能给新报告开会。
3. 两位 Agent 只能写自己的纪要。两份正文不同，必须保存逐项差异及处理结果；空差异列表不能掩盖正文差异。
4. 每次会议先生成新的轮次，再由两位 Agent 分别提交两份独立纪要。会议目标未达成，CEO 的 Agreement 仍保留，但只能继续讨论；不能跳过下一次纪要确认进入更新。
5. 纪要确认、Agreement 确认、是否调整的判断、正式更新确认彼此分离。`needs_update=false` 保存原因并完成本轮；不把所有关联议题设为关闭，也不产生 Mission 或执行授权。
6. 更新方案引用当前且生效的精确目标。Co-agent 审查的影响层级必须与方案内容一致；正式确认再次核对目标版本，期间另一议题改变目标时拒绝旧方案。CEO Agent 可以重新提出适配当前目标的方案，但须重新经过 Co-agent 审查。
7. 公司 Strategy 与 map 同一事务生效。域内更新只产生 StrategicJudgment。一个方案同时调整公司与域内判断，且域内判断引用本次更新的战略基线时，域内正文改为引用同一事务生成的 Strategy 版本；方案仍保留确认时采用的原基线。
8. 既有 LTCO、PCO、Mission 不因战略更新改变正文、原有依据或效力。底座追加影响记录供显式复核；新的 M1B 周期采用当前有效 Strategy。

## 4. 接口结果与恢复

创建动作返回 `{object_id, revision_id, payload_hash, phase}`。确认战略议题返回新建 StrategicIssue 的上述字段及 `issue_ref`。后续动作的顶层 `object_id/revision_id` 指议题；`memo_ref`、`report_ref`、`agreement_ref` 等结果字段指派生产物。更新确认返回 `changed_refs[]`，同时保留确认 ReviewRecord。

调用方以动作回执、议题 phase、当前 artifact refs 和独立运行尝试记录判断恢复位置。重试使用原 idempotency key；版本冲突后重新读取并 prepare。任何步骤的事务失败均不得只保存正文而遗漏 state、来源、审计或回执。

本模块不编排后续动作，不发送外部消息，不调用模型供应商，不创建 M2 授权。Clark 接入方负责把当前用户或 Agent 的真实身份交给 API，并按返回状态推进流程。

Context Pack 的 `stage` 支持 `general / research / meeting / strategic_update / planning / review / confirmation / period_review / handoff`；`purpose` 的结构化选择模式支持 `general / analysis / review / decision / handoff`。其它 stage 或自由文本 purpose 保留原文，以 general 选择，并在 `selection_notes` 明确说明回退。`research` 纳入所请求时点的 Memo、计划及报告，`meeting` 纳入对应会议、双纪要和 Agreement；其它工作产物不会只因其存在就加入。正式来源的溯源仍逐项检查当前权限。核对 PCO/Mission 草稿须传 `include_drafts=true`；PeriodReview 和研究报告作为分析输入无需批准。

Context 的阶段状态取自不可变 state event，且匹配选定内容版本及请求时点。报告 v1 的上下文不会混入 v2 的预审记录；正式依据需要在 `valid_at` 与 `known_at` 之前已有生效事件，不能以草稿创建时间冒充生效时间。旧有效版本按历史生效证据标记，不能因当前指针改版就解释成未批准草稿。读取历史 snapshot 时会重新核对每一份实际采用材料的当前读权限和内容 hash。

## 5. 验证边界

`tests/test_method_m1a.py` 独立验证状态与身份约束，包括完整多轮流程、无更新路径、两位 Agent 绑定、旧报告预审失效、Agent 不能代替人的决定，以及正式更新前重新核对战略。该文件使用内存 helper double，**不证明 PostgreSQL、HTTP、证据字节读取或回滚**。这些由联合 Method 独立验收驱动器以隔离数据库及实际 HTTP 验证，结果在总验收报告中单独记录。

# M1B L5 revision 837：底座契约

本契约属于 `tkos.method/0.1`，与历史 Contract-A 0.1、A2 公司组合和 A3 执行交接分别解释。来源为 [M1B L5](https://tokenking.feishu.cn/wiki/LsO5wvXW5iM8wPkfImTcJCoEnOf) revision 837。下面描述实现规则；HTTP、并发、恢复和兼容是否验收通过，以独立验收报告为准。

## 对象和效力

| 类型 | 保存内容 | 效力规则 |
| --- | --- | --- |
| Strategy | M1A 的正式战略与 `map.units[]` | M1B 只读取精确的已生效版本 |
| BusinessFact | `fact_id/subject_ref/as_of/metric/value/unit/source_ref` | 独立原子观察；修正产生新的事实对象并引用原对象，原始记录保留 |
| PeriodReview | 周期、目标版本、事实版本、findings/learnings/implications、generation_version | Agent 分析。没有 approved/confirmed 状态或审批动作；重新生成保留原版本 |
| LTCOReviewAdvice | 精确复盘及战略依据、已有 LTCO（如有）、审视建议 | Agent 分析，不代替 CEO 的 LTCO 决定 |
| LTCO | 周期、Strategy 版本、CEO Owner、按 unit_id 组织的成果、可选 advice_ref | CEO 退回后由其绑定 Agent 修订；CEO 确认精确版本后生效 |
| PCO | 周期、LTCO/Strategy 版本、按 unit_id 组织的成果和成果 DRI | 仅随候选整组确认生效 |
| Mission | 单个 Owner、其他参与人、精确 PCO、supports、交付要求、边界和截止时间 | Owner 可以与成果 DRI 同人；一个 Mission 可支撑多个成果；整组确认不产生执行授权 |
| ReviewWindow | 固定 PCO＋Mission 版本集合、参与人具体任职、可选个人 Agent、来源依据 | 内容不可变；开关状态和有效意见集合单独保存 |
| CandidateSet | 封存窗口、取舍记录、完整 PCO＋Mission 候选版本集合 | CEO 一次确认整个集合，或说明原因开启新窗口 |

对象引用是 `{object_id, revision_id, payload_hash}`。Mission 每个 `supports[].outcome_ref` 另含 `outcome_id`，前三个字段必须与其 `pco_ref` 完全相同；成果 ID 必须存在于该精确 PCO 版本。正文没有 BusinessFact 的拷贝或 `fact_refs`。历史名称和负责人由被引用版本解析。

ReviewRecord 保存于不可变协作记录中。评论、撤回、替代、辅助分析、LTCO 反馈、收拢理由、CEO 决定分别标明性质。`window_id` 只在窗口动作中必填；LTCO 反馈和确认不需要伪造窗口。

## 公开动作

所有动作使用 `POST /v1/actions/prepare` 获取当前依赖版本，再通过 `POST /v1/actions` 提交相同参数和返回的 CAS 条件。服务端身份来自各自的 bearer credential，参数不接受“代某个人作出决定”的 actor 字段。

| 动作 | 目标 | 行为及执行身份 |
| --- | --- | --- |
| m1b_record_fact | 创建 | Co-agent，或具备当前域动作权限的 CEO／DOMAIN_DRI，记录事实并验证原始证据可读且可获取 |
| m1b_correct_fact | BusinessFact | 同上；新事实 ID、原始事实精确引用与修正原因；原观察主体和 metric 不变 |
| m1b_generate_review | 创建 | Co-agent 按精确目标及事实生成周期复盘 |
| m1b_regenerate_review | PeriodReview | Co-agent 保持 review_id 和周期，记录新 generation_version 及采用的事实版本 |
| m1b_advise_ltco | 创建 | Co-agent 发布 LTCO 审视建议 |
| m1b_propose_ltco | 创建 | 绑定 CEO 的 CEO Agent 起草 LTCO；允许首次建立没有上期复盘的初始 LTCO |
| m1b_return_ltco | LTCO | LTCO 的 CEO Owner 给出反馈；原有效版本仍保留效力 |
| m1b_revise_ltco | LTCO | CEO Agent 对草稿或退回版本修订并保存响应，不自行启用新版本 |
| m1b_confirm_ltco | LTCO | CEO Owner 复核当前战略、精确 LTCO 内容并启用 |
| m1b_draft_pco / m1b_revise_pco | 创建／PCO | Co-agent 形成或修订尚未进入窗口的草稿 |
| m1b_draft_mission / m1b_revise_mission | 创建／Mission | Co-agent 形成或修订尚未进入窗口的草稿；校验单 Owner 和全部 supports |
| m1b_open_window | 创建 | Co-agent 固定一个 PCO 和至少一个 Mission，绑定参与人当前任职 |
| m1b_comment | ReviewWindow | 参与人本人发布意见；可携带自己的有效意见 ID 进行替代 |
| m1b_withdraw_comment | ReviewWindow | 参与人本人撤回自己的有效意见，保留原记录和原因 |
| m1b_assist_review | ReviewWindow | 预先绑定的个人 Agent 提供分析；不能产生人的正式意见 |
| m1b_close_window | ReviewWindow | Co-agent 封存有效意见 ID 集合，关闭后禁止评论、撤回、替代 |
| m1b_resolve_window | ReviewWindow | Co-agent 对全部有效意见逐项说明取舍，一次生成完整候选集合 |
| m1b_confirm_candidates | CandidateSet | CEO 原子确认所有候选 PCO/Mission；无需旧 A2 全体 DRI 签认 |
| m1b_reopen_candidates | CandidateSet | CEO 说明原因，新窗口以候选精确版本为基线 |
| m1b_reopen_window | ReviewWindow | CEO 说明原因重新启动尚未完成收拢的窗口，用于依据变化、撤权等恢复 |

`m1b_resolve_window` 的 Mission 参数通过 `object_id` 指定冻结成员，`supports` 仅提交 `outcome_id/contribution`；服务端先创建 PCO 新版本，再在同一事务内为所有 Mission 生成确切的 `pco_ref` 和成果引用。客户端不能预猜未来 revision_id。所有冻结 Mission 必须出现一次，所有封存意见必须有且仅有一个 disposition；调用重试不会产生第二份正式收拢。

## 状态转换与循环

下列业务阶段读取自 `method_state.phase`。不要将其与对象头 `lifecycle_status` 混用；例如 PeriodReview 的头状态是 `recorded`，分析阶段是 `generated`。

- LTCO：`draft → returned → draft → confirmed`。确认后再次审视可以退回修订，旧 `effective_revision_id` 保留到新版本显式确认。
- PCO/Mission：`draft → under_review → candidate → confirmed`；CEO 重开时，候选成员进入新窗口的 `under_review`。窗口固定后通用草稿修订被拒绝，只能由正式收拢生成候选修订。
- ReviewWindow：`open → closed → resolved → confirmed`；CEO 重开会标记旧窗口 `reopened` 并创建新 `open` 窗口。
- CandidateSet：`pending → confirmed` 或 `pending → reopened`。旧候选被重开后不能继续确认。
- PeriodReview：`generated`。每次重新生成是新的不可变分析版本；不存在审批状态机。
- BusinessFact：`recorded`，原记录可带 `corrected_by_ref` 状态指针；修正链完整保留。

业务内容版本与对象 CAS 版本不同。每次评论、撤回、辅助分析或开关窗口都会推进窗口 CAS，业务正文 revision_id 保持不变。并发评论与关闭在同一治理栅栏下检查；客户端遇到冲突需重新读取和 prepare。

## 战略变化与显式重新核对

M1A 正式战略更新不会改写已确认 LTCO/PCO/Mission 的正文、引用或效力。它们保留既有版本并获得影响提示。对新 LTCO、初始 PCO、新窗口、收拢和候选确认则检查当前已生效战略。

窗口打开后不能悄悄替换来源依据。如果新战略使待确认集合过期：先按 CEO 审视／修订／确认得到新依据下的 LTCO，再由 CEO 调用 reopen 动作，显式同时提供 `rebase_strategy_ref` 和 `rebase_ltco_ref`。新窗口仍以旧候选成员版本为讨论基线，但明确记录本轮需要核对的新来源。正式收拢必须严格使用新窗口固定的来源版本；晚到的无声替换被拒绝。

旧候选的历史负责人已撤权时，不强迫其重新取得权限才能修复方案：重开保留历史内容，收拢时为新候选重新校验当前负责人及任职。既有意见保留发表时的来源与作者；撤权阻止其继续写入或重试冒充有效权限。

## 读取、Context 和下游边界

窗口参与资格提供该窗口、固定目标、该窗口收拢产生的确切候选集合与成员版本，以及相关协作记录的必要访问。参与其他业务域的核对不自动开放其 BusinessFact、EvidenceAsset 或其他底层资料；个人 Agent 仍以自己的身份检查材料权限。

Context Pack 区分正式依据、草稿、原始材料、人的决定、Agent 分析和周期复盘。使用无需审批的 PeriodReview 不代表批准其中建议；它不能自动修改目标或授权。Context 记录实际采用的版本和排除原因。

新版 Mission 的下游投影提供已确认版本、`definition` 内的精确 `pco_ref` 与 supports、Owner、交付要求，以及 `confirmation_record_id` 和需要进一步约定的执行责任。`confirmation_record_id` 是人的协作确认记录 ID，并非 action receipt ID；可通过该记录的 `action_id` 追溯正式回执。缺少 M2 的 DRI/IC/验收人及授权约定时，不调用旧 A3 自动创建 ExecutionCommitment 或 ExecutionAuthority。

## 校验分层

本模块的静态模型与内存状态机测试检查结构、权限意图和业务循环，不能替代真实 HTTP、数据库事务、证据存储、并发和重启验收。独立验收还必须证明：撤权后重试、关闭与评论竞争、并发 CEO 确认、候选首写失败回滚、进程重启后的回执读取，以及旧 A1–A3 和 legacy 契约保留原义。受控 Agent 输出仅用于治理和来源机制验收，研究质量与经营效果后续以真实案例评价。

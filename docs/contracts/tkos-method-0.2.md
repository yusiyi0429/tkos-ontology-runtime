# tkos.method/0.2 — 生命周期与页面阶段对照契约

状态：实现与独立验收中；不是已发布能力。来源为 2026-09-15 读取的 [四张生命周期图](https://tokenking.feishu.cn/docx/Jzb2drkZOopQk9x2vipcde9HnFc)及本轮用户确认。Clark 由伙伴维护，本仓只交付 Runtime、契约和验收材料。

## 冻结决策

- Signal 不进入交互对话，允许专用研究入口读取；通用 Context 不得通过递归或历史快照绕过。
- CEO 确认后正式立题；DRI/Agent 仅提交有真实来源的建议。CEO 直接创建须同时记录确认理由，不补造 Signal。
- ResearchBrief 是独立版本化分析材料。轻量路径由 CEO 确认材料充分；深入路径保留报告和预审。修改材料使旧确认失效。
- MeetingMinutes 仍由双 Agent 起草和收拢，DRI 确认最终纪要，CEO 确认 Agreement。页面范围止于 Agreement，不自动更新 Strategy。
- BusinessFact 与 PeriodReview 独立存储；PCO 页面只聚合，不将事实复制进目标正文。PeriodReview 没有审批状态。
- ReviewWindow 必须记录独立评论截止时间；服务端取得治理锁后在截止时刻起拒绝评论、替代和撤回。Co-agent 显式关窗，CEO 人工触发伙伴的模型收拢。重放已成功原请求返回原回执，不重新提交。
- 不原地延期。需要继续讨论由 CEO 显式重开新窗口，指定未来截止时间。

## 页面阶段—对象—动作—主体—效力

| 页面阶段 | 业务对象 | 合法动作 | 确认主体 | 正式效力 |
| --- | --- | --- | --- | --- |
| 采集 Signal | Signal / EvidenceAsset | m1a_record_signal | 现有来源贡献身份 | captured，仅记录来源 |
| 监测、归档、恢复 | Signal | m1a_activate_signal / m1a_archive_signal | 同域当前 CEO | 处置状态；不立题、不授执行权 |
| 升级建议、直接上报、复盘发现 | PotentialIssue | m1a_open_potential_issue / m1a_revise_potential_issue | 当前授权来源贡献者 | 建议，保存真实来源 |
| 正式立题 | StrategicIssue | m1a_confirm_strategic_issue / m1a_create_direct_issue | CEO 本人 | 确认议题；Signal converted 从真实确认派生 |
| 指定责任人 | StrategicIssue | m1a_assign_research | CEO 本人 | 固定 DRI 与 Agent；轻量路径也需要责任人 |
| 预研与探索 | ResearchBrief / MethodRun | m1a_publish_brief；研究 Context 读取 | 已指派 CEO Agent | 版本化分析，不形成决定 |
| 轻量材料充分 | ResearchBrief / StrategicIssue | m1a_confirm_brief | CEO 本人 | 指定材料可进入会议，修改后失效 |
| 深入研究 | Memo / Plan / Report | 既有澄清、计划、报告、退回和预审动作 | 沿用 DRI、CEO、Agent 职责 | 分析与质量检查 |
| 准备及多轮会议 | MeetingRound | m1a_open_meeting | 已指派 DRI 本人 | 锁定轻量已确认材料或深入研究已预审报告 |
| 已阅、开会、结束、发布单份会议稿 | tkos.workspace 场景 | read / meeting_start / meeting_finish / meeting_publish | 场景授权人员/负责人 | formal_effect=none |
| 双 Agent 纪要、差异核对 | MeetingMinutes | 既有 publish/reconcile/confirm_minutes | Agent 起草收拢；DRI 本人确认 | 精确最终纪要，不自动形成 Agreement |
| Agreement | StrategicAgreement | m1a_confirm_agreement | CEO 本人 | 正式共识；后续战略更新必须另行确认 |
| 事实记录与修正 | BusinessFact | m1b_record_fact / m1b_correct_fact | 现有授权身份 | 事实与修正关系，原始证据不改写 |
| 周期复盘 | PeriodReview | m1b_generate_review / m1b_regenerate_review | Co-agent | agent_analysis，不审批 |
| LTCO 审视 | LTCOReviewAdvice / LTCO | 既有建议、起草、修订、确认 | CEO Agent 起草；CEO 确认 | 精确目标版本 |
| 月度草稿与开窗 | PCO / Mission / ReviewWindow | 既有 draft / revise / open_window | Co-agent | 固定版本集合与参与人，候选未生效 |
| 评论、替代、撤回 | ReviewRecord | m1b_comment / m1b_withdraw_comment | 窗口成员本人 | 仅截止前且 open 可写；意见外置 |
| 关窗与收拢 | ReviewWindow / CandidateSet / Resolution | m1b_close_window / m1b_resolve_window | Co-agent | 冻结完整意见，生成完整候选和逐意见取舍 |
| 核对完成 | workspace diff_response | reviewed / commented | 本人 | 个人核对记录，不代替正式确认 |
| 确认或重开 | CandidateSet / PCO / Mission | m1b_confirm_candidates / reopen | CEO 本人 | 整组同时生效或新窗口；不启用半套 |
| 正式 Mission 与周进展 | Mission / handoff / weekly 场景 | 读取、补充、确认材料 | DRI 本人 | 待执行承接，不产生执行授权或验收 |

## 版本、权限与读取

0.1 的 schema、哈希、绑定和历史语义保持不变。0.2 按服务器对象绑定/创建政策选择解释器，请求版本声明不授予权限。不自动迁移旧对象，不默认接受跨版本依赖。首轮使用全新隔离 scope，通过合法动作创建证据与业务基线。

Issue 的 business_scope=strategic/battlefield、urgency=red/yellow/gray 与生命周期分开；红色需要理由。map 单元 unit_type=battlefield/capability。分类不扩大授权域，Scope 租户隔离与 business_scope 不是同一概念。

一个 Signal 可支持多个议题；已归档须重新激活后才能新增升级；旧升级引用保留。Signal 的 converted 状态必须来自 CEO 确认的真实议题，不接受单独标记转换的请求。

专用研究 Context 绑定 Agent 自身身份、运行中的 MethodRun 和授权根对象；用于研究的 Context 快照不能冒充对话 Context。Runtime 控制接口输出，伙伴控制模型调用路由与会话注入；不承诺阻止合法读取后任意转用。所有读取与错误响应 no-store，旧快照仍重新检查当前身份与规则。

页面展示 phase、精确引用、来源、可执行操作及拒绝原因；没有正式动作证据不补造阶段。object_version、revision_id、场景 version 分开；正式提交沿用 prepare/commit、CAS、幂等、当前任职及原子回执。超时不等于失败，查询回执或重放原信封；明确冲突后才重新读取并构造新请求。

## 验收门槛

接口验收、伙伴接线、Clark 浏览器闭环、真实模型收拢分别报告。必须覆盖双版本与撤销、直接立题、轻量材料变更、Signal Context 与快照、截止边界及锁竞争、同信封重放、角色撤销、跨域、Agent 代人确认、事务回滚及重启恢复。保留既有 Method 与 workspace 回归。SQL 只核查结果，不预置业务成功状态。

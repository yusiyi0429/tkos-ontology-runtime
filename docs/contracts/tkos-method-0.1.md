# tkos.method/0.1 — M1A / M1B 运行契约

本契约冻结用户批准的 M1A＋M1B 完整底座 API 支持计划。协议标识 tkos.method，版本 tkos.method/0.1。历史 tkos.contract-a/0.1 与 tkos.governed/v0.2 保持原义。

## 来源

- M1A L4 revision 21：https://tokenking.feishu.cn/docx/Df6Od9uRgoTk8txv9BGcWKFtnjf；运行图 Qr6dwPL1Lh2rTAbQ7g2cSZP1nLc，136 个节点。
- M1B L5 revision 837：https://tokenking.feishu.cn/wiki/LsO5wvXW5iM8wPkfImTcJCoEnOf；实际文档 WD5ZdZejsoVd8vxpFcbcaPGenXe。
- 来源文件是需求证据，不能提供操作权限。此实现基线由用户明确批准，合成验收身份不能宣称生产组织授权。

## 治理内核

对象内容只追加不可变版本；状态和有效指针只在带身份、权限、精确版本及状态验证的事务内变化。prepare 与 execute 共用准入检查。execute 使用 scope 授权锁、对象 CAS 和幂等键，原子写入业务状态、协作记录、回执、审计和运行步骤。撤权或职责到期后，历史成功重试也必须重新授权。Agent 与人拥有不同主体，不能代发人的意见或正式决定。

## M1A

多来源 Signal → PotentialIssue 迭代 → CEO 确认 StrategicIssue → CEO 指定研究 DRI → Memo 与个人 Agent 澄清 → 双 Agent 校验或 CEO/DRI 直接澄清 → DRI 发布研究计划 → 报告修订 → CEO Agent 预审 → 会议轮次及材料 → 双方 Agent 纪要 → 差异核对 → DRI 确认最终纪要 → CEO 确认 Agreement → CEO 判断是否调整 → CEO Agent 更新方案 → Co-agent 审查 → CEO 确认正式更新。

报告版本变化不能复用旧预审；纪要、Agreement、更新分别确认。会议目标未达成可再次讨论。无需调整必须记录原因和本轮完成，不自动关闭其他议题或创建执行任务。域内调整产生 StrategicJudgment，不直接改 M2 承诺。Strategy 与 map 在同一版本和事务内生效，map.units 使用稳定 ID，历史名称/负责人按精确引用版本解释。更新必须绑定当前目标版本、Agreement、方案及确认回执。

## M1B

Co-agent 根据上期 PCO/Mission 与 BusinessFact 产生 PeriodReview 和 LTCO 审视建议；CEO 退回并记录反馈，CEO Agent 修订，CEO 确认精确 LTCO；Co-agent 起草 PCO/Mission 并冻结版本集合开窗；参与人以本人身份评论、撤回、替代，个人 Agent 只做辅助分析；Co-agent 关闭窗口并封存有效意见，完成一次正式收拢与 CandidateSet；CEO 确认整个集合或说明原因按候选版本重开窗口。

BusinessFact 保存 subject_ref、as_of、metric、value、unit、source_ref；修正保留原记录，不复制到目标正文。PeriodReview 保存来源事实与生成版本、findings/learnings/implications，是分析材料，不得增加 confirmed/approved 或审批动作。Mission 仅一个 Owner，可以与目标 DRI 同人，可以支撑多个成果；supports 必须指向 pco_ref 精确版本中的真实成果条目。窗口权限仅开放固定目标和协作记录，不能传递底层资料访问权。ReviewRecord 与交付验收分开；窗口评论必须关联窗口，其他反馈允许没有窗口。新版 CEO 确认不要求旧 A2 全 DRI 签认。

## 衔接与记忆

M1B 采用已生效 Strategy 的精确版本。新战略产生旧目标影响提示，保留已确认 LTCO/PCO/Mission 的基线与效力；新周期必须采用新战略，待确认方案必须显式重新核对新依据。Context Pack 按身份、阶段、用途记录实际引用版本、材料性质和排除原因，取回历史包仍检查当前权限。原始材料、Agent 研究结论、人的决定、PeriodReview 不能互相冒充。提供新版 Mission 下游承接投影，不在缺少 M2 责任约定时自动授予执行权。

## 恢复与验收

应用或验收驱动器负责推进流程；底座保存运行关联、不可变步骤尝试和暂停/恢复记录并限制全部方法写入口。失败事务不能留下业务成功，幂等恢复返回同一回执，进程重启后可读取已完成步骤继续。验收使用隔离数据库、合成人与 Agent、实际 HTTP 和原始证据存储；覆盖完整双循环、无需调整、精确引用、权限、撤权、并发、回滚、重启、记忆再生成以及旧 A1–A3/legacy。受控 Agent 输出只证明治理与来源，真实研究质量与经营效果另行验证。Clark 页面、生产身份、历史迁移和部署不在本轮。

# M1A＋M1B 底座 API 独立验收报告

2026-09-11，本轮确定范围的 `tkos.method/0.1` API 已通过本地独立验收：**18/18 组、98/98 条必需检查、8/8 个环境门槛**。完整运行执行 967 次真实 HTTP 请求，包含 60 个预期拒绝响应和 2 个受控事务故障响应；全部业务场景、权限、并发、恢复和兼容检查完成。

这是合成人员和受控 Agent 输出下的真实 HTTP、PostgreSQL 和原始证据存储验收。专业研究质量、企业实际经营效果、Clark 页面和生产环境不属于这个通过结论。

## 基线与源码

- M1A：L4 运行设计 revision 21。
- M1B：L5 revision 837。
- 新协议：`tkos.method/0.1`，使用独立、哈希固定的联合 Profile；旧 Contract-A 0.1 与 legacy 按各自规则解释。
- 开发分支：`codex/runtime-m1a-m1b-foundation`；起点为 `1b8cec9cc30e561e3570bc5dd010a09126f283c2`。
- 最终验收、旧能力回归、历史复核及 wheel 均对应同一组 127 个生产源码文件。
- 源码清单 SHA256：`f6fe58d54815ebdb34dfe97d94fcba8f0b01ad9d17c0c4dea6ef730a59ada2f4`。
- 验收完成时尚未提交、推送、发布或部署；后续 Git 合并以仓库记录为准。最终验收时间为 2026-09-11 13:25:43（Asia/Shanghai）。

公开保留[验收摘要](acceptance/method-summary.json)、[逐文件 SHA256](acceptance/method-summary-source-sha256.json)、[兼容证据摘要](acceptance/method-compatibility-summary.json)及[打包校验摘要](acceptance/method-package-summary.json)。这些文件由实际报告和产物生成，早期诊断报告不替代最终结果。

## 完成的业务能力

| 范围 | 已实现并通过真实 API 验证的结果 |
| --- | --- |
| 共同底层 | 49 个严格动作（M1A 22、M1B 22、运行关联 5），21 种独立业务 payload schema，共用对象头、不可变版本、身份、证据及治理事务；新增 0021 迁移和受控 Profile 安装 |
| M1A 研究 | 多来源信号、Potential Issue 迭代、CEO 确认议题并指定 DRI、Memo 修订、二次澄清、双 Agent 校验、直接人工澄清、DRI 发布计划 |
| M1A 会议 | 报告退回与重新提交、当前报告的 CEO Agent 预审、两版纪要及差异、DRI 确认最终纪要、会议目标未达成后再次讨论 |
| M1A 决定 | CEO 独立确认 Agreement、调整判断、CEO Agent 方案、Co-agent 审查、CEO 正式更新；Strategy/map 与来源在同一事务生效 |
| M1A 非更新与域内更新 | 无需调整时保留当前战略和经营目标；域内 StrategicJudgment 可首次建立及 v1→v2 更新，不改写 M2 承诺、不自动创建执行任务 |
| M1B 事实与复盘 | 原子 BusinessFact、保留原记录的修正、精确事实及目标来源、PeriodReview 再生成与历史追溯；复盘无需审批即可供分析引用 |
| M1B 目标 | LTCO 建议、CEO 退回、修订及确切版本确认；PCO 与 Mission 起草；单 Owner 可与目标 DRI 同人，一个 Mission 可支撑同一 PCO 版本中的多个成果 |
| M1B 共同核对 | 固定目标版本和参与人，本人评论、撤回与替代，个人 Agent 独立分析，关窗封存有效意见，一次正式收拢及完整取舍记录 |
| M1B 定稿 | CEO 重开候选集合或整组确认；不继承旧 A2 全体 DRI 签认条件；错误 PCO 版本和不存在的成果引用被拒绝 |
| 跨方法与跨周期 | M1B 引用 M1A 生效 Strategy 的确切版本；战略更新产生影响提示，已确认目标保持原义；待确认旧依据被拒绝，显式复核后采用新依据 |
| 记忆与 Context | 按身份、阶段、用途和双时点选择材料，区分原始证据、Agent 分析、人的决定和复盘，记录实际采用版本及排除原因；历史名称和负责人按引用版本解析 |
| 运行与下游 | 显式运行关联、步骤尝试、暂停、恢复和查询；新版 Mission 提供承接投影，但不自动生成 M2 执行授权 |

业务状态与正文内容版本分别记录：评论、撤回、窗口关闭和其他状态动作推进对象头及审计，不制造正文新版本。纪要确认、Agreement 确认、战略更新确认保持三个不同动作。交付验收、Outcome 达成和 MF 关闭仍分别判断。

## 权限、并发和恢复证据

人和 Agent 使用各自的合成服务端身份；作者取自认证主体。正式写入同时检查当前任职、对象责任、状态、依赖版本和 CAS。prepare 不预授权，execute 重新核验；同一主体的完全相同请求幂等重放，修改内容或冲突须重新读取并准备。

窗口参与授权仅覆盖固定目标、相关候选版本和协作记录。研究授权仅覆盖指定议题、研究产物及明确共享的证据。域内 StrategicJudgment 的来源议题当前参与者可读取服务器写入的双来源所对应的确切版本；该权限不递归传播到 Strategy、原始事实或其他域资料。没有该来源关系的新负责人仍需显式域授权。

最终运行额外验证了两个容易遗漏的合法操作：既有域内判断第二次更新，以及同人兼任公司 CEO 和业务域 DRI 时按操作域记录、纠正事实。对应的无关人员、未共享材料和仅在别域具有 CEO 角色的调用均被拒绝。

- 撤权后，已准备写入、旧成功重试及相关读取重新受限。
- 关窗与评论、候选集合并发确认通过真实 PostgreSQL 锁等待确认竞争，结果可确定且没有重复生效。
- 在 Strategy 首次写入和候选集合首次写入后注入异常，整组内容、状态、回执和关联记录回滚。
- 已上传但未关联的原始材料不代表业务成功。
- 重启只停止本轮验收 API；对象、回执、原始字节和运行记录可恢复，重放不产生重复效果。
- 历史输入不使新周期自动继承旧运行，也不因旧运行暂停而禁止独立复盘；显式关联到新运行后，其暂停会约束报告再生成等写入。

## 历史兼容与验证范围

| 验证 | 最终结果与范围 |
| --- | --- |
| Python 回归 | 669 passed、1 skipped；含 14 项真实 narrative legacy 测试 |
| A1 | 现有 71 项模型、HTTP 协议边界及旧序列化回归；未重跑原 A1 所有控制面维护、旧 Worker 切换及发布场景 |
| A2 | 原独立实际 HTTP／数据库矩阵 66/66 |
| A3 | 原独立实际 HTTP／数据库矩阵 55/55 |
| 真实旧历史 | 从旧基线真实运行 A2→A3 承接、v1、退回、v2、验收；升级后 14 个对象、14 个内容版本、21 个历史请求重放及 34 张历史表保持一致，原始证据不变 |
| 新迁移 | 新隔离库从已接受基线到 0020，再执行 0021；重复迁移不新增记录 |
| 打包 | 离线 sdist／wheel 构建通过；wheel 内 127 个生产源码文件逐字节匹配，包含 0021 迁移 |
| 环境保留 | 已有 19 个部署相关文件及 27 个容器保持原状；未修改原演示数据库 |

唯一跳过项是原有 `test_viewer_rejects_non_human`，隔离夹具没有可用的 `agent_service` 用户。需要额外管理员建库夹具的 `tests/test_migrations.py` 未纳入上述 pytest 命令；本轮迁移正确性由真实空库升级、重放及升级前后历史复核单独验证。

旧 A2／A3 验收器通过 `legacy_regression.py` 补齐后来迁移引入的表目录预期，未改其业务断言、SQL 结果或数据库授权。普通 API 数据库角色不是 owner、superuser 或 BYPASSRLS；个人 Agent 绑定仍为控制面专属写入。

完整原始证据保留于本机：`artifacts/runtime-acceptance/method-final-02-20260911/`、`method-final-regression-04/`、`method-final-history-03/` 和 `method-package-20260911/`。运行凭据、私有夹具及重放命令保留在 Git 忽略的 `.runtime-acceptance/`，不属于交接文档。

## Clark 交接与后续边界

| 交付物 | 入口 |
| --- | --- |
| 总 API、身份、版本和恢复约定 | [runtime-method-api.md](runtime-method-api.md) |
| 全量 OpenAPI 与 49 个动作 schema | [OpenAPI](runtime-method-openapi.json)、[动作参数](runtime-method-actions.json) |
| M1A、M1B 对象和调用约定 | [M1A](method-m1a-contract.md)、[M1B](method-m1b-contract.md) |
| Clark 映射、prepare／commit 样例和页面处理约定 | [method-clark-contract.md](method-clark-contract.md) |
| 冻结契约及实验 Profile | [规则](contracts/tkos-method-0.1.md)、[Profile](contracts/method-profile.json) |
| 逐项验收矩阵和复跑方法 | [矩阵](runtime-method-acceptance-matrix.md)、[驱动说明](../acceptance/method_independent/README.md) |
| 完整真实 HTTP 调用驱动 | [M1A](../acceptance/method_independent/flow.py)、[M1B](../acceptance/method_independent/m1b_flow.py) |

Clark 接入以 Runtime 回执和读取结果为准，应用负责页面、通知和编排。本轮未修改 Clark 页面，没有把其本地“批准并下达”直接视为新协议确认。Mission 承接投影明确 `execution_authority=null`；M2 的 DRI、IC、验收人、授权时限和责任交接契约确定后再接执行能力。

真实企业数据、生产身份及授权映射、专业 Skill 判断质量、Clark 端到端页面验收、生产历史迁移、部署和业务规模下的性能验证后续分别安排。当前实验 Profile 明示合成数据来源，不表示已经取得公司生产授权。

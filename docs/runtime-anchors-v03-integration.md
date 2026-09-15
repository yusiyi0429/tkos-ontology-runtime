# Runtime Anchor 0.3 伙伴接入契约

状态：Runtime 源码接口增量通过隔离验收；Clark 接线、浏览器闭环和真实模型尚未验收。本轮只修改 Runtime。

## 固定契约与存储

新对象显式使用 `tkos.method/0.3`。参见 [冻结规则](contracts/tkos-method-0.3.md)、[Profile](contracts/method-profile-0.3.json)、[动作注册表](runtime-method-registry-0.3.json)、[实际应用 OpenAPI](runtime-anchors-v03-openapi.json)。0.1/0.2 对象继续按原绑定解释和写入，禁止普通引用跨版本接续。

Architecture 的实际类型为 `StrategicArchitecture`，整体版本化；Battlefield/Capability 是 `units` 中稳定的 `unit_id`，不新建独立生命周期表。内容保存定义、边界、interfaces、strategic_basis 和 domain_id。State 和 Problem 分别为 `OperatingState`、`OperatingProblem`，内容、revision、关系、治理状态、审计与回执复用既有存储。迁移 0025 仅补充类型约束、0.3 绑定门禁及 `gov_method_state_keys`、`gov_method_problem_keys` 身份唯一索引表，使用强制 RLS 和不可变键。

## 页面阶段—对象—动作—主体—效力

| 页面操作 | 对象与动作 | 有权主体 | 回执后页面状态及正式效力 |
| --- | --- | --- | --- |
| 查看当前身份 | GET /v1/identity | 当前会话 | 当前任职与 scope；不推断 Owner 等于 DRI |
| 查看责任结构 | GET /v1/workspaces/ceo?collection=architectures；GET /v1/method/anchors/{id} | 当前授权身份 | 展示 latest 与 effective 精确版本、来源、operations |
| 提出／修订责任结构 | method_propose_architecture / method_revise_architecture | 相关当前 DRI 或授权 Agent | 保存候选；不改变正式结构 |
| 确认结构 | method_confirm_architecture | 当前 CEO | 正式结构版本更新；历史目标依据保留 |
| 同时更新战略与结构 | m1a_propose_update 的 company change 携带 architecture；既有审查链后 m1a_confirm_update | CEO Agent 起草、Co-agent 审查、CEO 确认 | 同一事务生成并配对 Strategy 与 Architecture；回执含 architecture_refs |
| 读取个人状态 | GET /v1/workspaces/dri?collection=operating-states | 当前对象责任人 | Mission Owner 无需兼任 DRI；读取不授予修改权 |
| 推荐／重新推荐状态 | method_propose_state | 授权 Agent 或对象有权人员 | 保存推荐；已有 canonical 状态在新推荐待确认期间保持有效 |
| 确认／人工修正状态 | method_confirm_state | 精确对象／Outcome 的当前有权责任人 | 确认确切观察时点及基准；修正仅限 summary 与 rag，保留推荐历史 |
| 记录／修订问题 | method_open_problem / method_revise_problem | 授权主体，按层级检查责任任职 | 稳定核心问题身份及来源；不自动关闭 |
| 关闭问题 | method_close_problem | 当前绑定的有权人 | resolved 或 no_further_action，必须有理由及依据；Agent 不代关闭 |
| 查看待处理问题 | GET /v1/workspaces/dri?collection=operating-problems | 当前授权身份 | 移交、解决、无需继续处理退出活动集合；历史详情仍可读 |
| 从复盘／问题发起候选 | m1a_open_potential_issue | 授权 CEO Agent | 同一候选池，source_refs 保存来源；不新增 Strategic Signal |
| 归并或正式立项 | m1a_confirm_strategic_issue，关联时传 existing_issue_ref | 当前同域 CEO 的绑定 CEO Agent | 创建或关联议题；战略 Problem 原子转为 transferred、停止原跟踪 |
| 指派研究 | m1a_assign_research | CEO 本人 | 单独记录研究 DRI 与 Agent；立项 Agent 不冒充 CEO |
| 生成周期复盘 | m1b_generate_review / m1b_regenerate_review | Co-agent | state_refs 必须包含各目标精确版本的正式整体状态；复盘无需审批 |
| 月度评论到正式 Mission | 原 M1B 评论、替代、撤回、关窗、resolve、整组确认 | DRI 本人／Co-agent／CEO 各自身份 | 候选与正式版本分开；不创建执行授权或验收 |

## 精确引用与状态边界

从响应复制 `object_id / revision_id / content_hash`，动作目标另带 `expected_version`；不要自行分配正式版本。LTCO/PCO/Mission 增加 architecture_ref，Mission 增加 primary_scope_id。Strategy 的 method_state 保存 architecture_ref，Architecture 内容反向引用确切 Strategy，避免循环内容 hash。

State 的 subject_ref 指向确切目标版本，可附 outcome_id；as_of 必须带时区。baseline_refs 包含目标本身，其他基准限目标直接引用的 Architecture/PCO/LTCO；evidence_refs 使用授权事实或证据。无证据时必须为 unknown 并说明 data_gaps。同一目标身份、Outcome 和观察时点只有一个 State 身份；重新推荐提供 previous_state_ref，防止双击创建另一个正式状态。正式展示使用 effective_revision/canonical_ref，不能将 latest 推荐显示成确认结论。

Mission State 由 owner_principal_id 确认；LTCO 按其 CEO owner，PCO 整体按同域唯一当前 CEO，PCO Outcome 按其 DRI，并校验当前任职。负责填写周报不增加确认权。Problem 的责任任职按层级校验，域责任与 Architecture 定义项承接域一致。

## Agent、来源与恢复

CEO Agent 使用自己的认证，打开本人 MethodRun，读取 `/v1/method/research-context-packs` 授权 Context，再提交候选与立项。Context 与重放均重新校验当前绑定和任职。模型调用、模型版本证据及编排由伙伴保存并接入；Runtime 不把模型判断等同于战略确认。

Problem 移交与议题及 source_refs 保存同一事务：任何失败都保留原待办；成功仅在 M1A 跟踪，历史 Problem 保留议题引用。核心问题归并由 Agent 显式指定 existing_issue_ref；Runtime 不以文本相似度自动合并。普通关闭不接受 transferred。

所有动作先 `POST /v1/actions/prepare`，按返回的版本要求补齐原信封并持久保存，再 `POST /v1/actions`。成功后按回执结果引用刷新；响应丢失时重放同一身份的原始信封和 idempotency_key，不能修改内容沿用旧键。权限撤销后重放仍可能拒绝。版本冲突必须刷新并重新 prepare，业务变化采用新键。重启恢复依赖持久对象、回执和原请求，不依赖页面内存。

权限拒绝时不回退样例；404 可以表示对象不可见。operations 的 allowed/reason 为当前读取时的建议，提交仍重新校验；required_user_input 需由调用方补齐。不得据 allowed 推断任意参数均合法。未提供的优先级、进度及来源统计不得由页面补造。

读接口 no-store；前台每五秒、获得焦点及提交成功后刷新。伙伴应在身份、窗口、页面切换时取消旧请求并清除旧内容。旧战略待确认候选不能确认；通过显式 reopen 和新 LTCO/Strategy/Architecture 依据重新生成，历史正式目标不被覆盖。

## 接线示例与验收

[请求模板](runtime-anchors-v03-examples.json) 使用占位引用，不能直接当作已存在数据。完整合法准备、成功、权限拒绝、冲突、原请求恢复与事务失败案例见 [复跑入口](../acceptance/anchors_v03/README.md) 及 run.py/extended.py。严格动作 schema 以 OpenAPI 和注册表为准。

[验收报告](runtime-anchors-v03-acceptance.md) 分别记录 Runtime、伙伴接线、浏览器和真实模型。尚未提供历史对象自动升级、Play、Human+AI Plan、Work Package、M2/M3 完整链。

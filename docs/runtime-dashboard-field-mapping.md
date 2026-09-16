# 看板字段 → 来源映射（tkos.dashboard/0.1）

本表固定每个展示字段的数据来源与语义。实现位于
`src/memory_service_runtime/governed/dashboard.py`；所有字段都在当前身份、当前权限的
`db.transaction` 内读取，历史读取仍用当前权限。

## 策略选择与分组

| 看板字段 | 来源 | 语义 |
| --- | --- | --- |
| `strategy_choices[].strategy_id/revision_id/payload_hash` | `gov_method_strategy_heads` + 精确 revision | 每个业务域唯一当前正式 Strategy；不可读则整条省略 |
| `strategy_choices[].contract_version` | `gov_object_protocol_bindings` + profile/registry | 决定 Architecture 是否存在等版本语义 |
| `groups[].available` | 实际扫描该分组、以 selected Strategy 精确匹配后仍有可读对象 | 只统计可读对象；没有全局总数 |
| `groups[].historical_available` | `gov_method_impacts` 指向 selected Strategy，或 basis 为同域旧 Strategy | 旧依据目标单独分组 |
| `groups[].unattached_available` | 主题型 BusinessFact（`subject_ref.topic`） | 未关联事实，不自动挂 Mission |
| `historical_basis.items[].old_strategy_ref` | `gov_method_impacts.old_strategy_ref` | 服务端在新 Strategy 激活时记录的原依据 |

## 对象列表项

| 看板字段 | 来源 | 语义 |
| --- | --- | --- |
| `object_id/domain_id/object_type/...` | `gov_objects` | 稳定对象身份与当前头指针 |
| `title` | effective（无则 latest）revision 的 `payload.title` / `core_question` / `metric` | 发现用标题，不代表候选版本已生效 |
| `basis.status` | 精确引用遍历（见下） | `current` / `historical` / `unattached` / `mixed` / `unrelated` / `unavailable` |
| `basis.strategy_ref` | payload 的 `strategy_ref`，或经 `pco_ref`→PCO→`strategy_ref` | 保留对象**自己记录的**精确版本，不随头指针变化 |
| `basis.impact_linked` | `gov_method_impacts`（selected Strategy + 该对象） | 服务端记录的历史依据链接 |
| `basis_revision_id` | 用于计算 basis 的精确 revision（effective 优先，否则 latest） | 不把候选当正式 |
| `formal_state` | `gov_method_state` + 覆盖该精确版本的确认记录 | 见「正式状态」 |
| `owner` / `dri` | payload 的 `owner_principal_id` / `unit_outcomes[].dri_principal_id` / `participants` | Owner 与 DRI 分开，绝不互相等同 |
| `owner.appointment` | `gov_role_assignments` + `gov_principals` | 见「任命语义」 |
| `period` / `period_source` | payload `period`；Mission 经精确 `pco_ref` 取 PCO 版本周期 | Mission 无自有周期；`hard_deadline` 不是周期 |
| `deadline` | payload `hard_deadline` / `decision_deadline` / `feedback_deadline` | 期限独立显示 |

## 详情

| 看板字段 | 来源 | 语义 |
| --- | --- | --- |
| `selected_revision` | `revision_id` 参数（无则 effective，再否则 latest） | 历史 revision 保留，刷新不静默替换 |
| `content` | 所选 revision 的 `payload` | 原文精确内容；技术 JSON 折叠在「技术溯源」 |
| `business.*` | payload 的规范化视图（`_normalised_business`） | 只搬运记录字段，不解释 |
| `business.period_view` | `_derived_period` | `source=payload`（LTCO/PCO/Review）或 `source=pco_ref`（Mission） |
| `business.supports[].outcome` | 精确 `outcome_ref` revision 的 `unit_outcomes/outcomes`（经当前授权） | Outcome 名称与标准；不可读时显式缺失 |
| `responsibility.*` | payload 引用 + `gov_principals` / `gov_role_assignments` | 最小身份投影；不提供人员目录 |
| `relations.own_basis_refs` | `strategy_ref` / `architecture_ref` / `pco_ref` / `ltco_ref` / `subject_ref` / `state_ref` | 每条都按精确 revision 授权后返回 |
| `relations.support_refs` | `supports[].outcome_ref`（精确版本） | Mission → PCO Outcome |
| `relations.upstream_refs` | payload 树的精确引用去重 | 来源材料 |
| `relations.downstream` | `DOWNSTREAM_FIELDS` 的类型化字段（精确 object+revision+hash 校验） | 下一层对象，授权在分页计数前完成 |
| `formal_state` | 见「正式状态」 | 绑定所选精确版本 |
| `content_confirmation.records` | `gov_method_reviews` + `gov_method_state.confirmation_record_id` / `confirmed_candidate_ref` / `source_proposal_ref` | 覆盖所选精确版本的人工确认、确认人、理由、回执 |
| `candidates.latest/effective` | 同一对象的精确 revision 与字段差异 | 只比较同一对象，不跨对象 |
| `history` | `gov_object_revisions` | 精确版本列表 |
| `evidence.items` | payload 的 `evidence_refs` / `source_refs` / `baseline_refs` / `source_ref` / `corrects_ref` | 逐条授权；EvidenceAsset 提供字节下载 |
| `receipts.items` | `gov_action_receipts` + `authorize_receipt` | 任一引用不可读则整条隐藏 |
| `missing[]` | 显式缺失/不可用原因 | 绝不补造 |

## basis（精确引用遍历）

| 类型 | 依据来源 |
| --- | --- |
| Strategy | 与 `gov_method_strategy_heads` 精确比较；其他域 Strategy 为 `unrelated` |
| StrategicArchitecture / LTCO / PCO / ReviewWindow / CandidateSet | payload `strategy_ref` |
| Mission | payload `pco_ref` → 该**精确 PCO revision** 的 `strategy_ref` |
| OperatingState | payload `subject_ref` → 精确对象 revision 的策略依据 |
| BusinessFact | `subject_ref.topic` → `unattached`；否则经精确 subject revision |
| OperatingProblem | payload `state_ref` → 精确 OperatingState revision → subject |
| PeriodReview | `target_refs` 各精确目标；跨依据为 `mixed` |

同域旧 Strategy 且（或）有 impact 记录 → `historical`；其他独立战略域 → `unrelated`；
引用 revision 不可读 → `unavailable`（不猜测）。带 `payload_hash` 的引用必须与
不可变 revision 一致，否则整条关系不成立；基础设施错误直接抛出，不降级成“缺失”。

判定依据的 revision 一定通过精确引用加载：Mission 引用 PCO v1 时始终用 v1 的
Strategy，即使 PCO 头已到 v2。

## 正式状态

| 类型 | 正式判定 |
| --- | --- |
| Strategy | `gov_method_strategy_heads` 精确匹配所选版本 |
| StrategicArchitecture | effective revision + 覆盖该版本的 `architecture_confirmation` |
| LTCO / PCO / Mission | effective revision + 覆盖该精确版本的确认记录（`ltco_confirmation` / `candidate_set_confirmation`，经 `confirmed_candidate_ref` 的 `changed_refs`/`target_refs`） |
| OperatingState | `method_state.canonical_ref` 精确匹配；旧确认版本为 `superseded_confirmed`，新建议为 `recommendation` |
| BusinessFact | 已记录；纠错保留原始引用 |
| PeriodReview | 始终 `authority=agent_analysis`、`human_approved=false`（effective 指针不等于人工批准） |
| OperatingProblem | 当前 open/tracking；关闭 disposition 绑定精确 revision；`transferred` 表示移交而非解决 |

后续建议改变 `method_state.phase` 时，已确认的精确版本仍是正式内容；历史版本不会借用
另一版本或当前 Problem 处置的确认。

## 任命语义

负责人必须能跨业务域读取其真实任命，因此：

- `mission_owner`：查询该 principal 的**全部可见**任命（Mission 存储在 company 域，
  Owner 的合法 DRI 任命可能在子域）。
- `outcome_dri`：使用所选 payload 的 `architecture_ref` 中该 `unit_id` 的
  `domain_id`（精确责任域）。
- 其他 owner：对象存储域；Problem 责任人使用 assignment 记录域。
- 不可读域的任命不显示域名/角色数量，只给 `not_visible`；状态区分
  `current` / `future`（`valid_from` 在未来）/ `expired` / `revoked` / `not_recorded`。

## 只读

以上读取不改变 `gov_objects`、`gov_object_revisions`、`gov_action_receipts`、
`gov_method_reviews`、`gov_method_state`、`gov_context_snapshots`、`runtime_tasks`
或任何业务/权限记录；隔离验收在浏览前后对固定表集合做计数 oracle。

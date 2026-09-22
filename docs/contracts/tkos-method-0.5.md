# tkos.method/0.5 — 本体 v0.7 对齐增量契约（Constraint / 审视结论 / 复盘确认 / DRI 承诺 / 无人确认状态）

状态：**契约文字按《TKOS 本体结构 v0.7（M1 范围）｜CEO 对齐稿 2026-09-22》定稿；Mission 最终确认人待 CEO 裁决，本版按 DRI；Runtime 启用由 0.5 实施增量交付**。0.5 只有在协议注册表显式登记、且支持状态为本进程编译支持后才可调用；未启用前任何 0.5 请求返回 `PROTOCOL_NOT_SUPPORTED`，不产生业务成功。0.1／0.2／0.3／0.4 的解释、绑定、历史回执与回归保持不变；不自动迁移旧对象，不做跨版本放宽。

来源与校准：本体结构 v0.7（飞书 docx `KuU8d7rq6oozKDxtZqkcHI0nnJa`）、本体登记 `tkos.ontology-registry` 0.7.1（`docs/contracts/ontology-registry-0.7.json`）、总表 v0.8（即 v0.8.1）、M1-B v5.1、2026-09-22 内部对齐会（妙记 `obcn629472ww9266s9qy47l4`）、`docs/contracts/tkos-method-0.4.md`。本契约只写与 0.4 的差异，其余条款逐字沿用 0.4。

## 1. 版本、启用与隔离

- 每个隔离 scope 显式选择业务规则版本。0.5 使用独立的新 scope 启用；0.4 及更早 scope 继续按原绑定解释，不新增跨版本依赖、不就地重解释历史。
- 对象类型：0.4 的 15 个加 `Constraint`，共 16 个。动作：0.4 的 41 个去掉 `method_confirm_state`，加 `m1b_record_constraint`、`m1b_revise_constraint`、`m1b_confirm_constraint`、`m1b_confirm_review`，共 44 个。
- profile 除钉定本契约字节外，同时钉定本体登记 `tkos.ontology-registry` 0.7.1 的 JSON 字节（`ontology_registry_ref`）；两者任一改动都产生新 profile 修订并重新 pin。
- 未列入本契约的规则，以 0.4 契约为准。

## 2. Constraint（经营条件与约束）

- 独立对象类型 `Constraint`：`applies_to` 三选一（`company`；`scope` 加确切 Architecture 版本中的 Battlefield／Domain `unit_id`；`mission` 加确切 Mission 版本引用）、`statement`、`constraint_type`（people／money／capacity／policy／dependency／other）、`effective` 起止、`source`、`authority`、`severity`（hard／soft）、`evidence_refs`。
- 登记（`m1b_record_constraint`）与修订（`m1b_revise_constraint`）：CO_AGENT，或该范围的责任人本人。修订保持 `applies_to` 不变，产生新草稿版本；历史正式版本不变。
- 确认（`m1b_confirm_constraint`）：`company` 由当前 CEO；`scope` 由该 Scope 映射授权域的唯一当前 DOMAIN_DRI；`mission` 由其主 Scope 的当前 DRI。确认后成为正式版本（effective）。
- LTCO、PCO、Mission 的 `constraint_refs` 只能引用已确认的 Constraint，且其 `applies_to` 为 `company`、或与本对象主 Scope 相同（Mission 还可引用 `applies_to` 为本 Mission 的）。引用是参考，不是承接；抢人、超限、错期的校验是 Agent 分析，本契约不自动判定。

## 3. LTCO 审视结论

- `LTCO` 新增 `realization_logic`、`key_assumptions`、`constraint_refs`。`horizon` 的语义是"从当下起滚动的未来 6 个月"，文本承载，不自动计算。
- `m1b_confirm_ltco` 携带 `conclusion`：`established`（首次确认，对象尚无正式版本）、`revised`（对象已有正式版本，本次确认新草稿）、`maintained`（对象已是正式版本，本期审视维持不变：不产生新版本，只留一条带结论与说明的确认记录，只推进 CAS）。草稿目标不接受 `maintained`；已确认目标只接受 `maintained`，且必须指向确切的生效版本。
- 每次确认写入 `state.last_review = {conclusion, record_id}`；历次确认记录不覆盖。

## 4. Period Review 确认门与 PCO 承接

- `m1b_confirm_review`：当前 CEO 本人确认 `generated` 状态的 PeriodReview；可同时改写 `findings`／`learnings`／`implications`（产生 CEO 署名的新版本，Agent 起草版保留并记在 `state.agent_generation_ref`）。确认后 effective，`phase=confirmed`。`m1b_regenerate_review` 在 0.5 不再使复盘生效，生效只来自确认。
- `PCO` 新增 `period_review_ref`、`boundary`、`constraint_refs`。`period_review_ref` 必须指向已确认（effective 且 `phase=confirmed`）、且 `period.end` 不晚于本 PCO `period.start` 的 PeriodReview；本 scope 若已存在任一满足该时间条件的已确认 PeriodReview，则不允许为空（首个周期例外）。
- 候选与激活沿用 0.4；候选 PCO 保留起草时的 `period_review_ref`。

## 5. Mission：贡献、依赖、资源与承诺人

- `Mission` 新增 `boundary`、`contributes_to_scope_ids`（父 PCO 所引确切 Architecture 中、且不等于主 Scope 的 `unit_id`）、`dependencies`（`mission` 指确切 Mission 版本，或 `scope` 指 `unit_id`；`needed_by` 落在本 Mission `period` 内）、`resource_needs`、`constraint_refs`。
- 责任承诺只由责任域 DRI 对本域 PCO 做，一次承诺覆盖该 PCO 及其全部 Mission；`m1b_commit_candidate` 指向 Mission 一律拒绝（`INVALID_REQUEST`）。激活前置只检查每个 PCO 的 DRI 承诺；`unresolved_differences` 的关键分歧仍阻止激活。
- Mission Owner 参与窗口讨论，不握手；Owner 的正式指派在 CEO 整组激活时生效，Mission `state.owner_activation_record_id` 记该激活记录。Mission 最终确认人若 CEO 裁决为 Owner，进入 0.6，本版不变。

## 6. Operating State：起止时间、无人确认、下钻

- `OperatingState` 新增 `period`（起止）与 `drilldown_refs`；`as_of` 保留且必须等于 `period.end`。
- `method_propose_state` 直接产生正式（canonical）状态：`status=active`、effective、`phase=recorded`、`canonical_ref` 指向自身；0.5 没有 `method_confirm_state`。再次生成用 `previous_state_ref` 产生同一身份（同主体、同 `period`）的新版本；同一主体同一 `as_of` 只有一个身份。
- 生成人：CO_AGENT、CEO_AGENT，或主体责任人本人（责任解析沿用 0.4）。无证据只能 Unknown 并列明缺口（沿用 0.4）。
- `drilldown_refs` 只能引用已是 canonical 的 OperatingState，且不能引用与自身同主体的状态；上层引用下层，不自动汇总，不新增公司／Battlefield／Domain 聚合 State 对象（沿用 0.4）。
- 对状态有异议的责任人走 `method_open_problem`（0.4 语义），不改写状态。

## 7. 读取

- `GET /v1/method/company-view?period_start=&period_end=`：按主 Scope 汇总当前正式 LTCO、与该时段重叠的 PCO 与 Mission（Mission 含 `owner_effective_from`）、生效 Constraint；公司级 Constraint 单列，Mission 级 Constraint 按 Mission 归集。是投影，不是对象。
- `GET /v1/method/objects/{id}/confirmations`：该对象的决定类记录（确认、正式化、激活、重开、关闭、移交）与承诺行，各带 `principal_id`、`recorded_at`；承诺行带 `assignment_id`。
- `GET /v1/method/objects/{id}/reviews` 的每条记录附 `effect`：`decision`／`opinion`／`analysis`／`record`。
- `GET /v1/method/{collection}` 新增 `ltcos`、`pcos`、`missions`、`constraints`、`operating-states`。
- 以上读取按当前授权过滤，不返回不可见记录的计数；no-store。

## 8. 未交付边界（本契约不承诺）

- Mission Play、人 + Agent Plan、Mission Result、Finding、Management Issue 的新对象；M1B.3 仍用 0.4 的 `OperatingProblem` 语义。
- 战略相关输入（Signal）四类分型；M1-A 本期不展开。
- Mission Owner 作为 Role Assignment 行；`gov_method_relations` 投影表；旧对象自动升级；跨版本接续；Clark 与真实模型编排。

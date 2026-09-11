# Runtime A3：执行责任交接 API

日期：2026-09-11。A3 在 `tkos.contract-a/0.1` 下增加正常 DRI–IC 交接。工程映射见 [runtime-a3-engineering.md](runtime-a3-engineering.md)，本地验证边界与证据见 [runtime-a3-acceptance-report.md](runtime-a3-acceptance-report.md)。本文件不修改冻结 Method 契约或实验 Profile，不表示已对接真实经营系统。

## 启用与协议

需要迁移 `0020_execution_handover.sql`，并通过既有控制面为目标 scope 追加 [A3 support registry](runtime-a3-registry.json)。Profile、创建策略、对象绑定仍由 A1 控制面治理，A3 不自动迁移或改绑旧对象。旧 A1 只读 registry、A2 registry 和 legacy 绑定保持原语义。

新增对象为 ExecutionPlan；ExecutionCommitment、WorkItem、Deliverable 按服务端协议绑定选择 A3 或旧语义。客户端必须显式携带 `contract_version: "tkos.contract-a/0.1"`。缺失声明不能降级执行。更新后的 [OpenAPI](../contracts/openapi.json) 对应当前应用生成结果；服务自身也提供 `/openapi.json`。

所有业务命令仍为 `POST /v1/actions/prepare` 与 `POST /v1/actions`，使用调用者自己的 Bearer 身份。共同字段为 `action_type`、`target`、`expected_versions`、`idempotency_key`、`reason`、`params`、`contract_version`。创建时 target=null；其他动作 target 包含 object_id、revision_id、expected_version。

先用当前目标版本调用 prepare，再将其返回的 target/expected_versions 原样用于提交。prepare 完成相同准入检查，不锁定未来权限或保留事务；正式执行仍重查。接到版本冲突后重新读取和准备，不能只替换 CAS 数字后继续使用旧授权、旧计划或旧签认。成功响应是 ActionReceipt；`GET /v1/action-receipts/{id}` 返回 `{receipt, effects}` 包装。

## 正常流程与责任

本轮是三个不同自然人：A2 正式成员中的 DOMAIN_DRI、指定 IC、被任命为 VERIFIER 的独立验收者。公司批准不会自动授予 IC 执行权或验收权。

| 步骤 | 动作与目标 | 当前有权人 | 关键内容／返回值 |
|---|---|---|---|
| 1 | A2 activate_company_composition | 指定 CEO | 取得真实激活的本域 Mission、DomainCommitment 精确版本 |
| 2 | create_object / ExecutionCommitment | 本域指定 DRI | 创建待签认 What；返回 object_id、revision_id |
| 3 | accept_commitment / EC | DRI、IC 各自 | party_assignment_id、understanding、accepted_terms_hash；返回 handshake_id |
| 4 | activate_commitment / EC | 同一指定 DRI | 精确两条 handshake_record_ids、activation_policy_revision_id；返回 execution_authority_id、execution_epoch、appointment_id、appointment_version |
| 5 | create_object / WorkItem | 同一指定 DRI | 必须绑定当前 EC 与执行授权，标准和期限不能扩张 |
| 6 | accept_work_item / WorkItem | 指定 IC | execution_authority_id、execution_epoch；返回 work_receipt_id、work_item_revision_id |
| 7 | create_object / ExecutionPlan | 已接收任务的 IC | 计划创建即发布；返回 object_id、revision_id |
| 8 | propose_revision / Plan | 同一 IC | 新版本只修改 How；原计划版本保留，无额外批准动作 |
| 9 | submit_deliverable / WorkItem | 同一 IC | 当前执行授权、当前计划与真实证据；返回交付对象、版本、hash、submission_seq |
| 10 | review_deliverable / WorkItem | 被任命的独立验收者 | 精确交付版本、hash、逐项标准、任命 ID／版本；返回 delivery_acceptance_id |
| 11 | 再次 submit_deliverable | 同一 IC | v2 的 responds_to_acceptance_id 必须是本任务 v1 的当前退回记录 |
| 12 | 再次 review_deliverable | 同一独立验收者 | 对 v2 另行判断；通过后 WorkItem 为 delivery_accepted |

两次签认绑定同一个不可变 EC revision/hash；改版后旧签认不能拼到新版本。WorkReceipt 只是接收已确认责任，不再创造一份 What。一个 WorkItem 只有一个 Deliverable 身份，每次提交建立新的不可变 revision；每个提交版本最多产生一次正式评审。

## 字段约定

ExactRef 为 `{object_id, revision_id, payload_hash}`，包含规范 UUID 和 SHA256。时间为含时区的 ISO8601；未知字段、重复引用、重复标准、非严格整数拒绝。完整字段类型见 OpenAPI 与 `a3_models.py`。

ExecutionCommitment payload：

- title、mission_ref、domain_commitment_ref。
- dri_assignment_id、ic_assignment_id、acceptor_assignment_id。
- what：result_statement、boundary、acceptance_criteria、hard_deadline、external_dependency_refs。
- execution_window、acceptance_window：各自包含 valid_from、valid_to，前闭后开。

What 的结果、边界、标准、完整依赖集合必须与真实生效 Mission 一致；hard_deadline 位于 Round 周期内，执行窗口不能晚于该期限。草案只能由指定 DRI 修改，生效后不能通用改版。

WorkItem payload：title、execution_commitment_ref、execution_authority_id、execution_epoch、acceptance_criteria、due_at。due_at 等于已确认 What 硬期限；任务基线不可通用改版。

ExecutionPlan payload：title、work_item_ref、execution_commitment_ref、steps。每步为 step_id、description、可选 due_at、depends_on_step_ids。步骤 ID 唯一、依赖必须存在且无循环，步骤时间不超过 What 期限。正文不具有改变结构化 What 的效力；计划不接受结构化责任、标准或依赖改写。

提交参数：title、summary、evidence_revision_ids、可选 responds_to_acceptance_id、execution_authority_id、execution_epoch、plan_ref。提交前用 `POST /v1/evidence-assets` 上传原始字节。准备和执行都会核对证据版本和实际 S3 字节。author_principal_ids 由服务器按提交人及证据 revision.recorded_by 推导，客户端不能填作者集合。

评审参数：deliverable_revision_id、delivery_payload_hash、verification_result、criterion_results、review_note、appointment_id、appointment_version。verification_result 为 accepted 或 changes_requested；criterion_results 恰好覆盖全部标准，每项包含 criterion_id、result（passed/failed）、note。全部通过才能 accepted；退回必须明确至少一项失败。

## 执行权、验收权与回放

ExecutionAuthority 与 AcceptanceAppointment 使用不同的当前指针、代次、身份和时间窗口。IC 的执行任职撤销、执行窗口到期或执行代次失效，会阻止后续执行；不会自动撤销独立验收人的任命。已经提交的内容可以在 IC 离任或执行到期后由仍有权的验收者评审，历史作者不被改写。

验收者不能是 DRI、IC 或证据推导出的作者／共同作者，即使同一人换另一角色发起操作也不能规避。另一周期中属于同一个人的任命不能授权当前任务。

GET 历史回执只判断当前读取权。POST 原始命令回放还复核原动作要求的具体责任；同一人剩余的其他角色不能恢复已撤销的 IC 执行权。正常授权仍有效时，原请求重试返回原回执，不增加提交、评审或外部任务；同键改内容返回 IDEMPOTENCY_CONFLICT。

A3 的 effect_task_ids 恒为 []。本轮不产生 legacy activate_commitment 的 governance.dispatch；公司组合激活也不自动对外派单。所有业务状态、版本、授权、评审和回执在同一 scope 栅栏事务中提交。最终提交前再次按数据库时钟核对权利窗口与 A2 正式依据。

## 独立 Outcome 判断

`record_outcome_assessment` 的 A3 目标是被真实激活组合采用的精确 CompanyReference。原 CompanyOutcome 继续按 legacy 处理，不自动重释或迁移。CompanyReference.terms.outcome_spec 显式提供 metric=activated_customers、正整数 target_count、无重复 client_ids。

本轮只支持 `tkos.synthetic.customer-events/0.1` 合成事件包，record_origin 必须为 synthetic；包须绑定该 CompanyReference 的 ExactRef 和 Round 的 period_id。一个客户只有同时具备 onboarding_completion_event 与 first_valid_business_transaction_event 才计数；时间在本周期且不晚于数据库实际评估时间。重复事件不重复计数，冲突事件 ID、错误目标、上周期数据和未来事件拒绝。格式样例见 [工程映射 §5](runtime-a3-engineering.md)。

参数沿用 assessment_result、observation_revision_ids、evidence_revision_ids、assessment_note、delivery_acceptance_ids。observation_revision_ids 在 A3 下指向实际上传的 JSON EvidenceAsset。指定 CEO 另行作出 achieved、not_achieved 或 inconclusive 判断；前两者须与服务器按原始证据算出的合格客户数一致。可附同一公司组合下已通过的交付评审，其本身不增加客户数。

评估只追加独立记录并推进目标的审计/CAS，CompanyReference 的内容 revision/hash 不改；不会改动交付结论或 MF 状态。这证明合成事实处理机制，不证明现实客户激活，也不构成完整指标体系。

## 读取与应用接入边界

- `GET /v1/objects/{id}`：A3 对象返回 a3_projection；EC 中分开显示当前执行授权、代次与验收任命／版本；WorkItem 显示接收、当前计划、提交和评审。
- `GET /v1/objects/{id}/revisions/{revision_id}`：读取精确历史内容及当前协议元数据。
- `GET /v1/objects/{id}/relations`：读取当前可见的精确责任和证据关系，隐藏目标不返回边或数量。
- CompanyReference 已有可读评估时追加 a3_outcome；每条评估携带自己的目标精确版本，latest_assessment_id 表示最新可授权读取的评估标识。若真正最新一条无权读取，不把较旧记录冒充最新。
- 所有披露受当前读权限约束；IC 的准入检查不会让全量公司组合、外域任务或其 CAS 进入响应。
- 原 `/responsibility` 是 legacy WorkItem 投影，对 A3 继续明确拒绝旧责任解释。A3 责任以 a3_projection 中的独立记录为准；Clark 的 A3 操作页与工作台交互升级不在本轮。
- A1 的 Context Pack 对 Contract-A 仍保持原有范围限制；本轮不把 A3 对象强塞进 legacy 的历史语义解释。

常见拒绝：FORBIDDEN（当前人／具体责任不符）、STALE_DEPENDENCY（计划、代次、来源或基线改变）、EXECUTION_AUTHORITY_EXPIRED、ACCEPTANCE_APPOINTMENT_EXPIRED、INVALID_STATE（未接收、已评审等）、VERSION_CONFLICT、IDEMPOTENCY_CONFLICT、INVALID_REQUEST 和协议围栏错误。

尚未实现：替岗、临时代理、验收人变更、暂停／恢复、正式调整、Profile 迁移、外部业务派发、Clark A3 界面、部署与真实企业系统接入。上述入口不会作为成功业务路径放行。

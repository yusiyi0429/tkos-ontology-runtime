# A3 工程映射与交付边界

2026-09-11。状态：实现前冻结的工程约束；当前实现与验证结论另见 [A3 验收记录](runtime-a3-acceptance-report.md)。规范为契约包 A §1／§5／§6、独立验收 A3-01–14；不改动已冻结契约或 Profile hash。

## 1. 首批实现

沿用 `/v1/actions` 和 `/v1/actions/prepare`。同名动作由服务端对象协议登记选择 handler；请求的 `contract_version` 仅表明客户端理解的格式。保留 legacy 请求序列化、所有 A1／A2 围栏和已记录历史。

链条为 A2 真实激活 → 指定 DRI 创建 ExecutionCommitment → DRI 与 IC 同版确认 → DRI 释放 ExecutionAuthority 并建立独立 AcceptanceAppointment → DRI 下达 WorkItem → IC 接收 → IC 发布／修订 ExecutionPlan → v1 → 退回 → v2 → 有权验收。三位不同自然人；验收任命固定，无替岗／代理／重审功能。

## 2. 基础模型接口

精确引用包含 `object_id`、`revision_id`、`payload_hash`。UUID、hash、非空文本、时间及整数采用严格校验；未知字段拒绝，列表禁止重复引用。以下字段是本轮字段映射，不改变规范术语。

- `ExecutionCommitment`：`title`、`mission_ref`、`domain_commitment_ref`、`dri_assignment_id`、`ic_assignment_id`、`acceptor_assignment_id`、`what`、`execution_window`、`acceptance_window`。
- `what`：`result_statement`、`boundary`、`acceptance_criteria`、`hard_deadline`、`external_dependency_refs`。结果、边界、标准与依赖须匹配 A2 Mission 的正式内容；期限在本轮周期内，由 DRI 在首次 What 中明确。
- 两种 window：`valid_from`、`valid_to`，前闭后开。执行截止不晚于 What 硬期限。验收窗口独立于执行窗口，不能把执行权失效自动解释为验收任命失效。
- `WorkItem`：`title`、`execution_commitment_ref`、`execution_authority_id`、`execution_epoch`、`acceptance_criteria`、`due_at`。标准和硬期限与已确认 What 一致，不通过任务接收增加责任。
- `ExecutionPlan`：`title`、`work_item_ref`、`execution_commitment_ref`、`steps`；每步含 `step_id`、`description`、可选 `due_at`、`depends_on_step_ids`。步骤 ID 唯一、引用存在、无循环，时间不超过 What 硬期限。步骤文本不具有改变结构化 What 的效力。计划创建即发布，不增加批准动作。
- `accept_commitment`、`activate_commitment` 沿用现有参数形状，分别校验本人精确任职／terms hash 和指定同版双签／当前激活策略。`accept_work_item` 增加明确的执行授权 ID 与代次参数；提交和评审也携带相应当前权利引用，不能靠旧回执恢复授权。
- `submit_deliverable` 保留标题、摘要、真实证据版本和回应退回 ID；另外精确绑定接收时执行授权／代次及当前计划引用。作者集合由服务端提交人和本次证据的实际记录者推导，不接受请求自由填写作者。
- `review_deliverable` 保留精确交付版本／hash、逐项判断、评语；另外绑定验收任命 ID／版本。当前验收者不得是 DRI、IC 或可信作者／共同作者。

## 3. 存储与事务约束

复用 `gov_objects`、不可变 revision、`gov_handshakes`、Receipt 和 lifecycle events。A3 自有执行状态、执行授权、验收任命、任务接收和交付评审使用独立受约束记录；不把旧 `gov_work_item_state.dri_assignment_id` 改释为 IC。

新增迁移为 `0020_execution_handover.sql`，只追加 A3 所需对象类型／表／约束。所有新表按 scope 使用 ENABLE／FORCE RLS，写入需要既有 runtime capability；确认／授权／任命／接收／评审记录不可变，可变指针和代次单独保存。外键绑定精确 scope、对象、revision、主体和任职，避免仅应用层约束。

所有写入口经过 scope 栅栏、当前角色和对象责任、服务端协议、完整可见依赖 CAS、状态和精确版本检查。外部来源有效性／自然到期在 `before_business_commit` 之后再次核对。`prepare` 执行相同准入判断，且不修改业务状态。

执行检查 ExecutionAuthority 和指定 IC；评审检查 AcceptanceAppointment 和独立验收者，二者实现为独立函数。已提交内容的作者历史不因 IC 离任重写。A3 不产生外部业务派发：`effect_task_ids=[]`；不能沿用 legacy `activate_commitment` 的 dispatch 副作用。

## 4. A2 依据与读取

执行承诺只能关联同域、精确生效 Mission 和 DomainCommitment；两者须来自同一真实 A2 activation。执行时核对 Round 当前生效指针、manifest、正式成员／提交、binding 来源及当前责任，不调用只面向“未激活候选”的 A2 准入函数冒充执行校验。

公司组合的内部准入复查不能假扮 CEO／DRI，也不能向 IC 返回其无权读取的全量组合、外域任务或额外 CAS 对象。只有本域责任材料及显式共享到其当前可读域的来源可披露。内部 scope SQL 校验与对外读取授权分开，历史回执不扩展当前读取权限。

## 5. A3-11 的最小结果评估映射

本轮沿用 A2 已采用的 CompanyReference 作为“本周期目标”的精确锚点，`record_outcome_assessment` 的 A3 handler 对该目标记录独立判断，不改释旧 CompanyOutcome，不开放新的 CompanyOutcome／MetricObservation 通用创建入口。这是同一逻辑结果的工程承载，不宣称旧对象已迁移。

试验的 CompanyReference `terms.outcome_spec` 由其原有 CEO 来源发布入口明确提供：`metric="activated_customers"`、`target_count` 正整数、无重复 `client_ids`。被当前激活组合采用后才能评估；周期和窗口取对应 Round，而非由评估请求自由指定。除该明示合成指标之外的经营指标返回不支持，不尝试通用自然语言推理。

`record_outcome_assessment` 保留现有参数名：`assessment_result`、`observation_revision_ids`、`evidence_revision_ids`、`assessment_note`、`delivery_acceptance_ids`。A3 下观察引用明确指向真实上传的 EvidenceAsset JSON 包，格式为：

```json
{
  "schema_version": "tkos.synthetic.customer-events/0.1",
  "record_origin": "synthetic",
  "company_reference_ref": {"object_id": "UUID", "revision_id": "UUID", "payload_hash": "SHA256"},
  "period_id": "UUID",
  "events": [{"event_id": "UUID", "client_id": "SYN-C01", "event_type": "onboarding_completion_event", "occurred_at": "ISO8601"}]
}
```

每个客户必须同时具备 `onboarding_completion_event` 和 `first_valid_business_transaction_event`，事件时间在本轮窗口内且不晚于实际评估时钟。按目标客户去重计数；一个事件 ID 不能冲突复用，同一客户同种事件不能重复计数。登录、交付物通过、来自上一周期或其他目标的包不能充数。原始字节、S3 版本、hash 与当前读取权均须验证；客户端不能伪造实际值。此包只证明合成事实处理机制，不证明现实客户已激活。

CEO 对证据另行判断：`achieved` 要求当期合格客户数达到目标，`not_achieved` 要求小于目标，`inconclusive` 保留人工暂不下结论的语义。可附 A3 交付评审 ID，但仅允许同一公司组合下的评审；交付通过不增加客户计数。观察包与补充证据均保留精确来源。评估只追加结果记录并更新对应读取投影，不激活、关闭或改写交付和 MF。

P01 使用 C01／C02 的完整双事件和 C03 不足证据，独立判断 2 < 3；P02 使用 C04／C05／C06、新周期与新证据判断 3。原 CompanyReference 的目标内容、revision/hash 不因评估改变。旧协议的 CompanyOutcome／MetricObservation 行为保持原状。

## 6. 物理记录与工程边界

建议并冻结独立验收的物理表名：`gov_execution_state`（当前执行授权与验收任命的独立指针）、`gov_execution_authorities`、`gov_acceptance_appointments`、`gov_a3_work_item_state`、`gov_work_receipts`、`gov_a3_delivery_acceptances`、`gov_a3_outcome_assessments`。其中只有两个 state 表允许 UPDATE；其余仅追加。Plan 和 Deliverable 使用不可变 governed revision，当前 Plan／交付指针位于 A3 WorkItem state。字段可以在迁移审查中补强，但不得把不同判断合为一个状态字段。

`after_a3_submission_write` 与 `after_a3_review_write` 仅为生产 no-op checkpoint，用于私有验收包装器在首条业务写入后注入失败。生产实现不得导入测试代码或从测试输入决定业务结果。

Codex 负责独立验收目录，Kimi 不修改其矩阵或预期结果。应用端由 Clark 伙伴承接；本轮不改 Clark、部署、现有容器或演示数据库，不新增生产依赖，不提交推送。

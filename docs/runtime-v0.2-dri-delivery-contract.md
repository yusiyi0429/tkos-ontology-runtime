# Runtime v0.2：真实 DRI 交付闭环契约

本版在既有承诺与 MF 治理内核上增加真实交付流程：**承接 → 提交 v1 → 退回补充 → 提交 v2 → 有权人验收**。范围是一个业务域内已生效 ExecutionCommitment 下的交付，不修改 Clark，不增加生产依赖，不默认产生外部 dispatch。

本文是实现与独立验收契约。文档存在、代码合并或单元测试通过，不代表已经完成 Runtime 验收、Clark 联调、服务器部署或生产升级。v0.1 的授权、版本、证据与 MF 规则继续适用，参见 [原独立验收契约](runtime-independent-contract.md)。

后续 Clark 本地接入与实际验收结果见 [Clark v0.2 联调报告](clark-v0.2-acceptance-report.md)，与本内核的独立验收分别记录。

## 1. 三项判断分别存储

| 判断 | 权威记录 | 允许改变的结果 | 不推导的结果 |
| --- | --- | --- | --- |
| 交付通过 | 精确绑定交付版本的 DeliveryAcceptance | WorkItem 为 `delivery_accepted`，Deliverable 为 `accepted` | 不推导 Outcome 达成，不关闭 MF |
| Outcome 达成 | 独立 OutcomeAssessment | 当前目标版本的 `achieved`、`not_achieved` 或 `inconclusive` | 不替代交付验收，不关闭 MF |
| MF 关闭 | 原 MF acceptance、解决 Decision 与 closure 记录 | 对应 FeedbackThread 处理周期的关闭或驳回状态 | 不推导交付通过或 Outcome 达成 |

`confirm_outcome` 仍表示**确认目标内容**：将 CompanyOutcome 的某一 revision 设为 effective，生命周期为 `confirmed`。它不是达成评估。`record_outcome_assessment` 追加独立判断并递增目标的 `object_version`，不将目标内容状态改写为 `achieved`。

首次读取尚无判断的当前目标，`outcome_assessment` 为 `null`、`outcome_achievement` 为 `not_assessed`。新目标 revision 生效后，旧 revision 的判断保留为历史，但不能显示为新目标已达成。

## 2. WorkItem：不可暗改的派单基线

通过现有 `create_object` 创建 `WorkItem`，使用现有 `domain_id`、`target:null` 和命令信封。其 payload 为：

```json
{
  "title": "完成业务域交付包",
  "execution_commitment_ref": {
    "object_id": "ExecutionCommitment UUID",
    "revision_id": "effective revision UUID"
  },
  "dri_assignment_id": "MISSION_DRI assignment UUID",
  "acceptor_assignment_id": "acceptor assignment UUID",
  "acceptance_criteria": [
    {"criterion_id": "complete", "description": "交付材料完整且可核验"},
    {"criterion_id": "consistent", "description": "关键数据与证据一致"}
  ],
  "feedback_ref": {
    "object_id": "optional FeedbackThread UUID",
    "revision_id": "optional feedback revision UUID"
  },
  "due_at": "2026-09-30T10:00:00+08:00"
}
```

示例 UUID 文字需替换为真实 UUID；`feedback_ref`、`due_at` 可以省略。时间必须包含时区。

- 仅当前拥有派单权限的人工 CEO 或 DOMAIN_DRI 能创建 WorkItem。
- 引用必须属于同一 scope、同一业务域，并指向当前生效的 ExecutionCommitment revision；该承诺已经完成所需签认及激活。
- 指定 DRI 必须是该 ExecutionCommitment 中的 MISSION_DRI 签认人，且 assignment 当前有效。首期不扩展到 IC 委派或任意同角色人员。
- 指定验收人必须是当前有效的人工 DOMAIN_DRI、VERIFIER 或 CEO，且与 DRI 为不同自然人。不同 assignment ID 不能掩盖同一 principal 自验。
- `acceptance_criteria` 非空，`criterion_id` 唯一。标准随 WorkItem revision 固定，交付人或评审人不能在提交/评审时临时替换。
- 可选 `feedback_ref` 仅建立来源关系，不代表承接 MF、不借用 MF 权限，也不触发 MF 状态变化。
- WorkItem 创建后基线冻结；不允许通用 `propose_revision` 改写任务、DRI、验收人、验收标准或上游承诺。首期没有隐式改派、重基线或通过修改 payload 重开已验收交付的入口。

生命周期：

```text
offered --accept_work_item--> in_progress
in_progress --submit_deliverable(v1)--> submitted
submitted --review_deliverable(changes_requested)--> changes_requested
changes_requested --submit_deliverable(v2)--> submitted
submitted --review_deliverable(accepted)--> delivery_accepted
```

首个提交也可以直接通过；多轮补充沿同一规则继续递增提交序号。

## 3. 命令信封与权限

所有新命令沿用 `POST /v1/actions` 与 `POST /v1/actions/prepare`，包含 `action_type`、`target`、`expected_versions`、`idempotency_key`、`reason`、`params`。下表中的 `target` 必须包含对象 ID、当前 revision ID 和 `expected_version`。

| action_type | target | params | 额外权限限制 |
| --- | --- | --- | --- |
| `accept_work_item` | WorkItem | `{}` | WorkItem 指定的当前有效 DRI 本人 |
| `submit_deliverable` | WorkItem | 见下一节 | WorkItem 指定的当前有效 DRI 本人 |
| `review_deliverable` | WorkItem | 见第 5 节 | WorkItem 指定的当前有效验收人本人，且不能自验 |
| `record_outcome_assessment` | CompanyOutcome | 见第 6 节 | 当前有权的人工 CEO |

身份来自现有凭据解析，客户端不能选择 principal、scope、角色或冒充 assignment。角色 allowlist 只是权限的一部分；执行时还必须匹配 WorkItem 固定的具体 assignment。

每次授权继续使用 scope 授权锁，在锁内重查当前 principal、assignment 与 policy。被撤销或过期的身份不能利用旧页面、旧 prepare 结果或旧凭据继续提交/验收。同一人的另一个角色不能绕过禁止自验。

**旧 scope 的 policy 没有新增 action allowlist 时，新命令默认拒绝。** 数据库迁移不自动替既有客户授予新权限；启用 v0.2 需要单独设置新的、经过审核的 policy revision。测试 seed 的新策略不是生产升级授权。

## 4. 提交：同一交付物的不可变 v1、v2

`submit_deliverable` 的 params：

```json
{
  "title": "交付包 v2",
  "summary": "补齐上次评审指出的原始数据与核验说明",
  "evidence_revision_ids": ["EvidenceAsset revision UUID"],
  "responds_to_acceptance_id": "previous changes_requested acceptance UUID"
}
```

首个提交省略 `responds_to_acceptance_id`。补充提交必须引用**本任务上一次退回的** DeliveryAcceptance ID，不能引用别的任务、旧轮次、已经通过的记录或 MF acceptance。

- 第一次提交创建一个稳定的 Deliverable 对象；后续提交在同一对象上追加不可变 revision。
- `submission_seq` 由服务器生成，依次为 1、2、3；不是客户端可选字段，也不等于 `object_version`。
- 每个 revision 保存当次 title、summary、证据版本、来源任务基线和所回应的退回记录。v2 不覆盖 v1 的字节、hash、提交人或证据引用。
- 引用的证据必须是真实、可访问、同域的 EvidenceAsset revision。保存精确 S3 version、内容 hash 的现有证据机制继续生效；URL 或说明文本不能代替真实证据。
- WorkItem 必须为 `in_progress` 或 `changes_requested`。未承接不能提交；等待评审时不能另行覆盖提交；通过之后不能继续补交。
- 提交同时更新 Deliverable 为 `submitted`、WorkItem 为 `submitted`，并同事务记录不可变事件及 receipt。
- 上游承诺基线若已失效，继续推进不得悄悄切换到最新承诺；应返回明确的版本/依赖错误。首期不提供隐式重基线。

Deliverable 生命周期为 `submitted → changes_requested → submitted → accepted`。

## 5. 验收：精确绑定当前交付版本

`review_deliverable` 的 params：

```json
{
  "deliverable_revision_id": "current submitted Deliverable revision UUID",
  "delivery_payload_hash": "64-character lowercase SHA256",
  "verification_result": "changes_requested",
  "criterion_results": [
    {"criterion_id": "complete", "result": "failed", "note": "缺少原始数据，请补充"},
    {"criterion_id": "consistent", "result": "passed", "note": "已核对已提供材料"}
  ],
  "review_note": "补充原始数据后重新提交"
}
```

- WorkItem 当前必须为 `submitted`，目标 Deliverable revision 必须是该任务最新待评审提交；请求 hash 必须与数据库中该不可变 revision 的 payload hash 一致。
- `criterion_results` 必须恰好覆盖冻结标准集合：不遗漏、不添加、不重复；每项结果为 `passed` 或 `failed`。
- `accepted` 要求全部标准 `passed`；`changes_requested` 至少一条 `failed`，且有明确的补充理由。不能把不通过的标准藏在整体 `accepted` 下。
- DeliveryAcceptance 保存确切任务基线、交付 revision/hash/提交序号、逐项判断、总体判断、review note、评审人 principal/assignment、服务端时间与 action receipt。
- 退回只让 WorkItem/Deliverable 进入 `changes_requested`；通过只让 WorkItem 进入 `delivery_accepted`、Deliverable 进入 `accepted`。
- v1 退回后不能被重新验收，v2 提交后不能拿 v1 的 hash、评审结果或 receipt 为 v2 盖章。
- DeliveryAcceptance 是 append-only；纠正业务结果要走明确的后续流程，不能 UPDATE 旧评审。首期未提供已通过后撤销或重开的动作。

人工判断负责业务质量，Runtime 负责身份、状态、版本、标准覆盖、证据真实性和审计约束。`passed` 不是由 LLM、页面展示或外部任务成功状态自动决定的。

## 6. Outcome：独立评估实际结果

`record_outcome_assessment` 的 params：

```json
{
  "assessment_result": "not_achieved",
  "observation_revision_ids": ["MetricObservation revision UUID"],
  "evidence_revision_ids": ["EvidenceAsset revision UUID"],
  "assessment_note": "交付包已通过，但当期指标尚未达到目标",
  "delivery_acceptance_ids": ["DeliveryAcceptance UUID"]
}
```

- target 是 CompanyOutcome；要求精确指向当前 effective revision，并满足现有命令对当前目标版本的要求。
- 判断值仅为 `achieved`、`not_achieved`、`inconclusive`。服务端记录时间和判断人，不能由客户端填写审计身份/时间。
- MetricObservation、EvidenceAsset 及交付验收引用必须存在、类型正确、可访问并处于同域；使用确切版本，不在读取时替换成最新观测或证据。
- 本期判断是当前时点的评估：观测必须为该指标对象当前有效的 revision，且当前时间处于其 `valid_from` / `valid_to` 窗口内；未来或已经过期的观测不能直接支撑当前判断。历史期间的追溯评估与判断回填不在本期范围内。
- `delivery_acceptance_ids` 可以为空；它们是评估的来源，不是自动推导成功的布尔开关。即使交付通过，仍可记录 `not_achieved` 或 `inconclusive`。
- 不把 CompanyOutcome 中自由格式的 `terms` 当作未经定义的自动判分程序。本期记录有权人的、证据支撑的判断；不声称实现任意指标公式的自动评估。
- 新评估追加独立记录、递增目标 `object_version`，保留此前判断和所绑定的目标 revision。目标生命周期继续为 `confirmed`。
- 单独记录 Outcome 达成，不生成 WorkItem 验收或 MF closure；没有交付引用也不能借此伪造交付已通过。

观测与证据数组均非空；交付验收数组可以省略或为空。字段长度与其余边界以实际 Pydantic schema 为准；重复标识、未知字段与类型不匹配不得静默接受。

## 7. 读取、审计与事务不变量

`GET /v1/objects/{id}`：

- WorkItem 返回 `delivery:{state,submissions,acceptances}`，其中 state 为交付状态行，submissions 为提交 revision，acceptances 为交付评审记录，能还原 v1 退回、v2 再次提交与最终验收的事实。
- CompanyOutcome 返回独立 `outcome_assessment` 和 `outcome_achievement`；当前有效目标尚无评估时分别为 `null`、`not_assessed`。
- 为支持 Clark 跨人继续 MF 流程，FeedbackThread 增加 `feedback:{state,resolution_decision,resolution_decision_revision,acceptances}` 只读投影。`resolution_decision_revision` 是状态中精确绑定的决策版本；界面不能用最新决策内容替代它。决策、验收证据和调整对象逐一重新检查当前读取权限。
- 不把三项判断合并成一个 `done`。Clark 等消费者应分别展示交付判断、Outcome 判断和 MF 状态。

新增模型继续参与现有对象 revision、receipt 和 context 读取。历史 `source_refs` 需要覆盖 `execution_commitment_ref`、可选 `feedback_ref`、交付来源任务、证据及判断来源；不能只处理既有 `upstream_refs`。历史内容以当时的确切 revision 为准，访问权限以当前授权为准。

context 对 WorkItem 仍选择已承接生效的任务基线；对 Deliverable 选择当时已存在的提交 revision，并另附 `delivery_review` 与 `delivery_status`。尚未评审的提交返回 `delivery_review:null`、`delivery_status:submitted`；后续可返回该精确 revision 的退回/通过记录，不能拿 v2 的通过结果替 v1 盖章。提交事实可以进入记忆，不意味着交付已经通过。

Outcome 评估没有客户端指定的回填时间，其有效判断时间为服务端 recorded_at。历史 context 只有在评估 `recorded_at <= valid_at` 且 `recorded_at <= known_at` 时才投影该判断：评估前的业务时间或评估前的知识时间都不能读到后来发生的 `achieved`。

运行不变量：

1. Runtime 根据真实关系发现完整依赖集合，客户端必须通过 `expected_versions` 提供相应版本；缺失依赖或 prepare 后变化必须拒绝。
2. WorkItem/Deliverable 状态、revision、评审/评估记录、事件和 receipt 同事务提交；失败不留下半个提交或半个验收。
3. 相同 principal/scope/idempotency key 和请求重复返回原 receipt，不增加提交序号或评审次数；同 key 不同请求返回冲突。
4. Deliverable revision、DeliveryAcceptance、OutcomeAssessment 不允许通过通用 create/propose 路径绕过专用动作。
5. 新表保留 scope 复合引用约束、ENABLE/FORCE RLS、append-only 防修改约束及受限应用角色权限；评审不可复用 `gov_acceptances` 中绑定 MF 周期的记录。
6. 历史读取、回执重放和 context snapshot 重新检查当前权限；引用不可成为跨域读取入口。
7. 交付的数据库提交不会默认调外部系统。原 v0.1 承诺/调整的 outbox 行为不因本扩展被改写。

## 8. 独立验收要求与交付边界

正向验证必须使用真实 HTTP 身份、PostgreSQL 和版本化证据存储，完成派单、指定 DRI 承接、v1 提交、有权人退回、v2 回应上次退回、有权人逐项验收。不得预先写入成功交付、评审或评估记录作为通过证据。

至少覆盖以下可观察断言：

- v1、v2 同属一个 Deliverable，revision/hash 各自保留，序号递增，且能读出两次评审及其具体标准判断。
- 非指定 DRI、非指定验收人、相同自然人自验、agent 最终动作及撤权后的操作被拒绝。
- 未承接提交、重复提交、错误退回引用、旧 v1 复审、伪造 hash、缺失/多余标准、通过与失败标准矛盾被拒绝。
- 并发 CAS 与重复请求不会生成双提交或双验收；故障回滚不留下部分状态。
- 交付通过后，**Outcome 仍未评估，MF 仍未关闭**；继续独立记录 Outcome 未达成/达成，以及执行原 MF 验收/关闭，验证没有自动级联。
- 不同目标 revision 的评估不会串用；历史观测、证据、交付来源不会被最新内容替换。
- 未来/过期观测不得用于当前 Outcome 判断；双时间读取不能把后发生的达成评估投影到过去。
- 新表在受限角色下满足隔离与不可覆盖约束，并纳入数据库/证据备份恢复核验。
- 原 v0.1 独立验收和相关项目测试保持通过。

验收报告应区分代码/测试交付、本地 Runtime 独立验收、Clark 联调、远程部署与生产升级。当前任务只覆盖本 Runtime；不替应用伙伴修改 Clark，不自动推送、部署或授予旧 scope 新权限。

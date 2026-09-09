# 工作台读取 API（workbench v0.1）

本文档描述为四页工作台原型新增的七个只读接口及其与既有接口的页面映射。
实现位于 `src/memory_service_runtime/governed/workbench.py`（读取逻辑）与
`src/memory_service_runtime/governed/routes.py`（HTTP 接入），契约见
`contracts/openapi.json`。示例中的 `display_name`/`name` 为便于阅读的别名；
所有 id 字段在真实响应中均为服务端生成的 UUID。

## 共同边界

- 所有接口要求当前 Bearer 身份（`Authorization: Bearer <credential>`）；
  无有效凭据为 401 `UNAUTHENTICATED`。调用方身份、scope、assignment 全部由
  凭据在服务端解析，请求不能选择 actor/角色/租户。
- 所有新增读取响应设置 `Cache-Control: no-store`。
- 七个接口全部只读：不改变业务对象、权限、评审、回执或 Outbox。唯一的
  快照写入例外是**既有的** `POST /v1/context-packs`（见下文「业务只读与
  快照写入」），不属于本批新接口。
- 历史读取一律使用**当前**权限：撤权后旧 revision、旧 receipt、旧快照同样
  拒绝读取。
- 错误一律 `{error:{code,message}}`：401 `UNAUTHENTICATED`、403 `FORBIDDEN`、
  404 `NOT_FOUND`、422 `INVALID_REQUEST`。错误不含 SQL、凭据或外部响应。
- 无权对象统一 404 `NOT_FOUND`（不区分「不存在」与「不可读」）；调用者失去
  全部当前 assignment 时为 403。

## 分页与 cursor

- `limit`：1..100，默认 50。越界、非整数、重复参数、未知参数一律
  422 `INVALID_REQUEST`，不会落到 500。
- `cursor`：不透明字符串，最长 4096 字符。编码错误、结构错误、字段类型错误、
  与当前 endpoint/principal/scope/过滤参数（含 source revision）不匹配，
  一律 422 `INVALID_REQUEST`，不泄露失败原因。
- 固定 keyset 排序：对象按不可变 `created_at + object_id`；revision 按
  `recorded_at + revision_id`；receipt 按 `recorded_at + receipt_id`；
  domain 与 relations 按稳定 UUID 元组。不使用可变的 `updated_at`。
- `next_cursor` 仅在还有**可见**条目时返回；空页与末页为 `null`。授权过滤
  发生在分页语义内部：被过滤的行既不出现也不计入任何数量字段。
- cursor 是**未签名书签**，绑定 endpoint/身份/scope/过滤参数以防误复用，
  但不是不可篡改的审计凭据；每页都重新检查当前权限，书签本身不构成授权。
- 固定排序不代表跨请求事务快照：本期是实时遍历，不承诺并发插入或撤权期间
  的一致快照。

## 四页映射

| 页面 | 新增接口 | 既有接口 |
| --- | --- | --- |
| 对象与关系 | `GET /v1/object-types`、`GET /v1/domains`、`GET /v1/objects`、`GET /v1/objects/{id}/relations` | — |
| 实例详情 | `GET /v1/objects/{id}/revisions`、`GET /v1/objects/{id}/responsibility` | `GET /v1/objects/{id}`、`GET /v1/objects/{id}/revisions/{rid}` |
| 动作与回执 | `GET /v1/objects/{id}/action-receipts` | `GET /v1/action-receipts/{rid}` |
| Context Pack | — | `POST /v1/context-packs`、`GET /v1/context-packs/{sid}` |

## 1. `GET /v1/object-types`

静态类型目录。包含实际支持的 8 个通用创建类型（`generic_action`：
CompanyOutcome、BusinessCommitment、ExecutionCommitment、FeedbackThread、
ManagementAdjustment、Decision、MetricObservation、WorkItem）与两个专用类型
（`dedicated_action`：EvidenceAsset 经 `POST /v1/evidence-assets` 进入，
Deliverable 经 `submit_deliverable` 进入）。不含 Mission/Risk/Lesson。

- `payload_schema`：通用类型直接从真实 Pydantic 模型生成（
  `models.PAYLOAD_MODELS[t].model_json_schema()`）；两个专用类型没有公开
  创建 payload 模型，明确为 `null`。
- 目录是纯静态元数据：**不含**任何客户对象名称、计数或策略内容；类型说明
  中的动作语义**不代表**当前调用者拥有执行权限。

```json
{
  "schema_version": "workbench-read/0.1",
  "items": [
    {
      "object_type": "WorkItem",
      "label": "工作项",
      "creation_mode": "generic_action",
      "description": "冻结基线的 DRI 交付工作项；……",
      "reference_fields": ["execution_commitment_ref: 指向 ExecutionCommitment 的精确 revision", "……"],
      "payload_schema": {"title": "WorkItemPayload", "type": "object", "……": "……"},
      "versioning": "内容按不可变 revision 保存；latest_revision_id 是最新候选版本，effective_revision_id 是当前生效版本，两者可能指向不同 revision。"
    },
    {
      "object_type": "Deliverable",
      "label": "交付物",
      "creation_mode": "dedicated_action",
      "description": "只能经 submit_deliverable 进入的交付提交；……",
      "reference_fields": ["work_item_ref: 所属 WorkItem 的冻结 baseline revision", "……"],
      "payload_schema": null,
      "versioning": "……"
    }
  ]
}
```

## 2. `GET /v1/domains?limit=50&cursor=...`

只列出当前 assignment + 各域**最新 policy** 允许 `read` 的业务域；其余域
整条隐藏，不暴露其 ID/name/count。

```json
{
  "items": [{"domain_id": "<uuid>", "name": "示例：交付业务域"}],
  "next_cursor": null
}
```

## 3. `GET /v1/objects?domain_id=<uuid>&object_type=<可选>&limit=50&cursor=...`

- `domain_id` 必填；缺失、非 UUID、重复参数为 422。不可读、不存在或其他
  scope 的域统一 404 `NOT_FOUND`。
- `object_type` 必须是已知类型（8 通用 + EvidenceAsset + Deliverable），
  未知值 422 `INVALID_REQUEST`。不支持自由 SQL/JSON 过滤、任意排序，也不
  返回包含未授权对象的 total。
- `title` 取自 **latest revision** 的 payload 标题：这是发现列表，不代表
  该候选版本已生效。`latest_revision_id` 与 `effective_revision_id` 分开
  返回。不内嵌完整 payload/result/证据 locator。

```json
{
  "items": [
    {
      "object_id": "<uuid>", "domain_id": "<uuid>", "object_type": "WorkItem",
      "title": "示例：三季度交付验收报告", "lifecycle_status": "delivery_accepted",
      "object_version": 6, "latest_revision_id": "<uuid>",
      "effective_revision_id": "<uuid>", "created_at": "2026-09-01T08:00:00+00:00"
    }
  ],
  "next_cursor": "<opaque>"
}
```

## 4. `GET /v1/objects/{object_id}/revisions?limit=50&cursor=...`

revision 摘要列表（详情仍走既有 `GET /v1/objects/{id}/revisions/{rid}`）。
`object_version` 是该 revision 建立时写入的对象版本号，**不等同**当前对象
版本、R1 展示序号或 `submission_seq`。`is_latest`/`is_effective` 与对象当前
头指针实时比较得出。

```json
{
  "items": [
    {"revision_id": "<uuid>", "object_id": "<uuid>", "object_version": 1,
     "payload_hash": "<64-hex>", "recorded_at": "2026-09-01T08:00:00+00:00",
     "valid_from": "2026-09-01T08:00:00+00:00", "valid_to": null,
     "is_latest": true, "is_effective": false}
  ],
  "next_cursor": null
}
```

## 5. `GET /v1/objects/{object_id}/relations?revision_id=<可选>&limit=50&cursor=...`

只查询**指定 source revision** 的出向一跳来源引用。省略 `revision_id` 时选择
对象当前 latest revision；响应始终返回精确的 `source_ref`。

支持的 typed 字段集合（除此以外不递归猜测任意 JSON 中的 object_id）：

- `upstream_refs[]`、`execution_commitment_ref`、`feedback_ref`、`work_item_ref`
  （复用 `delivery.payload_references`）；
- ManagementAdjustment 的 `feedback_revision_id`、`decision_revision_id` 与
  `changes[].from_revision_id/to_revision_id`（仅含 revision 的引用在当前
  scope 内解析所属对象；无法解析的引用跳过）；
- Deliverable 的 `evidence_revision_ids` 在写入时已持久化进 `upstream_refs`，
  原始证据关系因此不会丢失。

同一 (object_id, revision_id) 的完全重复引用去重；同对象的不同 revision
各自保留。每个 target 对象+revision 都重新授权：无权的 target **整条边
隐藏**——不返回其 ID/title/hash，不返回隐藏数量，不返回错误细节。

不提供入向图查询。WorkItem → Deliverable 的当前业务关联继续来自既有对象
详情里的 `delivery` 投影，不混入历史 source revision。

```json
{
  "source_ref": {"object_id": "<uuid>", "revision_id": "<uuid>"},
  "items": [
    {"relation_type": "source_reference",
     "source_ref": {"object_id": "<uuid>", "revision_id": "<uuid>"},
     "target_ref": {"object_id": "<uuid>", "revision_id": "<uuid>"},
     "target_type": "EvidenceAsset"}
  ],
  "next_cursor": null
}
```

## 6. `GET /v1/objects/{object_id}/action-receipts?limit=50&cursor=...`

仅返回明确与该对象有关的 **committed** receipt 摘要。关联依据：
`target_object_id`、`object_versions[].object_id`、
`result.referenced_object_ids[]`。每个候选项都执行与单条读取完全相同的
`authorize_receipt`：只要任一引用当前不可读，**整条 receipt 隐藏**（不局部
删 payload 后返回，也不返回隐藏数量）。完整 result 走既有
`GET /v1/action-receipts/{id}`。

授权拒绝不产生持久化 receipt；`changes_requested` 是成功评审的正常业务
结果。目录不创建任何拒绝日志。

```json
{
  "items": [
    {"receipt_id": "<uuid>", "action_type": "review_deliverable",
     "actor_id": "<uuid>", "status": "committed",
     "recorded_at": "2026-09-02T03:10:00+00:00"}
  ],
  "next_cursor": null
}
```

## 7. `GET /v1/objects/{object_id}/responsibility`

把 WorkItem 冻结 baseline 中的 assignment 引用解析为可读责任信息，用于实例
页。**首期只支持 WorkItem**，其他类型 422 `INVALID_REQUEST`。调用者需要该
WorkItem 的当前读取权。

- 每项只含 `assignment_id`、`principal_id`、`display_name`、`role`、
  `domain_id`、`current_assignment_active`；数据从当前 scope 内真实关联
  （`gov_work_item_state` → `gov_role_assignments` → `gov_principals`）读取，
  不接受客户端 principal 或角色输入。
- `current_assignment_active` 同时考虑 principal active、assignment active
  与有效时间区间。**assignment active 不等于拥有 Action 执行权限**；本接口
  不提供全局人员目录、token、策略内容或「当前可执行动作」结论。被引用人仅
  作为本工作项 DRI/验收人的最小必要身份投影。

```json
{
  "object_id": "<uuid>",
  "baseline_revision_id": "<uuid>",
  "dri": {"assignment_id": "<uuid>", "principal_id": "<uuid>",
          "display_name": "示例：交付负责人", "role": "MISSION_DRI",
          "domain_id": "<uuid>", "current_assignment_active": true},
  "acceptor": {"assignment_id": "<uuid>", "principal_id": "<uuid>",
               "display_name": "示例：独立验收人", "role": "VERIFIER",
               "domain_id": "<uuid>", "current_assignment_active": true}
}
```

## Context Pack 页（既有接口，无新增）

Context Pack 页完全使用既有接口，本批没有为它新增端点。

请求：`POST /v1/context-packs`。

```json
{
  "object_ids": ["<uuid>", "<uuid>"],
  "valid_at": "2026-08-31T12:00:00Z",
  "known_at": "2026-09-02T03:00:00Z"
}
```

- `object_ids`：1..100 个互不重复的 UUID；每个对象按当前权限逐个授权。
  任一对象或其来源引用当前不可读时，整个请求失败，不会返回局部 Context Pack；
  `excluded` 用于记录时间截面内没有合适版本等情况，不用于绕过授权失败。
- 双时间截面：`valid_at` 是**业务有效时间**（选择在该时刻有效的 revision，
  依据 `valid_from`/`valid_to`），`known_at` 是**系统知悉时间**（只考虑
  `recorded_at <= known_at` 的记录）。两个时间独立作用：纠错后回读
  「当时已知的内容」用旧 `known_at`；未来生效的观测在早于其 `valid_from`
  的截面中被排除。
- 响应含 `context_snapshot_id` 与每个选中对象的**精确** `revision_id`、
  `payload`、`payload_hash`、`valid_from/valid_to` 和 `source_refs`（来源
  对象的精确 revision 与 hash）。这些是历史内容选择结果：未被选中的对象
  列入 `excluded` 并附原因。注意区分——`source_refs`/`payload_hash` 描述的是
  **冻结 revision 的内容来源**，而对象详情中的 `delivery`、
  `outcome_assessment` 等是**当前业务状态投影**（Context Pack 选中项里的
  `delivery_review`/`outcome_assessment` 同样按 valid/known 双时间过滤，
  不代表当前状态）；Context Pack 不提供任何聚合 snapshot hash。
- 原始证据字节不走 Context Pack：按 `source_refs` 中的证据 revision 经既有
  `GET /v1/evidence-assets/{object_id}/revisions/{revision_id}` 下载，服务端
  逐字节校验 hash。
- 该 POST 会持久化一条 append-only 审计快照（见「业务只读与快照写入」）；
  `GET /v1/context-packs/{snapshot_id}` 回读快照时对所有引用按**当前**权限
  重新授权，快照内容不绕过检查。

## 身份与版本边界

- 原型中的身份模拟**没有**成为生产 impersonation 接口；所有读取都以 Bearer
  凭据解析出的当前 principal/assignment 为准。
- 撤权即时生效：域级读权撤掉后，`GET /v1/domains` 仍返回 200，只是该域从
  列表中过滤消失；对该域的显式读取 `GET /v1/objects?domain_id=...` 才返回
  404 `NOT_FOUND`。relations 中目标被撤权的边整条隐藏，action-receipts 中
  含不可读引用的 receipt 整条隐藏。
- `latest` 与 `effective` 始终分开返回；候选 revision 的出现不代表生效。

## 业务只读与快照写入的区别

本批七个接口不产生任何持久化写入。既有 `POST /v1/context-packs` 会在返回
内容的同时保存一条**审计快照**（`gov_context_snapshots`，append-only），这是
契约显式允许的例外：快照不改变业务对象/权限/评审/回执/Outbox，读取快照
（`GET /v1/context-packs/{id}`）时仍对每个引用对象/证据按当前权限重新授权。

## 已实现与未接入界限

- 已实现：上述七个读取接口、静态类型目录（payload_schema 来自真实模型）、
  keyset 分页 + 绑定式 cursor、逐目标/逐 receipt 的当前权限重检、
  no-store 响应头、422/404/403/401 错误边界。
- 未接入/不承诺：入向关系图查询、跨请求一致性快照、自由过滤/排序、
  total 计数、人员目录、可执行动作结论、签名书签、Clark UI/BFF（由 Clark
  伙伴负责）。无库单元测试见 `tests/workbench/`；真实数据库+对象存储的
  端到端验收由 Codex 独立执行，演练数据生成入口见 `acceptance/workbench/`。

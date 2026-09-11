# Runtime A2：公司组合 API 精确映射（工程补充，不改变冻结契约）

日期：2026-09-10。状态：实现用接口规范（已并入 Codex interface review 14 条修正）。
语义 oracle 仍是冻结的契约包 A（`契约包A.md`、`独立验收契约.md` A2-01…A2-18、
`模拟策略与数据.md`、profile-core.json 及 schemas/ 五份 schema）。本文只把冻结语义
落到具体 HTTP 字段、JSON 形状、表名与登记命令；与冻结文本冲突时以冻结文本为准，
本文件不构成改契约/Profile 的授权。

协议身份：`protocol_id="tkos.contract-a"`、`contract_version="tkos.contract-a/0.1"`。
全部 6 个动作与 2 个受控输入写动作沿用现有 `POST /v1/actions` 与
`POST /v1/actions/prepare` 信封（`action_type / target / expected_versions /
idempotency_key / reason / params / contract_version`）。

## 0. 全局规则

### 0.1 信封与 contract_version

- 6 个 A2 动作与 A2 输入对象的 `create_object`/`propose_revision` 必须显式携带
  `contract_version="tkos.contract-a/0.1"`；缺省 → `PROTOCOL_UPGRADE_REQUIRED`(409)，
  写成 legacy 值同样 409（沿用 `_declared_mismatch` 冻结矩阵）。
- 未知 `action_type`（含 `withdraw_composition_confirmation`、profile 迁移、暂停、
  A3 动作名）在信封模型层即 422 `INVALID_REQUEST`；A2 不实现撤回/迁移/暂停，
  这些明确 `not_implemented`，不以任何其他动作伪装。
- 动作与对象类型同时受**编译白名单**（代码内常量）与 **registry 登记**双重控制
  （review 12）：编译集合不扩大，registry 不含即 409；A1 默认 Contract-A
  metadata-only registry 完全不变，无静默激活。
- 请求哈希沿用 `request.model_dump(mode="json", exclude_none=True)` +
  tkos-json-v1 SHA-256；幂等重放、IDEMPOTENCY_CONFLICT(409) 语义不变。
  重放只在**当前**读取权下返回原回执，不恢复任何现行授权。

### 0.2 公司身份与 Round 归属域（review 1）

- `gov_scopes.company_id` 是公司身份权威值。`open_formation_round.params.company_id`
  必须是规范小写 UUID（否则 422）且与 `ctx.company_id` **字符串完全相等**
  （否则 403）；scope 自身 company_id 非规范 UUID 时不能开 Round（422）。
  A2 专用验收 scope 以规范 UUID 作 company_id 供给；legacy bootstrap 不变。
- open 另带 **`company_domain_id`**（Round/Composition 的归属域，必须是本 scope
  真实存在的域）与 **`ceo_assignment_id`**（company 域中当前有效 CEO 任职的精确
  ID）。Round 对象落在 company 域；manifest 的 `company_decider` 签认槽绑定这个
  精确 assignment，不取“第一个成员域/第一条任职”。
- CEO 授权**逐参与域独立校验**：actor 必须在 Round 当前成员集合的每个域（含
  company 域）持有当前有效且被该域策略授权该动作的 CEO 任职。
- amend 时 CEO 必须同时控制**旧成员域与拟议新成员域**（不能靠移除域逃避授权）；
  合法的新任 CEO（当前有效任职）可以 amend——新任 CEO 槽变化本身是定义/输入
  变化，导致全体重签。不要求已失效的原创建人任职继续有效。

### 0.3 鉴权与多域授权（additive，不删旧 same_domain 守卫）

- 顺序不变：401 → 404/403 → 协议围栏（409 族）。
- 域授权仍走 `db.authorize_domain(...)`；新动作的 `action_roles` 条目由受控
  fixture/seed 写入 `gov_activation_policies.content`（初始化允许身份/任职/策略，
  不种子任何业务成功）。每个相关域当前策略必须列出该动作，缺任一即 403。
- `publish_domain_submission`：actor 必须是 Round 当前定义为该域指定的 DRI 本人
  （principal+assignment 精确匹配、当前有效）。
- `confirm_company_composition`：actor 必须是 manifest `required_signers[]` 本人；
  params 显式携带 `assignment_id`，与 actor 身份和该槽位精确 assignment 三方一致，
  否则 403（review 4）。
- 全部 6 个动作 `principal_type` 必须 `human`（agent 403，并入 HUMAN_ACTIONS）。
- CEO＋各成员域指定 DRI 必须是不同自然人；同人两槽在 form 时即拒绝（对齐冻结
  校验器 C02），不靠激活时才发现。

### 0.4 CAS、代次与 scope 栅栏（review 11）

- scope 栅栏沿用：`db.authenticate` 的 `gov_scopes ... FOR UPDATE` 在事务开始即
  序列化同 scope 全部冲突写（amend/publish/confirm/activate/受控来源改版/撤权），
  满足 A2-14/A2-15 两种锁序；激活在持有栅栏后重读完整集合与当前授权。
- 对象头 CAS 沿用 `gov_objects.object_version`。
- Round 另有 `member_set_version`/`input_set_version` 代次。form 与 activate 的
  params 显式携带两个代次预期值（不符 → 409 `COMPOSITION_INPUT_CHANGED`）；
  **confirm 不带代次参数**——confirm 以 `composition_ref` 精确三元组 +
  `expected_versions` 中的 Round 头 CAS 为准，代次当前性由 manifest 内记录的
  代次与 Round 当前代次比较完成。
- `prepare` 只在短事务栅栏内解析当时可见依赖与版本，**不持有任何延伸到
  execute 的锁**，不产生回执；正式请求重新检查。prepare 对 A2 动作返回目标
  CAS＋依赖版本清单（Round、候选 Composition、当前正式 Submission 对象、
  已发布来源对象的精确引用），且不暴露调用方无权读取的私有来源 ID（§6）。

### 0.5 新错误码（其余沿用现有冻结矩阵）

| code | HTTP | 触发 |
| --- | --- | --- |
| `COMPOSITION_INPUT_CHANGED` | 409 | 成员/正式提交/binding 来源/代次不等于 manifest 或请求预期；绑定来源已过期或不再是当前 effective |
| `COMPOSITION_NOT_READY` | 409 | 四项判断非全 pass、确定性容量冲突、声明的未决硬冲突、闭包超限/成环 |
| `CONFIRMATION_INCOMPLETE` | 409 | 激活时签认集合未精确覆盖或签认人资格已失效 |

## 1. 新对象类型与受控输入类型

`gov_objects.object_type` CHECK 新增 7 类（migration 0019）：

| object_type | 创建路径 | 生命周期 | 说明 |
| --- | --- | --- | --- |
| `CompanyReference` | 通用 `create_object`（受控输入，§5） | `recorded` | 公司正式 Reference；改版走 `propose_revision` |
| `CapacityObservation` | 通用 `create_object`（受控输入） | `recorded` | 容量来源观察；改版走 `propose_revision` |
| `FormationRound` | `open_formation_round` 内部创建 | `open`→`active` | 禁通用 create/propose/activate |
| `DomainSubmission` | `publish_domain_submission` 内部创建 | `submitted` | 不可变 revision 追加；禁通用写 |
| `CompanyComposition` | `form_company_composition` 内部创建 | `formed`→`active` | manifest 不可变；仅头部/进度可变 |
| `Mission` | `publish_domain_submission` 派生 | `proposed`→`active` | 服务端稳定身份；禁通用写 |
| `DomainCommitment` | `activate_company_composition` 派生 | `active` | 生效时建立 |

`lifecycle_status` CHECK 新增 `'formed'`；`'submitted'` 自 0017（Deliverable）已存在，
DomainSubmission 直接复用。旧协议 registry 的
`object_types` 不含上述类型；legacy 动作落在 A2 对象上被 `gate_target_action`
拒绝（A1 围栏语义不动）。通用 `create_object`/`propose_revision` 对 Contract-A
**只**放行 `CompanyReference`/`CapacityObservation` 两个输入类型（review 12），
其余 5 类 409 `ACTION_NOT_SUPPORTED_FOR_PROTOCOL`。

## 2. 表设计（migration 0019，全部 ENABLE+FORCE RLS）

沿用 0016/0018：不写角色、不 GRANT；`gov_require_runtime_capability()` 触发器
覆盖全部新表 INSERT/UPDATE；append-only 表加 `gov_reject_mutation()`；全部带
`scope_id` 供独立 oracle 扫描；跨表引用一律带 scope 的复合 FK。

| 表 | 可变性 | 关键列与约束 |
| --- | --- | --- |
| `gov_formation_round_state` | 可变头（无 DELETE） | `object_id` PK→gov_objects；`scope_id`；`company_id uuid`；`company_domain_id`→gov_domains；`period_id uuid`；`member_set_version int>=1`；`input_set_version int>=1`；`activated_composition_object_id/revision_id` 可空→gov_objects/gov_object_revisions；`activated_at`；`updated_at`。`UNIQUE(scope_id, company_id, period_id)` —— 同公司同周期唯一 Round，第二个在创建处拒绝 |
| `gov_round_formal_submissions` | 可变指针（无 DELETE） | `PRIMARY KEY(scope_id, round_object_id, domain_id)`；`submission_object_id`＋`submission_revision_id` 复合 FK→gov_object_revisions；`published_by_principal_id`＋`published_by_assignment_id` 复合 FK→gov_role_assignments；`action_id`→gov_action_receipts DEFERRABLE；`published_at` |
| `gov_composition_confirmations` | append-only | `confirmation_id` PK；`composition_object_id`＋`composition_revision_id` 复合 FK→gov_object_revisions；`manifest_hash ~ ^[0-9a-f]{64}$`；`principal_id`＋`assignment_id` 复合 FK→gov_role_assignments；`responsibility_role IN ('company_decider','area_accountable')`；`confirmation_statement`；`action_id` DEFERRABLE；`recorded_at`。**双唯一**：`(scope_id, composition_object_id, composition_revision_id, assignment_id)` 与 `(scope_id, composition_object_id, composition_revision_id, principal_id)`（review 4：同一自然人不得借另一任职获得重复票权） |
| `gov_mission_index` | append-only | `PRIMARY KEY(scope_id, round_object_id, domain_id, mission_key)` → `mission_object_id`→gov_objects；Round/域/mission_key 的稳定 Mission 身份；正式重提只追加新 revision。注意：本表是身份索引，**不是**激活清单（§3.6） |
| `gov_activation_records` | append-only | `activation_id` PK；`UNIQUE(scope_id, round_object_id)`；`composition_object_id`＋`composition_revision_id` 复合 FK；`manifest_hash`；`member_set_version`/`input_set_version >=1`；`detail jsonb`（DomainCommitment/Mission 生效映射）；`action_id` DEFERRABLE；`activated_at` 默认 `clock_timestamp()` |

定义性内容（成员集合、期间、Profile、Reference 采用版本、Submission 内容、
manifest）全部进 `gov_object_revisions` 不可变 payload；上表只放可变头/指针与
追加记录。Round 定义改版 = 新 revision＋`member_set_version` 递增；每次正式提交/
成员/Reference 采用变化 = `input_set_version` 递增。

### 检查点

新增 `checkpoints.checkpoint("after_first_member_activation", {...})`：激活事务中
第一个成员 DomainCommitment 生效指针写入之后、其余成员与回执之前（A2-16 注入
回滚点）。现有 `before_business_commit`、`auth_fence_acquired` 保持不变。

## 3. 六个动作：params 与 result 精确形状

所有 params 模型 `extra="forbid"`、严格类型（`StrictInt` 拒绝 bool/float/str；
hash 字段必须 64 位小写 hex；进入 hash 的 UUID 必须规范小写）。示例 UUID 仅为
形状示例。每个成功 result 附 `governance` 元数据（review 14）：

```json
"governance": {
  "contract_version": "tkos.contract-a/0.1",
  "method_profile_ref": {"profile_id": "...", "revision": "0.1.0", "canonical_hash": "<64 hex>"},
  "actor_assignment_ids": ["<uuid>"],
  "dependency_versions": [{"object_id": "<uuid>", "object_version": 3}]
}
```

`governance` 不携带任何调用方无权读取的私有来源/证据 ID。

### 3.1 `open_formation_round`（CEO；target=null）

```json
{
  "action_type": "open_formation_round",
  "target": null,
  "expected_versions": [],
  "idempotency_key": "open-round-p01-0001",
  "reason": "open P01 formation round",
  "contract_version": "tkos.contract-a/0.1",
  "params": {
    "company_id": "11111111-1111-4111-8111-111111111111",
    "company_domain_id": "<company 域 uuid>",
    "ceo_assignment_id": "<company 域当前 CEO 任职 uuid>",
    "period_id": "22222222-2222-4222-8222-222222222222",
    "period_window": {"start": "2026-10-01T00:00:00+08:00", "end": "2026-10-15T00:00:00+08:00"},
    "method_profile_ref": {
      "profile_id": "urn:tkos:experimental:method-profile:contract-a",
      "revision": "0.1.0"
    },
    "company_reference_ref": {"object_id": "<uuid>", "revision_id": "<uuid>", "payload_hash": "<64 hex>"},
    "members": [
      {"domain_id": "<uuid-A>", "dri_assignment_id": "<uuid>"},
      {"domain_id": "<uuid-B>", "dri_assignment_id": "<uuid>"}
    ]
  }
}
```

规则：`company_id` 按 §0.2；`company_domain_id` 真实存在且 actor 在其中持有
`ceo_assignment_id` 指定的当前有效 CEO 任职（principal_type=human）；CEO 对
company 域与每个成员域逐域授权；`method_profile_ref` 等于相关域当前协议策略
默认 Profile 且已安装、hash 匹配；`company_reference_ref` 指向当前 effective
`CompanyReference` 精确三元组（hash 现场复算）且其发布授权覆盖全部当前必需
参与域（§6）；members ≥1、domain_id 唯一、每域在本 scope、每个
`dri_assignment_id` 是该域当前有效 DOMAIN_DRI 任职、DRI principal 互不相同且
均非 CEO 本人。同 `(company_id, period_id)` 已有 Round → 409 `INVALID_STATE`。
效果：创建 `FormationRound`（status `open`，归属 company 域，定义 revision v1，
payload 含 company/period/window/profile 精确引用/成员含 dri_principal_id/
member_set_version）、`gov_formation_round_state`(1,1)、绑定、事件、回执。
result：

```json
{
  "round_object_id": "<uuid>", "round_revision_id": "<uuid>",
  "company_id": "<uuid>", "company_domain_id": "<uuid>", "period_id": "<uuid>",
  "member_set_version": 1, "input_set_version": 1,
  "members": [{"domain_id": "<uuid>", "dri_assignment_id": "<uuid>", "dri_principal_id": "<uuid>"}],
  "domain_id": "<company 域>", "referenced_object_ids": ["..."], "governance": {}
}
```

### 3.2 `amend_formation_round`（CEO；target=Round CAS）

```json
{
  "action_type": "amend_formation_round",
  "target": {"object_id": "<round>", "revision_id": "<round 当前 latest>", "expected_version": 3},
  "expected_versions": [{"object_id": "<新 reference>", "expected_version": 2}],
  "idempotency_key": "amend-round-0001",
  "reason": "add domain C to the formation",
  "contract_version": "tkos.contract-a/0.1",
  "params": {
    "expected_member_set_version": 1,
    "members": [
      {"domain_id": "<uuid-A>", "dri_assignment_id": "<uuid>"},
      {"domain_id": "<uuid-B>", "dri_assignment_id": "<uuid>"},
      {"domain_id": "<uuid-C>", "dri_assignment_id": "<uuid>"}
    ],
    "company_reference_ref": null,
    "change_reason": "add responsibility area C"
  }
}
```

规则（review 1/2）：Round 必须 `open`（已激活 → 409 `INVALID_STATE`）；
period/Profile 不可变（请求不含这两个字段，携带 → 422）；
`expected_member_set_version` 不符 → 409 `COMPOSITION_INPUT_CHANGED`；
`members` 与 `company_reference_ref` 均为可选，**至少一项实际变化**，全不变
（或与当前定义完全相同的纯 no-op）→ 422 `INVALID_REQUEST`；仅换 Reference、
成员不变是合法 amend。CEO 必须同时控制旧成员域与拟议新成员域；合法新任 CEO
可以 amend，其新 `ceo_assignment_id` 由当前任职解析并写入新定义。
效果：新定义 revision（member_set_version+1）、input_set_version+1、头 CAS+1；
旧候选 Composition 失去生效资格；旧 Submission/签认历史保留，**被移除域的
Submission 与 Mission 保持历史记录**（不删、不激活，§3.6 的选择规则保证它们
永不生效）；不补造新成员提交。
result：`{"round_object_id","round_revision_id","member_set_version":2,
"input_set_version":n,"members":[...],"governance":{}}`。

### 3.3 `publish_domain_submission`（本域指定 DRI 本人；target=Round CAS）
```json
{
  "action_type": "publish_domain_submission",
  "target": {"object_id": "<round>", "revision_id": "<round 当前 latest>", "expected_version": 3},
  "expected_versions": [{"object_id": "<capacity 来源对象>", "expected_version": 2}],
  "idempotency_key": "publish-b-r1-0001",
  "reason": "formal submission for domain B",
  "contract_version": "tkos.contract-a/0.1",
  "params": {
    "domain_id": "<uuid-B>",
    "dri_assignment_id": "<uuid>",
    "draft_checkpoint": "local-draft-2026-10-02T10:00",
    "commitment_statement": "本人以当前指定 DRI 身份正式提交并确认本包内容",
    "submission": {
      "result_statement": "本周期支撑 3 家试点客户上线",
      "pdo": {"pdo_key": "pdo-b-1", "statement": "...", "result_criteria": [
        {"criterion_id": "cap-ok", "description": "3 家按期完成 onboarding"}]},
      "missions": [{
        "mission_key": "b-onboarding",
        "result_statement": "交付 SSO 与上线能力",
        "boundary": "不含客户侧数据迁移",
        "acceptance_criteria": [{"criterion_id": "m1", "description": "..."}],
        "dependency_refs": []
      }],
      "resources": [{
        "resource_id": "<pool uuid>", "period_id": "<period uuid>",
        "unit": "synthetic_onboarding_slot", "required": 3
      }],
      "bindings": [{
        "relation_type": "resource_capacity",
        "source_ref": {"object_id": "<CapacityObservation>", "revision_id": "<rev>", "payload_hash": "<64 hex>"},
        "resource_id": "<pool uuid>", "period_id": "<period uuid>",
        "unit": "synthetic_onboarding_slot"
      }],
      "upstream_refs": [],
      "unknowns": []
    }
  }
}
```

规则：Round `open`；`domain_id`+`dri_assignment_id` 等于 Round 当前定义中该域
指定任职，actor 即本人且当前有效。
**需求与 binding（review 5）**：`resources[].required` 是唯一需求量（非负严格
整数；bool/float/string 422）；`bindings[]` 只标识精确来源/资源/周期/单位，
**不含**第二个需求量；单份 Submission 内 `resources[]` 与 `bindings[]` 各自
池键 `(resource_id, period_id, unit)` 唯一（同一 Submission 内禁止重复池
键），但**不再要求**单份 Submission 内两份池键一一对应——每个独立域可只发
布 demand-only（仅 `resources[]`，声明需求）或 provider-only（仅
`bindings[]`，声明对一份 `CapacityObservation` 的供应绑定）。完整池覆盖与
每个池键唯一一致的 binding（`required = Σ required`、`available =
available − reserved`、每个池恰好一条 binding 指向当时当前 effective 来源）
由 service 层在 `form_company_composition` 跨全部当前有效正式 Submission 汇
总强制：缺池键 / 重复池键 / 多绑定或漏绑定均在 form/activate 时 409。典型
场景：A 域 demand-only（`required=3`）、B 域 provider-only（无 `required`）、
对应容量来源 `CapacityObservation` 当前 effective 且 `available−reserved≥3`。

> **重复 / 聚合 / 矛盾 source 引用（澄清；review 5/7）**：
> - 同一份 Submission 内 `resources[]`、`bindings[]`、`upstream_refs[]`、
>   `missions[].dependency_refs[]` 各自保持严格唯一池键 / 唯一引用；**同一份
>   Submission 内重复**的源定义或池键一律拒绝。
> - 跨多个域的完整正式 Submission 集合在 form/activate 阶段由 service 端现场
>   汇总：多个域对同一 `(resource_id, period_id, unit)` 池的需求量**累加**
>   为 `Σ required`；一份 binding 缺失或重复则 409 `COMPOSITION_NOT_READY`。
> - 多份 Submission 对**同一份精确三元组来源**（`object_id + revision_id +
>   payload_hash`）的引用若**完全一致**则允许折叠为同一条绑定（一致的 source
>   pool）；**矛盾**（同一对象的不同 revision 或不同 hash）必须由 service 在
>   form/activate 时显式拒绝，不允许后到的写入静默覆盖。
> - 没有强制要求每个成员域同时提供 demand 与 binding：demand-only 与
>   provider-only 各自独立成立；只要完整集合汇总后满足上述约束即可。
> - 此澄清仅重述 §3.3 的 demand-only / provider-only 拆分，不撤销独立
>   demand-only / provider-only 验收点。
**binding 语义（review 7）**：`bindings[].source_ref`、`upstream_refs[]` 与
`missions[].dependency_refs[]` 都是正式绑定，全部进入有界闭包（§4）；未被正式
引用的备注/文档可自由变化；被绑定来源的新 revision 即使只改备注也是新精确
输入，须重新采用。`source_ref` 必须是当前 effective、未过期且已发布给全部当前
必需参与域的来源（§4/§6）。
效果（单事务；review 9）：追加不可变 `DomainSubmission` revision（同一 Round 同
一域的正式重提 = 同一 Submission 对象的新 revision），**存储 payload 为
元数据＋结构化内容整体**：`{round_object_id, domain_id, dri_assignment_id,
dri_principal_id, draft_checkpoint, commitment_statement, submission: {...}}`——
本人身份/任职/草稿检查点/承诺声明随 payload 一起不可变持久化，payload_hash
覆盖以上全部、**不含**派生 Mission 的 hash（无循环）；为每个 `mission_key` 经
`gov_mission_index` 取得/创建稳定 Mission 对象并追加不可变 revision
（`origin_submission_ref`+`mission_key`）；更新正式提交指针；
input_set_version+1；Round 头 CAS+1；不激活组合。
result：`{"round_object_id","submission_object_id","submission_revision_id",
"payload_hash","domain_id","input_set_version","missions":[{"mission_key",
"mission_object_id","mission_revision_id"}],"governance":{}}`。
本地草稿改动不经过本动作即不改变正式输入集（A2-03）；`draft_checkpoint` 只作
记录字段。

### 3.4 `form_company_composition`（CEO；target=Round CAS）

```json
{
  "action_type": "form_company_composition",
  "target": {"object_id": "<round>", "revision_id": "<round 当前 latest>", "expected_version": 5},
  "expected_versions": [],
  "idempotency_key": "form-c1-0001",
  "reason": "form composition candidate C1",
  "contract_version": "tkos.contract-a/0.1",
  "params": {
    "expected_member_set_version": 1,
    "expected_input_set_version": 3,
    "judgments": {
      "coverage":    {"conclusion": "pass", "reason": "...", "evidence_refs": []},
      "coherence":   {"conclusion": "pass", "reason": "...", "evidence_refs": []},
      "feasibility": {"conclusion": "pass", "reason": "...", "evidence_refs": []},
      "tradeoff":    {"conclusion": "pass", "reason": "...", "evidence_refs": []}
    },
    "unresolved_conflicts": [{"summary": "B 域上线窗口未确认", "blocking": false}]
  }
}
```

规则：Round `open`；代次预期匹配；**服务端现场派生**当前完整必需成员集合与每域
最新正式 Submission（当前指针），不使用调用方子集或旧 prepare 集合——params
不含成员/提交清单，携带 → 422；缺一域正式提交 → 409 `COMPOSITION_INPUT_CHANGED`。
四项判断必填，`judge_principal_id` 由服务端记为 actor（CEO）。fail/unknown 候选
允许落库供审查，不拦截 form。**来源统一校验**：写入任何候选前，服务端对
`company_reference_ref`、全部正式 Submission 的绑定来源引用与四项判断的
`evidence_refs` 的并集统一重查（§4：受控输入类型、当前 effective、hash、
DB 时钟有效期、观察新鲜度、发布授权覆盖 company＋全部成员域；有界闭包
64 节点/深度 8，同节点重复是 DAG 不是环；私有 EvidenceAsset 不进入来源图，
绝不自动共享），任何一项不合格即拒绝，不静默过滤。
**未决冲突（review 10）**：`unresolved_conflicts` 不被静默忽略——原样写入候选
创建时的生命周期事件 detail（不可变、随共享读暴露，候选创建后不再变化）；
任何 `blocking=true` 或服务器判定的确定性容量冲突都使 confirm/activate 返回
`COMPOSITION_NOT_READY`；确定性冲突由服务器附加，不接受客户端自报代替。
票权永不跨候选继承，即使另一候选 manifest 字节完全相同。
manifest 构造（冻结 schema `tkos.composition-manifest/0.1`；排序/唯一性同冻结
校验器）：成员按 domain_id、签认人按 principal_id、依赖按服务端生成的
`dependency_id` 升序；`required_signers` = 1 个 `company_decider`（Round 定义中
的精确 CEO assignment）＋每成员域指定 DRI，全部不同自然人；
`binding_dependencies` 由全部正式 Submission 汇总：池键合并为一条，
`constraint.required` = 各提交 `resources[].required` 求和，
`constraint.available` = 来源当前 effective 的 `available - reserved`
（严格整数，reserved ≤ available，否则 409）；`constraint.period_id` 必须等于
Round period。
效果：创建 `CompanyComposition`（status `formed`，归属 company 域）＋不可变
manifest revision（`manifest_hash` = 剔除自身后 tkos-json-v1 SHA-256，不含签认
进度/回执/头 CAS）＋创建事件（detail 含 unresolved_conflicts 与确定性冲突）。
result：`{"composition_object_id","composition_revision_id","manifest_hash",
"member_set_version","input_set_version","required_signers":[...],
"binding_dependencies":[...],"readiness":{"judgments_all_pass":true,
"hard_conflicts":[]},"governance":{}}`。

### 3.5 `confirm_company_composition`（manifest 指定 CEO/DRI 本人；target=Composition 头 CAS）

```json
{
  "action_type": "confirm_company_composition",
  "target": {"object_id": "<composition>", "revision_id": "<manifest revision>", "expected_version": 1},
  "expected_versions": [{"object_id": "<round>", "expected_version": 5}],
  "idempotency_key": "confirm-c1-ceo-0001",
  "reason": "confirm composition C1 as CEO",
  "contract_version": "tkos.contract-a/0.1",
  "params": {
    "composition_ref": {"object_id": "<composition>", "revision_id": "<manifest revision>",
                        "manifest_hash": "<64 hex>"},
    "assignment_id": "<本人在 required_signers 中的精确 assignment>",
    "confirmation_statement": "本人已阅读完整 manifest 并以当前任职确认同一版本"
  }
}
```

规则（顺序）：候选当前性——Round 仍 `open`、Round 当前代次与 manifest 记录代次
相等、当前完整成员/正式提交/binding 来源与 manifest 逐项相等，否则 409
`COMPOSITION_INPUT_CHANGED`（ABA：数值回到 3 也不复活旧 manifest，精确
revision/hash 不同即新输入）；来源发布授权与新鲜度重查（§4/§6）。准备门槛——
四项判断全 pass、无确定性容量冲突、无 blocking 未决冲突，否则 409
`COMPOSITION_NOT_READY`（**写票之前**）。签认人——actor 本人＋params
`assignment_id`＝该槽位精确 assignment 三方一致（review 4），assignment 当前
有效，`principal_type=human`；同 assignment 或同 principal 的重复票被拒
（409 `INVALID_STATE`；DB 双唯一约束兜底）。头 CAS 不符 → 409
`VERSION_CONFLICT`（A2-02：CEO 签后头部 +1，其余人用新 CAS 签同一
manifest_hash）。
效果：追加 `gov_composition_confirmations`；Composition 头 CAS+1（内容
revision 不变）。
result：`{"composition_object_id","composition_revision_id","manifest_hash",
"confirmation_id","signed_count":2,"required_count":3,"complete":false,
"governance":{}}`。

### 3.6 `activate_company_composition`（CEO；target=Composition 头 CAS）

```json
{
  "action_type": "activate_company_composition",
  "target": {"object_id": "<composition>", "revision_id": "<manifest revision>", "expected_version": 4},
  "expected_versions": [{"object_id": "<round>", "expected_version": 5}],
  "idempotency_key": "activate-c3-0001",
  "reason": "activate composition C3",
  "contract_version": "tkos.contract-a/0.1",
  "params": {
    "composition_ref": {"object_id": "<composition>", "revision_id": "<manifest revision>",
                        "manifest_hash": "<64 hex>"},
    "expected_member_set_version": 1,
    "expected_input_set_version": 5
  }
}
```

规则（持有 scope 栅栏后全部重查，A2-14/A2-15）：Round 当前且 `open`、代次匹配；
重算当前完整必需域集合与最新正式提交集合，与 manifest **集合相等**（新增 C 域
即使不改 A/B revision 也使旧 manifest 失效）；binding 来源当前性（§4：精确
revision 仍 effective、未过期、observed_at 在 TTL 内、发布授权仍覆盖全部当前
必需参与域）；容量重汇总仍满足；四项判断全 pass、无硬冲突；签认集合当前精确
覆盖 `required_signers`（principal+assignment+role+同一 manifest_hash，无缺无多；
缺一 → 409 `CONFIRMATION_INCOMPLETE`）；每个必需签认 assignment 与 CEO 授权在
**最终准入点**以 DB `clock_timestamp()` 重查（自然到期独立判断，不看
auth_epoch，A2-11；无关撤权只触发重查不强制重签，A2-10）。
**Mission 生效选择（review 3）**：只为 manifest 成员 `submission_ref` 精确指向的
那些 Submission revision 派生的 Mission revision 置生效——逐条核对
`gov_mission_index` 身份与 Mission revision 的 `origin_submission_ref` ==
manifest 成员 submission_ref（object_id+revision_id+payload_hash 三元组）后
才切换。绝不把 `gov_mission_index` 全表或已撤回/被移除成员的历史候选一并生效。
效果（单事务原子）：Round → `active`＋`activated_composition_*`；
`CompanyComposition` → `active`；每成员域创建 `DomainCommitment`（status
`active`，revision 引用 composition 精确三元组与该域 Submission 精确引用及本域
选中 Mission 精确引用）并置 effective；选中 Mission 置 `active`＋effective；
写 `gov_activation_records`（`activated_at`=DB 当前时钟）、事件、回执。
`after_first_member_activation` 检查点在第一个成员指针写入后触发。
**不**创建 WorkItem/WorkReceipt、不释放执行授权、不外发：`effect_task_ids=[]`。
result：`{"composition_object_id","composition_revision_id","manifest_hash",
"round_object_id","activation_id","activated_at","domain_commitments":[{"domain_id",
"object_id","revision_id"}],"missions":[{"mission_key","mission_object_id",
"effective_revision_id"}],"effect_task_ids":[],"governance":{}}`。

## 4. 来源采用、有效期与当前性（CompanyReference / CapacityObservation）

- 采用：`open` 的 `company_reference_ref` 与 Submission 的
  `bindings[].source_ref` 必须是该来源**当时当前 effective** 的精确三元组，
  且来源发布授权覆盖全部当前必需参与域（§6）；引用非当前版本 → 409
  `STALE_DEPENDENCY`。
- 替换：未激活 Round 的 Reference 替换只走 `amend_formation_round`；容量等来源
  的后续改版走 §5 受控 `propose_revision`（真实类型化入口，无 SQL 后门），随后
  须由相应 DRI 正式重提 Submission 才进入新 manifest。
- 绑定语义（review 7）：`upstream_refs`、`missions[].dependency_refs`、
  `bindings[].source_ref` 全部是绑定引用并进入有界闭包；未正式引用的材料变化
  不使 manifest 失效；被绑定来源的新 revision——即使只改备注——也是新精确
  输入，旧 manifest/Submission 引用在 confirm/activate 时 → 409
  `COMPOSITION_INPUT_CHANGED`（同值 ABA 不豁免，A2-04/A2-06）。
- 过期（review 8）：来源 revision 的 `valid_to` 已过（DB 当前时钟）→ 409
  `COMPOSITION_INPUT_CHANGED`，已过期 binding 不得准入。payload 的
  `valid_from/valid_to` 即 revision 的有效期来源（沿用 insert_revision 的派生
  规则，二者不会不一致；冲突即 422）。
- 新鲜度：`CapacityObservation.observed_at` 是显式观察时刻（不得是未来时刻，
  初始准入即 422），距 DB 当前时钟超过合成 TTL 86400 秒即过期；**初始准入与
  屏障后的最终准入都检查**有效期与新鲜度；未知/过期不取上一周期数值。
- 有界闭包（review 13）：从完整正式 Submission 集合出发按绑定引用计算传递闭包，
  上限 64 节点 / 深度 8；超限或发现环 → 409 `COMPOSITION_NOT_READY`，明确拒绝，
  绝不截断后按成功输出。环可由合法类型化输入产生（如两个 CapacityObservation
  互相引用），不需要 SQL 种入不可能图。
- 来源变化与撤权走同一 scope 栅栏；初始 Reference/容量观察由受控合成供给创建
  （`record_origin=synthetic`），之后一切变化走上述正式入口。
- 来源证据随 manifest/回执记录 actor/scope/时间/精确 ref。

## 5. 受控输入写（支持路径，不是额外审批动作）

仅两个类型对 Contract-A 开放通用写（review 12），且须 registry 已显式安装 A2
支持、对象绑定为 Contract-A、请求带 `contract_version="tkos.contract-a/0.1"`：

- `create_object` + `object_type="CompanyReference"`，payload：
  ```json
  {"title": "...", "statement": "...", "period_id": "<uuid>",
   "terms": {}, "shared_with_domain_ids": ["<uuid>", "..."],
   "upstream_refs": [{"object_id": "<uuid>", "revision_id": "<uuid>"}]}
  ```
  权限：actor 持有该域当前 CEO 任职。创建即 effective（status `recorded`）。
- `create_object`/`propose_revision` + `CapacityObservation`，payload：
  ```json
  {"title": "...", "resource_id": "<uuid>", "period_id": "<uuid>",
   "unit": "synthetic_onboarding_slot", "available": 3, "reserved": 0,
   "observed_at": "2026-10-01T09:00:00+08:00",
   "valid_from": "2026-10-01T00:00:00+08:00", "valid_to": null,
   "note": "", "shared_with_domain_ids": ["<uuid-A>"],
   "upstream_refs": []}
  ```
  权限：提供方域当前 CEO 或 DOMAIN_DRI。`available`/`reserved` 非负严格整数且
  `reserved <= available`；`propose_revision` 追加不可变 revision 并移动
  effective 指针。
- **`shared_with_domain_ids`（review 6）**：发布者显式选择的本 scope 域白名单；
  空 = 私有。正式 binding/Reference 被接受前，校验来源的发布授权覆盖**全部当前
  必需参与域**（company 域/CEO 走自身普通读权，不依赖发布）；confirm/activate
  时重查当前发布授权。来源的 `upstream_refs` 只允许指向这两个受控输入类型且
  同样已发布；任何 EvidencAsset/私有对象不得经来源图被共享。
- 其余 A2 对象类型在通用 create/propose 下 409
  `ACTION_NOT_SUPPORTED_FOR_PROTOCOL`；Mission 只由 §3.3 派生，DomainCommitment
  只由 §3.6 派生。
- 这些入口只写“事实来源”，不产生签认、候选或生效；legacy 行为完全不变。

## 6. 读取与共享披露（A2-18，review 6/14）

读仍走现有 GET 路径与 workbench 端点。新增 Contract-A 投影（不放宽任何现有
端点）：

- 基线：对象读取要求该对象 `domain_id` 当前 read 授权，否则 404。
- 共享投影（显式发布，替代盲签）：当前 Round 的**当前必需签认人**（Round 定义
  的 CEO assignment 本人＋成员域指定 DRI 本人）可读：该 Round 对象与定义
  revisions、当前正式 `DomainSubmission` 及其 revisions（Submission 发布即把
  结构化条款共享给 Round 参与者）、已 form 的 `CompanyComposition` 完整
  manifest、签认进度与候选创建事件中的未决冲突上下文、以及 manifest
  `company_reference_ref`/`binding_dependencies[].source_ref`/
  `members[].submission_ref` 精确指向的**已发布来源对象的指定 revision**。
- 发布授权收窄（review 6）：共享读只覆盖被精确引用且发布授权当前有效的**那个
  revision**；不自动暴露同一来源对象的全部历史/新版私有 revision。来源对象
  所属域的授权读者保留普通完整历史读。
- 不披露：域内 `EvidenceAsset` 原件与未被 manifest 引用的对象对跨域读者仍
  404；列表/relations/prepare/回执/重放不泄露隐藏对象 ID、名称、数量或隐含
  边；共享 payload 的 `upstream_refs` 只含已发布输入类型。
- Composition 读投影：`manifest`、`confirmations`（confirmation_id、
  principal/assignment、role、statement、recorded_at）、候选创建事件 detail 中的
  `unresolved_conflicts` / `deterministic_conflicts`（scope 限定、且事件记录的
  revision_id 必须与候选 manifest revision 精确一致），以及 `readiness`：
  `{"judgments_all_pass","static_conflict_reasons","hard_conflicts",
  "blocking_unresolved","basis":"at_form","requires_live_admission":true}`。
  readiness 是 **form 形成时**的复查记录（来源为不可变 manifest 与创建事件
  detail），不宣称候选当前可激活；confirm/activate 在最终准入点仍按 §3.5/§3.6
  实际重查代次、来源当前性/发布/新鲜度、容量汇总与签认覆盖。成员代次等当前
  状态见 FormationRound 读投影。
- FormationRound 读投影：定义、代次、当前正式提交指针、当前生效组合引用。
- **A2 回执读取（review 14，当前实现）**：回执重放授权一律按**当前**权限重核
  回执引用的全部材料——`target_object_id`（如有）、`object_versions[]` 每条
  （含其精确 revision）、`result.referenced_object_ids[]` 每个 id——任一当前
  不可读即整体 404；**无历史 actor_id 豁免**，被撤权/替换的签认人连自己写过的
  回执也不能重放。6 个 A2 组合动作的回执在此之上还要求 actor 当前持有该
  Round 的精确槽位（当前有效 assignment＋本人 principal＋角色/域一致，且该
  assignment 在 `authorize_domain` 当前允许集合内），或当前对 company 域有
  read 权且仍是 Round 定义记录的 CEO 本人；无 Round 锚点的来源
  create/propose 回执仅按上述全部引用重核放行。legacy 回执核对路径一字不改。

## 7. Registry 与供给命令

A2 支持必须显式安装；A1 的 Contract-A registry（只读元数据）保持不变，不存在
默认激活。安装顺序（控制面 CLI，`MIGRATION_DATABASE_URL` owner DSN）：

1. `install-profile`（既有命令）安装冻结 P1 profile-core。
2. 对参与域 `install-policy`：`default_protocol=tkos.contract-a`、
   `default_contract_version=tkos.contract-a/0.1`、`allow_legacy_create=false`、
   `record_origin=synthetic`、`default_profile_ref` 指向 P1。
3. `set-registry --protocol-id tkos.contract-a --contract-version tkos.contract-a/0.1`
   安装 A2 registry 内容（新 `registry_seq` 追加；规范 JSON 为
   `docs/runtime-a2-registry.json`，与代码内编译常量一致）：

```json
{
  "can_read": true, "can_create": true, "can_write": true, "evidence_upload": true,
  "actions": ["open_formation_round", "amend_formation_round",
              "publish_domain_submission", "form_company_composition",
              "confirm_company_composition", "activate_company_composition",
              "create_object", "propose_revision"],
  "object_types": ["CompanyReference", "CapacityObservation", "FormationRound",
                   "DomainSubmission", "CompanyComposition", "Mission",
                   "DomainCommitment"],
  "readonly_compat": ["tkos.contract-a/0.1"],
  "notes": "Contract-A A2 company composition support; generic create/propose limited to CompanyReference/CapacityObservation; actions/types also compiled-allowlisted."
}
```

registry 不含某动作/类型 → 409 `ACTION_NOT_SUPPORTED_FOR_PROTOCOL`；
`can_write=false` → 409 `PROTOCOL_WRITE_DISABLED`；编译白名单
`SUPPORTED_PROTOCOL_CONTRACTS` 不变。legacy 对象/动作/回执/history 完全不变。

## 8. 明确不支持（not_implemented，拒绝而非伪装）

- 签认撤回（A2-09）：无动作名；调用 422 `INVALID_REQUEST`，报告 `not_implemented`。
- Profile 迁移/改绑（A2-12）：通用 propose/amend 不能改绑 Round Profile；
  控制面 rebinding 不属于本包；`not_implemented`。
- 暂停/连续性指令、`BASELINE_SUSPENDED`：未实现，不走旧协议绕过。
- A3 全部动作语义：未实现；旧 `accept_commitment/activate_commitment` 对
  Contract-A 对象仍 409。
- 共享容量跨并发 Round 协调：一期直接拒绝同公司同周期第二 Round（唯一约束）。

# Runtime A1：方法协议登记与围栏

日期：2026-09-10。本文档描述契约包 A 的 A1 部分在 Runtime 的服务端实现。
范围仅为 A1：Profile 登记、协议归属、全入口围栏、旧程序栅栏、迁移回填、兼容。
公司组合（A2）与 IC 交接（A3）不实现；相关动作名在信封模型层即被拒绝（422）。

## 1. 协议身份与冻结常量

| 项 | 值 |
| --- | --- |
| legacy 协议对 | `tkos.legacy-governed` / `tkos.governed/v0.2` |
| Contract-A 协议对 | `tkos.contract-a` / `tkos.contract-a/0.1` |
| legacy Profile | `urn:tkos:legacy:governed-v0.2` rev `0.2.0`，canonical hash `93c5278a…182598` |
| legacy schema_version | `tkos.legacy-interpretation-record/0.2` |
| ProfileCore schema_version | `tkos.profile-core/0.1` |
| 主契约字节 SHA256 | `fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4` |
| 冻结 profile-core.json 文件 SHA256 | `2b7ec89bc489090f4d12d094d19ba49a52124425e094c6131d68c94488efe654` |
| 写能力 GUC | `app.runtime_write_capability='tkos-runtime-a1'` |
| 控制面 GUC | `app.gov_control_plane='on'`（且 session_user 必须真是数据库 owner/成员） |

常量集中在 `governed/profile.py`；规范化与摘要（tkos-json-v1）在 `governed/canon.py`。
冻结工件（profile-core.json、主契约精确字节）随包携带于
`governed/resources/`，由 `governed/artifacts.py` 装载并在装载时校验钉住 SHA256；
运行时不解析任何 Semantica 目录绝对路径。

## 2. 数据表（migration 0018，全部 ENABLE+FORCE RLS + append-only 触发器）

- `gov_method_profile_revisions`：每 scope 已安装 Profile/解释记录；(scope,profile_id,revision) 唯一，
  同 identity 不同内容由控制面拒绝（PROFILE_CONTENT_CONFLICT）。
- `gov_protocol_policies`：创建归属策略；`domain_id NULL` 表示 scope 默认，域级覆盖（部分唯一索引）。
- `gov_protocol_support_registry`：当前支持矩阵（含 `contract_version` 精确列），按 registry_seq 取最新。
- `gov_protocol_control_events`：控制面审计事件。
- `gov_object_protocol_bindings`：对象协议绑定，PK (scope_id,object_id,binding_version)，
  复合 FK 绑定 profile 三元组，receipt FK DEFERRABLE。

## 3. 服务端归属与门槛（governed/protocol.py）

协议归属只由服务端从绑定/策略/registry 决定，客户端不得自选中。
请求可选字段 `contract_version` 只声明客户端理解的动作格式；缺省/null 不改变
legacy request_hash（`model_dump(exclude_none=True)`，tests/protocol_a1 有 golden）。

门槛顺序：先完成 404/403 认证授权，再返回任何协议错误，协议细节不泄露对象存在性。
prepare 与 execute 共用本模块。

- `gate_target_action`：目标对象绑定存在 → registry 精确版本 + 编译白名单 →
  profile hash + implied 语义匹配 → Contract-A 目标的声明矩阵 → legacy 的
  can_write/actions 校验。
- `resolve_creation`：域策略优先、scope 默认兜底、两者皆无 fail closed；
  policy 的 default_profile_ref 必须语义属于策略协议（implied 匹配）；
  Contract-A 业务创建在 A1 一律 ACTION_NOT_SUPPORTED_FOR_PROTOCOL；
  evidence 上传走 `for_evidence` 分支只看 `evidence_upload`。
- `gate_dependency`：写入拉入的每个非目标对象必须与写入同协议，否则
  PROTOCOL_BINDING_CONFLICT。
- `gate_effect_dispatch`：worker 外发前按**回执不可变 action_type** 逐对象重验当前
  registry（can_write 与 actions 成员）；撤总开关或撤单动作都能挡住已排队任务（B04）。
- 读侧：`read_metadata`/`list_metadata` 只对 registry 精确版本匹配且 can_read +
  readonly_compat 命中的绑定报告解释身份；`require_read_support` 是硬门
  （registration_status 必须 registered 且 interpretation_status ∈
  {legacy_v0_2, contract_a_metadata_read_only}，否则 PROTOCOL_NOT_SUPPORTED 409）。
  未登记对象标记 unregistered/unsupported_unregistered，绝不默认猜成 legacy。

冻结错误矩阵（全部 409）：PROTOCOL_UPGRADE_REQUIRED、PROTOCOL_NOT_SUPPORTED、
METHOD_PROFILE_UNSUPPORTED、ACTION_NOT_SUPPORTED_FOR_PROTOCOL、
PROFILE_CONTENT_CONFLICT、PROTOCOL_POLICY_MISSING、PROTOCOL_BINDING_MISSING、
PROTOCOL_BINDING_CONFLICT、PROTOCOL_WRITE_DISABLED。

## 4. 旧程序栅栏（DB 级，migration 0018）

- 写围栏：`gov_require_runtime_capability()` BEFORE INSERT/UPDATE 触发器覆盖 11 张业务表；
  `runtime_tasks` 上仅对 `task_type='governance.dispatch'` 设 WHEN 触发器——不携带
  capability GUC 的旧 writer/worker 无法写治理状态或领取/入队派发任务（在 HTTP 外发之前被挡）。
- 读围栏：`gov_scopes/gov_principals/gov_role_assignments/gov_credentials` 四张身份表加
  `AS RESTRICTIVE FOR SELECT` 策略，要求 capability 或控制面；旧二进制读到空身份集，
  在任何 S3/HTTP 副作用之前停下。新版 `db.authenticate` 在首条 credentials 查询之前设置
  capability（B03）。
- 提交围栏：`gov_objects` DEFERRABLE 约束触发器要求每个对象提交时有绑定；
  `gov_binding_insert_gate` 在绑定插入时校验：version>1 必须控制面、version1 不得已有绑定、
  profile hash 匹配已安装行、且 profile 语义必须属于绑定协议（legacy 钉固定 identity；
  Contract-A 钉主契约 SHA；其他协议对拒绝）。全部判定用 `IS NOT TRUE`——
  JSON 缺字段/null 得到 SQL NULL 时拒绝而非放行（B08）。
- capability 只是旧程序兼容围栏：不授予任何权限，不是管理授权，也不是对抗应用角色
  任意 SQL 的安全边界。管理授权由 `gov_control_plane_on()` 独立判定（GUC + 真实 owner 双条件，
  函数总返回布尔，绝不 NULL）。

## 5. 迁移回填

0018 逐 scope 设 scope GUC 后以控制面身份回填：legacy 解释记录、scope 默认策略、
两条 registry（legacy 全处理器 / Contract-A 只读元数据）、每个既有对象的 version-1 legacy 绑定；
每 scope 一条审计事件（含 object_bindings_inserted/object_count）。不重写任何历史
payload/revision/receipt/event/hash。
幂等性准确表述：**migration runner 重放幂等**（schema_migrations 跳过已应用项）；
直接重跑原始 SQL 不保证逐句幂等（binding INSERT 触发器先于 ON CONFLICT）。

B02（version-1 绑定属于新创建事务）以联合不变量证明，不加 xmin：旧对象完整回填 +
新对象提交必须有绑定 + 绑定 append-only 不可删 + version1 唯一。独立验收逐项验证。

## 6. 控制面（governed/control.py，`tkos-governed-control`）

只读 `MIGRATION_DATABASE_URL`（owner DSN）；每个命令单事务，先设
control GUC + capability 并断言 `gov_control_plane_on()`，失败整体回滚；
输出 JSON（`{"ok": true, ...}` / `{"ok": false, "error": {code,message}}`，exit 2），
不打印 DSN/凭证。命令：install-profile（严格校验 + 契约 bytes 核对 +
钉住主契约 SHA + `pg_advisory_xact_lock(profile_id@revision)` 跨 scope 并发串行化 +
同内容幂等 no-op + 审计；--profile-json/--contract-file 缺省用包内冻结资源）、
install-policy、set-registry、freeze_writes（kill switch，保留 can_read）、
register-sentinel（ProtocolSentinel，无业务语义）、backfill-legacy（单 scope 只补无绑定对象）、status。

应用角色即使被历史脚本宽泛 GRANT，也只能 SELECT 控制面 4 表（acceptance/runtime/infra.py
REVOKE INSERT + 断言）；`WITH CHECK gov_control_plane_on()` 使非 owner 写入仍被拒。

## 7. 读投影与解释标识（A1-12）

- 对象 GET、revision GET、workbench objects/revisions/action-receipts/relations/responsibility
  响应附当前 protocol 元数据；revision 字节与 payload_hash 不动。
- context_pack/context_snapshot/responsibility/narrative 旧解释分支只在
  interpretation_status == legacy_v0_2 时运行；Contract-A 对象是
  metadata_read_only，不复用旧 DRI/acceptor 语义；不可解释对象显式 excluded
  （reason + protocol 身份），不猜成旧含义。
- Narrative 第二事务对每个 fact 与 source 重验当前权限、精确 revision、read support，
  且要求 interpretation_status 仍为 legacy_v0_2（B09）：两事务之间合法的
  控制面改绑为可读 A metadata 也得到 409 NARRATIVE_SOURCE_CHANGED，不返回旧含义事实。
- **B10 legacy 派生解释收口**：`protocol.require_legacy_read_support` 在
  `require_read_support` 之上要求 interpretation_status 恰为 legacy_v0_2，
  否则 409 PROTOCOL_NOT_SUPPORTED。接线点：context_snapshot 存储 selected 项与
  其 delivery_review 的 WorkItem；`delivery.read_delivery_review` 的 review WorkItem；
  `delivery.read_outcome_assessment` 的 observation/evidence 解析对象与每个
  acceptance 的 work_item/deliverable 业务参与对象。明确**不**推广到：原始
  object/revision metadata GET、一般 source_reference 边、不可变 receipt/重放、
  Evidence 原件按 A 元数据权限的下载。Narrative 的 NARRATIVE_SOURCE_CHANGED/409
  矩阵保持不变。
- Context Pack 的 selected 与**全部** excluded 分支（含 no_effective 与
  source_not_known）均附本轮已读取的 protocol 身份；旧存储 snapshot 字节不改写。

## 8. 切换前置与边界

- 切换前停旧 writer/worker 并静默排空在途任务；不宣称追回已在途副作用。
- A1 只做 Contract-A 的登记与元数据/读支持；A2/A3 业务创建与写动作拒绝，
  不造成功签认做演示。
- 认证/授权边界（401/403/404）不变且在协议错误之前。

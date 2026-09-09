# 本体与记忆工作台 v0.1：Runtime 实现任务书

Owner: Codex（契约与独立验收）。Implementer: Kimi Code。
Base: c977da9588f48f08b7f76b6828e75e0b02576076。状态：已实现并通过本机工作台读取独立验收，未发布。验收记录见 [workbench-read-v0.1-20260909.md](acceptance/workbench-read-v0.1-20260909.md)。

## 目标与边界

已确认四页原型：对象与关系、实例详情、动作与回执、Context Pack。实现这些页面所缺的 Runtime 读取能力，并交付接口对照表与可重复的演练数据生成方法。Clark UI/BFF 由 Clark 伙伴负责，不修改 Clark。

复用已有 GET 对象详情、指定 revision、单条 receipt、证据原件，以及 POST/GET Context Pack。新接口必须使用现有 db.transaction(token) / 当前策略授权。不要改动治理写路径、身份模型、现有读接口语义或数据库 schema。不得新增生产依赖。

本期“只读”不改变业务对象、权限、评审、回执或 Outbox。已有 POST /context-packs 保存审计快照是显式例外，文档必须说明。原型的身份模拟不能成为生产 impersonation 接口。

## 冻结的新增接口

所有接口都需要当前 Bearer 身份。新增读取响应设置 Cache-Control: no-store。路径为以下七个；若发现与实际模型冲突先在交付说明报告，不擅自改路径。

1. `GET /v1/object-types`
   - 返回 `schema_version`、`items`。包括实际支持的 8 个通用创建类型与专用 `EvidenceAsset`、`Deliverable`；不加入 Mission/Risk/Lesson。
   - 类型项包含 `object_type`、中文 `label`、`creation_mode`（generic_action/dedicated_action）、`description`、`payload_schema`（可用时直接从真实模型生成，否则明确 null）、静态关系/版本说明。不要把静态类型动作说明声称为当前用户可执行权限。
   - 不含任何客户对象名称、计数或策略内容。

2. `GET /v1/domains?limit=50&cursor=...`
   - `items:[{domain_id,name}]`、`next_cursor`。只列当前 assignment + 最新 policy 允许 read 的业务域。不得暴露被过滤域的 ID/name/count。

3. `GET /v1/objects?domain_id=<uuid>&object_type=<optional>&limit=50&cursor=...`
   - domain_id 必填。指定不可读或其他 scope 的域统一 NOT_FOUND。
   - 返回摘要：object_id/domain_id/object_type/title/lifecycle_status/object_version/latest_revision_id/effective_revision_id/created_at，及 next_cursor。
   - title 明确取 latest revision 的标题，是发现列表，不代表该候选版本已生效。不得内嵌完整 payload/result/证据 locator。latest/effective 必须分开。
   - object_type 为实际已知类型，未知值 INVALID_REQUEST。不增加自由 SQL / JSON / arbitrary sort / 包含未授权对象的 total。

4. `GET /v1/objects/{object_id}/revisions?limit=50&cursor=...`
   - 摘要 items 包含 revision_id/object_id/object_version/payload_hash/recorded_at/valid_from/valid_to/is_latest/is_effective。详情仍走既有指定 revision API。
   - object_version 此处是 revision 建立时的对象版本，不等同当前对象版本、R1 展示序号或 submission_seq。

5. `GET /v1/objects/{object_id}/relations?revision_id=<optional>&limit=50&cursor=...`
   - 明确只查询指定 source revision 的出向一跳来源引用；省略 revision_id 时选择 latest，并返回精确 source_ref。
   - 返回 source_ref、`items:[{relation_type:"source_reference",source_ref,target_ref,target_type}]`、next_cursor。
   - 依据已定义 typed reference 字段，不递归猜测任意 JSON 中的 object_id；同对象不同 revision 保留，完全相同引用去重。
   - 优先复用 delivery.payload_references；审查 ManagementAdjustment 的 typed adjustment refs 等实际模型，文档明确支持的字段集合。Deliverable 的 evidence_revision_ids 已持久化对应 upstream_refs，原始证据关系不可丢失。
   - 每个 target object + revision 重新授权；无权 target 整条边隐藏，不能返回其 ID/title/hash、隐藏数量或错误细节。
   - 不声称已提供入向图查询。WorkItem → Deliverable 的当前业务关联继续来自现有对象详情 delivery 投影，不混入历史 source revision。

6. `GET /v1/objects/{object_id}/action-receipts?limit=50&cursor=...`
   - 仅返回明确与对象有关的 committed receipt 摘要：receipt_id/action_type/actor_id/status/recorded_at。关联依据 target_object_id、object_versions、result.referenced_object_ids。
   - 每个候选调用与单条读取相同的 authorize_receipt；包含任一当前不可读引用时整条隐藏，不局部删 payload 后返回。
   - 不返回完整 result，详情沿用已有 GET /action-receipts/{id}。
   - 授权拒绝不是持久化 receipt；changes_requested 是成功评审的业务结果。目录不创建拒绝日志。

7. `GET /v1/objects/{object_id}/responsibility`
   - 用于实例页把冻结 assignment 引用解析为可读责任信息。首期只支持 WorkItem（其他类型 INVALID_REQUEST）。
   - 返回 object_id、baseline_revision_id、dri 和 acceptor；每项只含 assignment_id/principal_id/display_name/role/domain_id 以及 current_assignment_active 布尔值（同时考虑 principal active、assignment active/有效时间）。数据从当前 scope 内真实关联读取，不接受客户端 principal 或角色输入。
   - 不提供全局人员目录、token、策略内容或“当前可执行动作”结论；assignment active 不等于有 Action 权限。字段说明必须明确此点。
   - 调用者需要该 WorkItem 当前读取权；被引用人仅作为本工作项 DRI/验收人的最小必要身份投影。

## 分页与错误规则

- limit 1..100，默认 50。cursor 字符串限制长度（建议 <=4096）；严格拒绝错误编码/结构/类型/参数，返回标准 `INVALID_REQUEST` 422，而非 500。
- 固定 keyset 排序并文档化；对象按 immutable created_at + object_id，revision 按 recorded_at + revision_id，receipt 按 recorded_at + receipt_id，domain/relations 使用稳定 UUID 元组。不能用可变 updated_at 排序。
- next_cursor 仅在还有可见条目时返回；空页、末页为 null；授权过滤在分页语义中生效，不泄露被过滤行的数量。
- cursor 绑定 endpoint、principal、scope、过滤参数、source revision（适用时），防止误复用。书签不能作为授权凭据；每页均重新检查当前权限。若是无签名书签，文档说明不可作为不可篡改审计凭据。
- 固定排序不意味着跨请求事务快照；本期是实时遍历，不承诺并发插入/撤权期间的完整快照。所有新 query 参数采用 allowlist，未知参数/重复参数拒绝。
- 保持既有 401/403/404 边界。db.object_row 隐藏无权对象为 NOT_FOUND；失去所有 assignment 可为 403。保留其他域角色后，目标域撤权不得造成跨域泄露。

## 文档与演练数据

- 编写 `docs/workbench-read-api.md`：七个新增接口+既有接口的四页映射、JSON 样例（阅读别名不得伪装 UUID）、分页语义、错误、身份与版本边界、业务只读/快照写入区别、已实现/未接入界限。
- 添加 `acceptance/workbench/` 下的可运行演练生成入口：复用现有 Harness/Scenario/seed_scope，身份/最初起点可 bootstrap；承诺、交付、退回、再提交、验收必须通过真实 HTTP Action 产生。最终交付通过，Outcome 未评估，MF 跟进中。原始证据由 HTTP 上传。只输出脱敏对象/revision/receipt/证据 hash 标识；不输出凭据。
- 支持显式 run_id，新 run 使用隔离 synthetic scope；禁止覆盖旧业务数据、直接 seed 成功结果、访问生产服务。测试基础设施由 Codex 管理；你只实现入口，不启动/停止 Docker。
- 复用现有解释器 `/Users/yusiyi/ysy/tkos-ontology-runtime/.venv/bin/python`，运行时 PYTHONPATH 必须优先当前 worktree/src，防止测试错代码。可以做无数据库单测；真实数据库独立验收由 Codex 执行。

## 开发自测与交付

- 为新校验和读取行为加聚焦测试，特别是 current policy、分页授权过滤、恶意 cursor、精确 revision 关系和回执整体授权。
- 旧 tests/conftest.py 需要 DATABASE_URL；没有测试环境时如实记录未执行，不使用生产配置或伪造通过。可将无数据库测试置于 tests/workbench/ 并用 --confcutdir 避免无关数据库夹具。
- 修改后输出 git diff --stat、测试命令与真实结果、变更文件清单、尚未验证事项。更新 OpenAPI 契约时保证 import 指向当前代码且无生产服务访问。
- 禁止提交/推送、修改历史、部署、扩大 policy、创建正式身份、添加生产依赖、改 Clark、改治理 Action 来配合 UI。

Codex 会独立真实 HTTP 验收、源码审查并返修；Kimi 自测不等于最终验收。

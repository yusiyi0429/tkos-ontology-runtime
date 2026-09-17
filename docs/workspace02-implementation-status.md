# workspace/0.2（B2）实施状态 — 稳定检查点 3

更新：2026-09-17。最终交付报告见 [method-04-delivery-report.md](method-04-delivery-report.md)。最终独立验收（qa-final-workspace=64、qa-final-v03=35、qa-final-v04=29+39、qa-facade02=47；root final Python 920 total=919+1、UI 165/21）后，B2 保持稳定；迁移 replay 已 PASS，root 标记 source frozen（`index-CvwLgGIZ.js`）。不改来源 ACL。负责人：pi #2（B2 backend only）。代码在当前 worktree，未提交、未部署。

## 迁移状态（root 协调）

| 文件 | 状态 | sha256 |
| --- | --- | --- |
| `0026_workspace_sources_v02.sql` | **已应用**（隔离库 `tkos_a1_method_68416bb966de4479`，replay 为空） | `9f643a4fec4cde3d3ed0eb205996d153ed2c9e5353ecf04660f17cb6d43f0ef9` |
| `0028_workspace_v02_grant_repair.sql` | **已应用**（0026 授权镜像修复） | `6c6ccb898b7c421c9b53022adcae7b37020521b3a348d75996db5ac514c189cb` |

- 与 pi#3 的 `0028_method_v04_contract_repin.sql` 共享数字前缀 0028（文件名不同，两者均已应用）。**保留已应用文件名，不重命名、不改写已应用迁移**；0029 及以后编号由 root 分配。
- 迁移 replay（root）：fresh database create → 全部 migrations → replay 0 → drop，**1 passed**（`root-migration-replay-final.log`）；0029 及以后编号仍由 root 分配。
- `acceptance/workspace_v02/bootstrap.py` 验证：B2 迁移已应用、replay 为空、三张表 FORCE RLS 与不可变触发器、应用角色仅 `SELECT, INSERT`，且任何角色的 `SELECT`/`INSERT` 必须与其在 `gov_workspace_events` 上的同级权限一致（PUBLIC 除外）。0028 修复了 0026 可能把只读角色提升为写入者的镜像缺陷。

## B2 源码清单（sha256）

| 文件 | sha256 |
| --- | --- |
| `workspace_v02_collaboration.py` | `c9fb9a4722578c13fbc89586a16cb23167b24f34d9f0e83c479f14d1f021a1eb` |
| `workspace_v02_guard.py` | `3e4766bae531451fe75dc951731b7be33d027e3d1f626c54d8493c5fbcd5cda6` |
| `workspace_v02_models.py` | `87f9f8a317d6017ed636c55c537081d70fec76d622f4df187d5366eed5c987ea` |
| `workspace_v02_readers.py` | `4e357b08f67111905973b7ff081e646fa6e607482f2f2bc2d114b7fbadec0b03` |
| `workspace_v02_service.py` | `93b4375688353a8effd4f3e37c6bcde751ab7fc7ae6ab0cd8f633706576e4d71` |
| `acceptance/workspace_v02/run.py` | `47b1640e609b336e1b3c2bbd2602a5d9ab9f6d2a40e49a80e70693ab41dd21e2` |
| `docs/runtime-workspace-v02-openapi.json` | `0adf16a6ac50c0540c061cb41c29f4eaeadef622781ba04d97d7fb783a54ffb2` |

## method_access.py 最小集成补丁（共享文件，已报告 root）

两处函数内延迟 import，不改签名、不重写 Method 授权：

```python
# head_access() 开头（对象级读取）
from . import workspace_v02_guard
workspace_v02_guard.enforce_object(conn, ctx, str(object_id))

# revision() 开头（保留精确共享 revision 读取）
from . import workspace_v02_guard
shared = workspace_v02_guard.shared_revision(conn, ctx, str(object_id), str(revision_id))
if shared is not None:
    protocol.require_read_support(conn, ctx.scope_id, str(object_id))
    return raw_revision(conn, ctx, str(object_id), db._uuid(revision_id))
```

语义：链接到 0.2 私有来源的 EvidenceAsset 在 Method 内部读取（如 `m1a_record_signal` 的 `source_refs`）同样受围栏约束，域 read/角色不绕过；未链接对象行为不变；精确版本共享仍可读取被授 revision。Method 业务动作自身授权不变（B2 验收中 `method_action_still_requires_method_authority` 记录该边界）。

## 共享文件改动（additive，全部报告给 root/pi#3）

- `readers.py`：0.2 回执分派；`object_state`/`revision`/`context_pack`/`context_snapshot`/`action_receipt` 围栏钩子；精确共享 revision 读取路径。
- `routes.py`：`evidence_download` 围栏 + 精确共享下载；`context_create` 结果过滤；`/v1/workspace-sources` 加入 validation no-store allowlist。
- `dashboard.py`：`_visible_head` 围栏钩子。
- `method_access.py`：上述两处最小围栏钩子（共享文件，已报告 root/pi#3）。
- `tests/test_migrations.py`：期望迁移清单加入 0026/0027/两个 0028。
- `tests/protocol_a1/test_context_pack_excluded.py`：fake 连接识别 `gov_workspace_v02_assets` 空表查询（新表 schema 演进，不改变断言）。
- **未修改** `method_access.py`。给 pi#3 的待保留钩子：`method_access.head_access`/`revision` 直接调用方（非 dashboard 的其它消费者）应调用 `workspace_v02_guard.enforce_object`。

## 验证结果（自我 vs root 独立）

- **root 独立（最终）**：`qa-final-workspace` = **64 checks passed**（含 internal Method ref fence、Agent-created Context/run、creator-purpose revoke、operation privacy、receipt 修复、member display）；旧轮 qa-workspace-03=60 为过程证据，最终以 64 为准。
- **本机自我**：本次集成后 `acceptance/workspace_v02/run.py` **64 checks passed**（`artifacts/workspace_v02_run_17/`），新增 `scene_member_display_names_bounded_display_only`、`deactivated_member_name_explicit_not_current`、`deactivated_agent_member_status_explicit`、`synthetic_run_model_is_controlled_fixture`；run_16 为 63 checks（member 投影，模型修正前），run_15 因断言用了错误 status 枚举在最后一个新检查失败，代码/ACL 未变，产物保留。
- 单元/契约：`tests/test_workspace_v02*.py` **34 passed**；上一次完整套件 **881 passed / 16 skipped / 0 failed**。
- 独立 HTTP+PG+MinIO 来源导入：main-transcript 299 segments/4 speakers；followup-document 59 segments/24 speakers、format=markup、document_id 与 revision 3 保留；`acquired_at_basis=local-copy-file-mtime`；`evidence_bytes_exposed=false`；transcript **leak-free**。
- 旧 v0.3 HTTP（自我）：`artifacts/anchors_v03_regress_3/` **35 checks passed**（含 `source_unchanged`）；最终 freeze 后的旧协议回归由 root 执行。
- 迁移：`acceptance/workspace_v02/bootstrap.py` 复跑通过（replay 空、授权/RLS/触发器正确）。
- 伙伴 OpenAPI：`export_contract --check` 验证 `docs/runtime-workspace-v02-openapi.json`。

### 本次集成：成员显示投影（不改 ACL）

- `scene.members` 仅列出该投影已可见的 owner/participants/agents，仅按这些 id 查询，无全 scope 目录；`authority=display_only_not_authorization`。
- `display_name` 仅供 UI 按姓名分享；`current`/`status` 显式：`current`、`inactive`（principal 记录停用）、`not_currently_appointed`（记录活跃但无当前任职/binding）、`missing`（记录缺失，name=null）。
- 不改来源可见性/共享/围栏；无隐藏来源或授权通过成员列表推断。

### 本次修正：合成 AgentRun 模型元数据

- 受控验收 AgentRun 改为 `provider=controlled-fixture, name=synthetic-acceptance, version=test-version-1`；不再出现 deepseek/flash/max，也不把 pi 开发设置/thinking 当作业务模型版本。
- Runtime 不调用模型；`synthetic_run_model_is_controlled_fixture` 在 HTTP 端验证记录的就是受控 fixture 值，报告继续声明 `real_model_invoked=false`。

### 伙伴接线材料核对（本轮，仅 docs）

- 用现有 `scripts/export_governance_openapi.py` 刷新 `docs/runtime-governance-openapi.json`：现已含 session、commands prepare/get/list/commit/retry、全部新 facade 读取（`method-tasks`、`sources`、`sources/{scene_id}`、`sources/contexts/{context_id}`、`objects/{object_id}/actions`）及核心 governance 读取。未改 `src`。
- workspace/0.2 伙伴面 `docs/runtime-workspace-v02-openapi.json` 由 `acceptance/workspace_v02/export_contract.py --check` 验证。
- 0.4 typed human：governance OpenAPI 的 `/commands/prepare` 体是通用 governed envelope（按设计）；逐动作参数 schema 以 `docs/runtime-method-registry-0.4.json` 与 `docs/contracts/method-profile-0.4.json` 为准。
- 新增 `docs/partner-ui-event-mapping.md`：UI 事件→接口→结果/恢复；明确 Context 是真快照非 receipt（顶层 `context_id`）、0.4 直接 issue/共同签署/整组 commit 激活、未知结果保留原信封 retry；不拄私有样本。
- OpenAPI 别名缺口已修复：pi#1 的 alias `include_in_schema=False` 消除了 `/governance/method/tasks` 与 `/governance/method-tasks` 的重复 operationId（最终 23 paths / 25 operations）；未新增功能。
- 状态只引用已执行证据：`qa-final-workspace` = **64**、`qa-final-v03` = **35**、`qa-final-v04` = **29+39**、`qa-facade02` = **47/47**；root final Python **920 total（套件 919 + 迁移 replay 1）/ 16 skip 另列**，UI **165 pass/21 files** + typecheck/uv build/manifest PASS；同一案例本地受控全链（Agreement、4 承诺、CEO 激活、2 正式 Mission、评论含撤回、来源 2 会话 Context withheld）已由 root 独立验证；迁移 replay PASS、source frozen（`index-CvwLgGIZ.js`）；真实业务模型/Clark 未运行；不发布、不部署。
- 生成/更新的 docs 已核对：OpenAPI 无 example 实体、无私有验收路径与凭据；映射/集成文档只使用合成占位符。先前的 `method/tasks` 与 `method-tasks` 别名 operationId 重复已由 pi#1 的 alias `include_in_schema=False` 修复（最终 23 paths / 25 operations）。

### B5 会话 facade 验收（新增，仅 tests/acceptance）

- 新增 `acceptance/workspace_v02/facade_bootstrap.py`（新 scope + 本机账号，不预置业务成功）与 `facade_sessions.py`（真实 HTTP `/dashboard/api/v1`，无生产代码修改）。
- 最终复跑（pi1 修复 commit 尾段返回当前授权 `get()` 视图、committed-create 分支、路由与 context get 后，新进程 58837）：**47/47 passed**。通过项包括 session；scene/source/share/context prepare→commit→get→list→retry；未 commit `scene_create` 双击同 command、get/list 可见、提交一次；Context 保存顶层 `context_id`（retry 同 `context_id`，get 重检当前授权）；journal 撤权后正文/自由文本不恢复；新增关键检查：`source_version` 撤回后 commit/retry/get 不返回旧 segments，Context 撤权后不返回 `envelope.purpose`，draft 正文与 `draft_decision.note` 撤权后 commit/retry/get 均不恢复；journal 按身份 404；成员显示/受控 fixture metadata；新 facade 读取 URL（method-tasks/sources/detail/context/actions）鉴权正确（匿名 401、非成员列表不含/详情 404）。
- 私有完整报告：`.runtime-acceptance/pi-method-04/facade-scenario-1/facade-findings.md`（并在共享 `facade-findings.md` 追加最终状态；root 可独立复跑）。
- B2 自身修复：`workspace_v02_service.authorize_receipt` 此前对 `source_add`（身份=事件 id）与 `source_unshare`（经 share 事件解析来源）误用 `payload.source_id`，facade journal 读回执时 KeyError；已修并新增单测，core 验收 `ws02-run-18` 仍 **64 checks passed**。

## 语义要点（已实现并测试）

- 默认私有：来源可见性只由场景成员 + 来源所有者 + 精确版本共享决定；CEO/域 read 不能绕过。Agent 每次读取重检当前 personal-agent binding 与 owner 当前任职（`current_member`），撤销后场景与通用路径同时关闭。
- 无遍历：共享 v1 不暴露 v2/更正版本；未共享版本不返回 id/hash/origin。
- 围栏覆盖：object/revision/download、dashboard、通用 Context（创建与历史快照）、回执、run 自由文本、draft 正文；历史快照静默扣留、无计数泄漏。
- Context：不可变快照、并发同键单快照（`ON CONFLICT DO NOTHING` + 重读）、稳定重放、同键不同 body 409。
- Agent run 必填 Context 输入快照，input_refs 必须是该快照精确条目；快照归属仅允许 run 所有者（绑定人类）或执行 Agent 本人；succeeded/failed/unknown 状态诚实；quote 必须命中 segment。
- Context `purpose` 只在全部捕获条目当前可读时返回给任何读者（含创建者）；撤权/撤回后为 `null`，非创建者同时不获得隐藏条目计数。
- `operations` 可用性只从读取者自己的来源/当前授权与当前可读内容推导；不通过动作可用性泄漏他人私有来源/授权存在，已撤回来源不显示为可写。
- 私有原文不得先上传为域可读 EvidenceAsset；`evidence_ref` 仅用于原本按域协议有意共享的原始件，记录 `evidence_basis=domain_shared_original`，链接即纳入围栏（链接人必须是该 EvidenceAsset 最早 revision 的记录人）。

## 未完成 / 边界

- B0-B5 全计划其余批次由其它 pi 负责；本检查点仅 B2。
- Runtime 本地副本共享不代表飞书/外部 ACL 同步；连接器、真实浏览器、真实模型、发布/部署未验收。
- `method_access.head_access` 的内部围栏已补齐；未链接对象不变。后续其它直接消费者仍应走 `workspace_v02_guard.enforce_object`。
- 真实来源文档的 `occurred_at` 为 0：Runtime 如实记录，无编造时间轴；若后续 manifest 提供原始事件时间，导入驱动可按同一 `acquired_at_basis` 机制扩展。

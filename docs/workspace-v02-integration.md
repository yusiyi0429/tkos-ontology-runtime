# tkos.workspace/0.2 独立来源协作接入（B2）

基线：Runtime 工作区 `codex/method-04-realignment`，迁移 `0026_workspace_sources_v02.sql`。
本契约与 `tkos.workspace/0.1` 并行存在：0.1 场景仍必须绑定精确 Method 锚点；0.2 独立来源场景**没有也不要求业务锚点**，不产生正式业务效力，不创建 MethodRun / Issue / Method 对象 / 任务。

本文同时作为 B5 工作台接入的接口清单。0.1 的页面、请求与错误处理保持不变。

## 1. 边界与效力

- 0.2 记录只进入 `gov_workspace_v02_events`（追加不可变）、`gov_workspace_v02_contexts`（不可变快照）与 `gov_workspace_v02_assets`（来源围栏链接表）。
- 回执复用 `gov_action_receipts`；事件、来源围栏链接与回执在同一事务提交。`formal_effect: "none"`。
- 来源内容默认私有。可见性只由场景成员、来源所有者与**精确版本**授权决定；域角色、CEO 身份或对 EvidenceAsset 所在域的 read 授权都不能绕过。
- Runtime 本地副本的共享是 Runtime 内授权，不代表飞书/任何外部系统的 ACL 已同步。导入的 `system`/`external_id` 只是元数据，连接器送达与外部 ACL 由伙伴负责。

## 2. 身份与场景

- 场景成员：`owner_principal_id` 与 `participant_principal_ids`，必须都是当前有效的人类 principal。
- `agent_bindings`：`{agent_principal_id, owner_principal_id}`；Agent 只继承其显式绑定人类成员的当前可见性，不能成为成员，不能代替人类确认/决策。绑定在创建时核对当前 `gov_method_agent_bindings`。
- 写入 `POST /v1/workspace-scenes/events`，body 以 `contract_version` 判别：
  - `tkos.workspace/0.1` → 既有 `WorkspaceCommand`（不变）。
  - `tkos.workspace/0.2` → `SourceSceneCommand`。

```json
{
  "contract_version": "tkos.workspace/0.2",
  "scene_id": "<UUID>",
  "expected_version": 0,
  "idempotency_key": "<16..128>",
  "event": { "kind": "scene_create", "scene_type": "meeting",
             "external_id": "<scope 内同类型唯一>", "title": "…",
             "owner_principal_id": "<本人 principal UUID>",
             "participant_principal_ids": ["<UUID>"],
             "agent_bindings": [{"agent_principal_id": "<UUID>", "owner_principal_id": "<UUID>"}] }
}
```

成功回执：

```json
{ "receipt_id": "…", "action_type": "workspace_v02.source_version", "actor_id": "…",
  "auth_epoch": 7, "status": "committed",
  "result": {"contract_version": "tkos.workspace/0.2", "scene_id": "…", "event_id": "…",
             "version": 4, "kind": "source_version", "payload_hash": "<64hex>",
             "source_id": "…", "evidence_linked": true, "formal_effect": "none"},
  "object_versions": [], "effect_task_ids": [], "recorded_at": "…" }
```

## 3. 写入动作

| kind | 关键字段 | 权限/语义 |
| --- | --- | --- |
| `scene_create` | scene_type `meeting/document/selected_conversation`、external_id、title、owner、participants、agent_bindings | 仅创建者本人；`expected_version=0`；external_id 按 scope+类型唯一 |
| `source_add` | system、external_id、title、media_type、acquired_at、origin_label、sensitivity=private | 成员本人或其绑定 Agent；owner 取绑定人类；返回 `source_id`（= 该事件 event_id） |
| `source_version` | source_id、fingerprint（原始文件 sha256）、media_type、acquired_at、segments[]、evidence_ref? | 仅来源所有者/绑定 Agent；版本号服务端分配；追加不可变 |
| `source_correct` | source_id、corrects_event_id、reason、segments、evidence_ref? | 同上；原版本保持不可变；被撤回版本不可更正 |
| `source_withdraw` | source_id、version_event_id?、reason | 撤回整源或精确版本；撤回后内容不再可读（包括所有者正文） |
| `source_share` | source_id、version_event_id、payload_hash、share_to_principal_id、note? | 仅来源所有者/绑定 Agent；grantee 必须是当前场景人类成员；只授精确版本 |
| `source_unshare` | share_event_id、reason | 仅原授权人类/其绑定 Agent；授权立即失效 |
| `agent_run` | agent_principal_id（=本人）、purpose、model{provider,name,version,parameters?}、input_refs[]、context_id（必填）、external_run_id?、status、started_at、finished_at?、error?、output_refs[]/draft_event_id? | 仅场景内绑定 Agent；`input_refs` 必须恰为其 Context 快照中的精确来源版本，且当前可读；快照归属只能是 run 所有者（绑定人类）或执行 Agent 本人；succeeded 必须有输出、failed 必须有 error 且无输出、unknown 不得声称输出 |
| `followup_draft` | title、run_event_id?、items[{item_kind: fact/request/suggestion/accepted, text, citations[{source_id, version_event_id, payload_hash, segment_index, quote}]}] | 成员或绑定 Agent；每个引用必须精确命中来源版本 segment 文本；若带 run，则引用集合必须是 run input_refs 子集 |
| `draft_decision` | draft_event_id、item_index、decision `accepted/rejected/noted`、note? | 仅人类成员；引用源当前不可读时拒绝（409 DEPENDENCY_MISSING） |

`segments[]` 元素：`{speaker? , text, occurred_at?}`。来源的 `acquired_at` 与每条 segment 的 `occurred_at` 是各自独立时间轴，Runtime 不做跨源合并。

`agent_run.model` 只是运行元数据：Runtime 不调用模型。受控/合成验收必须显式标注为 fixture/controlled（例如 `provider=controlled-fixture, name=synthetic-acceptance, version=test-version-1`），不得填写真实模型名或把开发工具设置当成业务模型版本，也不得用 `thinking` 等参数冒充模型版本。

`evidence_ref`：`{object_id, revision_id, payload_hash}`，指向**原本就已按当前域协议有意共享/可读**的 EvidenceAsset 精确 revision（0.3 或后续 0.4），例如已在业务 Context 中使用的原始件。默认私有来源（含私有会议原文）**不得**先把内容上传成域可读 EvidenceAsset 再链接；私有文本只存 `segments`（追加不可变事件、受 RLS 与来源围栏保护），原始本地文件留在伙伴/本地侧。链接会把该精确 revision 纳入来源围栏（撤权后通用路径也拒绝），Runtime 不把它转成 Method 对象；写入时会记录 `evidence_basis=domain_shared_original` 以区分。**只有该 EvidenceAsset 最早 revision 的记录人本人（或其当前绑定 Agent）才能链接**，防止把他人已有对象纳入私有源。

## 4. 读取

| 接口 | 返回 |
| --- | --- |
| `GET /v1/workspace-sources?scene_type=&after=&limit=` | 当前成员可见的独立场景摘要（`items/next_after`，无全域计数） |
| `GET /v1/workspace-sources/{scene_id}` | 场景定义、可见来源与版本、runs、drafts、decisions、operations |
| `POST /v1/workspace-sources/contexts` | 用精确来源版本创建不可变 Context 快照 |
| `GET /v1/workspace-sources/contexts/{context_id}` | 重检当前授权后返回快照；失效条目 `status=withheld` 且无正文 |
| `GET /v1/action-receipts/{receipt_id}` | 0.2 回执重新校验场景成员与当前来源可见性 |

场景读取要点：

- 非成员一律 `404 NOT_FOUND`（不披露存在）。
- `scene.members` 只列出该投影已经可见的成员（owner/participants/agent bindings），用于按姓名分享，不提供全 scope 目录：仅按这些 `principal_id` 查询。

```json
"members": {
  "authority": "display_only_not_authorization",
  "owner": {"principal_id": "…", "principal_type": "human", "display_name": "张三",
            "current": true, "status": "current"},
  "participants": [{"principal_id": "…", "principal_type": "human", "display_name": "李四",
                    "current": false, "status": "inactive"}],
  "agents": [{"agent": {"principal_id": "…", "principal_type": "agent", "display_name": "…",
                         "current": true, "status": "not_currently_appointed"},
              "owner": {"principal_id": "…", "principal_type": "human", "display_name": "…",
                        "current": true, "status": "current"}}]
}
```

- `display_name` 仅供显示：不推导任何权限。已停用 principal 保留存储姓名并标记 `current=false, status=inactive`；记录缺失为 `display_name=null, status=missing`；有记录但当前无任职/binding 为 `status=not_currently_appointed`。
- 来源所有者可见自己所有版本；撤回后正文为空、只余元数据。
- 精确授权者只看到**当前仍被共享的那一个版本**；未共享、被更正替代或撤回的版本不返回 id/hash/origin。
- `drafts`：任一引用来源当前不可读 → 整个 draft `status=withheld`，不返回 items、标题或引用数量。
- `runs`：`input_refs` 不可读时只返回事件与身份/状态元数据，不返回 purpose、error、external_run_id、model 参数、输出。
- `operations` 是当前角色提示，提交时重新校验，不是授权凭据。

Context 快照：

```json
{"contract_version": "tkos.workspace/0.2", "scene_id": "…",
 "idempotency_key": "<16..128>", "purpose": "…",
 "items": [{"source_id": "…", "version_event_id": "…", "payload_hash": "<64hex>"}]}
```

同键同 body 的并发/重放返回同一 `context_id` 与原始快照；同键不同 body 返回 `409 IDEMPOTENCY_CONFLICT`。Context 可由人类成员或其绑定 Agent 创建；`purpose` 是自由文本，只在所有捕获条目当前可读时返回（含创建者），否则为 `null`。

## 5. 来源围栏（安全边界）

`workspace_v02_guard` 是唯一复用判定：

- 直接对象：`GET /v1/objects/{id}`、`GET /v1/objects/{id}/revisions/{rid}`、`GET …/evidence-assets/{id}/revisions/{rid}`（下载）在域授权之后仍检查来源围栏；未链接对象行为不变。
  - 精确 revision：所有者或该版本有效授权者可读；精确版本共享本身就是授权，Method 绑定的原始件也按此精确 revision 读取/下载。
  - 对象级读取：该对象所有 revision 都链接且都可读才可读，否则 404。
  - 当前任职：场景成员与绑定 Agent 在每次读取/Context/回执重放时重检当前 personal-agent binding 与 owner 当前任职；撤销后场景与通用路径同时拒绝。
- 通用 Context：`POST /v1/context-packs` 与 `GET /v1/context-packs/{snapshot_id}` 会整条剔除自身是受围栏对象、或 `source_refs` 含不可读受围栏对象的派生条目；历史快照静默丢弃、不产生 withheld 计数。快照读取按当前授权过滤：创建者可见逐项扣留标记；非创建者在非完整可读时不获得 `purpose`、不获得隐藏条目计数，只得到当前可读条目与一个扣留布尔。
- 回执：`GET /v1/action-receipts/{id}` 检查回执内对象引用；来源撤权后不再返回相关回执。
- 已集成钩子：`readers.object_state`、`readers.revision`、`readers.context_pack`、`readers.context_snapshot`、`readers.action_receipt`、`routes.evidence_download`、`routes.context_create`、`dashboard._visible_head`、`method_access.head_access`/`revision`（Method 内部读取也受围栏约束，未链接对象不变，精确共享 revision 保留）。

## 6. 伙伴接入注意

1. 浏览器只用自己的会话。Agent 连接器使用固定 scope+域到独立 Co-agent/个人 Agent 身份的映射，不得接受页面传入 Agent 凭据。
2. 默认私有来源只提交 `segments` 与原始文件 sha256 `fingerprint`，不得先把私有文本上传为域可读 EvidenceAsset。只有原本就有意按域协议共享的原始件才使用 `evidence_ref`；链接即把该精确 revision 纳入来源围栏。
3. 0.2 场景直接提交自身 CAS；不调用 Method prepare。超时/网络中断按幂等键原信封重放，先查回执，不换键重写。
4. 每次读取为 no-store；页面切换身份/场景时取消旧请求并校验响应属于当前身份。
5. 外部来源系统的真实 ACL、消息送达与模型编排不在 Runtime 内；页面不得把 Runtime 授权显示为飞书 ACL 同步。

## 7. 验证

- 单元/契约：`tests/test_workspace_v02*.py`
- 独立 HTTP+PG+MinIO：`acceptance/workspace_v02/README.md`
- 私有真实来源导入只报告 sha256 与计数；canary 场景验证所有读路径撤权后无正文泄漏。

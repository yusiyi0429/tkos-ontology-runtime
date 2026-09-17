# tkos.workspace/0.2 — 独立来源协作契约

状态：**已实现并经独立 HTTP/PostgreSQL 验收脚本验证；与 tkos.workspace/0.1 并行，不替换、不放宽 0.1**。运行支持以本工作区编译版本为准；未实现的动作不提供成功路径。

## 1. 版本与隔离

- 0.1 场景（monthly/weekly/meeting）继续要求精确 Method 锚点及其授权语义，历史事件、回执与重放保持不变。
- 0.2 独立来源场景（meeting/document/selected_conversation）**没有也不要求业务锚点**。它不创建、不引用 Method 对象，不产生 Issue/Agreement/Mission，不创建 MethodRun，不写执行授权、交付验收或 Outcome。
- 0.2 只使用独立存储：`gov_workspace_v02_events`、`gov_workspace_v02_contexts`、`gov_workspace_v02_assets`；回执复用 `gov_action_receipts`。事件、来源版本围栏链接与回执在同一当前权限事务内提交；任一步失败不留下成功记录。
- 两个版本各自显式声明；请求里的 `contract_version` 只说明客户端理解的格式，不授予权限，也不改变服务端绑定。

## 2. 隐私与授权总则

1. **默认私有**：来源内容只对其所有者、场景成员（仅限被精确授权的版本）及其显式绑定的 Agent 可见。
2. **场景成员不等于来源读取权**：加入场景不会自动获得任何来源正文。参与者只看到被精确共享的版本。
3. **角色不越权**：CEO、域 DRI、公司域 read 授权或对 EvidenceAsset 所在域的 read 授权，都不能读取未共享的个人来源。平台没有“CEO 可读全部场景”例外。
4. **精确材料、无遍历**：`source_share` 只授 `version_event_id` 精确版本；不扩散到同源其他版本、更正版本、引用来源或派生内容。
5. **当前授权重检**：每次读取按当前成员、当前授权与来源状态重新判定；撤回、解除共享、来源撤回后历史 Context、派生 draft、run 元数据与回执都按当前权限过滤，冻结不构成豁免。
6. **现状与元数据分离**：来源撤回后所有者仍可见该来源/版本的存在与撤回记录，但不返回正文；精确授权者在解除授权后连版本 id/hash/origin 都不再返回。
7. **派生内容整体限制**：draft/Context 条目一旦引用的任一来源版本当前不可读，整个派生正文被扣留；Runtime 不倒推“剩余部分”冒充完整内容。
8. **无隐藏计数**：不可见来源、版本或派生条目不出现在列表与投影中；历史快照的扣留不产生计数或标记。

## 3. 来源身份、版本、更正与撤回

- `source_add` 建立来源身份：`system`、`external_id`、`title`、`media_type`、`acquired_at`、`origin_label`、`sensitivity=private`。所有者由认证主体的显式绑定推导（Agent 写入时归其绑定人类）。
- `source_version` 追加不可变版本：`fingerprint`（原始工件 sha256）、`segments[]`、每段 `speaker/text/occurred_at`、`media_type`、`acquired_at`、可选 `evidence_ref`。版本序号由 Runtime 分配。`acquired_at` 与 segment `occurred_at` 是各自独立时间轴，不跨源合并。
- `source_correct` 追加新版本并指向被更正版本与原因；原版本保持不可变，被撤回版本不可更正。
- `source_withdraw` 可撤回整源或精确版本；撤回追加记录、不可撤销，也不复活旧版本。
- `evidence_ref` 只指向**原本就已按域协议有意共享/可读**的 EvidenceAsset 精确 revision（含 0.3 与后续 0.4），并记录 `evidence_basis=domain_shared_original`；默认私有来源（含私有会议原文）不得先上传为域可读证据，私有文本只存 `segments`。链接把该 revision 纳入来源围栏，Runtime 不做 Method 转换。**只有该对象最早 revision 的记录人本人或其当前绑定 Agent 可以建立链接**；否则拒绝，防止来源创建者围栏或转售他人对象。

## 4. 场景、成员与 Agent 绑定

- 创建者必须是场景所有者本人（人类）。参与者必须是当前有效人类 principal；成员至少有一条当前任职。
- `agent_bindings` 显式指定 Agent 与其人类所有者；创建时与当前身份绑定核对。Agent 继承所有者的可见性，不能成为成员、不能执行 `draft_decision`、不能代替人类确认。
- 场景 `external_id` 在 scope+场景类型内唯一。场景流 `version` 是唯一 CAS 版本，包含共享/撤回/运行/草稿等全部写入。
- 读取投影中的 `operations` 只是读取者的当前权限提示：可用性只从读取者自己的来源/当前授权与当前可读内容推导；不通过动作可用性泄漏他人私有来源或授权存在，已撤回来源不显示为可写。
- `scene_create` 必须 `expected_version=0`；其他动作不得为 0。

## 5. Agent 运行与引用草稿

- `agent_run` 由该精确 Agent 身份记录：`purpose`、`model{provider,name,version,parameters?}`、`input_refs[]`、必填 `context_id`、可选 `external_run_id`、`status(succeeded/failed/unknown)`、`started_at`、`finished_at?`、`error?`、`output_refs[]`/`draft_event_id?`。
  - `input_refs` 必须恰好是其 Context 快照中捕获的精确来源版本；运行不得声称任意可读引用作为输入。Context 快照的创建者必须是 run 所有者（绑定人类）或执行 Agent 本人。
  - succeeded 必须有完成时间与输出且无 error；failed 必须有完成时间与 error 且无输出；unknown 不得声称输出。
  - Runtime 只记录运行事实，不调用模型、不创建 MethodRun、不保证模型恰好一次。
- `followup_draft` 的每个 item 具有 `item_kind`（`fact/request/suggestion/accepted`）与精确 `citations`：来源、版本、payload hash、segment 序号与原文引用（必须命中该版本 segment 文本）。带 `run_event_id` 时引用必须是该 run 输入子集。
- `draft_decision` 是个人人类动作（`accepted/rejected/noted`），追加记录最新有效；引用来源当前不可读时拒绝提交。

## 6. Context 快照

- `POST /v1/workspace-sources/contexts` 用精确来源版本创建不可变快照；快照保存版本序号、fingerprint、segments_hash、媒体信息、采集时间与精确 segments。创建者必须当前可读每个条目。
- 同键同 body 的重放（含并发）返回同一 `context_id` 与原始快照；同键不同 body 返回 `IDEMPOTENCY_CONFLICT`。
- 读取时按当前场景成员与当前来源授权重检：失效条目标记 `status=withheld` 且不返回正文；非创建者成员在扣留条目上不获得来源 id。`purpose` 是调用者自由文本，仅当全部捕获条目当前可读时才返回（含创建者），否则为 `null`。

## 7. 通用读取路径的围栏

0.2 私有来源正文不得通过 0.2 场景之外的任何读取路径泄漏：

- 直接对象、精确 revision 与 EvidenceAsset 下载在域授权之后仍执行来源围栏；未链接对象行为不变。
- 通用 Context 创建与历史快照读取会剔除受围栏对象，或整条剔除含受围栏 `source_refs` 的派生条目；历史快照静默扣留、不产生计数。
- 回执读取检查回执内对象引用与场景成员资格。
- 撤权后重放：同一幂等键的原信封仍返回同一回执元数据，但正文/正文引用按当前权限重检。

## 8. 效力与边界

- 0.2 的 `formal_effect` 恒为 `none`；不产生也不替代任何正式业务成功。
- Runtime 本地副本共享**不等于**外部系统 ACL 同步；来源 `system/external_id` 仅作标识。飞书连接器、消息送达、身份同步与真实模型编排由伙伴负责。
- 本契约不承诺：Method 0.4 动作在 0.2 中执行、旧对象迁移、Play/Plan/WorkPackage、公司聚合 State。

## 9. 错误

| code | 场景 |
| --- | --- |
| 401 UNAUTHENTICATED | 无有效 Bearer |
| 403 FORBIDDEN | 非来源所有者/绑定 Agent 变更来源；Agent 代替人类决策；非成员写入；Context 条目不可读 |
| 404 NOT_FOUND | 非成员场景；来源/版本/共享/回执不可见；受围栏对象不可读 |
| 409 VERSION_CONFLICT | CAS 落后；external_id 重复；精确版本 hash 变化；来源已撤回 |
| 409 IDEMPOTENCY_CONFLICT | 同幂等键不同请求体 |
| 409 INVALID_STATE | 重复共享/重复撤回；撤回源新增版本 |
| 409 DEPENDENCY_MISSING | draft 决策时引用来源不可读 |
| 422 INVALID_REQUEST | 严格 schema、引用、segment 越界、quote 不命中、run 输入不在 Context 快照中 |

完整请求/响应 schema、动作清单与伙伴接入注意见 [workspace-v02-integration.md](../workspace-v02-integration.md)。

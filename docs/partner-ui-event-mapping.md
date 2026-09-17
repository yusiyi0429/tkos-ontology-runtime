# 伙伴接线：UI 事件 → Runtime 接口 → 结果与恢复

范围：本机 Runtime 的浏览器会话 facade 与受控核心接口。浏览器不持有 Agent 凭据，不提供 Agent 身份选择器。示例值均为合成占位符；不含私有会议样本或凭据。

## 0. 会话与传输

| 步骤 | 接口 | 结果 |
| --- | --- | --- |
| 登录 | `POST /dashboard/api/v1/session` `{username, code}` | `identity` + `csrf`，HttpOnly `tkos_governance_session` cookie；失败 401，60 秒内同 peer 超过 10 次登录失败返回 429 |
| 会话检查 | `GET /dashboard/api/v1/session` | 当前 `identity`/`csrf`；无会话 401 |
| 写请求 | 所有 `POST`/`DELETE` | 必须带 `X-CSRF-Token`；`Origin` 必须同源，`Sec-Fetch-Site` 为 `same-origin`/`none`，否则 403 |

所有读取与错误响应 `Cache-Control: no-store`。浏览器切换到其它身份/页面时取消旧请求并清空旧内容。

## 1. 命令生命周期与恢复（所有人类正式动作）

| UI 事件 | 接口 | 结果 | 恢复 |
| --- | --- | --- | --- |
| 打开表单/预览 | `POST /dashboard/api/v1/commands/prepare`（原样 governed envelope） | `command_id`、`preview`、本人 `envelope`、`status=prepared`；同一 body 双击/重复 prepare 返回**同一 command_id** | 同键不同 body → 409 `IDEMPOTENCY_CONFLICT`；恢复原请求，不覆盖 |
| 提交 | `POST /dashboard/api/v1/commands/{command_id}/commit` | 当前授权读视图：`status=committed` + 重新授权的 `receipt`（0.2 场景为 action-receipt 读投影，Context 为快照视图）；`envelope` 按当前授权扣留，绝不返回私有原文 | 409 `VERSION_CONFLICT` → 读取最新状态，用**新的 idempotency_key** 构造有业务意图的新命令；不要改已保存信封 |
| 重试 | `POST /dashboard/api/v1/commands/{command_id}/retry` | 与 commit 相同；已提交命令重放返回原回执/原快照 | 超时、5xx、`RESULT_UNKNOWN`：结果未知，先 `GET /commands/{id}` 或查询回执；仍未知再对**同一 command_id** retry，不换键、不新建命令 |
| 我的提交 | `GET /dashboard/api/v1/commands` | 本人命令列表；撤权后仅返回扣留后的读视图 | 401 清空会话；403/404 不得回退样例 |
| 单条命令 | `GET /dashboard/api/v1/commands/{command_id}` | 原信封或按当前来源授权扣留的视图 | 跨身份一律 404 |

规则：

1. prepare 后未 commit 的命令已可在 get/list 中看到；双击不会产生第二条命令。
2. 只有真正的业务意图变化（明确 409 且已读取新状态）才允许新 key；网络结果未知时永远复用原信封。
3. commit/retry 的响应与 `GET /commands/{id}` 一样经过当前授权重检；`source_unshare`/`source_withdraw` 后 `segments`、Context `purpose`、draft 正文与 `draft_decision.note` 都不可从 commit/retry/get 恢复。

## 2. Context 是真实快照，不是回执

| 步骤 | 接口 | 结果 |
| --- | --- | --- |
| 保存 Context | 核心 `POST /v1/workspace-sources/contexts`；会话内为 Context 命令 prepare→commit | **顶层** `context_id`、`scene_id`、`purpose`、`complete`、`withheld`、`items`；不是 `receipt.result`，没有 `receipt_id` |
| 重复保存/重试 | 同一命令 retry 或同键重放 | 同一不可变 `context_id` 与原始捕获内容；同键不同 body → 409 `IDEMPOTENCY_CONFLICT` |
| 读取 Context | 核心 `GET /v1/workspace-sources/contexts/{context_id}`；facade `GET /dashboard/api/v1/governance/sources/contexts/{context_id}` | 每次按当前场景成员与逐条来源授权重检：全部可读才返回 `purpose`；失效条目 `status=withheld`，非创建者不获得 id/数量 |
| 撤权后 | 同上 | `complete=false`、`purpose=null`，正文不可恢复；快照身份 `context_id` 保留 |

## 3. workspace/0.2 来源场景事件

核心 `POST /v1/workspace-scenes/events`（`contract_version=tkos.workspace/0.2`）；会话 facade 提供其中的人类事件（`scene_create`、`source_add/version/correct/withdraw/share/unshare`、`followup_draft`、`draft_decision`）。`agent_run` 属于 Agent 契约，不在人类 facade。

| UI 事件 | event.kind | 结果与注意 |
| --- | --- | --- |
| 新建独立来源场景 | `scene_create` | 无业务锚点；`formal_effect=none`；external_id 同类型唯一 |
| 登记来源 | `source_add` | 返回 `source_id`（= 事件 id）；默认私有 |
| 追加版本 | `source_version` | `fingerprint` 为原始文件/提交文本 SHA-256；`segments` 为精确文本；版本号服务端分配 |
| 更正 | `source_correct` | 指向 `corrects_event_id`，原版本不可变 |
| 撤回 | `source_withdraw` | 整源或精确版本；撤回后正文不可读（含所有者投影） |
| 分享/取消 | `source_share` / `source_unshare` | 只授/撤**精确版本**；不扩散到其它版本 |
| 跟进草稿 | `followup_draft` | 每项引用必须命中 segment 原文；任一引用撤权即整条扣留 |
| 草稿决定 | `draft_decision` | 仅人类本人；`note` 与 draft 同受当前授权约束 |

所有写入使用场景流 `version` 做 CAS（`expected_version`）；冲突 409 后刷新重提。

## 4. method/0.4 人类事件

入口：`GET /dashboard/api/v1/governance/method-tasks`（别名 `/governance/method-tasks`；核心 `/v1/governance/method/tasks`）返回当前身份可办理的 0.4 事项；`GET /governance/objects/{object_id}/actions` 返回逐动作 allowed/reason（提交仍由服务端重检）。

| UI 事件 | action_type | 结果与注意 |
| --- | --- | --- |
| 直接立项 | `m1a_create_issue`（Agent 动作，浏览器不代办） | CEO Agent 用本人 MethodRun 直接创建/重新界定/关联；无 PotentialIssue 门槛 |
| 指定参与人/研究责任 | `m1a_set_participants` | 提名必要当前人类；Agent 不代替确认 |
| 共同签署 | `m1a_confirm_agreement` | 每个必要签署人确认**同一精确版本**；名单/正文/证据变化使待确认失效；重查当前任职 |
| 正式更新 | `m1a_confirm_update` | Co-agent 复核后 CEO 最终确认；无变化只记录共识 |
| M1B 评论 | `m1b_comment` / `m1b_replace_comment` / `m1b_withdraw_comment` | 评论与承诺分离；本人意见历史保留 |
| 本人承诺 | `m1b_commit_candidate` | 具名 DRI/Owner 只承诺本人责任；候选集合变化全部失效 |
| 整组激活 | `m1b_activate_candidates` | CEO 最后一次性原子激活；关键未决依赖阻止；无执行/验收副作用 |
| 重开 | `m1b_reopen_candidates` / `m1b_reopen_window` | 显式重开，保留历史 |
| LTCO 确认 | `m1b_confirm_ltco` | CEO 确认精确长期目标版本 |
| State 确认 | `method_confirm_state` | 责任人当前任职重查；无证据 → Unknown + 缺口 |
| 问题登记/修订/关闭 | `method_open_problem` / `method_revise_problem` / `method_close_problem` | 目标可为空但需精确 state_ref；战略移交不等于解决 |

结果均为 ActionReceipt（`receipt_id`、`result`、`effect_task_ids=[]`）；0.4 正式动作不产生执行授权或交付验收。

## 5. OpenAPI 与机器可读定义

- `docs/runtime-governance-openapi.json`（现有 exporter 生成）：会话、commands prepare/get/list/commit/retry、全部新 facade 读取（method-tasks、sources、sources/{scene_id}、sources/contexts/{context_id}、objects/{id}/actions）及核心 governance 读取。
- `docs/runtime-workspace-v02-openapi.json`：workspace/0.2 核心路径与共享读取。
- 0.4 动作参数 schema 以 `docs/runtime-method-registry-0.4.json` 与 `docs/contracts/method-profile-0.4.json` 为准；facade 的 `/commands/prepare` 体是通用 governed envelope（按设计），不逐动作生成 typed request schema。

已知材料状态：`/governance/method/tasks` 与 `/governance/method-tasks` 别名的重复 operationId 已由 pi#1 的 alias `include_in_schema=False` 修复（最终 23 paths / 25 operations）；0.4 无逐动作 typed schema 是设计选择，以 registry/profile 为准。

## 6. 状态（只引用已执行证据）

- 会话 facade 真实 HTTP：root 独立 `qa-facade02` = **47/47 passed**（含 commit/retry/get 撤权不恢复正文与自由文本）。
- workspace/0.2 后端：`qa-final-workspace` = **64 passed**；旧协议 `qa-final-v03` = **35 passed**。
- method/0.4 后端：`qa-final-v04` = **29 正向 + 39 负向 passed**。
- 回归（root final）：Python **920 total**（套件 919 pass + 独立迁移 replay 1 pass）/ 16 skip 另列；UI **165 pass / 21 files**，typecheck、uv build、manifest 72/8 PASS。
- 浏览器（root 独立）：同一案例本地受控全链已完成：DRI A/B 评论（含 replace/withdraw/new）、controlled Co-agent close/resolve 3 份有效意见恰好一次、4 承诺→CEO 整组激活→Owner 正式 Mission；final DB check：confirmed、2 正式 Mission、4 承诺、5 评论含 1 撤回、0 执行/0 work receipt。
- 迁移 replay PASS；root 标记 source frozen（`index-CvwLgGIZ.js`）；未发布/未部署。受控 Agent 的 HTTP/DB 独立验收计入 Runtime API 证据（仅非真实模型）；真实业务模型与 Clark **未运行**；伙伴未验证。

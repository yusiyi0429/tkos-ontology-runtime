# Clark 现有工作面接入 Runtime

基线：Clark `ea686a9c9ee724378a496ffe888cca9d9ff82511`；Runtime `1fb3226225a787d0c9c5bc7410727696f0539d18 / v0.2.1`。本扩展尚未发布。源码、契约和验收材料均在 Runtime 独立工作区；不交付 Clark 补丁。

伙伴维护页面、BFF、个人会话、身份绑定、模型调用、Agent 编排及消息发送。Runtime 保存权威对象、授权、精确版本、来源、交互历史和回执。已有 BFF/client 可以作为参考，伙伴无需采用指定实现。

## 契约和数据边界

- 已发布 `tkos.method/0.1` 的 49 个动作及对象 schema 保持原义。正式业务写入仍使用 `/v1/actions/prepare` → `/v1/actions`。
- 新交互契约为 `tkos.workspace/0.1`，单表迁移 `0022_workspace_scenes.sql`；`gov_workspace_events` 与既有 `gov_action_receipts` 原子提交。事件不可更新或删除，修改追加新版本，撤回追加关联记录。不会写 Method 状态、创建外部任务或发送通知。
- 场景类型固定为 `monthly`、`weekly`、`meeting`。每个场景绑定一个精确 Method 对象版本、当前负责人任职和参与人任职，`external_id` 在 scope＋场景类型内唯一。Clark 的展示 ID、`v3` 字符串或客户端计数不能充当 Runtime revision/CAS。
- 所有新读取及错误响应为 `Cache-Control: no-store`。服务端重新读取当前任职；场景成员身份不扩展业务对象或证据的原有读取权限。资料引用越权时，当前材料返回 `status=missing, reason=not_recorded_or_not_authorized`，不返回隐藏内容、来源数量或旧材料作为当前材料。
- `version` 是整个场景事件流的 CAS 版本，包含协作写入；不是可见评论数量。业务对象 `object_version`、不可变 `revision_id` 和场景 `version` 是三种独立概念。
- `formal_effect: none` 表示场景交互本身不产生目标生效、战略更新、执行授权、交付验收或 Outcome 达成。

完整请求及响应结构见 [OpenAPI](runtime-workspace-openapi.json)。以下场景资料需要先有合法创建的 Method 锚点；无法读取必要依据时返回缺失或拒绝，不创建替代的研究、纪要或审批历史。

## 入口与读取

| 接口 | 用途 |
| --- | --- |
| `GET /v1/identity` | 本人的 scope、principal、主体类型、显示名、授权 epoch 和当前有效任职；不接受选择身份的参数 |
| `GET /v1/workspaces/dri?collection=…` | DRI 可见的工作面数据；`missions` 只返回本人负责的已确认 Mission 与 handoff |
| `GET /v1/workspaces/ceo?collection=…` | 当前 CEO 所在域的工作面数据 |
| `GET /v1/workspace-scenes?scene_type=…` | 当前可读场景与可见事件；可按月度／周度／会议筛选 |
| `GET /v1/workspace-scenes/{scene_id}` | 场景精确历史、当前材料、本人记录、操作提示与业务投影 |
| `POST /v1/workspace-scenes/events` | 创建场景或追加有限类型事件，返回正式 ActionReceipt |
| `GET /v1/action-receipts/{receipt_id}` | 复用现有回执查询；对场景及来源重新检查当前权限 |

工作面 collection 支持 `review-windows`（默认）、`missions`、`strategies`、`strategic-issues`、`signals`、`potential-issues`、`business-facts`、`period-reviews`、`monthly-reviews`、`weekly-reviews`、`meetings`。后面三种返回对应场景的聚合投影。列表使用 `limit=1..100`、`after` 与 `next_after`，只对可见事项分页，没有全域总数。`operations.allowed` 为当前角色／状态提示，提交仍核对参数、所有依据和版本；不能把它当成授权凭据。

场景创建需要：当前 CEO 或 Co-agent 可创建本域场景；当前人类负责人可创建本人周度／会议场景。月度创建由 CEO／Co-agent 完成；周度负责人必须是精确 Mission 的 owner。所有成员均须拥有有效人类任职并能读取锚点，任职 ID 不能代替身份认证。

| Clark 字段 | Runtime 对照 |
| --- | --- |
| actorId／authorName | `/v1/identity` 的 principal_id／display_name；写入作者取 Bearer，拒绝 body 选人 |
| mission.id／PCO id | Method 的 object_id；样例字符串不能直接使用，伙伴保存外部 ID 到 Runtime object_id 的接线映射 |
| mission.version／targetVersion | revision_id＋payload_hash，另带 object_version 做正式动作 CAS；不解析 `v3` 为权威版本 |
| reviewComment.id／revision | Method review_record_id 与 replaces_record_id；场景只补字段定位 |
| review/diff/weekly/meeting.id | 创建场景时的 external_id，对应回执 scene_id；新材料用 event_id/version/payload_hash |
| ownerName／recipientName | 已授权定义内 `responsibilities`／`member_details` 的 principal_id、display_name、assignment_id 与 current 状态 |
| source／transcriptUpdatedAt | EvidenceAsset exact ref 与实际 observed_at、材料 event_id；浏览器时间不替代存储版本 |

PeriodReview 的 `effective_revision` 表示当前可用分析版本，不表示经过审批。读取同时返回 `nature=agent_analysis` 和 `phase=generated`，页面必须按对象类型及 nature 表达效力。

材料由负责人或同域 CEO／Co-agent 提交，月度资料由 CEO／Co-agent 提交。周回答和确认只允许本人负责人；会议开始、结束、发布只允许当前人类会议负责人。Co-agent 不能代人完成上述确认。

## 第一阶段：月度共同核对

| Clark 控件／事件 | Runtime 请求 | 回执后刷新与效力 |
| --- | --- | --- |
| PCO/Mission 卡片 | `collection=review-windows` 或月度场景 GET | 原始固定版本、候选版本、正式版本分别读取；优先级、why、里程碑、依赖没有来源时明确缺失 |
| 字段评论发布／替代 | 既有 `m1b_comment`，target 为 ReviewWindow，params 为 target_ref＋content＋可选 replaces_record_id | 成功后取 `review_record_id`，刷新窗口 CAS 和本人意见历史 |
| 评论字段定位 | 场景 `comment_anchor`，携带刚收到的 review_record_id、同一 target_ref、JSON Pointer `field_path` | 校验本人正式评论与精确字段；这是定位附注，不再创建一条正式评论 |
| 撤回意见 | 既有 `m1b_withdraw_comment` | 本人有效意见撤回；原评论、替代关系和字段附注仍保留 |
| 展示补充资料／评论截止 | `monthly_material`，精确 target_ref、source_refs 及可选 priority/why/milestones/dependencies/feedback_deadline | 场景资料有来源和独立版本；截止时间只用于展示，不自动关窗；PCO `period` 单独返回 |
| Co-agent 收拢 | 本域 Co-agent `m1b_close_window` → Context → `m1b_resolve_window` | 冻结意见；完整 PCO＋Mission 候选集和逐意见取舍由既有 Runtime 规则核验和原子创建 |
| Semantic Diff | 月度投影 `differences`＋`candidate.revision.payload.dispositions` | 字段差异由精确前后载荷比较产生；模型的理由保留 CandidateSet 来源，不把解释冒充差异事实 |
| 核对完成／补充说明 | `diff_response`：candidate_ref、response=`reviewed`/`commented`、note | 保存本人对该候选版本的核对；`reviewed_current_candidate` 明确是否仍对应当前候选 |
| CEO 确认／重开 | CEO 本人 `m1b_confirm_candidates`／`m1b_reopen_candidates`，闭窗时也可 `m1b_reopen_window` | 整组确认或显式重开；核对记录不替代 CEO 决定，不要求所有 DRI 先评论或核对 |
| 正式 Mission | `collection=missions`＋既有 handoff | 标明“待执行承接”，展示责任人、supports、交付物、标准、边界、期限、确认记录；执行权和验收结论保持空 |

正式评论与字段附注是两个可恢复请求。若评论回执已成功而附注失败，先查询原评论／回执，再重试附注；不能重新创建一条评论。JSON Pointer 使用真实载荷字段，例如 `/unit_outcomes/0/result_statement`、`/deliverable`、`/acceptance_criteria/0`；Clark 的 outcome/success-evidence 等展示标签在伙伴接线时映射，禁止猜测字段。

## 第二阶段：周进展与事实复盘

| Clark 控件／事件 | Runtime 请求 | 回执后刷新与效力 |
| --- | --- | --- |
| 刷新来源 | `refresh_sources`＋reason | 只记录待伙伴刷新请求，`pending_partner_refresh` 不代表读取已完成 |
| 查看周进展 | `weekly_material`：period、headline/caveat、advances/problems/implications/handling、questions、sources、source_refs | 每次提交是新材料版本；来源状态为 read/stale/unavailable，read 必须带证据版本，其余状态必须说明原因 |
| 回答缺口 | `weekly_answer`：material_event_id、question_id、answer | 本人同题新答案替代当前答案；旧版本保留。撤回最新答案不恢复更早答案 |
| 确认本周复盘 | `weekly_confirm`：当前 material_event_id、全部当前本人 answer_event_ids、signal | 确认确切材料与答案；材料或答案改变后 `confirmed_current_material=false` |
| 记录／修正 BusinessFact | `m1b_record_fact`／`m1b_correct_fact` | 原始证据经既有存储校验；修正保留原事实及对应关系 |
| 周材料引用经营事实／周期复盘 | weekly_material 的 fact_refs／period_review_refs | fact 必须对应该 Mission 或它的精确 PCO；PeriodReview 必须包含该 Mission 精确基线 |
| 生成／再生成 PeriodReview | Co-agent `m1b_generate_review`／`m1b_regenerate_review` | 分析产物，不提供审批动作；LTCO/PCO/Mission 调整继续使用对应 Method 动作 |

不计算没有依据的进度百分比或来源数量。若某个材料引用的业务事实、周期复盘或证据不在当前身份权限内，材料当前内容保持缺失；伙伴应按授权配置提供可读材料，不能通过更换浏览器身份获取 Agent 凭据。

## 第三阶段：会议与 CEO 工作面

| Clark 控件／事件 | Runtime 请求 | 回执后刷新与效力 |
| --- | --- | --- |
| 已阅／请补充／带到会议 | `read`／`request_supplement`／`bring_to_meeting`，subject_ref＋可选 note | 记录本人对精确版本的操作及协作历史 |
| 开始会议 | 负责人 `meeting_start(recording_requested)` | scheduled → in_progress；只记录录音请求，不声称录音设备已工作 |
| 结束会议 | 负责人 `meeting_finish(extract_requested)` | in_progress → ended；只记录提取请求，不伪造模型完成状态 |
| 上传转写与生成材料 | 先上传 EvidenceAsset，再写 `meeting_material(transcript_ref, transcript, source_refs)` | transcript 是有顺序的 speaker/text；原始 UTF-8 证据须按顺序包含每条 `speaker: text`，版本修改另写新材料 |
| 发布会议结果 | 负责人 `meeting_publish`，精确 material_event_id、summary、decisions/actions/open_questions/ceo_judgments、routes | 每项引用原文行号和原话；校验原话属于该版本。最新单份发布稿是当前发布结果，旧稿留历史 |
| 负责人分流 | publication.routes 对每个 action 和 ceo_judgment 恰好一条 | action 发给明确 owner 任职，无 owner 则回到会议负责人；CEO 判断发给同域当前 CEO；所有接收人须能读取场景锚点与场景 |
| CEO 查看判断 | `GET /v1/workspaces/ceo?collection=meetings` | 当前有来源读取权限的发布稿、原话和分流数据。缺少转写权限时明确缺失，成员身份不扩展原始证据权限 |
| CEO 正式决定 | 按目标调用现有 Method 动作 | 缺 Memo、双 Agent 纪要、Agreement 或战略依据时返回既有依赖错误；会议发布稿不自动补造这些记录 |

撤回当前发布稿后回到 review，旧发布稿不自动重新生效。新转写材料也会使此前发布稿成为历史，必须由负责人重新发布。`routing_status=pending_handling`、`message_delivery=not_managed_by_runtime`；真实消息由伙伴负责，Runtime 不把分流等同于送达。

## Agent 接入与恢复

1. 浏览器使用个人会话。伙伴服务端固定 scope＋domain 到独立 Co-agent 身份的映射；不得接受浏览器指定 Agent 凭据。
2. 人工触发前及正式提交前重新检查请求者为窗口域当前 CEO。Co-agent 以自身凭据关窗；提交 CEO 看到的 CAS，遇评论竞争时返回冲突并刷新。
3. Co-agent 自身调用 `POST /v1/context-packs`，contract_version=`tkos.method/0.1`、stage=`review`、purpose=`analysis`、include_drafts=true。先核对一个 PCO、全部 Mission、全部冻结意见均被授权选入；Context 缺失时不能依赖浏览器传来的私有内容继续。
4. 伙伴持久保存 Context 快照 ID、精确引用、模型版本、输入摘要和受控结构输出。场景额外材料通过同一 Agent 身份的场景 GET 读取并记录 event_id/version/payload_hash，不能当成已经确认的 Method 决定。
5. MethodRun 使用既有 open/attach/record_attempt/pause/resume/recovery；模型运行关联放在该机制和伙伴私有任务记录中，不向严格 Method params 增添私有字段。
6. `m1b_resolve_window` 必须完整提交所有冻结 Mission、所有意见取舍。Runtime 生成未来 PCO/Mission 的精确引用，模型不能编造。伙伴使用 prepare 后持久保存完整原信封，再 commit；CEO 后续确认使用本人会话。
7. 两次点击使用同一幂等键和原信封。超时、断连或 5xx 表示结果可能不明：保留请求，先查询已知回执或原请求重放。不能“更新版本＋换键”后把重放变成新动作。明确 409 VERSION_CONFLICT 后才读取新状态、重新构造有业务意图的新请求。
8. 场景直接提交自身 CAS，不调用 Method prepare；成功回执的 result 给出 scene_id/event_id/version/payload_hash。撤权后旧场景、回执和重放仍重新检查权限。
9. 伙伴模型调用尚无保存结果时的中断应标为未知并人工决定是否重试；本 API 不承诺模型调用恰好一次。

前台每五秒与重新获得焦点时读取，提交成功后立即刷新。切换身份、窗口、页面时取消旧请求、清空旧内容并校验响应仍属于当前身份和选择。API 提供真实版本和 no-store；取消请求、页面加载态和站内待办展示由伙伴实现。

## 示例与错误

所有 ID／ref 均从当前身份查询、合法 Method 动作和页面读取取得。下面是附注请求结构，示例变量不是可直接提交的业务数据：

```json
{
  "contract_version": "tkos.workspace/0.1",
  "scene_id": "<scene UUID>",
  "expected_version": 3,
  "idempotency_key": "<persisted unique request key, 16..128 characters>",
  "event": {
    "kind": "comment_anchor",
    "review_record_id": "<formal m1b_comment receipt result>",
    "target_ref": {"object_id": "<PCO UUID>", "revision_id": "<exact revision UUID>", "payload_hash": "<64 hex>"},
    "field_path": "/unit_outcomes/0/result_statement"
  }
}
```

成功为 HTTP 200，返回 `receipt_id, action_type, actor_id, auth_epoch, status=committed, result, object_versions=[], effect_task_ids=[], recorded_at`。result 中 `version=4`、`formal_effect=none`；应立即重新读取场景和受影响 Method 对象。

| 状态／error.code | 页面处理 |
| --- | --- |
| 401 UNAUTHENTICATED | 重新建立本人会话，清空旧数据 |
| 403 FORBIDDEN／404 NOT_FOUND | 身份、当前任职或来源不可用；不得回退样例或尝试 Agent 身份 |
| 409 VERSION_CONFLICT | 保留输入，读取最新窗口／场景／材料后由当前意图重新提交 |
| 409 IDEMPOTENCY_CONFLICT | 同一键对应不同内容；恢复原请求，不覆盖它 |
| 409 INVALID_STATE／DEPENDENCY_MISSING | 显示缺失的业务前提，不自动补造或确认 |
| 422 INVALID_REQUEST | 字段、引用类型、问题 ID、原话、分流完整性或严格 schema 不符 |
| 503 EVIDENCE_UNAVAILABLE | 原始证据存储不可验证；不发布成功 |
| 网络断连／5xx／响应无法解析 | 结果不明；保存原请求并按幂等恢复流程核对 |

正式评论与 Method 业务读写的完整 schema 和错误仍见 [Method 接入契约](method-clark-contract.md)。可复跑的全部成功、拒绝及恢复样例见 [验收入口](../acceptance/workspace_scenes/README.md)。验收材料必须区分 Runtime 接口、伙伴接线、Clark 浏览器闭环和真实模型，不以本地 API 测试替代后面三项。

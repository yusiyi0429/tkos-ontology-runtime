# M1A＋M1B 底座 API：tkos.method/0.1

本轮实现基线是 M1A L4 revision 21 与 M1B L5 revision 837。服务使用现有 PostgreSQL、不可变原始证据存储和治理事务，没有新增基础设施或生产依赖。是否完成验收以 `docs/runtime-method-acceptance-report.md` 和对应机器报告为准。

## 契约与安装

- 冻结规则：`docs/contracts/tkos-method-0.1.md`。
- 严格联合 Profile：`docs/contracts/method-profile.json`。
- 显式支持注册表：`docs/runtime-method-registry.json`。
- 新迁移：`0021_method_foundation.sql`。迁移不回填历史，不创建业务成功，不安装身份，不修改旧 Profile。
- 完整 OpenAPI：`docs/runtime-method-openapi.json`；每个 Method 动作及参数 schema：`docs/runtime-method-actions.json`。

新对象由服务端域创建策略选择协议；客户端 `contract_version` 只是其理解的命令格式。新 Method 域须经现有 owner 控制面安装上述 Profile、创建策略及支持注册表。严格 Profile 只接受冻结来源版本和契约 hash。真实身份接入另行安排；样例 Profile 的 `record_origin=synthetic`、`experimental=true`、`corporate_approved=false` 明示验收性质。

控制面沿用 `python -m memory_service_runtime.governed.control`，使用 `MIGRATION_DATABASE_URL`；普通应用角色不能安装 Profile 或个人 Agent 身份绑定。安装时传 `install-profile --scope-id ... --profile-json docs/contracts/method-profile.json --contract-file docs/contracts/tkos-method-0.1.md --reason "Install the frozen Method baseline"`；随后按该专用域安装策略，再 `set-registry --scope-id ... --protocol-id tkos.method --contract-version tkos.method/0.1 --content-json docs/runtime-method-registry.json --reason "Enable the reviewed Method API"`。完整合成配置与受控安装可参考 `acceptance/method_independent/fixture.py`，不应把合成授权复制为生产授权。

## 命令信封

所有动作继续走 `POST /v1/actions/prepare` 与 `POST /v1/actions`。49 个新动作包括 22 个 M1A、22 个 M1B 及 5 个运行关联动作。新的 21 种业务 payload schema 与 EvidenceAsset 共用对象头、不可变版本、身份、审计及回执底表。

```json
{
  "action_type": "m1b_confirm_candidates",
  "contract_version": "tkos.method/0.1",
  "target": {
    "object_id": "11111111-1111-4111-8111-111111111111",
    "revision_id": "22222222-2222-4222-8222-222222222222",
    "expected_version": 3
  },
  "expected_versions": [],
  "idempotency_key": "clark-confirm-candidate-example-001",
  "reason": "CEO personally confirms this exact candidate set",
  "params": {"reason": "Confirmed after the shared review"}
}
```

先读取对象得到目标内容版本，再 prepare 获得全部依赖的 `expected_versions`，最后以同一信封 commit。prepare 不生成业务成功或预授权：commit 会重新检查当前身份、职责、状态及精确版本。依赖引用是 `{object_id, revision_id, payload_hash}` 三元组。目标的 `expected_version` 是可变对象头 CAS，不能拿不可变内容修订号替代。评论、撤回、关窗等会推进头版本，但不制造正文新版本。

已提交请求丢失响应时，用同一主体、相同命令和相同幂等键重试返回原回执。不得自动刷新 CAS 后继续复用同一个键；内容变化用新键。`VERSION_CONFLICT`、`STALE_DEPENDENCY` 要重新读取业务状态及依据并重新 prepare。撤权后的旧成功重试也重新检查当前权限。全部准入失败和故障注入事务均回滚；原始证据上传与正式业务动作分离，孤立的已上传材料不能代表业务成功。

## 身份与职责

人使用 CEO、DOMAIN_DRI、IC 等当前角色及明确对象责任。Agent 使用 CEO_AGENT、CO_AGENT、PERSONAL_AGENT 独立身份。个人 Agent 代表本人参与研究或共同核对时，还必须具有控制面记录的本人绑定，其本人及相应责任必须仍有效；Agent 以自己身份贡献线索时，按当前域的相应角色和动作权限核验。禁止提交 `actor_id` 伪装作者，作者始终取自 Bearer 身份。

M1A 对 CEO 指定的域内研究 DRI 及个人 Agent 提供该 StrategicIssue、研究产物和显式共享原始材料的精确范围权限；按研究者实际域的当前动作策略核验，不授予全公司资料权限。公司 CEO 的域内 StrategicJudgment 更新仍须具备该域相应写权限。

派生 StrategicJudgment 的确切版本也向其来源议题的当前参与者开放，依据服务端写入且相互匹配的 Agreement 与更新方案双来源精确版本；该权限不递归开放 Strategy、Evidence 或整个业务域。新负责人若未参与来源议题，仍须获得显式域授权，不能仅凭知道 target_ref 取得读取或更新资格。

M1B 窗口固定参与人的 assignment＋principal，个人 Agent 必须匹配其绑定。参与权限只覆盖固定目标、相关候选版本及协作记录，不传递到 Strategy、LTCO、原始事实或其他域资料。确认后 Mission 的 Owner/参与人可以读取确切正式 Mission 及其 PCO 依据；这仍不构成 M2 执行授权。旧身份撤权或到期时，读取和写入都重新受限。

## M1A 动作

详细字段与状态见 `docs/method-m1a-contract.md`，可运行示例见 `acceptance/method_independent/flow.py`。

| 阶段 | API 动作 |
|---|---|
| 线索与议题 | m1a_record_signal、m1a_open_potential_issue、m1a_revise_potential_issue、m1a_confirm_strategic_issue |
| 责任与澄清 | m1a_assign_research、m1a_publish_memo、m1a_record_clarification、m1a_check_memo、m1a_direct_clarification |
| 研究与质量 | m1a_publish_research_plan、m1a_publish_report、m1a_submit_report、m1a_precheck_report |
| 会议与纪要 | m1a_open_meeting、m1a_publish_minutes、m1a_reconcile_minutes、m1a_confirm_minutes |
| 协议与更新 | m1a_confirm_agreement、m1a_decide_update、m1a_propose_update、m1a_review_update、m1a_confirm_update |

纪要由 DRI 确认，Agreement 由 CEO 确认，更新另经 CEO 确认。报告改版清除预审放行，方案改版清除旧 Co-agent 审查。会议目标未达成可再开会议；必要时可重回 Memo/研究阶段。无需调整时记录本轮完成及原因，保留当前 Strategy，不自动关闭其他议题或创建执行任务。

Strategy 含 `map.units[]`，单元有稳定 `unit_id`。新 Strategy/map、来源 Agreement、方案与确认回执在同一事务落地；域内判断是 StrategicJudgment。经营目标保留原引用和效力，变化写入独立影响记录。

## M1B 动作

详细字段与状态见 `docs/method-m1b-contract.md`，可运行示例见 `acceptance/method_independent/m1b_flow.py`。

| 阶段 | API 动作 |
|---|---|
| 事实和复盘 | m1b_record_fact、m1b_correct_fact、m1b_generate_review、m1b_regenerate_review、m1b_advise_ltco |
| LTCO | m1b_propose_ltco、m1b_revise_ltco、m1b_return_ltco、m1b_confirm_ltco |
| 目标草案 | m1b_draft_pco、m1b_revise_pco、m1b_draft_mission、m1b_revise_mission |
| 共同核对 | m1b_open_window、m1b_comment、m1b_withdraw_comment、m1b_assist_review、m1b_close_window |
| 收拢与决定 | m1b_resolve_window、m1b_confirm_candidates、m1b_reopen_candidates、m1b_reopen_window |

BusinessFact 保存原子观察及原始证据引用，修正为新的事实记录并保留被修正记录。PeriodReview 保存精确目标、事实、生成版本、发现、经验与后续含义；它是无需审批的分析产物，可直接被下一次分析引用，不能自行修改目标或授权。

Mission 只有一个 Owner，可与成果 DRI 同人，并可通过多条 `supports` 支撑多个成果。每条支持关系必须同时匹配 `pco_ref` 的对象、内容版本、hash 及实际成果条目。

开窗固定确切 PCO＋Mission 版本。评论替代、撤回只影响本人有效意见，保留不可变历史。关窗封存有效意见；一次收拢创建一个 CandidateSet，列明意见取舍与未决差异。候选修订由服务端先产生 PCO 版本，再把 Mission 支持关系绑定到该新版本。CEO 整组确认不沿用旧 A2 全体 DRI 签认规则。

战略变化后待确认方案拒绝沿用旧依据。CEO 可以基于候选或未收拢窗口显式重开，并同时传 `rebase_strategy_ref` 与 `rebase_ltco_ref` 采用已生效的新依据；新窗口保留旧候选作为审阅基线，收拢后再次确认。变更不会改写已经确认的历史 LTCO/PCO/Mission。

## 读取、记忆与恢复

| 接口 | 用途 |
|---|---|
| GET /v1/object-types?contract_version=tkos.method/0.1 | 新版业务类型 schema；不带参数保留旧目录契约 |
| GET /v1/objects?domain_id=…&object_type=… | 域权限内对象列表，带服务端协议元数据 |
| GET /v1/objects/{id} | 最新/有效内容、Method 状态及影响提示 |
| GET /v1/objects/{id}/revisions[/{revision_id}] | 不可变版本与历史，仍检查当前权限 |
| GET /v1/objects/{id}/relations | 按精确版本解析关系，隐藏未授权来源 |
| GET /v1/objects/{id}/action-receipts | 授权内审计回执；单条沿用 /v1/action-receipts/{id} |
| GET /v1/method/review-windows | 可见窗口 |
| GET /v1/method/candidate-sets | 可见候选集合 |
| GET /v1/method/business-facts | 独立事实及修正状态 |
| GET /v1/method/period-reviews | 无需审批的复盘分析及生成版本 |
| GET /v1/method/strategies、strategic-issues、runs | 对应可见集合 |
| GET /v1/method/objects/{id}/reviews?effective_only=true | 协作历史或封存/当前有效意见 |
| POST /v1/context-packs | 按身份、阶段、用途、双时点生成采用快照 |
| GET /v1/context-packs/{snapshot_id} | 原快照取回及当前权限复核 |
| GET /v1/method/objects/{id}/recovery | 关联运行、步骤尝试、暂停状态与恢复依据 |
| GET /v1/method/missions/{id}/handoff | 已确认新版 Mission 的明确下游承接投影 |

Method 集合支持 `domain_id`、`after` 与 `limit`（1–100），返回 `next_after`。单条对象对有限授权者不返回其无权访问的其他版本正文。原始证据沿用上传/下载接口，读取精确存储版本并复核 hash；对象内容与原始字节分离。

Context 请求在已有 `object_ids、valid_at、known_at` 上增加 `contract_version="tkos.method/0.1"、stage、purpose、include_drafts`。声明 Method 协议时 `stage` 与 `purpose` 均必填。阶段支持 general、research、meeting、strategic_update、planning、review、confirmation、period_review、handoff；用途支持 general、analysis、review、decision、handoff。未识别词会明确记录 general fallback 与 selection_notes。材料选择依据请求时点的不可变状态事件和实际生效事件；不会把后来确认的草稿当成过去正式依据。返回每个采用版本、材料性质、协作记录及排除原因。正文来源仍逐条做授权，窗口授权不扩大来源访问范围。

运行关联动作是 `method_open_run、method_attach_run、method_pause_run、method_resume_run、method_record_attempt`。新运行是独立根，不接受嵌套 `run_ref`；新业务根可在信封显式传 `run_ref` 三元组与 `step_key`。已有对象的后续动作继承其目标运行。历史分析来源不使下一周期自动继承旧运行，也不因旧运行暂停而失去可引用性。暂停阻止关联业务写；恢复先读取当前状态，再 prepare 合法下一步。失败调用本身不冒充已持久化步骤：应用可通过 record_attempt 记录失败原因或放弃，成功业务步骤与回执在同一事务记录。底座不自建业务编排引擎。

## Clark 边界

Clark 的当前页面和本地状态保持原有行为。本轮交付新协议接入契约、动作 schema 和真实调用驱动，见 `docs/method-clark-contract.md`。Clark 应使用人的各自身份，依据提交成功回执刷新页面、触发其通知编排；不得将本地按钮状态视为 Runtime 成功。接口返回的错误不应被本地“强制确认”绕过。

新版 Mission handoff 明示 `execution_authority=null`，交付验收、Outcome 达成、MF 关闭分别未判断。对接 M2 前还需要明确 DRI、IC、验收人、授权时限和承接契约。本轮不部署、不迁移生产，不以合成 Agent 输出证明专业研究质量或经营效果。

# Governed Runtime 实现说明与验收边界

日期：2026-09-07。本文描述独立验收工作区中的实现，不是发布或生产部署声明。是否通过独立验收，应以对应 `test_run_id` 的真实 HTTP、SQL、对象存储、Worker 与接收器证据为准；本文不预先宣布测试通过。

## 1. 本期实现范围

`src/memory_service_runtime/governed/` 是 Memory Service 内新增的治理模块。正式写入口为认证后的 `/v1` HTTP API，PostgreSQL 保存权威状态，版本化 S3/MinIO 保存原始证据，现有 `runtime_tasks` 与 `RuntimeWorker` 承担事务外任务分发。

首期独立运行一个业务域的以下闭环：

`CompanyOutcome → BusinessCommitment → ExecutionCommitment → FeedbackThread → ManagementAdjustment → Acceptance → Closure`

其中 `Decision` 提供人类确认的处理依据，`MetricObservation` 提供带有效时间和系统记录时间的观测，`EvidenceAsset` 指向实际保存且可校验的证据字节。正式承诺必须使用精确版本引用和两个不同自然人的签认；同一自然人的多个角色不形成独立双方。

这个内核不等于完整企业本体实现：没有实现全部 28 个业务类、战略判断工作台、自动因果验证或全面领域扩展管理。当前 `CompanyOutcome.terms`、承诺 `terms` 和 `Decision.statement` 是试点的最小业务内容契约；验收结果来自有权且独立的人的明确判断，系统负责验证作用域、证据完整性、版本、人员独立性及处理条件，不能据此宣称机器已证明经营结论为真。

## 2. 身份、作用域与权限

- 随机生成的 opaque bearer credential 只用于解析数据库中的真实 principal；数据库保存 credential digest。请求 body 不能选择 tenant、company、actor 或 role。
- `gov_scopes` 把公共 scope UUID 映射为 tenant/company；每个动作和读取事务先锁定该 scope 的 `auth_epoch` 行，再检查 credential、principal 和当前有效的 RoleAssignment。
- 角色的有效期、active 状态、domain 及当前存储策略的 `action_roles` 共同决定动作权限。未知动作、没有规则的动作、域外访问以及过期/撤销的身份均不能获得许可。
- BusinessCommitment 指定精确的 CEO 与 DOMAIN_DRI assignment；ExecutionCommitment 指定精确的 DOMAIN_DRI 与 MISSION_DRI assignment。具备同名角色但不在该承诺 required parties 中的人不能代签。
- 独立验收使用 `VERIFIER`，且不得由反馈承接人、处理决策作者、调整作者/实施人或相关候选承诺的作者与签认人自行验收。
- Agent 只能按策略创建/修改 FeedbackThread 和 MetricObservation 候选，不能签认、激活、确认决策/目标、应用调整或关闭反馈。

当前权限是数据库策略支持的试点身份适配器，不是外部 SSO、完整企业 IAM 或任意条件表达式 DecisionRight 引擎。RoleAssignment 的物理作用域目前是 domain；使命的精确签认资格进一步通过承诺 required assignment IDs 限定。更细的字段级权限、跨域联合审批与使命级通用委托仍需另外设计和验收。

所有新业务表启用并强制 RLS，受限 runtime DB role 不是表所有者、superuser 或 BYPASSRLS。隔离策略使用服务端认证后设置的事务级 scope，应用代码仍执行域和对象检查。scope 锁是首期刻意采用的简单一致性策略：同 scope 的请求会串行化，不能把它描述成已经完成大规模并发性能设计。

## 3. 数据表写权限分类

迁移本身不创建应用角色，也不向一个固定角色授予权限；独立验收基础设施按照以下分类授予最小权限。

| 类别 | 表 | 允许的应用操作 |
|---|---|---|
| 可变权威状态 | `gov_scopes`, `gov_principals`, `gov_credentials`, `gov_role_assignments`, `gov_objects`, `gov_feedback_state` | 按实际入口需要授予 SELECT/INSERT/UPDATE；不授予 DELETE |
| 追加记录 | `gov_domains`, `gov_activation_policies`, `gov_object_revisions`, `gov_handshakes`, `gov_lifecycle_events`, `gov_action_receipts`, `gov_acceptances`, `gov_context_snapshots` | SELECT/INSERT，禁止 UPDATE/DELETE，追加表触发器作为额外约束 |

`object_id` 是稳定身份；内容变化创建新的不可变 `revision_id`；每次权威状态变化递增 `object_version`。`latest_revision_id` 可以是尚未生效的候选，`effective_revision_id` 在新版本完整确认前保持原值。

对象事件同时保存 `before_version`、`object_version`、`revision_id`、`effective_revision_id` 和处理周期，供历史重建。撤权对象属于授权表，不伪装成业务对象；其不可变 ActionReceipt 记录 before/after active 和 auth_epoch，承担授权变更审计记录。

## 4. 动作契约

`models.ActionRequest` 禁止多余字段；版本使用 StrictInt，不能把布尔值或数字字符串当成合法版本。UUID 标准化为字符串；时间必须包含时区。创建与修订的 payload 按对象类型校验，修改时再次以数据库中实际 object_type 为准，不能通过请求声称另一类型绕开约束。

四个核心动作保留原设计的参数契约：

| 动作 | 实际行为 |
|---|---|
| `accept_commitment` | 仅记录本人对当前精确 candidate revision/hash 的接受；不切换 effective 指针。重复同人同版接受不生成第二票 |
| `activate_commitment` | 验证当前策略、完整双方签认、当前角色及上游有效版本，首次使承诺生效并预约外部任务 |
| `confirm_adjustment` | 验证完整变更集合、所有同版签认、内部调整后引用、外部有效引用与反向有效依赖，同事务切换全部有效指针、记录反馈处理范围并预约外部任务 |
| `confirm_closure` | 验证当前处理周期、最新独立验收、相同决策和证据、实际 S3 字节及相关外部效果完成；显式区分 resolved/no_change/dismissed |

支持动作包括 `create_object`、`propose_revision`、`confirm_decision`、`confirm_outcome`、`route_feedback`、`accept_feedback`、`investigate_feedback`、`request_feedback_acceptance`、`record_acceptance`、`reopen_feedback`、`revoke_assignment`。`confirm_outcome` 是补齐新建正式公司目标所需的支持动作，只允许 CEO。

已经生效的承诺可提出和签认新候选，旧版仍有效。正式修改现有生效承诺通过 ManagementAdjustment；bundle-bound candidate 不能单独激活。调整可以先建立空 shell 来绑定候选，但空 shell 永远不能应用。确认时必须提交与不可变调整 revision 完全一致的非空 changes。

`/v1/actions/prepare` 只解析当前依赖与 expected_versions，不创建业务状态、签认或回执。客户端需要显式提交服务器要求的所有可变业务对象版本，包含创建/接受时读取的上游以及 bundle。授权表没有伪造 object_version，其当前有效性由同 scope 的授权 fence 保护，并在最终提交前再次检查自然到期情况。

## 5. 反馈、验收与重新打开

反馈通常沿 `open → routed → accepted → investigating → awaiting_acceptance → closed/dismissed` 流转。只有当前指定承接人能执行 accept/investigate。调整应用后只是等待验收，不能顺带关闭反馈。

有调整的 resolved 路径必须绑定已应用调整及其完整处理范围。无调整的 no_change/dismissed 路径先经 `request_feedback_acceptance` 绑定已确认 Decision，再由独立 verifier 验收。dismissed 单列统计，不计为经营问题解决。

`record_acceptance` 为每次判断追加独立记录，accepted 保持待关闭状态，changes_requested 返回调查。关闭只能引用最新、accepted、同对象/同 revision/同 cycle/同决策/同证据的记录，不能复用另一线程或旧处理周期的验收。

重新打开保存原历史，生成新 revision 和 processing cycle，清除旧处理指针。原承接人仍有效时进入 investigating；已失效时进入 open，重新路由和承接。新周期不能使用原关闭记录或原验收记录完成结案。

## 6. 回执、幂等与外部任务

同一个数据库事务保存业务变更、不可变 revision/签认/事件/验收、冻结的 ActionReceipt 与 `runtime_tasks` 预约。service 函数不自行 commit、不另开连接修改业务，也不把外部 HTTP 伪装成与 PostgreSQL 的单事务。

幂等键按 scope + principal + idempotency_key 唯一。相同键、不同动作或不同规范化命令内容返回冲突；重放在返回旧成功之前仍核验当前读取权限，包括结果中引用的对象和 domain。冻结回执不嵌入后来会变化的 task state；读取接口另返回当前 effects 状态。

Worker 使用已有 claim、lease、retry、lease token fencing 和 finalize 机制。`governance.dispatch` 的目标只能来自可信服务器环境 `GOVERNED_EFFECT_URL`，不能由业务请求选择。分发依据不可变回执重新校验 scope、task 归属、实际动作人以及全部 required assignment IDs，再发往目标。当前实现为简单且有时限的分发在检查期间保留 scope fence，慢调用会影响同 scope 请求延迟。

队列预约键和接收端幂等键是两个职责不同的标识。队列用 scope/receipt 派生的稳定预约键；实际 HTTP 使用 receipt_id + task_id 派生的稳定 effect key，并由不可变回执构造发送正文。接收器把调用次数与唯一效果分开持久化。接收器成功后、Worker ack 前发生进程崩溃时，允许重试同一外部效果；单次效果成立依赖接收端的持久幂等账本，不依赖“网络只发送一次”的假设。

这验证的是独立测试接收器的真实外部效果，不是 ERP、客户系统或生产业务动作已经接通。其他目标系统如果缺少幂等和可查询回执，需要补充对账与补偿设计。

## 7. 证据与时间

证据上传实际保存对象字节并记录 bucket/key/version_id/hash/length；下载和验收校验精确存储版本与 hash。数据库写入失败后可能残留未被引用的 S3 版本，但不能因此构造成功的证据业务记录。

MetricObservation 的有效区间写入 revision 的 valid_from/valid_to；系统 recorded_at 由服务器生成。更正保留原 object_id，追加新 revision。历史查询同时使用 valid_at 与 known_at，不把后来才知道的数据放入旧决策/上下文。正式承诺、Decision 和 CompanyOutcome 的历史生效版本通过事件重建，不能拿今天的 effective 指针代替历史状态。

ContextSnapshot 保留当次选择的精确版本，读取旧快照仍按当前权限检查。历史有效的授权不允许绕过今天已经撤销的读取权。

## 8. 验收与交付边界

独立验收使用新建的数据库、对象存储数据和 fixture 身份，业务成功状态由真实 HTTP 动作形成，不预置成功签认、已关闭对象或结果回执。受测进程与接收器是真实独立进程，响应丢失和 Worker 崩溃需要真实断连接/kill/restart，并用 SQL、S3 hash 和接收器账本交叉断言。

测试控制在 `acceptance/runtime/server.py`、`worker.py`、`proxy.py` 内，生产 main 不导入这些 helper。生产 checkpoint 默认为 no-op，不能通过普通 HTTP 请求获得绕过业务规则的能力。故障事件只保留白名单元数据，随机测试密钥与 bearer credentials 归入私有测试材料，不能打包进公开验收报告。

本期明确不包含外部 SSO 接入、Clark 写权迁移或新一轮 Clark 集成验收、Semantica 图/向量接入、生产容量验证、正式发布、staging 切换或生产部署。备份恢复、重建、迁移重放、原有测试是否完成，应在独立验收报告逐项给出证据；未验证项目不能以文档存在或服务健康替代。

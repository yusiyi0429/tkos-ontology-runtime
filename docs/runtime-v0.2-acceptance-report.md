# Runtime v0.2 独立验收报告

2026-09-07，完整运行 `runtime-a15a50851637` 通过全部 **20 组检查**，pytest 为 **164 passed、1 skipped、0 failed**，空库迁移重放与 sdist/wheel 构建通过。验收对象为本地 Runtime v0.2.0，包含真实 HTTP API、Worker、PostgreSQL 和启用版本管理的 MinIO；身份与业务数据为验收专用合成数据。

本结果表示 Runtime 独立验收通过，不表示真实员工账号已接入、Clark 联调完成、版本已发布或远程机器部署通过。运行时间为 UTC 05:56:35 至 05:57:28（北京时间 13:56:35 至 13:57:28）。

## 业务闭环证据

使用已生效 ExecutionCommitment 的指定 MISSION_DRI 创建 WorkItem，冻结承诺版本、DRI、验收人和验收标准。后续通过命令 API 完成承接、提交 v1、退回、回应退回后提交 v2、由指定且当前有权的人验收。

| 执行到的阶段 | 交付状态 | Outcome 判断 | MF 状态 |
| --- | --- | --- | --- |
| DRI 承接 | in_progress | not_assessed | investigating |
| 提交 v1，指定验收人退回 | changes_requested | not_assessed | investigating |
| 提交 v2，指定验收人通过 | delivery_accepted | not_assessed | investigating |
| 单独依据指标 72、目标 80 评估 | delivery_accepted | not_achieved | investigating |
| 单独依据后续指标 80 评估 | delivery_accepted | achieved | investigating |
| MF 独立完成 Decision、验收与关闭动作 | delivery_accepted | achieved | closed |

同一 Deliverable 保存不可变的 v1、v2 及两次验收记录。v2 必须回应确切的退回记录；验收绑定当前提交的 revision、payload hash 和冻结标准。交付验收 ID 不能替代 MF 验收 ID。`confirm_outcome` 只确认目标内容，达成判断由独立 `record_outcome_assessment` 记录。

## 权限、版本与证据

五组新增检查覆盖冻结派单基线、指定 DRI、提交并发和幂等、退回补充与精确版本验收、三个判断独立，以及独立作用域内的身份和撤权检查。关键拒绝场景包括：

- 相同角色但非指定人员代为承接或验收；同一人借不同角色任职自验收；旧 policy 未明确授权新动作。
- 偷改 WorkItem 或 Deliverable 基线；过期提交、错误 hash、缺失或重复标准、不合规的通过结论，以及 v2 未回应最新退回记录。
- 并发提交与重复请求；撤权后继续执行已准备的验收、重放旧回执、读取旧回执。保留其他域权限的正向读取仍返回 200。
- 过期或未来生效的指标观察用于当前 Outcome 判断。历史 Context 同时受 `valid_at`、`known_at` 约束，后来的达成结论不会泄漏到旧时点。

交付和 Outcome 动作校验真实 EvidenceAsset 对象的存储内容。原有故障检查继续证明：证据存储不可用时拒绝 MF 关闭；Worker 崩溃重放使用持久效果账本去重，9 个独立效果对应 10 次调用。

新增权限专用作用域只验证身份、策略和撤权：其中两条承诺任务保留为 queued，没有启动该作用域的 Worker，明确记录 `external_effects_accepted: false`。这些任务不计为外部效果验收；原有主作用域 Worker 故障重放组独立通过。

## 持久化与恢复

数据卷停启后和独立恢复后，逐项比较 37 个对象、47 个版本、126 条回执、10 个 Context 快照及 4 份证据，内容与哈希均一致。备份涉及 46 张表；恢复后再次核验交付的两次提交、两次验收及 Outcome 达成结果。

独立恢复使用同一 PostgreSQL 实例内的新数据库和单独的 MinIO 数据卷；此项不是整机灾难恢复。API/Worker 使用本地 Python 3.13.14 进程，Dockerfile 的 Python 3.12 容器及目标服务器尚需单独验收。

唯一跳过项为原有 `test_viewer_rejects_non_human`，原因是数据库没有 `agent_service` fixture；未将该项计为通过。

## 源码与可复验材料

- [脱敏验收摘要](acceptance/runtime-v0.2-summary.json)：20 组结果、回归结果、恢复数量和三个独立判断的结果。
- [验收源码清单](acceptance/runtime-v0.2-source-sha256.json)：135 个文件的 SHA256，包括运行源码、迁移和验收代码。
- [v0.2 接口与业务契约](runtime-v0.2-dri-delivery-contract.md) 与 [OpenAPI](../contracts/openapi.json)。
- [完整验收运行方法](../acceptance/runtime/README.md)。

验收工作树位于 `codex/runtime-v0.2-dri-delivery`，基于 `a02d8b297394be7f86853ffad22406d2ff0a95a4`，包含尚未提交的 v0.2 修改。本次未提交、推送、修改 Clark 或部署远程服务。不能以基线 commit 单独代表此次验收源码，应按清单核对工作树。

完整原始报告在本地忽略目录 `artifacts/runtime-acceptance/runtime-a15a50851637/`。发布用摘要不包含私有凭据、原始数据库或对象存储内容。文档整理没有修改已记录哈希的验收源码。

## 首期范围

DRI 限定为生效 ExecutionCommitment 的 MISSION_DRI 签署人；交付验收人为派单时指定的、与 DRI 不同的当前有效人类 CEO、DOMAIN_DRI 或 VERIFIER。Outcome 本期仅实现 CompanyOutcome，由当前有效人类 CEO 独立评估，并受 action policy 约束。

本期不包含责任人重指派、验收基线变更、交付通过后的重开或撤销、追溯日期评估、外部 SSO 和 Clark 新交互。当 CompanyOutcome 已有尚未确认的新候选版本时，现有最新版本约束会阻止继续对旧有效版本执行达成评估；需先处理该候选版本。

# TKOS Ontology Runtime v0.2

TKOS 企业本体与记忆系统的治理运行内核。PostgreSQL 保存权威状态与审计记录，版本化 S3/MinIO 保存原始证据，API 接收有权限、带版本和幂等键的动作，Worker 执行已授权的外部效果。

首期覆盖一个业务域的闭环：

`CompanyOutcome → BusinessCommitment → ExecutionCommitment → FeedbackThread → ManagementAdjustment → Acceptance → Closure`

## v0.2：真实 DRI 交付闭环

`派单 → DRI 承接 → 提交 v1 → 有权人退回补充 → 提交 v2 → 有权人验收`

WorkItem 固定承诺版本、指定 DRI、指定验收人及标准。Deliverable 每次提交生成不可变版本，v2 显式回应上轮退回记录；交付验收、Outcome 达成判断和 MF 关闭使用独立记录与动作。交付通过不会自动判定经营目标达成，也不会自动关闭反馈。详见 [v0.2 契约](docs/runtime-v0.2-dri-delivery-contract.md)。

本期 DRI 为生效 ExecutionCommitment 的 MISSION_DRI 签认人；Outcome 评估先覆盖 CompanyOutcome，由当前 CEO 权限判断。改派、任务重基线、完整企业身份接入和 Clark 界面联调后续单独完成。

## 当前能力

- 精确版本的双方签认、承诺激活、整组管理调整与事务回滚。
- 数据库中的身份与域权限、强制 RLS、撤权后旧回执和快照的读取校验。
- 乐观版本检查、不可变 revision/ActionReceipt、请求重放和冲突拒绝。
- 原始证据上传与 hash/version 校验，独立人员验收后显式关闭反馈。
- 有效时间与记录时间查询、冻结上下文快照。
- 持久任务队列、Worker 崩溃重试，以及接收端持久幂等账本验证。

服务负责执行治理规则与保留证据；经营判断的正确性仍由有权的人确认。它不是完整企业本体、外部 SSO 或应用工作台。

## 接口与代码

Native API 的完整路径、请求和响应见 [OpenAPI](contracts/openapi.json)，动作和验收要求见 [Runtime 契约](docs/runtime-independent-contract.md)。实现、权限与边界见 [实现说明](docs/runtime-implementation-notes.md)。

| 目录 | 用途 |
| --- | --- |
| `src/memory_service_runtime/governed/` | 本体对象、Action、授权、证据与回执 |
| `src/memory_service_runtime/` | 持久队列与 Worker |
| `src/memory_service_app/` | FastAPI、迁移与配置 |
| `src/memory_service/` | 原有 Memory 核心与数据库迁移 |
| `src/adapter/` | 保留的 Clark 只读兼容接口 |
| `acceptance/runtime/` | 独立 HTTP、数据库、S3、故障和恢复验收 |
| `tests/` | 原有回归与 Worker 测试 |

Python 包名 `tkos-memory-service`、模块名和 CLI 保持兼容。Clark 应用由伙伴维护，不在本仓库中；当前兼容读取接口不能代替新的 Clark 联调验收。`/v1/context-graph/narrative` 和长任务续租不属于当前实现。WorkItem/Deliverable 的 API 已加入本仓库，Clark 仍需按新契约接入。

## 本地独立验收

需要 Python 3.12+、uv、Docker Engine 与 Compose。先按 [验收说明](acceptance/runtime/README.md) 缓存固定镜像，然后在本仓库目录执行：

```bash
uv sync --frozen --extra s3
python3 acceptance/runtime/infra.py up
.venv/bin/python acceptance/runtime/run.py
```

脚本创建独立 PostgreSQL/MinIO 数据卷，生成私有测试凭据，并以本机子进程启动 API/Worker。完整验收包含原有 15 组检查和 5 组 v0.2 交付检查、原有 pytest、迁移重放、sdist/wheel 构建和独立恢复；只有全部通过才写入 `runtime_accepted: true`。它会短暂停启自己的数据服务，不应并行执行多个完整 runner。

本地凭据在 `.runtime-acceptance/`，运行证据在 `artifacts/`，均不提交。保留凭据目录与数据卷作为一组；脚本不提供删除数据卷的命令。

## 验收证据

后续 **Clark v0.2 本地业务联调已通过**：真实页面的 DRI v1/v2 交付、独立 Outcome 评估与 MF 关闭，以及响应丢失后刷新重试、服务不可用和重启检查。详见 [联调报告](docs/clark-v0.2-acceptance-report.md) 与 [联调启动方法](acceptance/clark_v02/README.md)。本次为 Clark 新增独立交付入口，使用合成个人测试身份，未发布或部署远程。以下独立验收报告仍作为各自时间点的历史证据保留。

2026-09-07，v0.2 完整独立验收 `runtime-a15a50851637` 通过 **20 组检查、164 passed、1 skipped**，迁移重放、sdist/wheel 构建、数据卷重启及独立备份恢复均通过。跳过项仍是缺少 `agent_service` fixture 的原有非 human viewer 检查。

详见 [v0.2 验收报告](docs/runtime-v0.2-acceptance-report.md)、[脱敏摘要](docs/acceptance/runtime-v0.2-summary.json) 与 [135 个源文件 SHA256](docs/acceptance/runtime-v0.2-source-sha256.json)。这是使用测试身份和业务数据完成的本地 API/Worker 验收，尚未进行 Clark v0.2 联调或远程部署验收。

以下是 v0.1 的历史验收基线：

2026-09-07，导出前的独立验收运行 `runtime-2e941f404803` 通过全部 15 组检查，原有测试为 **137 passed, 1 skipped**，sdist/wheel 构建通过。跳过项是缺少 `agent_service` fixture 的原有非 human viewer 检查。

公开保留 [脱敏验收摘要](docs/acceptance/runtime-independent-summary.json) 和 [87 个生产源文件 SHA256](docs/acceptance/production-source-sha256.json)。v0.1 导出时生产源文件逐字节一致；后续 v0.2 修改使用新的源码清单；原始数据库、对象存储内容、令牌及原始运行产物未公开。该结果是本地 Runtime 验收，不表示 Clark 集成、容器目标架构或远程部署已经验收。

导出后的独立仓库再次执行完整验收：`runtime-246ed4a1bc03` 同样通过 **15 组、137 passed, 1 skipped**，包含构建和恢复验证。详见 [独立仓库复验摘要](docs/acceptance/standalone-export-summary.json) 与 [本次源码清单](docs/acceptance/standalone-source-sha256.json)。本次复验使用 Python 3.13.14，本地 API/Worker 为 Python 进程；Dockerfile 的 Python 3.12 容器运行另需目标环境验证。

## 部署与来源

代码可构建 API/Worker 镜像，远程试点还需要环境初始化和部署验收，见 [部署边界](docs/deployment.md)。根目录没有可直接投产的 Compose；历史 Memory 模板仅保留在 `deploy/legacy-memory/`。

本仓库以独立源代码快照初始化，来源及基线见 [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)。

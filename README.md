# TKOS Ontology Runtime

TKOS 企业本体与记忆系统的治理运行内核。PostgreSQL 保存权威状态与审计记录，版本化 S3/MinIO 保存原始证据，API 接收有权限、带版本和幂等键的动作，Worker 执行已授权的外部效果。

首期覆盖一个业务域的闭环：

`CompanyOutcome → BusinessCommitment → ExecutionCommitment → FeedbackThread → ManagementAdjustment → Acceptance → Closure`

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

Python 包名 `tkos-memory-service`、模块名和 CLI 保持兼容。Clark 应用由伙伴维护，不在本仓库中；当前兼容读取接口不能代替新的 Clark 联调验收。`/v1/context-graph/narrative`、WorkItem/Deliverable 和长任务续租不属于当前实现。

## 本地独立验收

需要 Python 3.12+、uv、Docker Engine 与 Compose。先按 [验收说明](acceptance/runtime/README.md) 缓存固定镜像，然后在本仓库目录执行：

```bash
uv sync --frozen --extra s3
python3 acceptance/runtime/infra.py up
.venv/bin/python acceptance/runtime/run.py
```

脚本创建独立 PostgreSQL/MinIO 数据卷，生成私有测试凭据，并以本机子进程启动 API/Worker。完整验收包含 15 组检查、原有 pytest、迁移重放、sdist/wheel 构建和独立恢复；只有全部通过才写入 `runtime_accepted: true`。它会短暂停启自己的数据服务，不应并行执行多个完整 runner。

本地凭据在 `.runtime-acceptance/`，运行证据在 `artifacts/`，均不提交。保留凭据目录与数据卷作为一组；脚本不提供删除数据卷的命令。

## 已有验收证据

2026-09-07，导出前的独立验收运行 `runtime-2e941f404803` 通过全部 15 组检查，原有测试为 **137 passed, 1 skipped**，sdist/wheel 构建通过。跳过项是缺少 `agent_service` fixture 的原有非 human viewer 检查。

公开保留 [脱敏验收摘要](docs/acceptance/runtime-independent-summary.json) 和 [87 个生产源文件 SHA256](docs/acceptance/production-source-sha256.json)。导出时生产源文件逐字节一致；原始数据库、对象存储内容、令牌及原始运行产物未公开。该结果是本地 Runtime 验收，不表示 Clark 集成、容器目标架构或远程部署已经验收。

导出后的独立仓库再次执行完整验收：`runtime-246ed4a1bc03` 同样通过 **15 组、137 passed, 1 skipped**，包含构建和恢复验证。详见 [独立仓库复验摘要](docs/acceptance/standalone-export-summary.json) 与 [本次源码清单](docs/acceptance/standalone-source-sha256.json)。本次复验使用 Python 3.13.14，本地 API/Worker 为 Python 进程；Dockerfile 的 Python 3.12 容器运行另需目标环境验证。

## 部署与来源

代码可构建 API/Worker 镜像，远程试点还需要环境初始化和部署验收，见 [部署边界](docs/deployment.md)。根目录没有可直接投产的 Compose；历史 Memory 模板仅保留在 `deploy/legacy-memory/`。

本仓库以独立源代码快照初始化，来源及基线见 [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)。

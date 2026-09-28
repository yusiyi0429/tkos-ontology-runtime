# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本仓库的贡献约定见 `AGENTS.md`（目录结构、命令、风格、提交规范），本文件只补充"读多个文件才能看出来"的架构与约束。文档与代码注释以中文为主。

## 常用命令

```bash
uv sync --frozen --extra s3                      # 安装锁定依赖（S3 证据存储需 extra）
uv run uvicorn memory_service_app.main:app --host 127.0.0.1 --port 8010
uv run tkos-memory-worker                        # Worker；tkos-memory-worker-health 是探针
uv run tkos-memory-migrate                       # 按文件名顺序应用 append-only 迁移
uv run tkos-governed-control ...                 # A1 控制面（需 MIGRATION_DATABASE_URL）
uv build                                         # sdist + wheel
```

测试（`tests/conftest.py` 只对标了 `db` 的用例要求 `DATABASE_URL`，没设时这些用例直接失败、不会退回本机默认库；无库用例不设也能跑）：

```bash
uv run pytest tests -q -m "not db"                                             # 全部无库用例，无需 DATABASE_URL
uv run pytest tests/test_method_m1b_models.py -q -k original_source            # 按名字挑用例
(cd workbench/dashboard && npm test && npm run typecheck)                      # 看板（Vite + React + TypeScript），无需 DB
(cd workbench/dashboard && npm run build)                                      # 重建随仓库提交的 src/memory_service_app/dashboard_dist
python3 acceptance/runtime/infra.py up                                         # 隔离 PG+MinIO
python3 acceptance/runtime/infra.py run --migration -- .venv/bin/python -m pytest tests/test_migrations.py -q
```

`infra.py run` 只注入应用角色；`--migration` 才切到 owner DSN 并注入 `TEST_ADMIN_DATABASE_URL`（`test_migrations.py` 需要它建临时库）。角色分离不是可选项：API/Worker 与迁移所有者必须是不同的数据库身份。

改了 `workbench/dashboard/` 的源码要重新 `npm run build`：构建产物 `src/memory_service_app/dashboard_dist/` 随仓库提交，其中 `asset-manifest.json` 记录全部前端输入的哈希；`tests/dashboard/test_dashboard_assets.py` 与打包钩子都经 `scripts/verify_dashboard_assets.py` 校验，产物没重建就会失败。

## 架构

### 进程与 HTTP 边界

`memory_service_app.main` 把以下 router 装进同一个 FastAPI 应用：

- `routes_native`（`/v1/health`）——进程自身健康；
- `memory_service_runtime.governed.routes`（`/v1/actions`、`/v1/objects`、`/v1/context-packs`、`/v1/evidence-assets`、`/v1/method/*`）——治理内核，Bearer 身份；
- 同目录的 `governance`（`/v1/governance/*`，治理工作台）、`dashboard_routes`（`/v1/dashboard/*`）、`workspace_routes`（Workspace 场景）——工作台与看板用的 API；`memory_service_app.dashboard.mount_dashboard` 另外挂载构建好的看板静态包（`/dashboard/`）；
- `memory_service_app.narrative`（`/v1/context-graph/narrative`）——Clark 叙述兼容，`TKOS_NARRATIVE_ENABLED` 默认关闭，只读、不产生业务记录或回执；
- `adapter.routes_*`——保留的 Clark GraphKnowledge 只读 façade，统一挂 `require_clark_auth`（`ADAPTER_AUTH`）。

`src/memory_service/` 是更早的 Memory 核心（working memory、context profile、episodic 等），治理内核不经过它；`tests/conftest.py` 的 scope 夹具通过 `memory_service.working` 的治理写路径播种，不裸写 `wm_*` 表。

### 一次治理动作的生命周期

`routes.action` → `db.transaction(token)` → `service.execute_action`，全程一个事务、一个连接：

1. `db.authenticate`：token 取 SHA-256 摘要查 `gov_credentials`，`SELECT ... FOR UPDATE` 锁 `gov_scopes` 形成 **per-scope 授权栅栏**，然后重新校验凭证、principal、`gov_role_assignments` 的当前有效性，产出 `AuthContext`（含 `auth_epoch`）。
2. `protocol` 模块做服务端协议归属：对象绑定（`gov_object_protocol_bindings`）、域策略（`gov_protocol_policies`）、支持登记（`gov_protocol_support_registry`）共同决定谁能写什么。**顺序固定**：先认证/授权（404/403），再暴露协议错误，避免泄漏不可见对象的存在。
3. `db.authorize_domain` 用 `gov_activation_policies.action_roles` 判权——允许哪些角色做哪个 `action_type` 写在**数据库策略里**，不在代码里；改权限是控制面操作，不是改 Python。
4. 业务执行：乐观并发（`expected_version` + `latest_revision_id` 比对）、不可变 `gov_object_revisions`、`gov_action_receipts`，以及 `enqueue_task` 写入同事务的任务 outbox。要么一起提交，要么一起回滚。

因此：**任何治理代码都不得中途 commit、切换连接，或把 `AuthContext` 留到下一个事务用**（`governed/db.py` 顶部有明确说明）。

### 并存协议，语义各自独立

`SUPPORTED_PROTOCOL_CONTRACTS`（`governed/protocol.py`）是本进程编译期支持的 `(protocol_id, contract_version)` 全集——legacy v0.2、Contract-A，以及从 `tkos.method/0.1` 起的各个 Method 版本（当前清单以该常量为准）。控制面登记**不能**扩大这个集合；新增协议支持必须改代码并发版。

对应的执行类都继承 `service.ActionExecution`：

| 协议 | 模型 | 服务 | 读取 |
| --- | --- | --- | --- |
| legacy v0.2 / Contract-A | `models.py`、`delivery.py` | `service.py` | `readers.py` |
| A2 公司组合 | `a2_models.py` | `a2_service.py`、`a2_composition.py`、`a2_activation.py` | `a2_readers.py` |
| A3 执行交接 | `a3_models.py` | `a3_service.py`、`a3_governance.py`、`a3_delivery.py` | `a3_readers.py` |
| Method 0.1（M1A/M1B） | `method_m1a_models.py`、`method_m1b_models.py`、`method_common_models.py` | `method_service.py`（`MethodExecution`，按 `contract_version` 分派）、`method_m1a.py`、`method_m1b.py` | `method_readers.py` |
| Method 0.2 及以后 | 每版 `method_vNN_models.py`、`method_vNN_profile.py` | 每版 `method_vNN.py`；部分版本复用上一版模块（`method_v03` 引 `method_v02`，`method_v05` 引 `method_v04`），改旧版模块会连带改变新版本 | `method_readers.py`、`method_access.py`（读权限）；0.3 另有 `method_v03_readers.py` |

新增一个动作通常要同时改：该协议的 `*_models.py`（严格 Pydantic：`extra="forbid"`、`Strict*`、`allow_inf_nan=False`）、`*_service.py`/`method_*.py` 的授权与状态迁移、必要时 `*_readers.py`，以及 append-only 迁移。旧协议的对象含义、角色、生效规则必须原样保留——不要为了新协议"统一"旧语义。

### 数据库侧的护栏（迁移 0018）

- `app.runtime_write_capability` GUC：`db.set_write_capability` 在每个事务/连接首次读受保护表前声明；0018 的 restrictive policy 会对没声明的连接隐藏 `gov_credentials`/`gov_scopes` 等行。这是**拦截陈旧二进制**的机制，不是对抗任意 SQL 的安全边界。
- `gov_control_plane_on()`：控制面 CLI（`governed/control.py`）只接受 `MIGRATION_DATABASE_URL` 的 owner 身份，单事务执行，失败全回滚，变更记入 `gov_protocol_control_events`。应用角色即使知道 GUC 名也用不了这个面。
- 对象插入必须已有协议绑定（`gov_objects_require_binding`），不可变表有 `gov_reject_mutation` 触发器。
- RLS 按 `app.governed_scope_id` 生效，`db._set_scope` 每次事务设置。

### 规范化、证据与效果

- `canon.py` 实现 `tkos-json-v1`（键按码点排序、紧凑分隔符、禁 NaN/Infinity/孤立 surrogate，SHA-256）。`canonical_hash`/`manifest_hash` 字段本身不进入自己的摘要输入。不声称符合 RFC 8785。
- `evidence.py`：原始字节进版本化 S3/MinIO（≤2 MiB），数据库只保存 hash 与对象版本；存储不可用返回 `EVIDENCE_UNAVAILABLE`(503)，不降级写库。
- `memory_service_runtime/`：`repository.py` 是持久队列（claim/lease/retry/heartbeat，幂等键冲突显式报错），`worker.py` 单进程租约消费，`handlers.py` 是**白名单**任务类型，`governed/effects.py` 的外部派发会在独立连接上**重新校验**当前 scope、receipt 与 principal 权限，并依赖接收端对 `Idempotency-Key` 做持久去重。授权发生在动作提交时，执行发生在之后——不要假设两者之间权限没变。

### 迁移

`src/memory_service_app/migrations/` 严格 append-only，`migrate.py` 按文件名排序、记录在 `schema_migrations` 表里各应用一次。`tests/test_migrations.py` 硬断言完整文件清单，**新增 `NNNN_*.sql` 必须同步更新该列表**。新迁移取目录里下一个未用的编号；前缀并不唯一（已有两个 `0028_*`），执行顺序按完整文件名。

## 验收与测试的分工

`tests/` 的 pytest 和看板的 vitest 都不能代替独立验收矩阵，主要有：

- `acceptance/method_independent/`——M1A+M1B，18 组 98 项检查 + 8 个环境门槛，只有全通过才写 `method_api_accepted: true`。它固定在 2026-09-11 那次运行（只升级 0021），HEAD 上不能原样复跑，其 README 保留为执行记录；该目录的常量与 `method_grants` 仍被后续版本复用。
- 新建隔离验收库统一用 `acceptance/method_v05/database.py`（`create` 再 `upgrade` 到当前源码）：它只接受 `infra.py` 起的隔离验收栈，不连 54350/54351 的 Clark 联动栈；应用角色权限取自发布规则 `deploy/offline-release/db_admin.py`。
- 之后的 Method 版本用按版本命名的目录，例如 `acceptance/method_v04/`、`acceptance/method_v05/`（0.5 实际驱动了哪些路径见其 README）。
- `acceptance/composition_a2_independent/`、`acceptance/execution_a3_independent/`、`acceptance/protocol_a1_independent/`——各协议独立矩阵。
- `acceptance/runtime/`——v0.2 的 HTTP/DB/S3/故障与恢复验收（`infra.py up` 后跑 `run.py`）。
- `acceptance/narrative/`、`acceptance/clark_v02/`、`acceptance/clark_method/`——叙述兼容与 Clark 联调；`acceptance/workbench/`——工作台读取接口的独立 QA（见其 `INDEPENDENT.md`）。

汇报结果时，已执行的检查、跳过项、未验证的部署边界要分开写；合并或本地通过不等于已部署。

## 写代码时容易踩的约束

- 测试数据一律用随机 tenant/organization scope，禁止 `local/local-org`；业务记录必须经治理路径播种。
- 不重写已应用的迁移；包名 `tkos-memory-service`、模块名与 CLI 名保持兼容。
- 不打印或提交凭据、`.runtime-acceptance/`、原始验收产物。
- `artifacts/`、`.runtime-acceptance/`、`draft_*` 是本地产物目录，不要当成源码读写。

## Agent skills

### Issue tracker

票、规格与 wayfinder 地图放在本仓库的 GitHub Issues，用 `gh` CLI 操作。见 `docs/agents/issue-tracker.md`。

### Triage labels

沿用五个默认标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文：根目录 `CONTEXT.md` 是术语表，决策记录放 `docs/adr/`。见 `docs/agents/domain.md`。

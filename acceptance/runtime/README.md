# 独立 Runtime 验收

本目录只管理 `tkos-ontology-runtime-acceptance` 项目的 PostgreSQL/pgvector 与 MinIO。不会读取现有 `.env`，不会复用、停止或修改 `tkos-target-local` 的容器、网络和卷。API/Worker 由验收程序作为本机 Python 子进程运行；这是独立本地验收配置。

## 一次完整验收

启动脚本使用 `--pull never`，首次运行前须准备以下镜像（联网拉取一次，后续复用缓存）：

```bash
docker pull pgvector/pgvector@sha256:7ae6051efd0e60444282c27c7e141af07f322ce033300e727a49c3dd11075e38
docker pull quay.io/minio/minio@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e
docker pull minio/mc@sha256:ba8af71554963bb6ad5c4301ff3d82176e96f0b0c7595047da989cacfae83682
docker pull alpine:3.21.2
```

使用项目现有依赖和 S3 extra 建立 `.venv`，启动独立数据服务，然后执行：

```bash
uv sync --frozen --extra s3
python3 acceptance/runtime/infra.py up
.venv/bin/python acceptance/runtime/run.py
```

runner 每次建立新的合成业务 scope，按真实 HTTP 完成八组业务与故障测试，并补充跨域撤权、完整依赖遗漏、S3 不可用、数据库/S3 停启、第二数据库和独立对象存储卷恢复、原有 pytest 与构建检查。所有必需步骤通过才写 `runtime_accepted: true`。`--through N` 仅供开发定位，始终不构成完整验收。

本地验收证据位于 `artifacts/runtime-acceptance/<run_id>/`：结构化报告、脱敏 HTTP 记录、OpenAPI、SQL 断言、恢复比对、回归 JUnit、接收端 SQLite 账本、源文件 hash 和 `SHA256SUMS`。真实令牌与数据库/S3 凭据仅位于私有目录。不要同时运行两个完整 runner 或备份演练，它们会串行停止自己的 PostgreSQL/MinIO。

## 启动和工具链

导出前的源验收使用 Docker Engine 29.7.2、Compose 5.5.0 和 linux/arm64 镜像；其他目标环境需要自行验证。`infra.py` 使用标准库编排，数据库/S3 操作使用本仓库的 `.venv/bin/python`；也可通过 `TKOS_ACCEPTANCE_PYTHON` 显式指定包含 psycopg/botocore 的 Python 3.12+。API、Worker 和迁移共用该解释器。

```bash
cd tkos-ontology-runtime
python3 acceptance/runtime/infra.py up
python3 acceptance/runtime/infra.py status
```

启动时创建独立命名卷，只把 PG/S3 绑定到系统预选的空闲 `127.0.0.1` 端口，MinIO console 不发布。端口保存在私有状态中，后续启动再次校验实际映射。使用独立 bridge；Docker 29 的 internal network 在本机不创建可用的发布端口，因此这里不启用 internal。数据库和对象存储就绪后，初始化角色、迁移旧 schema、初始化两个 bucket，并用受限应用 S3 身份执行不可变配置探针。启动报告不代表治理动作或外部效果已经验收。

## 私有环境交接

第一次启动在 `.runtime-acceptance/` 生成随机验收凭据；目录 700、文件 600，内部 `.gitignore` 忽略全部内容。`compose.env`、`secrets.json` 与 `env.json` 禁止输出或加入证据报告。遗失凭据但命名卷仍在时，脚本拒绝生成新密码覆盖旧状态；保留目录和卷作为一组。

`env.json` 由测试代码加载，包含以下约定；bootstrap 可以追加测试 token，后续启动保留追加字段：

- `DATABASE_URL` / `APP_DATABASE_URL`：`tkos_acceptance_app`，不是 superuser、对象 owner 或 BYPASSRLS 角色。
- `MIGRATION_DATABASE_URL`：`tkos_acceptance_owner`，非 superuser，仅用于迁移和测试种子子进程；API/Worker 不使用。
- `MEMORY_TENANT`、`MEMORY_ORG`：独立随机 scope。用例仍应再划分独立 scope；不使用 `local/local-org`。
- `TKOS_OBJECT_STORE_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY/REGION/VERIFY_TLS`：现有 object_store adapter 同名参数，BUCKET 为 `runtime-acceptance-snapshots`。
- `TKOS_OBJECT_STORE_ARTIFACT_BUCKET`：`runtime-acceptance-artifacts`。
- `RUNTIME_WORKER_ID`、`TKOS_ACCEPTANCE_PYTHON`：本次 Worker 标识及已验证的 Python 路径。

`app.env` 排除 migration DSN。`infra.py run -- command` 仅给子进程应用权限；`--migration` 只对该子进程把 DATABASE_URL 切为 owner，并注入 `TEST_ADMIN_DATABASE_URL` 供创建/销毁隔离测试数据库。这个 admin DSN 不写入 env.json 或 app.env，不改变父进程或文件中的默认值。命令 stdout/stderr 会捕获并遮蔽已知验收凭据与 token，业务 payload 中的个人数据仍由用例自己避免输出。

```bash
python3 acceptance/runtime/infra.py run -- .venv/bin/python -c 'import os,psycopg; c=psycopg.connect(os.environ["DATABASE_URL"]); print(c.execute("select current_user").fetchone()[0]); c.close()'
python3 acceptance/runtime/infra.py run --migration -- .venv/bin/python -m pytest tests/test_runtime_worker.py -q
```

## 迁移与权限

PG bootstrap 的 superuser 创建独立数据库、vector 扩展及 owner/app 两个角色，隔离迁移测试和备份恢复也使用该管理身份。迁移由 owner 执行，应用角色不拥有表、不拥有数据库、不具备 CREATE ROLE/DB 或 BYPASSRLS。旧非 `gov_*` 表按照已有测试需要授予 CRUD；`schema_migrations` 只授 SELECT。`gov_*` 授 SELECT/INSERT，且仅 `gov_scopes`、`gov_principals`、`gov_credentials`、`gov_role_assignments`、`gov_objects`、`gov_feedback_state` 允许 UPDATE，所有 `gov_*` 均不授 DELETE。infra 不使用全 schema DEFAULT PRIVILEGES；API/Worker 环境无管理身份。

新增治理迁移文件到位后执行：

```bash
python3 acceptance/runtime/infra.py migrate
```

输出会列出实际新应用的迁移、应用角色属性与各 `gov_*` 表的 UPDATE/DELETE 权限，供检查 immutable 表确无这些权限。RLS 的 tenant/principal 设置及强制策略由治理 migration/runtime 实现，infra 仅证明应用连接不能通过 superuser/owner 身份绕过策略。

## 真实外部效果与持久化验收

MinIO artifacts bucket 开启版本化；snapshots bucket 在创建时开启 Object Lock，并设默认 GOVERNANCE 1 天保留。应用 S3 身份只能访问这两个 bucket；snapshot 不授 DeleteObject、BypassGovernanceRetention 或管理权限。它可列版本、读取指定版本及 retention，便于核实实际效果。

现有 `system.noop` 只证明队列；`object_store.preflight` 只读 bucket 设置。当前治理验收通过真实 HTTP 上传/读取 S3 原始证据，通过 `governance.dispatch` 调用另一个独立 HTTP 接收器。接收器把唯一效果和每次调用持久化到 SQLite，按 receipt/task 派生的稳定 key 去重。

关键故障窗是“接收端已提交外部效果，Worker 尚未提交结果时进程退出”。只杀本验收 Worker，恢复后由新 Worker 处理同一 effect，核对独立账本唯一行数、重试调用、task attempt 和 terminal 状态，再重启接收器验证账本持久化。应用数据库去重本身不能保证外部效果唯一；实际对接的外部系统也必须提供持久幂等或对账机制。当前 handler 有短 HTTP timeout；长任务续租和任意耗时作业不在本期验收范围。

```bash
python3 acceptance/runtime/infra.py stop
python3 acceptance/runtime/infra.py start
```

stop/start 保留两个命名卷，重启后用相同原始对象 key/version/hash 及数据库 receipt/task ID 重新读取，不能只看 health。重建 schema/清空数据都不是普通重启的一部分。脚本刻意不提供 down --volumes、reset 或删除命令。

验收报告只保留项目名、镜像 digest、角色布尔属性、迁移名、用例结果、非敏感对象 ID/hash/版本和持久化对比，不带 token/DSN/密码。`infra-report.json` 是基础设施观察，不能代替 API、Worker、授权、真实副作用和恢复验收。

## 独立备份恢复

由主验收程序先停止本测试 API/Worker 和其他写用例，确认静默，再调用：

```bash
.venv/bin/python acceptance/runtime/backup_restore.py --writers-quiesced
```

脚本不会停止其他项目。它先导出独立验收库，短暂停止自己的 MinIO 后用已缓存 `alpine:3.21.2` 归档整个命名卷，随后恢复源 MinIO；卷内 S3 原始 version_id、retention 和 IAM 元数据均保留。它创建新的 restore 卷/MinIO 容器及第二个验收数据库，原库和原卷均不覆盖、不删除。恢复数据库中的对象仍由 migration owner 持有，应用角色重新得到同一精确权限。

自动核对每张表计数及所有可见对象版本的原始字节 SHA256、version_id、retention；不能只比对象名或 ETag。私有 `.runtime-acceptance/restore-<id>/env.json` 是恢复后 API/Worker 的环境，继承原验收 bootstrap token，报告仅输出该文件路径。主验收程序还须以恢复环境启动 API/Worker，验证原 receipt、证据版本及幂等重放；仅报告恢复计数相等不表示业务验收已通过。

归档、恢复卷、恢复数据库及容器保留供复核。脚本不自动删除任何卷或数据库；失败时也保留中间产物，且在源 MinIO 停止后通过 finally 恢复它。再次执行会创建新的 restore ID，不覆盖前次证据。

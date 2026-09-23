# Method 0.5 独立 API 验收

合成人与受控 Agent 的真实 HTTP／PostgreSQL 验收，经 HTTP 驱动 0.5 相对 0.4 的主要差异（不是全部）：由 CO_AGENT 或 CEO 登记、按 company／scope／mission 三种范围确认的 Constraint（含 IC、CEO 的越权拒绝）与跨域引用拒绝、LTCO 审视结论（首次确立 / 维持 / 修订：主链末尾由 CEO_AGENT 修订已确认的 LTCO，再由 CEO 以 `revised` 确认）、只有 DRI 的承诺与 Owner 生效记录、生成即正式的状态与下钻、CEO 确认复盘与下期 PCO 承接、以 CEO 身份读取的公司集合视图与确认记录投影。另核对真实 0.5 对象的读取带 `method_v0_5` 解释状态，以及本 scope 每条绑定行都钉定 0.5 profile 身份、经由 0029 的绑定插入闸门写入。不证明真实模型行为，不代替 0.4 复跑。

以下 0.5 能力没有经 HTTP 驱动，只有单元测试或尚无覆盖：复盘重新生成（`m1b_regenerate_review`）、Constraint 修订（`m1b_revise_constraint`）、Scope DRI 本人登记 / 修订 Constraint 的回退路径、在 0.5 State 上登记经营问题（`method_open_problem`）、五个集合列表（`/v1/method/` 下的 `ltcos`、`pcos`、`missions`、`constraints`、`operating-states`）、非 CEO 调用者读取公司集合视图与确认记录投影，以及治理工作台读取面（`/v1/governance/*`）。

## 前提

- 隔离验收栈正在运行：`python3 acceptance/runtime/infra.py status`（项目 `tkos-ontology-runtime-acceptance`）。本目录工具不启动、停止或重建容器。
- 建库工具要求 Docker 上下文为 `desktop-linux`（仅 Docker Desktop），其他上下文以 `unexpected Docker context` 拒绝。
- 基础 env 为仅本人可读的 `.runtime-acceptance/env.json`（infra 自身状态文件）：应用与迁移角色分离，owner 端点必须就是上述栈 PostgreSQL 当前发布的回环端口。不要打印 env、DSN 或令牌，也不要提交 `.runtime-acceptance/`。

## 建库：新建隔离库并升级到 HEAD

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.method_v05.database create \
  --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/method-v05-db-$STAMP \
  --output artifacts/runtime-acceptance/method-v05-db-$STAMP
.venv/bin/python -m acceptance.method_v05.database upgrade \
  --env-file .runtime-acceptance/method-v05-db-$STAMP/env.json \
  --source src \
  --output artifacts/runtime-acceptance/method-v05-db-$STAMP-upgrade
```

`create` 在隔离容器内新建 `tkos_a1_method_*` 库，从接受基线 `1b8cec9` 迁移到 0020（重复迁移为空），私有 env 写入 `--private` 目录（0600）。`upgrade` 必须恰好应用给定源码中 0020 之后的全部迁移（含 `0029_method_v05.sql`），重复迁移为空，再授予应用角色运行时表权限。两者都不打印凭据，不改动已有数据库。`acceptance/method_independent/database.py` 写死 54350 端口、只接受升级 0021，不适用于当前环境与 HEAD。

## 运行

```sh
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.method_v05.run \
  --env-file .runtime-acceptance/method-v05-db-$STAMP/env.json \
  --private .runtime-acceptance/method-v05-$RUN \
  --output artifacts/runtime-acceptance/method-v05-$RUN
```

`summary.json` 的 `runtime_method_v05_api_accepted` 只有在全部检查通过时才为 true。每次运行都必须用新的 `--private`／`--output` 路径；运行期间不得改动 `src/`。

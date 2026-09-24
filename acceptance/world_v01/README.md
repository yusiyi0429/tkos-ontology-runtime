# tkos.world/0.1 独立 API 验收（骨架）

合成身份的真实 HTTP／PostgreSQL 验收。业务成功只来自 `/v1/actions/prepare` 与 `/v1/actions`，SQL 只用于播种身份与独立核对；每个拒绝用例都在它能到达的入口上核对整个 scope 的库快照不变。不运行真实模型。

本骨架对应票 #19，经 HTTP 驱动的是：world 迁移作为升级步骤单独应用且重复为空；控制面 CLI 安装 world profile（同时核对契约与 world 登记字节）、scope 默认策略与支持登记；CEO 建 Company 并经 `GET /v1/world/objects/{id}` 读回（块、引用形式、空块标准句、正式内容指针、`world_v0_1` 解释状态）；恰好一条 `object.created` 事件并指回回执；绑定经 0030 闸门钉定 world profile；scope 内任一指派可读、另一 scope 的身份读不到；同键重放返回原回执、同键异命令被拒；IC 与 Agent 建 Company 被拒、载荷多余字段与空内容块被拒、第二个 Company 被拒、错期望版本被拒。

尚未经 HTTP 驱动（随后续票补齐）：其余八类对象、引用钉定、修订与关系、状态快照与外部事件、指派与生命周期、承诺与确认的门、取上下文、MCP。`summary.json` 的 `world_api_accepted` 固定为 false，只由最终矩阵（票 #27）写 true。

## 前提

- 隔离验收栈正在运行：`python3 acceptance/runtime/infra.py status`（项目 `tkos-ontology-runtime-acceptance`）。本目录工具不启动、停止或重建容器。
- 建库要求 Docker 上下文为 `desktop-linux`。
- 基础 env 为仅本人可读的 `.runtime-acceptance/env.json`。不要打印 env、DSN 或令牌，也不要提交 `.runtime-acceptance/`。

## 建库：基线迁移到 0029，再单独应用 world 迁移

```sh
STAMP=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.world_v01.database create \
  --env-file .runtime-acceptance/env.json \
  --private .runtime-acceptance/world-v01-db-$STAMP \
  --output artifacts/runtime-acceptance/world-v01-db-$STAMP
.venv/bin/python -m acceptance.world_v01.database upgrade \
  --env-file .runtime-acceptance/world-v01-db-$STAMP/env.json \
  --source src \
  --output artifacts/runtime-acceptance/world-v01-db-$STAMP-upgrade
```

`create` 用基线提交 `ef31b02`（含 0.5）的源码迁移到 `0029_method_v05.sql`，重复迁移为空。`upgrade` 必须恰好应用给定源码中 0029 之后的全部迁移（world 迁移），重复迁移为空，再授予应用角色运行时表权限。

## 运行

```sh
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.world_v01.run \
  --env-file .runtime-acceptance/world-v01-db-$STAMP/env.json \
  --private .runtime-acceptance/world-v01-$RUN \
  --output artifacts/runtime-acceptance/world-v01-$RUN
```

每次运行都用新的 `--private`／`--output` 路径；运行期间不得改动 `src/`。

# 试点链「Method 0.5 × 治理工作台」独立验收

真实 API 进程、真实 PostgreSQL、工作台个人会话的 HTTP 验收（#32 批次 B）。每次运行新建隔离库（升级到 HEAD）与新随机 scope：Agent 一侧用 Agent 凭证走 `/v1/actions`（复用 `acceptance/method_v05` 的 `Flow`）；人的步骤一律经工作台个人会话走 `/dashboard/api/v1/commands/prepare` + `/commands/{id}/commit`，信封与浏览器 `methodEnvelope` 同形，动作、契约版本与目标取自服务端给本人的动作投影。SQL 只用于身份播种与独立断言。不证明真实模型行为，不代替浏览器复核。

链路：M1A（CEO 指定参与人、三人各自确认 Agreement、CEO 最终确认）→ CEO 确认两份 LTCO → Co-agent 开 0.5 复核窗口，DRI 发表意见 → Co-agent 关窗收拢 → 两位范围 DRI 承诺 → CEO 整组激活（正式回执）→ Co-agent 生成复盘、CEO 确认 → API 重启恢复 → 会话面边界 → 0.5 独有的 Constraint 人工门。

## 前提

- 隔离验收栈正在运行：`python3 acceptance/runtime/infra.py status`。本工具不启动、停止或重建容器，也不使用 54350/54351。
- Docker 上下文为 `desktop-linux`：建库沿用 `acceptance/method_v05/database.py`，它校验 env 指向的就是隔离栈当前发布的 PostgreSQL。
- 基础 env 为 `.runtime-acceptance/env.json`（仅本人可读）。不要打印 env、DSN、令牌或登录码。

## 运行

```sh
RUN=$(date +%Y%m%d-%H%M%S)
.venv/bin/python -m acceptance.pilot_workbench_v05.run run \
  --base-env .runtime-acceptance/env.json \
  --private .runtime-acceptance/pilot-v05-$RUN \
  --output artifacts/runtime-acceptance/pilot-v05-$RUN \
  --publish docs/acceptance/pilot-workbench-v05-summary.json
```

- 建库：`method_v05/database.py` 的 `create` + `upgrade`，新库名 `tkos_a1_method_*`，跑完不删（与其他验收工具一致），不触碰已有数据库。
- 身份：`seed_v05` / `register_v05` 在新 scope 播种合成身份并经维护 CLI 安装 0.5 profile / policy / registry；工作台账号 `ceo`、`dri-a`、`dri-b`、`owner-a` 用 `memory_service_app.governance_accounts.provision` 开通，人的 Runtime 凭证只进私有令牌文件。
- API：测试专用启动器 `acceptance/runtime/server.py`，空闲回环端口、固定端口重启；工作台开关与 `acceptance/governance_workbench/serve.py` 相同。
- `--private`、`--output` 必须是新路径；运行期间不得改动 `src/`（前后比对源码清单）。约 20 秒。

`summary.json`（`--publish` 另存一份）：`runtime_pilot_workbench_v05_accepted` 只有 29 项检查全部通过才为 true；任一项失败即停，其余记 `not_run`。每项带简短证据（状态码、错误码、阶段、版本、计数、回执 id），另记 `source`（提交、`src/` 未提交改动、源码清单哈希、本工具哈希）、`database`、`scope_id`、`limitations`、`uncovered`。

## 检查

| 组 | 检查 |
| --- | --- |
| 1 登录 | 错登录码 / 不存在的用户 → 401 `LOGIN_FAILED`，不发 cookie；对的登录码 → 会话 cookie（HttpOnly、SameSite=Strict、Path=/dashboard/）+ CSRF 令牌，响应里没有 Runtime 凭证 |
| 2 待办与 0.5 绑定 | CEO 的 Method 待办列出 0.5 的 LTCO 草稿、复盘；「我的待办」列出 0.5 复核窗口（`0.5 人工确认事项`）；窗口视图 `monthly` 为 null、无场景、绑定 `tkos.method/0.5` / `method_v0_5`，动作一律 0.5、不出现 0.3 |
| 3 人工门与正式回执 | CEO 经 prepare + commit 整组激活候选集合：发出与保存的信封都是 0.5，回执正式；候选集合 pending→confirmed、版本 +1，成员按候选版本生效，Mission 记 Owner 生效记录，窗口 confirmed，无执行授权 |
| 4 业务拒绝 | DRI 整组激活、CEO 确认范围约束 → 403 `FORBIDDEN`（不是 401），业务表不变、会话仍有效、不留命令 |
| 5 CSRF | 缺令牌 / 错令牌 → 403 `CSRF_TOKEN_INVALID`，业务表不变、命令仍是 prepared；重读 `GET /session` 的令牌后提交成功 |
| 6 幂等与响应丢失 | 再提交、重试、同一幂等键再准备 → 同一回执，不新增修订与回执；本人凭证重放原始信封 → 同一回执；提交在业务提交前挂起、客户端读超时断开，放行后从「我的提交」取回同一回执 |
| 7 重启恢复 | 同端口重启 API：回执、对象状态、CEO 的「我的提交」逐条不变；旧 cookie 401（会话在进程内存，已知限制）；重新登录后再提交仍是同一回执 |
| 8 其余 | M1A 三类人工步骤、LTCO 确认、窗口意见、DRI 承诺、复盘确认、Constraint 确认都经工作台；会话面拒绝 Agent 动作与 API-only 动作；写死 0.3 的旧信封打到 0.5 对象 → 409 `PROTOCOL_BINDING_CONFLICT`；别人的命令 404；跨源写 403；登出后 401 |

已知限制与未覆盖项由工具写进 `summary.json` 的 `limitations`、`uncovered`，以那里为准。

## 私有目录与产物

私有目录（0700，文件 0600）：`db/env.json`（新库的连接）、`identities.json`（合成身份与 Agent 凭证）、`tokens/`（人的 Runtime 凭证）、`accounts.json`、`login/<用户>-login.json`（登录码）、`commands/`（工作台命令日志）、`harness/`（进程日志原文、挂起点控制）、`state.json`（对象清单）。

产物目录：`summary.json`、`db/`、`db-upgrade/`（迁移记录）、`http-transcript.jsonl`（Bearer 调用，脱敏）、`workbench/http-transcript.jsonl`（会话调用；登录请求体与 CSRF 令牌不记录）、`pilot-api-*.log`（脱敏进程日志）。`.runtime-acceptance/` 与 `artifacts/` 都不提交。

## 浏览器复核

run 已把自己的事项办完。复核时对同一个库与 scope 起工作台，由 Agent 一侧新开一轮 0.5 事项，人的步骤在浏览器里办：

```sh
# 终端 1：起工作台（复用 acceptance/governance_workbench/serve.py），只打印 URL 与各人登录文件路径
.venv/bin/python -m acceptance.pilot_workbench_v05.run serve --private .runtime-acceptance/pilot-v05-$RUN
# 终端 2：Agent 新开一个 0.5 复核窗口（每轮用下一段 30 天期间，可多轮）
.venv/bin/python -m acceptance.pilot_workbench_v05.run stage --private .runtime-acceptance/pilot-v05-$RUN --url http://127.0.0.1:PORT
#   浏览器：独立 profile 分别登录 dri-a、dri-b，在「我的待办」打开 0.5 窗口发表意见
.venv/bin/python -m acceptance.pilot_workbench_v05.run consolidate --private .runtime-acceptance/pilot-v05-$RUN --url http://127.0.0.1:PORT
#   浏览器：dri-a、dri-b 在 Method 事项里提交承诺；ceo 整组激活，查看正式回执与「我的提交」
```

登录码只在私有登录文件里，手工粘贴到登录框，不要贴到其他地方；每个账号用独立浏览器 profile，不当作身份切换。可顺带核对 R03：输错登录码的提示、Ctrl-C 后重新 `serve --port <同一端口>` 时旧页面回到登录页、重新登录后「我的提交」仍在。`stage` / `consolidate` 只做 Agent 一侧，不代人办理任何动作。

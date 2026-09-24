# tkos-world-mcp：在 Codex CLI 里读写业务世界

`tkos-world-mcp` 是 tkos.world/0.1 的 stdio MCP server。它持一个 Agent 身份的凭证，把工具调用转发到现有 HTTP 面；不连数据库、不判权、不做事务（ADR-0004）。写入内容与三项声明由 HTTP 面校验，拒绝原样返回。

## 安装

MCP SDK 在可选依赖组 `mcp` 里，服务本身的安装不带它：

```sh
uv sync --frozen --extra s3 --extra mcp   # 仓库内：在常用的 s3 之外加上 mcp（uv sync 按给出的组精确同步）
pip install 'tkos-memory-service[mcp]'     # 从 wheel 安装
```

开发依赖组已含 mcp，所以只跑测试时照常 `uv sync --frozen --extra s3` 即可。

没装这个组时运行 `tkos-world-mcp` 会直接提示缺少可选依赖。

## 环境变量

| 变量 | 必填 | 含义 |
|-|-|-|
| `TKOS_WORLD_API_URL` | 是 | HTTP 面的地址，例如 `http://127.0.0.1:8010` |
| `TKOS_WORLD_AGENT_TOKEN` | 是 | Agent 身份的 Bearer 凭证；该身份须在 scope 内有生效的角色指派，写快照还须在主体所在域持 AGENT 角色 |
| `TKOS_WORLD_MCP_LOG_DIR` | 否 | 运行日志目录，默认 `artifacts/world-mcp-runs`（相对启动目录，本地产物，不入库） |

## Codex CLI 配置

在 `~/.codex/config.toml`（或项目的 `.codex/config.toml`）里加一段。Codex 给 stdio server 只传一小组固定的环境变量，其余要在 `env` 里写明或在 `env_vars` 里点名透传；凭证用 `env_vars` 从启动 Codex 的 shell 透传，不要写进配置文件：

```toml
[mcp_servers.tkos-world]
command = "/path/to/tkos-ontology-runtime/.venv/bin/tkos-world-mcp"
cwd = "/path/to/tkos-ontology-runtime"
env_vars = ["TKOS_WORLD_AGENT_TOKEN"]
tool_timeout_sec = 150   # 写入是 prepare 加 commit 两次请求，每次最长 60 秒

[mcp_servers.tkos-world.env]
TKOS_WORLD_API_URL = "http://127.0.0.1:8010"
TKOS_WORLD_MCP_LOG_DIR = "artifacts/world-mcp-runs"
```

直接指向虚拟环境里的脚本，不经 `uv run`：启动时不会顺带重新同步依赖。启动 Codex 前在 shell 里 `export TKOS_WORLD_AGENT_TOKEN=...`。

## 工具

名称与参数和 HTTP 面一一对应；门动作（承诺、确认、标核心战役）、指派与建关系不暴露，只走工作台或 HTTP（契约第 9 节）。

| 工具 | HTTP | 参数 |
|-|-|-|
| `world_get_object` | `GET /v1/world/objects/{object_id}` | `object_id`，可选 `version` |
| `world_get_context` | `POST /v1/world/objects/{object_id}/context` | `object_id`、`question`，可选 `budget`（`max_chars`、`max_events_per_object`）、`recent_days` |
| `world_get_events` | `GET /v1/world/objects/{object_id}/events` | `object_id`，可选 `since`（带时区的时刻） |
| `world_get_state` | `GET /v1/world/objects/{object_id}/state` | `object_id`，可选 `as_of`（带时区的时刻） |
| `world_revise_object` | 动作 `world_revise_object` | `target`（`object_id`、`revision_id`、`expected_version`，取自取对象返回的 `object_id`、`revision_id`、`object_version`）、`payload`（合并补丁）、`declaration`，可选 `idempotency_key` |
| `world_refresh_state` | 动作 `world_refresh_state` | `payload`（`title`、`subject_ref`、`as_of`、三块）、`declaration`，可选 `idempotency_key` |
| `world_record_event` | 动作 `world_record_event` | `category`、`subject_refs`、`occurred_at`、`content`，更正另带 `supersedes_event_id`；`declaration`，可选 `idempotency_key` |

写工具在内部先 `POST /v1/actions/prepare` 再 `POST /v1/actions`，同一条命令、同一个幂等键（不给则每次调用生成一个）；不替调用方补目标、不重试。版本冲突等错误原样返回，调用方重新取对象后再写。HTTP 面不可达时返回 `HTTP_UNAVAILABLE` 并带上这次用的幂等键：写入可能已经提交，用同一个幂等键重放即可，不会重复写。

`declaration` 三项：`scene`（所属 Mission 或 Task 的引用 `<id>@<版本>`）、`trigger`（触发事件的文字）、`human_acceptance`（`{required, acceptor}`，需要人工验收时 `acceptor` 是本 scope 内有效的人）。Agent 写入必须带齐；Agent 的修订只能落在无门类型上，且必须要人工验收并给出验收人。

## 运行日志

每个进程一个 JSONL 文件，每次工具调用一行：`at`（UTC）、`session`、`seq`、`tool`、`arguments`、`status`（HTTP 状态；没拿到 HTTP 答复为 null）、`error_code`、`refs`（返回里出现的引用 `<id>@<版本>[#块]`）、`event_ids`、`chars`（返回给调用方的字符数）；写入另有 `idempotency_key`；取上下文另有 `context_pack_id` 与 `used_chars`（渲染后 Markdown 的字符数），其 `refs` 与 `event_ids` 只取上下文包本身，检索计划里裁掉的条目与主干上钉定的旧版本不计入。四个读工具另有 `read_refs` 与 `read_event_ids`：带着内容回来的对象版本、块与事件，也就是对象视图（取对象，连同它顺带返回的最新快照；取状态的快照；上下文包里一层的对象与状态）、块视图（空块读到的是标准句）与事件视图；块内引用、关系、`referenced_by`、`supersedes`、生命周期里钉的事件只以引用形式出现，只进 `refs` 与 `event_ids`。凭证不进日志；日志写不进去只在 stderr 提示。

实验指标（召回、可追溯、预算、确定性）从这里算，「取到」按 `read_refs` 与 `read_event_ids`。`codex exec` 每次运行都会起一个新的 server 进程，所以一次运行对应一个日志文件和一个 `session`。

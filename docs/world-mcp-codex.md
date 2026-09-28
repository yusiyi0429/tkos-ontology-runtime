# tkos-world-mcp：在 Codex CLI 里读写业务世界

`tkos-world-mcp` 是 tkos.world 的 stdio MCP server，暴露所选契约版本的 Agent 面：默认 tkos.world/0.1，`TKOS_WORLD_CONTRACT_VERSION` 选 tkos.world/0.2（见[选 0.2](#选-02)）。它持一个 Agent 身份的凭证，把工具调用转发到现有 HTTP 面；不连数据库、不判权、不做事务（ADR-0004）。写入内容与三项声明由 HTTP 面校验，拒绝原样返回。

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
| `TKOS_WORLD_CONTRACT_VERSION` | 否 | 契约版本：`tkos.world/0.1`（不设或为空时的默认）或 `tkos.world/0.2`，只认这两个完整写法；其余取值启动即退出（退出码 2） |
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

直接指向虚拟环境里的脚本，不经 `uv run`：启动时不会顺带重新同步依赖。启动 Codex 前在 shell 里 `export TKOS_WORLD_AGENT_TOKEN=...`。接 0.2 时在 `[mcp_servers.tkos-world.env]` 里另加 `TKOS_WORLD_CONTRACT_VERSION = "tkos.world/0.2"`；不写即 0.1。

## 工具

下面是 0.1（默认）的七个工具；选 0.2 时的工具见[选 0.2](#选-02)。名称与参数和 HTTP 面一一对应；门动作（承诺、确认、标核心战役）、指派与建关系不暴露，只走工作台或 HTTP（契约第 9 节），调用不在清单里的工具在发请求之前就返回 `INVALID_ARGUMENTS`。

| 工具 | HTTP | 参数 |
|-|-|-|
| `world_get_object` | `GET /v1/world/objects/{object_id}` | `object_id`，可选 `version` |
| `world_get_context` | `POST /v1/world/objects/{object_id}/context` | `object_id`、`question`，可选 `budget`（`max_chars`、`max_events_per_object`）、`recent_days` |
| `world_get_events` | `GET /v1/world/objects/{object_id}/events` | `object_id`，可选 `since`（带时区的时刻） |
| `world_get_state` | `GET /v1/world/objects/{object_id}/state` | `object_id`，可选 `as_of`（带时区的时刻） |
| `world_revise_object` | 动作 `world_revise_object` | `target`（`object_id`、`revision_id`、`expected_version`，取自取对象返回的 `object_id`、`revision_id`、`object_version`）、`payload`（合并补丁）、`declaration`，可选 `idempotency_key` |
| `world_refresh_state` | 动作 `world_refresh_state` | `payload`（`title`、`subject_ref`、`as_of`、三块）、`declaration`，可选 `idempotency_key` |
| `world_record_event` | 动作 `world_record_event` | `category`、`subject_refs`、`occurred_at`、`content`，更正另带 `supersedes_event_id`；`declaration`，可选 `idempotency_key` |

取上下文成功时，工具只交给模型四项（实验报告建议 1）：

- `context_pack_id`：这次落表的上下文包；
- `markdown`：渲染后的上下文。开头是六问指引，按问题给出处（引用）；下文沿主干分层列块、单元长期目标经 `goal_ref` 多取一跳的公司级长期目标、最新状态快照与近期事件，事件行写出记录人与被指派者的名字；
- `coverage`：六问覆盖；
- `budget`：预算与用量，另加 `trimmed`，按原因、按类计的裁剪条数，例如 `{"over_budget": {"event": 3}}`，为空即没有裁剪。

分层 JSON、检索计划与钉定信息不交给模型，仍在 HTTP 面的返回与上下文包表里。`question` 只做记录，不影响包的内容，同一问题取一次即可。取上下文失败时与其他工具一样原样返回。

写工具在内部先 `POST /v1/actions/prepare` 再 `POST /v1/actions`，同一条命令、同一个幂等键（不给则每次调用生成一个）；不替调用方补目标、不重试。版本冲突等错误原样返回，调用方重新取对象后再写。HTTP 面不可达时返回 `HTTP_UNAVAILABLE` 并带上这次用的幂等键：写入可能已经提交，用同一个幂等键重放即可，不会重复写。

`declaration` 三项：`scene`（所属 Mission 或 Task 的引用 `<id>@<版本>`）、`trigger`（触发事件的文字）、`human_acceptance`（`{required, acceptor}`，需要人工验收时 `acceptor` 是本 scope 内有效的人）。Agent 写入必须带齐；Agent 的修订只能落在无门类型上，且必须要人工验收并给出验收人。

## 选 0.2

`TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2` 时，工具是契约第 9.3 节 Agent 面里已实现的部分，四读五写：

| 工具 | HTTP | 参数 |
|-|-|-|
| `world_get_object` | `GET /v1/world/objects/{object_id}` | 同 0.1 |
| `world_get_context` | `POST /v1/world/objects/{object_id}/context` | 同 0.1 |
| `world_get_events` | `GET /v1/world/objects/{object_id}/events` | 同 0.1 |
| `world_get_state` | `GET /v1/world/objects/{object_id}/state` | 同 0.1 |
| `world_record_event` | 动作 `world_record_event` | `category`、`subject_refs`（对象或组件形式）、`occurred_at`、`content`，更正的 `category` 为 `correction` 并带 `supersedes_event_id`；`declaration`，可选 `idempotency_key` |
| `world_refresh_state` | 动作 `world_refresh_state` | `payload`（外壳：`title`、`subject_ref`、`as_of`、可选 `period`、`payload_type`、`source_event_refs`（`event:<事件 id>`，至少一条）、`blocks`）、`declaration`，可选 `idempotency_key` |
| `world_revise_object` | 动作 `world_revise_object` | `target`、`payload`（合并补丁，组件按 id 合并）、`declaration`，可选 `idempotency_key` |
| `world_start` | 动作 `world_start` | `target`（已成立的 Mission，作为 Owner 的 Agent；或指派给自己的 Activity），可选 `content`；撤回带 `outcome: "withdrawn"` 与 `supersedes_event_id`；`declaration`，可选 `idempotency_key` |
| `world_deliver` | 动作 `world_deliver` | `target`（自己负责的 Activity），其余同 `world_start` |

与 0.1 的差别：

- 读的端点不变，HTTP 面按对象绑定的契约版本出形状。0.2 对象分三组：`business`（类型、版本与修订 id、`object_version`、属性、关系、块与组件、组件台账、正式内容指针、进行中的一轮）、`identity`（责任人、当前有效的委托）、`records`（生命周期与推出它的事件、最新状态快照）。修订、开始、交付的 `target` 取 `business` 组里的 `object_id`、`revision_id` 与 `object_version`（作 `expected_version`）。
- 写入的 `contract_version` 是 `tkos.world/0.2`；多了开始与交付。`declaration` 的 `scene` 可以是任一业务对象（0.1 只认 Mission 或 Task）。Agent 修订有门对象只能改活动块与活动属性；触及正式块或正式属性时，声明必须要求人工验收并给出验收人。Agent 记的 Activity 交付由 Task 的责任人验收。
- 门、指派、建关系、建对象、关注标记、代记一律不暴露：调用这些工具，或给开始、交付带 `on_behalf_of`，在发请求之前就返回 `INVALID_ARGUMENTS`。代记只走 HTTP（契约第 14 节）。
- 列对象（#63）与提出问题、路由问题、退回形成（#61）还没有 HTTP 实现，随各自的票加进来。
- 取上下文照旧只交出 `context_pack_id`、`markdown`、`coverage`、`budget` 四项；Markdown 里的引用细到组件，事件写成 `event:<事件 id>`。
- 运行日志字段不变，引用多识别组件与事件两种形式（见下节）。

## 运行日志

每个进程一个 JSONL 文件，每次工具调用一行：`at`（UTC）、`session`、`seq`、`tool`、`arguments`、`status`（HTTP 状态；没拿到 HTTP 答复为 null）、`error_code`、`refs`（返回里出现的引用 `<id>@<版本>[#块]`）、`event_ids`、`chars`（交给调用方的字符数；取上下文成功时是上面四项的紧凑 JSON）；写入另有 `idempotency_key`；取上下文另有 `context_pack_id` 与 `used_chars`（渲染后 Markdown 的字符数），其 `refs`、`event_ids` 与下面的 `read_refs`、`read_event_ids` 都按 HTTP 面返回的上下文包本身算：交出去的 Markdown 就是这个包渲染的，检索计划里裁掉的条目与主干上钉定的旧版本不计入。四个读工具另有 `read_refs` 与 `read_event_ids`：带着内容回来的对象版本、块与事件，也就是对象视图（取对象，连同它顺带返回的最新快照；取状态的快照；上下文包里一层的对象、状态与多取一跳的对象）、块视图（空块读到的是标准句）与事件视图；块内引用、关系、`referenced_by`、`supersedes`、生命周期里钉的事件只以引用形式出现，只进 `refs` 与 `event_ids`。凭证不进日志；日志写不进去只在 stderr 提示。

选 0.2 时字段不变，引用按 0.2 的四种业务形式识别（契约第 5 节）：对象 `<id>@<版本>`、块 `<id>@<版本>#<块>`、组件 `<id>@<版本>#<块>/<组件>` 与事件 `event:<事件 id>`，组件引用整条记下，不截到块；`read_refs` 另含随块带着内容回来的组件（块内与组件里引用的组件只进 `refs`）；推出生命周期的事件、快照的来源事件与委托的登记事件只以引用形式出现：进 `event_ids`，写成 `event:<事件 id>` 的另进 `refs`，不进 `read_event_ids`。

实验指标（召回、可追溯、预算、确定性）从这里算，「取到」按 `read_refs` 与 `read_event_ids`。`codex exec` 每次运行都会起一个新的 server 进程，所以一次运行对应一个日志文件和一个 `session`。

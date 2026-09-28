# tkos-world：在命令行里读写业务世界

`tkos-world` 是 tkos.world 的命令行，HTTP 面的薄封装，默认 tkos.world/0.1，`TKOS_WORLD_CONTRACT_VERSION` 选 tkos.world/0.2（见[选 0.2](#选-02)）。它持一枚凭证，把子命令转发到现有 HTTP 面，返回的 JSON 原样打到标准输出；不连数据库、不判权、不补目标、不重试。它在基础安装里，只依赖 httpx：仓库内 `uv sync --frozen --extra s3` 之后即有 `.venv/bin/tkos-world`，从 wheel 安装 `tkos-memory-service` 也带它。

只做 Agent 面，与选同一版本的 [`tkos-world-mcp`](world-mcp-codex.md) 暴露同样的操作。0.1 是七个：四读（取对象、取上下文、取事件、取状态，另有取子对象）加三写（记外部事件、写状态快照、修订无门对象）。门动作、指派、建关系与建对象不暴露：0.1 的 `act` 只接受 `world_record_event`、`world_refresh_state`、`world_revise_object`，其余动作名在发请求之前就以用法错误拒绝；人记门与指派走工作台或 HTTP。允许的动作之内能做什么仍由凭证的身份决定，由 HTTP 面判权。与 MCP 的差别：CLI 给人和脚本用，不记运行日志，取上下文返回 HTTP 面的完整响应。

## 环境变量

| 变量 | 必填 | 含义 |
|-|-|-|
| `TKOS_WORLD_API_URL` | 是 | HTTP 面的地址，例如 `https://world-lab.tokenkingos.com` |
| `TKOS_WORLD_TOKEN` | 是 | Bearer 凭证。只从环境变量读，不作命令行参数，免得留在 shell 历史与进程列表里 |
| `TKOS_WORLD_CONTRACT_VERSION` | 否 | 契约版本：`tkos.world/0.1`（不设或为空时的默认）或 `tkos.world/0.2`，只认这两个完整写法；其余取值退出码 2，什么都不发 |

## 命令

下面是 0.1（默认）的命令；0.2 的差别见[选 0.2](#选-02)。

| 命令 | HTTP | 选项 |
|-|-|-|
| `get <id>` | `GET /v1/world/objects/{id}` | `--version N` 取指定修订 |
| `state <id>` | `GET …/state` | `--as-of <带时区的时刻>` |
| `events <id>` | `GET …/events` | `--since <带时区的时刻>` |
| `children <id>` | `GET …/children` | |
| `context <id>` | `POST …/context` | `--question`（必填，只做记录）、`--max-chars`、`--max-events-per-object`、`--recent-days` |
| `act <动作>` | `POST /v1/actions/prepare`，再 `POST /v1/actions` | 动作只限 `world_record_event`、`world_refresh_state`、`world_revise_object`；`--params`、`--reason`（必填）、`--target`、`--idempotency-key`、`--prepare-only` |

对象 id 必须是 UUID，否则不发请求。取上下文每次调用都在 HTTP 面落一行上下文包。

## 写入

`act` 发的是契约里的动作（`contract_version` 是所选的契约版本，默认 `tkos.world/0.1`），参数与目标按契约原样给 JSON：字面量、`@文件`，或 `-` 从标准输入读。先 prepare，拿到 `expected_versions` 后用同一条命令、同一个幂等键 commit；`--prepare-only` 只做 prepare，什么都不提交。幂等键不给则生成一个。

`--target` 取自 `get` 返回的 `object_id`、`revision_id` 与 `object_version`（作 `expected_version`），修订对象时给；写快照与记外部事件不落在对象上，不给。CLI 不在写入时替你重新取目标：那样会绕过版本检查。版本冲突时重新 `get` 再写。

commit 已经发出而 HTTP 面没有答复时，写入可能已经提交：标准错误里会给出这次用的幂等键，带上 `--idempotency-key` 重放同一条命令即可，不会重复写。

## 输出与退出码

HTTP 面的返回（包括拒绝）原样打到标准输出，JSON 缩进打印；状态码为 4xx、5xx 时另在标准错误打一行 `HTTP <状态码>`。

| 退出码 | 含义 |
|-|-|
| 0 | 成功 |
| 1 | HTTP 面拒绝，或不可达 |
| 2 | 用法不对（缺参数、对象 id 不是 UUID、JSON 解析不了、动作或子命令不在所选版本的 Agent 面之内、0.2 下 `--params` 带 `on_behalf_of`），或缺环境变量、契约版本不认识 |

## 选 0.2

`TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2` 时，命令是契约第 9.3 节 Agent 面里已实现的部分：

| 命令 | 与 0.1 的差别 |
|-|-|
| `get <id>`、`state <id>`、`events <id>`、`context <id>` | 端点与选项不变，HTTP 面按对象绑定的契约版本出形状：0.2 对象分 `business`、`identity`、`records` 三组，引用细到组件（`<id>@<版本>#<块>/<组件>`），事件写成 `event:<事件 id>` |
| `children <id>` | 没有：取子对象不在 0.2 的 Agent 面上，HTTP 面对 0.2 对象也不支持；列对象随 #63 加 |
| `act <动作>` | 动作是 `world_record_event`、`world_refresh_state`、`world_revise_object`、`world_start`、`world_deliver`；`contract_version` 为 `tkos.world/0.2` |

- 修订、开始、交付要 `--target`，取 `get` 返回的 `business` 组里的 `object_id`、`revision_id` 与 `object_version`（作 `expected_version`）。开始的目标是已成立的 Mission（Agent 作为 Owner 的 Agent，须在它所在的域持 AGENT）或指派给自己的 Activity；交付的目标是自己负责的 Activity。撤回自己记的开始或交付，`--params` 带 `{"outcome": "withdrawn", "supersedes_event_id": …}`。
- 写入声明的 `scene` 可以是任一业务对象；写状态快照的 `payload` 是 0.2 的外壳（`payload_type`、`source_event_refs` 等，见契约第 7 节）。
- 门、指派、建关系、建对象、关注标记不在 `act` 的动作里；代记只走 HTTP，`--params` 带 `on_behalf_of` 在发请求之前以用法错误拒绝。提出问题、路由问题、退回形成随 #61 加进 `act`。

```sh
export TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2
TARGET=$(tkos-world get <activity id> | jq -c '.business | {object_id, revision_id, expected_version: .object_version}')
# start.json：{"declaration": {"scene": "<Task id>@<版本>", "trigger": "…", "human_acceptance": {"required": false}}}
tkos-world act world_start --target "$TARGET" --params @start.json --reason "开始执行"
```

## 例子

```sh
export TKOS_WORLD_API_URL=https://world-lab.tokenkingos.com
export TKOS_WORLD_TOKEN="$(cat ceo.token)"

tkos-world get <id>
tkos-world events <id> --since 2026-09-01T00:00:00+08:00
tkos-world context <id> --question "这个 Mission 为什么做？" | jq -r .context_pack.markdown

# 修订：目标取自刚读到的版本，补丁从文件读
TARGET=$(tkos-world get <id> | jq -c '{object_id, revision_id, expected_version: .object_version}')
tkos-world act world_revise_object --target "$TARGET" --params @patch.json --reason "补公司身份"

# 写一条状态快照，先看 prepare 的结果，不提交
tkos-world act world_refresh_state --params @snapshot.json --reason "本周进展" --prepare-only
```

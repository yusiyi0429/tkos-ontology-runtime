# Workbench 独立验收

`independent.py` 是 Codex 维护的独立验收入口，检查 Kimi Code 实现的 7 个读取 API，以及它们与现有 Runtime 授权、交付、证据和 Context Pack 的衔接。业务数据使用新建的 synthetic scope；成功业务状态必须由真实 HTTP Action 产生。

## 运行条件

- 将本目录放在 Runtime 仓库的 `acceptance/workbench/`，使用仓库已有 `.venv`，无需添加生产依赖。
- 本机隔离验收基础设施已按 `acceptance/runtime/README.md` 准备并启动，包含迁移完成的 PostgreSQL 和已配置的版本化对象存储。
- `.runtime-acceptance/` 的私有配置由原有 infra 工具管理。不要复制到证据目录或 Git，不要打印其中的凭据。
- 每个数据库 URL 必须显式指定数字形式的 loopback host、端口及数据库 `tkos_runtime_acceptance`；对象存储 URL 也必须是显式的数字 loopback host/port。入口拒绝远程地址、DNS 名称、Unix socket、DSN service 重定向和环境中的 `PGHOSTADDR` / `PGSERVICE` / `PGSERVICEFILE` 覆盖。
- 验收期间停止修改被测源码。入口在导入应用代码前及停止本次进程后计算源码、验收脚本和依赖锁定文件的 SHA-256；发生变化则判定未通过。

在 Runtime 仓库根目录运行：

```bash
.venv/bin/python acceptance/workbench/independent.py
```

也可以指定新的 run-id：

```bash
.venv/bin/python acceptance/workbench/independent.py --run-id workbench-independent-review-001
```

run-id 为 1–80 个 ASCII 字母、数字、下划线或连字符，首位必须是字母或数字。默认值含随机后缀；已存在私有状态或证据的 run-id 会被拒绝。重跑使用新值，保留失败证据。

入口不启动、停止或删除 Docker 服务，不执行迁移，不安装依赖。它只启动并清理本轮自己的 API 和 receiver 进程；Worker 保持停止，避免后台写入干扰只读断言。本轮 synthetic 数据保留在隔离数据库，便于追溯。

## 11 组检查

| 组 | 独立检查内容 |
| --- | --- |
| 00 | 实际应用数据库角色、7 个 GET 路径、被测源码前后不变 |
| 01 | 鉴权、10 个原生类型、当前可见业务域 |
| 02 | 承接 → v1 → 退回 → v2 → 有权人验收；交付、Outcome、MF 分开判断 |
| 03 | 对象与版本分页、最小字段、latest 与 effective 区分 |
| 04 | 精确来源版本、一跳关系、显式类型字段、忽略任意嵌套 JSON |
| 05 | 严格查询参数、游标跨对象/端点/身份/过滤条件复用拒绝、游标版本禁止布尔及浮点类型 |
| 06 | 最小 committed receipt 列表、当前 policy 收回读取权 |
| 07 | 最小责任人字段、任职有效期、任职有效不等于具备验收权限 |
| 08 | Context 双时间截面、快照语义、GET 不产生持久化业务变化 |
| 09 | 跨域 receipt 任一引用不可见则整体隐藏；隐藏记录不产生可见分页游标 |
| 10 | 撤权后历史、旧游标、Context、证据同步拒绝；其他授权域仍可访问 |

业务检查采用 fail-fast：失败组及此前通过组会保留，未运行的后续组不算通过。入口以退出码 `0` 表示本次 workbench 读取验收通过，否则返回 `1`。

## 证据与边界

可审查证据保存在 `artifacts/runtime-acceptance/<run-id>/`：

- `report.json`：11 组结果、失败位置、源码稳定性及明确的验收范围。
- `http-transcript.jsonl`：原有 acceptance client 输出的脱敏 HTTP 记录；证据正文用摘要代替。
- `openapi.json`：本轮实际启动的服务规范。
- `workbench-results.json`：全部业务组完成时的汇总。
- `source-sha256.json`：前后文件摘要、是否一致及变化文件列表。

私有 fixture、控制密钥及进程日志位于 `.runtime-acceptance/<run-id>/`，不作为公开交付材料。错误只记录类型和调用位置，不记录可能包含 DSN 的异常消息或局部变量。

即使全部通过，报告仍固定 `runtime_accepted=false`、`clark_integration_accepted=false`、`real_employee_pilot_accepted=false`、`released=false`、`production_deployed=false`。本次通过由 `workbench_read_accepted=true` 单独表示，不能替代完整 Runtime 验收、Clark 接入验证、员工真实业务试点或生产发布。

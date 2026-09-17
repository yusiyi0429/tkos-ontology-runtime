# tkos.workspace/0.2 独立验收

验收对象：Runtime HTTP API + 隔离 PostgreSQL + MinIO。无 Clark、无真实模型、无外部系统。身份与激活策略是唯一控制面预置；所有来源、版本、共享、Context、Agent run 与 draft 都来自合法 HTTP 动作。SQL 只做只读观察。

私有来源文件（`.runtime-acceptance/pi-method-04/real-sources/`）在导入时只在本地进程内读取以计算 sha256 与切分 segment；**不上传为域可读 EvidenceAsset**，报告只含哈希与计数，不输出正文。私有文本通过 `source_version.segments` 存入不可变事件表。

## 复跑

```bash
# 0) 隔离库由 root 提供（已到 0025；0027/0028 由 root 协调）
uv run python -m acceptance.workspace_v02.bootstrap \
  --env-file .runtime-acceptance/pi-method-04/db/env.json \
  --output artifacts/workspace_v02_bootstrap

# 1) 独立 HTTP+PG+MinIO 场景（私有来源可选）
uv run python -m acceptance.workspace_v02.run \
  --env-file .runtime-acceptance/pi-method-04/db/env.json \
  --private .runtime-acceptance/pi-method-04/workspace-v02-run \
  --output artifacts/workspace_v02_run \
  --sources-dir .runtime-acceptance/pi-method-04/real-sources

# 2) Python/workbench/build 回归
uv run python -m acceptance.workspace_v02.regression \
  --env-file .runtime-acceptance/pi-method-04/db/env.json \
  --output .runtime-acceptance/pi-method-04/workspace-v02-regression

# 3) 伙伴 OpenAPI 快照
uv run python -m acceptance.workspace_v02.export_contract --check

# 4) 会话 facade 真实 HTTP（B5 集成；需先 bootstrap + workbench serve）
uv run python -m acceptance.workspace_v02.facade_bootstrap \
  --env-file .runtime-acceptance/pi-method-04/db/env.json \
  --private .runtime-acceptance/pi-method-04/facade-scenario-N
uv run python -m acceptance.governance_workbench.serve \
  --env-file .runtime-acceptance/pi-method-04/db/env.json \
  --private .runtime-acceptance/pi-method-04/facade-scenario-N --port 58817
uv run python -m acceptance.workspace_v02.facade_sessions \
  --url http://127.0.0.1:58817 \
  --env-file .runtime-acceptance/pi-method-04/db/env.json \
  --private .runtime-acceptance/pi-method-04/facade-scenario-N
```

facade runner 不修改生产代码：发现写入私有 `facade-findings.md`（含缺失 facade 读取 URL 与 `context_v02` journal 500 的可复现步骤）；不把 core `/v1` 当作 facade 替代。

独立证据：`qa-facade02` = **47/47 passed**（含 commit/retry/get 撤权后不恢复正文与自由文本）；`qa-final-workspace` = **64**；root final Python **920 total（919+1 迁移 replay）/16 skip**，UI 165/21；UI 事件到接口/结果/恢复的映射见 [docs/partner-ui-event-mapping.md](../../docs/partner-ui-event-mapping.md)；同一案例本地受控全链（评论/收拢/4 承诺/CEO 激活/正式 Mission）与来源 2 会话 Context withheld 已由 root 独立验证；迁移 replay PASS、source frozen（`index-CvwLgGIZ.js`）。

每次使用新目录；runner 拒绝覆盖。

## 覆盖

- 独立场景无 Method 锚点；非成员（含 CEO/跨域）404 且无计数。
- 两个私有真实来源合法导入：文件 sha256 与 manifest 一致；segment/speaker/时间轴计数；**不产生域可读 EvidenceAsset**，版本无 `evidence_ref`。
- 精确版本授权、无遍历、更正版本不因旧共享可见。
- 来源围栏：CEO/域读不能读受围栏 EvidenceAsset（object/revision/dashboard download/receipt/generic Context）；精确授权者只能读被授 revision 与下载；撤权后立刻失效。
- 来源围栏 canary（合成且有意共享的测试工件）：上传→链接→撤回，检查直接对象/revision/下载、dashboard、通用 Context（含链接前旧快照）、回执、run 自由文本、draft 正文、来源状态。
- Context：不可变快照、并发同键单一快照、稳定重放、同键不同 body 409、撤权后 `purpose`/items 扣留。
- Agent run 必填精确输入快照；引用 quote 必须命中 segment；Agent 不能代替人类决策；Method 内部 `source_refs` 读取未共享私有证据被拒绝（`m1a_record_signal`），Agent 自建 Context + 本人 run 允许，他人 Context 拒绝。
- Context `purpose` 仅在全部条目当前可读时返回（含创建者）；撤权后为 `null`。
- 伙伴 OpenAPI：`export_contract --check` 验证 `docs/runtime-workspace-v02-openapi.json`。
- 幂等/CAS/响应丢失原信封重放/进程重启。
- SQL：`gov_method_runs`、`runtime_tasks` 无副作用；无 Method 对象；事件与 0.2 回执 1:1；应用角色边界。

## 状态边界

`summary.json` 单列 Runtime API 结果；Clark 浏览器、真实模型、外部 Feishu ACL 同步、发布/部署均不在本 runner 内，也不会被声明为通过。

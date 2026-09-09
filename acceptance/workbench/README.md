# 工作台演练数据生成（acceptance/workbench）

为四页工作台原型（对象与关系、实例详情、动作与回执、Context Pack）在**隔离的
synthetic scope** 中生成可复现的演练数据，并以真实 HTTP 调用七个新增
workbench 读取接口，输出脱敏 manifest。

## 前提

- 测试基础设施（PostgreSQL、版本化对象存储、`.runtime-acceptance/env.json`）
  由 Codex 管理；本入口不启动/停止 Docker，不访问远程或生产服务。
- 入口保护是**程序执行的检查**，不是文字声明：seed 之前先经
  `infra.load_environment()` 在程序内部消费既有 harness 配置（不读取、不打印
  其中秘密），复用独立 QA 入口的 `validate_environment` 实际拒绝非本机回环
  地址、非 `tkos_runtime_acceptance` 数据库及非本机对象存储；再用
  `reserve_paths` 原子保留全新运行目录，拒绝已存在目录/符号链接。任何一项
  失败都在 `create_fixture` 之前退出，不发生 seed 或进程启动。独立 QA 的
  范围与声明见 [INDEPENDENT.md](INDEPENDENT.md)。
- 运行时必须让当前 worktree 的代码优先：`PYTHONPATH=src:.`，解释器沿用
  `/Users/yusiyi/ysy/tkos-ontology-runtime/.venv/bin/python`。

## 运行

```bash
PYTHONPATH=src:. /Users/yusiyi/ysy/tkos-ontology-runtime/.venv/bin/python \
    acceptance/workbench/generate.py [--run-id workbench-xxx]
```

- 每次运行都经 `seed_scope` 新建隔离 synthetic scope（仅身份、策略与一条
  已确认 Outcome 起点）；不存在复用旧 fixture 的入口。
- `--run-id`：显式 run id，必须是安全文件名（字母数字开头，仅含字母、数字、
  `-`、`_`，最长 64 字符）；已存在或符号链接的运行目录由 `reserve_paths`
  原子拒绝，绝不覆盖旧数据。

## 产生什么

所有业务变迁只经认证 HTTP Action 产生（不 seed 成功结果）：

1. BusinessCommitment / ExecutionCommitment 双方握手并激活（承诺）；
2. FeedbackThread 路由→接受→调查（MF 保持**跟进中**，不验收不关闭）；
3. WorkItem 冻结基线，MISSION_DRI 接受；
4. 原始证据经 `POST /v1/evidence-assets` 上传；
5. 交付 v1 提交 → VERIFIER 退回（changes_requested）→ 补证据再提交 v2 →
   验收通过（**最终交付通过**）；
6. CompanyOutcome 保持**未评估**（not_assessed）。

随后真实调用 `/v1/object-types`、`/v1/domains`、`/v1/objects`（含翻页）、
`/v1/objects/{id}/revisions`、`/v1/objects/{id}/relations`、
`/v1/objects/{id}/action-receipts`、`/v1/objects/{id}/responsibility`
以及既有对象详情/回执详情接口。

## 输出

- `artifacts/runtime-acceptance/<run_id>/workbench-manifest.json`：脱敏
  manifest，只含对象/revision/receipt 标识与证据 sha256、四页↔接口映射、
  读取响应样本；经 `acceptance.runtime.client.sanitized` 处理，**不含任何
  凭据**。
- HTTP 转写（同样脱敏）在同目录 `http-transcript.jsonl`。
- 凭据仅存在于 `.runtime-acceptance/<run_id>/fixture.json`（0600，私有目录）。

本入口只生成演练数据；它不构成业务验收，最终验收由 Codex 独立执行。

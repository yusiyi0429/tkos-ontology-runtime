# Method 0.1 独立 API 验收

这是合成人和受控 Agent 的真实 HTTP／PostgreSQL／原始证据存储验收。客户端不导入生产 validator，不直接写业务成功记录。权限夹具只建立隔离 scope、人员、Agent 身份绑定、角色和策略；策略通过真实控制 CLI 安装。完整范围和来源见 `docs/runtime-method-acceptance-matrix.md`。

机器矩阵包含 18 组、98 条必需业务检查，另有 8 个环境门槛。`report.json` 的 `method_api_accepted` 只有在全部要求通过时才为 true；缺少脚本、未运行、失败和部分运行均不能推断通过。默认完整运行的部分结果返回退出码 2，真正异常返回非零退出码。

## 本地前提

- 从仓库根目录执行，使用现有 `.venv/bin/python`。
- 当前 Docker context 为 `desktop-linux`，已运行的隔离 PostgreSQL 容器为 `tkos-ontology-runtime-acceptance-postgres-1`，数据库端口 54350；原始证据 MinIO 端口 54351。
- 环境配置由操作者放在仅本人可读的 `.runtime-acceptance/*/env.json`。应用与迁移角色必须不同。不要打印文件或把数据库 URL／运行密钥写进命令行、报告或 Git。
- 本工具不会启动、停止、重建容器；不会删除数据库或修改旧数据库。失败保留新建数据供核查；重新执行必须选新输出目录。

## 保留环境和创建基线

```sh
.venv/bin/python -m acceptance.method_independent.baseline \
  --output .runtime-acceptance/method-local/baseline-before.json

.venv/bin/python -m acceptance.method_independent.database create \
  --env-file .runtime-acceptance/a3-database-20260911/env.json \
  --private .runtime-acceptance/method-local-database \
  --output artifacts/runtime-acceptance/method-local-database
```

第二步在新 `tkos_a1_method_*` 数据库内，从不可变提交 `1b8cec9cc30e561e3570bc5dd010a09126f283c2` 的源码迁移至 0020，并检查重放不增加迁移。不会在原环境内清空表。

## 创建真实旧历史，然后升级

```sh
.venv/bin/python -m acceptance.method_independent.history capture \
  --env-file .runtime-acceptance/method-local-database/env.json \
  --source .runtime-acceptance/method-local-database/base-source/src \
  --private .runtime-acceptance/method-local-history-before \
  --output artifacts/runtime-acceptance/method-local-history-before

.venv/bin/python -m acceptance.method_independent.database upgrade \
  --env-file .runtime-acceptance/method-local-database/env.json \
  --source src \
  --output artifacts/runtime-acceptance/method-local-upgrade
```

历史捕获使用真实 A2 组合激活和 A3 `承接 → 提交 v1 → 退回 → 提交 v2 → 有权人验收`，同时保存对象、回执、所有业务表哈希及原始存储清单。升级仅应用 0021，再次迁移必须为空。

## 运行 Method 和历史复核

先生成旧能力回归证据。旧 A2／A3 runner 保留原有数据库名称边界：`--legacy-env-file` 必须指向另一份专用 `tkos_a1_a2_a3_*` 隔离数据库环境，且该数据库也已从接受基线迁移到 0021。不要把已有演示或历史验收数据库原地升级来满足此参数。

```sh
.venv/bin/python -m acceptance.method_independent.regression \
  --env-file .runtime-acceptance/method-local-database/env.json \
  --legacy-env-file .runtime-acceptance/method-local-legacy-regression/env.json \
  --source src \
  --private .runtime-acceptance/method-local-regression \
  --output artifacts/runtime-acceptance/method-local-regression
```

命令运行 Python 测试、独立 narrative 测试和旧 A2／A3 实际 HTTP 矩阵，生成 `artifacts/runtime-acceptance/method-local-regression/summary.json`。其中 A1 覆盖现有 71 项模型、HTTP 协议边界及序列化回归，并未重跑每个原始 A1 控制面维护场景；具体计数以本次报告为准。迁移通过真实空库升级及重复迁移另行验证，要求独立管理员建库权限的 `tests/test_migrations.py` 不包含在该 Python 命令中。

`legacy_regression.py` 仅补齐后来迁移的表目录预期，保留原业务用例和 SQL／权限断言，不修改数据库授权。

```sh
.venv/bin/python -m acceptance.method_independent.run \
  --env-file .runtime-acceptance/method-local-database/env.json \
  --source src \
  --private .runtime-acceptance/method-local-run \
  --output artifacts/runtime-acceptance/method-local-run \
  --database-evidence artifacts/runtime-acceptance/method-local-database/database.json \
  --upgrade-evidence artifacts/runtime-acceptance/method-local-upgrade/upgrade.json \
  --preservation-baseline .runtime-acceptance/method-local/baseline-before.json

.venv/bin/python -m acceptance.method_independent.history verify \
  --env-file .runtime-acceptance/method-local-database/env.json \
  --source src \
  --private .runtime-acceptance/method-local-history-after \
  --output artifacts/runtime-acceptance/method-local-history-after \
  --history artifacts/runtime-acceptance/method-local-history-before/history.json \
  --fixture .runtime-acceptance/method-local-history-before/fixture.json \
  --commands .runtime-acceptance/method-local-history-before/commands.json
```

上述第一轮用于诊断业务链；未传入最终回归证据时会明确返回退出码 2。完整验收需提供先前生成的回归报告和历史复核报告，选择全新的私有／输出路径重跑 Method runner 并增加：

```sh
  --regression-evidence artifacts/runtime-acceptance/method-local-regression/summary.json \
  --history-evidence artifacts/runtime-acceptance/method-local-history-after/history-preservation.json
```

`final_regressions` 会逐项检查这两份报告的通过字段及源码清单；回归报告必须由真实测试命令产出，不得手工标记通过。最终成功报告可使用 `python -m acceptance.method_independent.summarize --report .../report.json --output docs/acceptance/method-summary.json` 生成可审查摘要；不完整报告会被拒绝。

本轮 API 均在随机 loopback 端口启动；用普通应用数据库角色运行。失败点只有私有验收进程的随机密钥可启用，且只能抛错或等待，不会授予权限或伪造业务成功。并发场景通过 PostgreSQL 等待锁证据确认请求确实竞争；进程重启只影响此轮验收自己启动的 API。

`http-transcript.jsonl` 为脱敏 HTTP 证据，上传正文保留 hash／字节数。私有凭据、原始进程日志和重放命令只进入 `.runtime-acceptance/`；公开结果只写 `artifacts/runtime-acceptance/` 和经过审查的报告。运行期间源码变更会令 `source_unchanged` 门槛失败，必须在稳定源码上重跑。

旧能力回归包括 A1 模型、HTTP 协议与序列化测试，A2／A3 完整独立矩阵，以及 legacy 交付和历史复核；各自绑定同一源码清单并注明范围。不能用本矩阵的通过推断整个产品、专业 Skill 研究质量、Clark 页面、生产身份、迁移或部署已经完成。

# 本体与记忆工作台 v0.1：Runtime 本机验收记录

2026-09-09。本轮新增读取能力通过本机独立验收，可以交给 Clark 伙伴接入四页工作台原型。代码已同步至 `tkos-ontology-runtime` 主工作目录，尚未提交或发布。

开发由 **Kimi Code 0.40.1** 执行；**Codex** 负责冻结接口契约、源码审查、返修要求、独立 HTTP/SQL 验收和本地集成。开发基线为 `c977da9588f48f08b7f76b6828e75e0b02576076`。

## 交付范围

新增 7 个 GET 接口：原生类型目录、可读业务域、域内对象列表、对象版本、指定版本的一跳来源关系、对象关联动作回执、WorkItem 最小责任人信息。复用既有对象详情、版本详情、回执详情、证据原件及 Context Pack 接口。

- [四页与 API 对照、字段及授权说明](../workbench-read-api.md)
- [演练数据生成入口](../../acceptance/workbench/README.md)
- [可重复的独立验收入口与运行条件](../../acceptance/workbench/INDEPENDENT.md)
- [结构化验收摘要与生产代码 SHA-256](workbench-read-v0.1-20260909.json)

未新增生产依赖、数据库迁移或业务 Action；本轮生产代码改动仅为读取模块、路由接入和 OpenAPI。独立验收脚本由 Codex 管理，未由开发者修改门禁以适配实现。

## 实际验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 主目录完整 pytest | 288 passed，1 skipped；包含 70 项工作台专项测试 | [JUnit](../../artifacts/runtime-acceptance/workbench-independent-20260909-final1/pytest-results.xml) |
| 独立真实 HTTP + PostgreSQL + 版本化对象存储 | 11/11 组通过 | [report.json](../../artifacts/runtime-acceptance/workbench-independent-20260909-final1/report.json) |
| 被测代码前后 SHA-256 | 完全一致 | [source-sha256.json](../../artifacts/runtime-acceptance/workbench-independent-20260909-final1/source-sha256.json) |
| 演练 CLI | 新建隔离数据成功，重复 run-id 被拒绝且旧 manifest 不变 | [演练 manifest](../../artifacts/runtime-acceptance/workbench-demo-20260909-final1/workbench-manifest.json) |
| OpenAPI | 与当前代码生成结果一致；仅新增 7 个 GET，既有路径及 schema 未变 | [contracts/openapi.json](../../contracts/openapi.json) |

跳过项为 `tests/test_health.py:188`：隔离测试库缺少 `agent_service` 用户，无法执行该既有非 human viewer 健康检查。本次新增工作台测试没有跳过。

独立验收使用新建 synthetic scope 和真实 HTTP Action，跑通承诺签署与激活、DRI 承接、提交 v1、退回补充、提交 v2、有权人验收。最终 WorkItem=`delivery_accepted`，Deliverable=`accepted`，Outcome=`not_assessed`，MF=`investigating`；交付通过没有自动改写 Outcome 或关闭 MF。

其他门禁覆盖当前 policy 收权、历史读取与旧 cursor 撤权、跨域回执任一引用不可读时整体隐藏、最小责任身份、任职有效不等于验收权、精确来源 revision/hash、双时间 Context Pack，以及 GET 前后业务持久化状态不变。关系隐藏目标与分页组合因合法写路径限制为同域，以聚焦单测补充；跨域回执过滤有真实 HTTP/SQL 证据。

审查实际复现了 cursor 的 JSON `v=true` 被误当作整数版本 1 接受的问题。Kimi 已修复，并补充 `true`/`1.0` 的 HTTP 负例、各 reader 的 key 校验及关系撤权翻页测试。演练入口在 seed 前执行本机连接目标校验及新目录独占保护。

## 重跑与接入

在 Runtime 仓库根目录，先按现有验收基础设施说明启动本机环境，再执行：

```bash
python3 acceptance/runtime/infra.py up
python3 acceptance/runtime/infra.py run --migration -- .venv/bin/python -m pytest tests -q -rs
.venv/bin/python acceptance/workbench/independent.py
.venv/bin/python acceptance/workbench/generate.py
python3 acceptance/runtime/infra.py stop
```

默认 run-id 每次随机生成；指定 run-id 时必须使用新值。不要复制 `.runtime-acceptance` 私有配置到交付材料。

本次仅证明本机隔离数据上的 Runtime 读取能力；Clark 页面/BFF 接入、真实员工业务试点、生产容量及生产部署尚未验收。报告固定保留 `runtime_accepted=false`（未重做全套 Runtime 运行期验收），通过由 `workbench_read_accepted=true` 单独表示。

你原有的 24 个修改文件保持原 SHA-256；10 个无关运行容器的 ID、镜像及启动时间未变。本轮使用的本机 PostgreSQL 与对象存储已恢复为停止状态，数据卷和验收证据保留。

# Governed Narrative 独立验收

本套验收使用真实 Runtime HTTP、PostgreSQL 和 MinIO，检验兼容 Clark 的 `POST /v1/context-graph/narrative` 是否正确读取治理事实。每次运行创建全新的 `runtime-acceptance-narrative-*` 隔离 scope，不重置共享基础设施，不调用原 Runtime 全量 runner。

```sh
.venv/bin/python acceptance/narrative/run.py
```

前提是本机独立验收的 PostgreSQL/MinIO 已准备好，私有连接配置位于 `.runtime-acceptance/env.json`，并且 Clark 位于 `/Users/yusiyi/ysy/clark`。可用 `--clark-root /absolute/path/to/clark` 指定现有 Clark checkout；需要该项目已经安装的 Node.js 与 TypeScript，不安装新依赖。

每次运行自行拥有并最终停止 API、receiver、Worker 及故障边界 API 进程。默认明确设置 `TKOS_NARRATIVE_ENABLED=1`、`TKOS_NARRATIVE_COMPRESSION=none`、`TKOS_NARRATIVE_LEGACY_ENABLED=0`。默认叙述来自确定性事实渲染，不依赖 LLM。

## 验收内容

1. 通过真实身份 HTTP 创建 Outcome、双方签认承诺、激活、待交付 WorkItem 和处理中 MF；不预置交付物、交付验收或 Outcome 判断。
2. 指定 DRI 承接、提交 v1，指定验收人退回，DRI 上传并提交 v2，有权人通过。每个阶段通过 Narrative 读取，并用真实数据库中的两个不可变验收记录核对责任人。
3. 交付通过后 Outcome 仍未评估；观测为 72 时独立判断未达成；MF 经独立处理、验收与关闭后，Outcome 仍未达成；再用新证据和观测 80 独立判断达成。
4. 六个历史节点分别交叉验证 `valid_at` 与 `known_at`，不得把后发生的 v2 验收、Outcome 达成或 MF 关闭投影到过去。草案和未生效的后续候选版本不替换已生效内容。
5. 逐项核对 `governed_facts.selected` 的精确 revision、payload 白名单投影、payload hash 和递归来源引用；独立重算 `governed_sha256` 与 `narrative_sha256`。
6. 使用应用数据库角色，在 `REPEATABLE READ READ ONLY` 事务中比较每次 Narrative POST 前后的整个 scope 表行数与内容哈希，包含 snapshot、receipt、审计历史及 outbox。身份凭据表按原独立 SQL oracle 规则排除，不将其描述成已做凭据审计。
7. 固定时间重复调用、真实 Clark NarrativeClient 三次调用，均不得产生业务写入；同时验证关闭入口、旧语义背景缺少明确授权、上游故障与最小匿名健康探针。
8. 跨租户、跨业务域、伪造租户/组织/scope 字段及撤权请求明确失败；其他租户自己的合法读取和未撤权 CEO 的读取作为正向对照。

## Clark 客户端真实性

`clark_client.cjs` 使用 Clark 已安装的 TypeScript，在内存中编译并执行原 `src/lib/ontology/narrative.ts`；网络层是该模块自己的 `undici` HTTP 代码。它调用压缩正文入口一次、原文入口两次，验证字段解析与确定性模式。

唯一的加载收敛是：`ontology/errors.ts` 对 `graphknowledge` 总出口中错误类的引用直接解析到定义该真实类的 `graphknowledge/errors.ts`。这避免加载与 NarrativeClient 无关的 demo store，没有替换客户端、传输或错误类。报告记录实际加载源码及 SHA-256。

## 故障夹具的边界

上游故障用一个本地 HTTP 服务返回固定 502，不产生 embedding，也不产生模型叙述。仅在本轮隔离测试 scope 中追加一条版本化测试授权后，验证真实 adapter 确实调用 embedding 端点，并返回经过脱敏的 `503 NARRATIVE_UNAVAILABLE`。没有修改旧授权记录，也没有为任何真实企业 scope 开启旧语义背景读取。

该项只证明上游故障处理，不代表旧语义图数据完成迁移、不代表真实 embedding 或 chat 模型验收，也不代表模型压缩质量通过。

## 输出与验收口径

脱敏报告写入 `artifacts/runtime-acceptance/narrative-*/report.json`，真实 Clark 客户端的脱敏报告为同目录下的 `clark-client-report.json`。HTTP 证据与本轮私有连接/身份文件继续留在 Git 忽略目录中。命令行不输出 Bearer、访问码或数据库连接串。

报告中的 `narrative_adapter_accepted` 与 `clark_client_accepted` 仅表示本套局部验收；`runtime_accepted` 保持 false，因为这不是完整 Runtime 发布闸门。`real_model_accepted`、发布与生产部署状态均保持 false。整个 Clark UI、真实 SSO、远程部署、生产资料迁移和真实模型质量需要各自的验收证据。

最终当前代码已重新完成本地运行 `narrative-90de6049a8ea`：7 组全部通过，包含新增的 `NarrativeResponse` 和旧背景 generation provenance。此前运行 `narrative-3d8d017b52b7` 的 7 组通过记录仍保留为历史证据。对应源代码尚未提交或发布。

## 本地恢复的真实旧数据对照

恢复与迁移负责人完成两源历史保全合并后，可使用本地演练专用工具配置一个 Narrative 读取身份：

```sh
.venv/bin/python acceptance/narrative/provision_local_reader.py \
  .runtime-acceptance/convergence-<run-id>/state.json
.venv/bin/python -m pytest -q acceptance/narrative/test_provision_local_reader.py
```

该工具只接受本仓库 `convergence-*/state.json`，state 与同目录报告须为当前用户所有的 `0600` 普通文件。报告必须 `status=passed`，明确启用两源保全合并且插入后、重放后、迁移后的三个 53 表联合保全检查全部通过；state 必须选择 `memory_plus_aw_history`，并且仅有一个 `primary_scope`。三个 DSN 必须是同一 loopback 上的 `tkos_convergence_*` 库。工具使用该私有 state 中的本地管理员连接，在事务内锁定并检查全部 `gov_*` 表为空，避免 RLS 把既有权限隐藏成空库。

它仅创建 scope、domain、agent principal、AGENT assignment、credential digest、读取 policy 各一行。assignment 严格 24 小时，policy 仅含 `read` 和 `read_legacy_context`；不会调用 `seed_scope`，不会生成治理对象或权威业务事实。Bearer 只保存回原私有 state，不打印到命令行。再次调用明确拒绝已有 reader；工具不会续期、扩权或覆盖凭据。若数据库提交后保存 state 失败，会拒绝后续重建身份，需要新的隔离恢复运行。

身份准备完成后，可执行：

```sh
.venv/bin/python acceptance/narrative/compare_legacy.py \
  .runtime-acceptance/convergence-<run-id>/state.json
```

脚本要求 state 位于本仓库 `.runtime-acceptance/convergence-*/`，权限不向 group/other 开放，且 `local_only=true`；DSN 必须指向 loopback、数据库名称必须以 `tkos_convergence_` 开头。必要字段为 `APP_DATABASE_URL`、`tenant_id`、`company_id`、`scope_id`、`domain_id` 和 `reader_token`；可提供 `reader_principal_id` 以核对实际身份。脚本不会使用 `seed_scope`、创建业务对象、授予角色、访问远程服务或重启共享数据库。

它从已恢复数据当前代的 confirmed 叶节点中选择至少 10 个带已有向量的对象；不足时准确记录实际数量。节点名称及用于区分同名对象的内部标识只作为私有 query 留在进程内。该节点已有 embedding 作为固定向量，一路交给真实旧 `query → ContextPack → render_narrative`，另一路由 loopback HTTP 回放给新 Narrative 接口。比较项包括完整 Pack SHA-256、每个来源引用及解析状态、主路径/旁支数量、根陈述与原始背景正文。新增治理事实不混入旧背景的等价判断。

`clark_legacy_client.cjs` 接着执行原 Clark NarrativeClient，每题分别请求正文与原文。私有 query 经 stdin 传入，命令行及报告不含企业问题、答案、向量、来源文档 ID 或密钥。数据库以只读事务做前后表内容哈希比较；只输出题号、数量、是否一致及哈希。

本地恢复运行 `convergence-74faddfde9b8` 已通过：32 个可用叶节点中抽取 10 个，10 题全部一致；真实 Clark 客户端完成 20 次解析，合计 30 次固定向量 HTTP 回放；所有选中来源可解析，数据库内容保持不变。脱敏报告为该运行产物目录中的 `legacy-narrative-comparison.json`。

最新两源历史保全合并候选 `convergence-d31068bce435` 已重新通过相同对照：32 个可用真实叶节点中 10 题全部一致，Clark 原客户端 20 次调用、固定向量 HTTP 30 次，33 个 scope 表的前后行数及内容哈希完全一致。实际 SQL 核对本工具只创建 6 行身份配置，assignment 有效期为 86400 秒，所有业务表仍为 0；重复 provision 明确返回 `READER_ALREADY_PRESENT`。工具的 36 个纯边界测试通过。该候选脱敏报告为 `artifacts/runtime-acceptance/convergence-d31068bce435/legacy-narrative-comparison.json`，此前 74f 运行证据继续保留。

**固定已有向量回放只证明迁移后的数据读取、旧检索核心、新接口与 Clark 解析保持一致。它不证明真实 embedding 模型召回质量、模型压缩质量、生产升级或远程切换已经验收。**

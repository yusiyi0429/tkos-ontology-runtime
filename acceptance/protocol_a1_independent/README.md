# Runtime A1 独立验收

本目录由 Codex 编写，Kimi 不修改。2026-09-10，root 已在冻结的 100 个源文件上完成 `independent-r3`：A1-01～14 全部通过，92 个必需子检查完整，0 failed / 0 not_complete。见 [最终验收记录](../../docs/acceptance/runtime-a1-20260910.md) 与 [同轮机器报告](../../artifacts/a1-acceptance/independent-r3/report.json)。A2/A3 尚未实现，未提交、推送、发布或部署。

当前实际证据：root 已运行 `preupgrade-r1`，旧 API 完成 20 个命令、9 个对象、10 个 revision、22 个 receipt，SQL 覆盖 17 表，保留 2 个原始证据版本。Codex 对公开捕获结果进行 16 项只读完备性复核，全部通过，见 `preupgrade-capture-readonly-review.json`。这证明旧基线真实存在；其后的正式迁移和 `independent-r3/history` 已进一步验证升级后的原记录、版本、哈希、证据与旧请求重放。实际三项状态为交付通过、Outcome 未评估、MF 仍 investigating。

不导入被测 Profile/协议校验函数来生成正确答案。现有 `acceptance/runtime` 仅复用 HTTP recorder、只读 SQL oracle 和仅故障注入的控制器。新 A2/A3 业务不实现、不种入成功记录。

## 35 个真实旧源序列化 golden

`legacy-request-golden.json` 的期望由导出的旧源码 `3cd9109d` 生成，覆盖全部 19 action_type、8 类旧 create payload、可空默认、空数组、时区、中文、空白、数值类型。固定内容不能随着新模型变化重新生成。

```sh
.venv/bin/python -B -m acceptance.protocol_a1_independent.golden check --report artifacts/a1-acceptance/legacy-serialization.json
```

该命令不访问 DB，只比较新模型的旧 canonical payload、UTF-8 字节与 request_hash。迁移前旧 HTTP 请求真实重放另由 history 验证。

## 由 root 操控隔离生命周期

前置：专用 PG/MinIO 健康、父环境已确认。此命令由 root 执行；不能与其他操作同一验收 DB/存储的完整 runner 并行。

```sh
python3 acceptance/runtime/infra.py run --migration -- .venv/bin/python -B -m acceptance.protocol_a1_independent.preupgrade --env-file /Users/yusiyi/ysy/tkos-ontology-runtime/.runtime-acceptance/env.json --old-source /tmp/tkos-a1-legacy-3cd9109d/src --output /Users/yusiyi/ysy/tkos-ontology-runtime/artifacts/a1-acceptance/preupgrade-r1 --private /Users/yusiyi/ysy/tkos-ontology-runtime/.runtime-acceptance/a1-independent-preupgrade-r1
```

它只新建随机 `tkos_a1_*` 一次性 DB、用旧迁移到 0017、旧 bootstrap 初始化合成身份/权限/起始事实、真实旧 HTTP 完成旧承诺/DRI v1→退回→v2→验收、保存旧所有请求/回执/版本/SQL/S3/Context Pack，再停止自己的 API。不会应用 A1、删除库、运行 Worker 或创建新 A2/A3 成功状态。失败保留现场；新的 run 必须用空私有目录。

`database.py upgrade` 只接受 `tkos_a1_*`，由 root 在所有旧 writer 静默后调用，使用指定源码迁移两遍。它不自动授予 A1 控制面权限；新表正常应用读取/业务绑定写权限应来自已审查部署配置。`history.py verify` 在 root 完成受控升级/必要初始化后回读原始记录，并用迁移前真实请求重放，检查原列 hash 与存储版本不变。

```sh
.venv/bin/python -B -m acceptance.protocol_a1_independent.database upgrade --env-file PRIVATE_ENV --source /Users/yusiyi/ysy/tkos-ontology-runtime/src --output ARTIFACTS_UPGRADE
.venv/bin/python -B -m acceptance.protocol_a1_independent.history verify --env-file PRIVATE_ENV --source /Users/yusiyi/ysy/tkos-ontology-runtime/src --history PRE_A1_HISTORY_JSON --fixture PRIVATE_FIXTURE --output ARTIFACTS_VERIFY --private PRIVATE_VERIFY
```

上面 `PRIVATE_*`/`ARTIFACTS_*` 是前阶段实际产物路径，不是已存在文件的声明。私有 env/fixture 包含随机验收凭据，保持 600/700，不输出或提交。库保留供 root 后续核对和明确清理；脚本没有 DROP/reset 命令。

## 结果与安全边界

- `matrix.py` 列出全部 14 个 ID 的必需子断言。`CaseBook` 只有记录全部子断言成功才将该 ID 设为 passed；缺项保留 incomplete/not_run。
- 根报告只可设 `contract_a1_accepted`；`runtime_accepted` 保持 false，A2/A3 为 not_implemented，不自动提交/推送/部署。
- 准备/普通读与失败业务用例必须保持完整 SQL 快照。Context Pack POST 只允许约定审计 snapshot 新增。
- Evidence 独立观察整个 scope 的 S3 对象版本和 delete markers；Worker 独立观察 receiver calls/effects。缺账本不是零副作用证明。
- 普通进程只注入 app DSN；owner/admin 不传给 API/Worker。子进程日志先写私有目录，结束后脱敏保存。
- 旧 API 直接从明确旧源导入，ready artifact 记录实际 application_source；不会注入新版 resolver。当前源码故障 wrapper 只能暂停/失败，不能批准。
- 所有检查完成前冻结并核对被测源码 manifest；Kimi 自测与 Codex 独立验收报告分别保存。

## 工具索引

- `control_adapter.py`：真实维护 CLI 子进程＋独立 SQL Profile/Binding/完整登记覆盖核对。必须先固定 CLI module/参数/退出码；不存在的接口报 NotReady。
- `profile_cases.py`：生成 19 份独立安装输入，错误引用/重复 ID 反例具有重算 hash 和独立 revision，防止被其它错误提前遮蔽。P2/P3 仅作为历史绑定不随新版本变化的实验输入，不证明新规则可执行。
- `db_adversary.py`：默认只出 NOT_RUN 计划。显式 root `--execute` 才对一次性 tkos_a1_* 库进行 owner 正例回滚、普通 app 负例回滚、临时 broad INSERT grant 及原 ACL 恢复；函数调用限确切允许的签名。不是向现有部署/用户库开放测试入口。
- `old_entrypoints.py`：真实旧 API 的 create 和 evidence 双观察面探针，SQL 拒绝后 S3 仍变化会失败。返回实际失败模式供精确冻结，不能仅凭任意 4xx 声称新协议全部验收。

## 已执行的完整 runner 入口

`control-contract.json` 固定实际 owner CLI 参数、完整 stdout/stderr JSON、退出码、读取解释状态与 Narrative 竞态拒绝码；源码冻结仍由 root 完成。普通 API/Worker 不接收 owner DSN。Profile 安装额外需要原始 `--contract-file`，不仅传摘要。Profile 重装只允许一条明确 noop 审计，不能重复创建安装/绑定/业务记录。

先依次完成：最终源码审查冻结 → `database upgrade` 两次迁移 → root 审查后的普通 app grants → root worker-r2 coordinator 释放并产出真实 old-worker cutover report（worker-r1 准备已主动中止，不能使用） → 新测试 scope → 最终离线 wheel/bundle → runner。不得与完整 runner 同时运行操作相同 scope/S3 的其他验收程序。

```sh
.venv/bin/python -B -m acceptance.protocol_a1_independent.current_fixture --env-file .runtime-acceptance/a1-independent-preupgrade-r1/env.json --source src --fixture .runtime-acceptance/a1-independent-current-r3/fixture.json --namespace runtime-acceptance-a1-current-r3 --report artifacts/a1-acceptance/current-r3/setup.json
```

以下为实际成功轮次的命令记录。再次执行必须使用新 fixture 和新的输出/私有目录，不能复用已写入状态的 scope 或覆盖旧证据：

```sh
.venv/bin/python -B -m acceptance.protocol_a1_independent.runner --execute --stages all --env-file .runtime-acceptance/a1-independent-preupgrade-r1/env.json --source src --old-source /tmp/tkos-a1-legacy-3cd9109d/src --history artifacts/a1-acceptance/preupgrade-r1/pre-upgrade/pre-a1-history.json --history-fixture .runtime-acceptance/a1-independent-preupgrade-r1/pre-upgrade/legacy-fixture.json --fixture .runtime-acceptance/a1-independent-current-r3/fixture.json --profile /Users/yusiyi/ysy/semantica/outputs/tkos-contract-a-20260910/profile-core.json --contract-file /Users/yusiyi/ysy/semantica/outputs/tkos-contract-a-20260910/契约包A.md --control-contract acceptance/protocol_a1_independent/control-contract.json --migration-report artifacts/a1-acceptance/upgrade-r1/migration-replay.json --worker-preparation artifacts/a1-acceptance/worker-r2/preparation.json --worker-cutover-report artifacts/a1-acceptance/worker-r2/worker-cutover-report.json --wheel artifacts/a1-acceptance/final-package/tkos_memory_service-0.2.0-py3-none-any.whl --output artifacts/a1-acceptance/independent-r3 --private .runtime-acceptance/a1-independent-run-r3
```

输出目录必须新建，不覆盖旧报告。`--bundle` 可替换为 `--wheel 实际工件路径`，前提是该 wheel 自带 P1。归档模式读取真实 wheel＋P1 sidecar，不能用本机源码/Profile 文件补缺。

14 项共有 92 个必需子断言。包括 21 类 Profile 输入（另有原装重放及同 scope/跨 scope 真事务竞争）、匹配旧类型的 A 元数据 fixture、真实旧迁移历史、创建/交付/调整/权限/读/队列/evidence 全入口、宽 GRANT＋伪造 GUC 普通 app 反例。CLI 并发插桩仅暂停真实 identity SELECT 返回，真实 PostgreSQL backend 与 lock 记录证明重叠。Narrative 插桩仅暂停真实 render 返回；不改输入、结果或治理判断。两种 hook 都不写被测生产源码。

接口边界：顶层对象、revision、Workbench、Context Pack selected/excluded、Narrative fact 必须明确 protocol 解释。`source_ref` 的额外嵌套 protocol 字段不是新强制接口；若提供就核验，同时始终以精确 object/revision、真实授权 GET 和 SQL binding 核验来源。保留派生来源重验的独立审查/反例，不能把缺字段当未授权，也不能跳过来源协议检查。

正式轮次已完成：保留旧历史库的 A1 迁移、最终版本全套 HTTP、root worker-r2 释放、最终 wheel 打包。预检的一次性库已经有真实 Profile/control 安装和部分 HTTP 结果，不能替代这些正式前置。缺真实工件/外部报告/接口仍报告 NotReady；实现报错或断言失败为 failed。runner 默认不传 `--execute` 只生成离线计划；不会访问数据库。

离线自检入口：

```sh
.venv/bin/python -B -m acceptance.protocol_a1_independent.harness_selfcheck
.venv/bin/python -B -m acceptance.protocol_a1_independent.cli_adapter_selfcheck
```

已实际执行最终自检：34＋12 项通过（后者23个模拟 CLI 返回）；完整 runner 离线计划结果为 0 passed / 0 failed / 14 not_complete。它们只证明验收器基础安全和接口解析，不能作为 Runtime A1 通过证据。

B09 仍属于 A1-12：`narrative_binding_case.py` 使用两个真实 bootstrap 的独立 synthetic scope，分别改绑所请求的 Outcome fact、以及仅位于 MetricObservation `source_refs` 的 Outcome。后者观察事实由真实 HTTP 创建，改绑对象不在 selected 顶层。真实 CLI 安装 P1；实际 Narrative render 返回后暂停，owner 带控制 flag 追加合法 A binding version 2 与一条 control audit，原版本不修改。can_read 与 auth_epoch 均不变，但旧 legacy 解释已不适用，因此固定要求 409 `NARRATIVE_SOURCE_CHANGED`，不返回旧叙述，拒绝后 SQL/S3/效果接收器均无变化。主 fixture 的唯一初始绑定与业务记录不受影响，不新增 A2/A3 业务接口。暂停与释放标记均采用原子文件发布。

root 已真实运行 `preflight-r1/diagnostic-narrative-r4`：两个 registry 竞态及上述 fact/source 竞态共四组通过，source_unchanged=true。`diagnostic-write-r2` 有 6 个完整 ID 通过、0 failed、8 not_complete；其中控制面跨域验证因未提供独立保留的 foreign scope 而明确 NotReady。旧 `diagnostic-http-r1` 的 A1-12 失败报告保留，不能被后来的局部成功覆盖为完整验收通过。随后 `independent-r3` 已在统一冻结 source manifest 下完成全部 14 项。

B10 的 `read_binding_cases.py` 已接入同一 A1-12 reads 组，并在 `independent-r3` 完整实跑通过。三个隔离 scope 分别覆盖旧 selected Outcome snapshot、真实交付/assessment 的 WorkItem、以及参与 assessment 的 Deliverable 变为可读 A：10 个派生读入口固定要求 409 `PROTOCOL_NOT_SUPPORTED`，同时逐条确认原始 revision、immutable receipt 仍可读且业务内容不变。交付通过、Outcome not_achieved、MF investigating 均由真实 HTTP 形成并分别记录。真实未 confirm 的 Outcome 另验证 excluded 原因和 protocol metadata。变更只追加 binding/control audit，不能改主 fixture 或旧历史。observation/evidence 单独改绑的 B10 分支本轮依据精确源码审查、原来源权限测试和新版 helper 离线覆盖，不冒称已完成这两种额外的真实改绑竞态。

最终读取验收边界：单对象 revision 分页必须每页有 protocol/Profile 元数据，且每行 object_id 属于该目标；混合对象列表仍逐行核验。A 协议 WorkItem 的 legacy responsibility 专用接口固定返回 409 / PROTOCOL_NOT_SUPPORTED，旧协议必须返回完整 200。`independent-r1` 和 `independent-r2` 的相关验收器误判及原失败报告保留。`read-projection-cases.json` 单模块有意保留 Narrative 未单独执行的标记；完整结果以同轮 Narrative/B10 子报告及总报告的所有必需检查为准，不能仅依据该模块布尔值宣称 A1 通过。

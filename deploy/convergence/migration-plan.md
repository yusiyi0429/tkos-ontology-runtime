# Memory、AW 与治理 Runtime 收敛计划

**最新本地完整保全演练 `convergence-d31068bce435` 已 PASS。** [固定 53 表 manifest](merge-manifest-v1.json) 验证“Memory 业务基底＋AW 独有运行历史”可在全新本地库无损合并，再运行 0015/0016/0017 和空重放。全部历史 union 经插入后、原插入计划重放后、迁移后三次全行 hash 核验；158 条 FK 全部 validated、孤儿为 0，public owner 正确，27 张 legacy public 表对 app 为 SELECT only，gov 行数在验收时为 0。详见 [最新报告](rehearsal-d31068bce435.json)。

仅追加 AW 独有 104 conversations、104 runs、904 run_events、16 evals_sync_state，原插入计划重放新增 0 行。真实数据库冲突/未知表/NOT VALID FK 负向注入均拒绝并回滚。新候选来自原始两源 dump，未接触生产、未改变旧候选或旧报告，线上切换尚未执行。

## 本地复跑入口

```sh
.venv/bin/python deploy/convergence/rehearse.py --execute-local-restore \
  --from-state .runtime-acceptance/convergence-74faddfde9b8/state.json \
  --preserve-aw-history
```

manifest 固定两源 dump SHA256、schema/PK、7 张 public WM 的 Memory 超集、4 张运行历史的 AW 超集、其余 42 表相等、每表共享/独有数量及 expected union hash。仅允许这四表按真实 FK 顺序，在一个新本地事务内追加缺失行，不更新现有行。原始 JSON 文本交回 PostgreSQL 以保留 numeric 精度；非主键 UNIQUE/FK 冲突直接失败，最终 union 门槛在提交前执行。幂等证明是原缺失行插入计划重放，不声称整个流程可对任意已修改库重入。

## 历史检查如何形成当前方案

已完成的演练 `convergence-74faddfde9b8`：两生产数据库各 53 张 legacy 表（含非 public schema）完成私有 custom-format 备份，备份前后指纹稳定；恢复到两个全新本地库，全部 count/hash 一致。Memory 候选应用 0015/0016/0017，第二次重放为空，所有 legacy 行保持不变，AW 的 Working Memory 子集完整保留。迁移后 158 条 FK 全部 validated，public 表 owner 正确，application role 非 superuser/BYPASSRLS 且拥有表数为 0。gov 表已建立但业务行数为 0，尚未导入治理权威或身份。详见 [rehearsal-74faddfde9b8.json](rehearsal-74faddfde9b8.json)。

独立审查补齐全表选源硬门槛后，使用上述原始 dump 在全新库执行 `convergence-e57e466e4099`，**选源门槛失败、尚未运行迁移**。AW 在 `conversations`、`runs`、`run_events`、`evals_sync_state` 分别独有 104、104、904、16 行，共享行无字段冲突。见 [全量门槛结果](rehearsal-e57e466e4099.json) 与 [逐主键差异](non-wm-diagnostics-e57e466e4099.json)。旧演练不能证明 AW 的全部历史已被保全。

## 1. 现场边界与源数据选择

观测时间：2026-09-07，服务器 `tokenhub-prod`。精确 image ID、全部检查结果和目标 SQL 文件 SHA256 见 [preflight-20260907.json](preflight-20260907.json)。

| 边界 | 实际部署 | 已应用迁移 | gov 表 |
| --- | --- | --- | --- |
| 旧 Memory | `tkos-memory-memory-service-1`，镜像 `tkos-memory-service:535fee7-amd64`，`127.0.0.1:8010`；数据库 `tkos-memory-postgres-1`，独立 memory-internal network | 0001–0007 | 0 |
| AW Memory | `aw-memory-api.service` active，工作目录 `/home/tokenhub-deploy/TKOS_Agent_WorkSpace`，监听 `127.0.0.1:8020`；另有 `aw-workspace-pg` 映射 `127.0.0.1:5435` | AW 数据库 0001–0007 | 0 |
| 独立 staging | `tkos-target-staging-*`；入口 `127.0.0.1:3400`；独立 PostgreSQL、MinIO、API、Worker | 0001–0007、0015 | 0 |

AW 服务与数据库的配对仍需在部署 manifest 中记录**不含密码的连接目标核验**；本次没有读取服务环境或环境文件以确定其实际连接串。旧 Memory 和 AW 数据库还存在同名 `dbg_97b840c7` schema，完整备份必须保留，不能把它默认为可删除的临时表。当前统计和业务对照针对 public schema。

两生产数据库各有 38 个 semantic entities、55 条 relations、9 份 documents、49 个 fragments、380 条 memory_audit、380 条 memory_proposals；当前图 1 个、退役图 1 个。上述表及相关来源引用、图版本的全表指纹一致。相同指纹证明相同数据，不证明业务权威归属。

Working Memory 逐主键对照如下；ID 和正文均未输出：

| 表 | 两边同 ID 且内容相同 | 仅旧 Memory | 仅 AW | 同 ID 内容冲突 |
| --- | ---: | ---: | ---: | ---: |
| `wm_issue_chains` | 7 | 7 | 0 | 0 |
| `wm_objects` | 20 | 14 | 0 | 0 |
| `wm_object_versions` | 21 | 21 | 0 | 0 |
| `wm_issue_signals` | 13 | 7 | 0 | 0 |
| `wm_version_source_refs` | 12 | 0 | 0 | 0 |
| `wm_agreement_parties` | 0 | 0 | 0 | 0 |
| `wm_agreement_confirmations` | 0 | 0 | 0 | 0 |

旧 Memory 与 AW 各检查全部 54 条 public 外键，全部已验证，孤儿行均为 0；staging 检查 55 条，亦为 0。AW 在这些 Working Memory 表上是旧 Memory 的子集，当前不存在需要择一覆盖的相同 ID 冲突。因此 Memory 可以作为 Working Memory 的候选基底；不能反向用 AW 的 20 个对象覆盖旧 Memory 的 34 个对象。这不等于全库择源通过，运行历史的 AW 独有行必须另行保全。

这些结论仅覆盖被检查表。两个数据库没有跨库原子快照，scope 统计不输出标识；切换前必须在写入边界内重新检查。直接择一仍被禁止。最新本地合并已将四表主键/引用、仅追加缺失记录、共享内容一致、原插入计划幂等和完整 union 保全固化为硬门槛；新源数据不匹配 manifest 时拒绝，不扩大自动允许名单或复制覆盖。

## 2. 完整备份与可回退清单

以下是正式收敛的备份要求。本地演练已经完成两源数据库备份/隔离恢复；全局角色、部署配置和外部对象字节备份仍须补齐，`plan.py` 不执行这些步骤：

1. 明确旧 Memory、AW 和 Clark 的实际写入入口，记录权威负责人和写入边界。不能仅因 API 看起来只读就假定 AW 后台没有写入。
2. 为旧 Memory 和 AW 分别制作 PostgreSQL custom-format 全库备份，包含 public 与其他 schema、序列、约束、触发器和迁移台账；将角色定义、grant/RLS 配置另做受控备份。记录 PostgreSQL 17 版本、备份起止时间、源标识、数据库内计数/指纹及 dump 文件 SHA256。
3. 盘点 documents/source refs 所指对象的真实存储位置，保存全部被引用原件、解析件、证据版本及校验值；数据库恢复不等于 S3 数据恢复。保存 bucket versioning/Object Lock 状态、ACL/保留策略元数据。外部存储清单和文件字节本次未核验。
4. 保存两套原镜像 digest、服务定义、Nginx 路由、连接目标和启动参数 manifest。密钥只进入权限受控的备份介质，不进入 Git、普通报告或 stdout。
5. 为预定新 Runtime 发布生成 code revision/dirty tree hash、API+Worker 镜像 digest、迁移 SQL hash、API contract hash 的同版本清单；本地测试产物不充当目标 Linux 镜像证据。

完整备份不得只导出 38 个实体或只复制 `.env`。在完成新库恢复校验前，保留两个源库、原服务和原对象存储。

## 3. 独立恢复与 schema 演练

使用独立的候选数据库/卷、Runtime API/Worker 网络和对象存储；不得复用已有 staging 数据库或占用原 8010/8020/3400 服务。具体名称、端口、磁盘容量和所有者写入经过核实的部署 manifest 后再执行。

1. 从旧 Memory 完整备份恢复到候选环境；AW 备份单独恢复为校验源，保留两源可追溯性。
2. 在未启动 Worker 的情况下检查所有表行数/指纹、迁移台账、序列、FK、source refs 和真实对象字节。先证明恢复没有丢数据，再讨论治理转换。
3. 仅在候选库应用当前代码的 `0015_runtime_tasks.sql`、`0016_governed_runtime.sql`、`0017_dri_delivery.sql`。不根据编号补不存在的 0008–0014。三份迁移只提供队列/治理结构，**不会把 `semantic_*` 或 `wm_*` 自动转成 gov 对象**。
4. 迁移 owner 与受限 API/Worker application role 分开。0016/0017 不创建 role、不 grant；另行按可变/追加表分类赋权，并验证 API role 既非 owner、superuser，也无 BYPASSRLS。
5. 单独建立版本化证据 bucket、快照保留策略、受限应用凭据以及固定且持久幂等的 effect receiver；在恢复/导入验收完成前保持候选 Worker 停止，防止恢复出的待处理任务产生真实外部效果。

这里的恢复演练必须保留失败证据和修复记录。不能直接在源库跑 migrations 再用“DDL 成功”替代恢复验收。

## 4. 历史数据进入 gov 的边界

建立逐类映射清单，而不是把旧表 rename 成新表：

| 旧数据 | 候选处理 | 必须保留/补齐 |
| --- | --- | --- |
| 已确认 semantic entities/relations | 用于核实公司事实与治理对象候选；先保留原始 legacy 投影 | 原 source ID、revision、图 generation、来源片段、内容 hash、有效期与确认来源 |
| `wm_issue_chains` / signals | 可作为 FeedbackThread/调查历史候选 | 议题边界、当前状态、历史链和 scope；不能自动补造 closure |
| `wm_object_versions` | 按实际 type 分成 Decision、承诺、证据或历史说明候选 | 版本链、旧作者/确认者映射、原判断状态；不能把模型草稿提升为有效 Decision |
| 旧签认/交付状态 | 仅在证据足以证明相同对象版本、签认人权限、时间与策略时迁入对应事实 | 独立的 DRI、acceptor、criteria、不可变证据、签认回执；缺项明确标记未建立 |
| legacy users | 作为映射线索 | 实际员工身份、任职范围、Decision Right 和生效期需核实；不能用现有测试 bootstrap 建生产身份 |

当前 `wm_agreement_parties` 和 `wm_agreement_confirmations` 都为空，不能从“有工作记忆”推断已有受权承诺签认。新 Runtime 的交付通过、Outcome 达成、MF 关闭仍分别判定。

导入必须生成可复查的 manifest：源定位与 hash、目标 object/revision、映射规则版本、接受/拒绝/待澄清原因、确认操作者及时间。只有经过确认的 gov 版本才进入权威事实投影；完整 legacy 数据继续保留用于审计。没有映射规则覆盖的历史类型保留在 legacy archive，不伪造对应 gov 行。

## 5. Narrative 对照与目标验收

先对齐 Clark 消费的 narrative 字段、错误码、身份和 scope；再用治理对象的受权读取结果生成叙述。LLM 不能决定读取权限、对象有效版本或事实是否确认。

真实数据对照只在授权的隔离恢复环境内进行，查询/回包内容保存在受控介质；普通报告只汇总覆盖数量和差异类别。对照覆盖源对象/版本 provenance、同 scope 与跨 scope、无权限不泄露、有效/草稿/过期版本区分、缺失证据、服务错误、同一问题的旧/新响应映射。不能把生成文本字面一致作为权威一致的唯一证据。

再在目标机器跑真实 DRI v1 退回/v2 通过、独立 Outcome/MF、回执幂等、丢响应重试、跨域拒绝、Worker 接收器幂等、服务重启/卷持久化、独立恢复。Clark 的模型/Search 凭据与 Runtime 个人身份和存储凭据分开验证。

## 6. 先只读试流，再切换写入

1. 发布清单、备份、恢复、数据映射、对照和目标验收均可审查后，再执行确定的生产切换步骤。
2. 先进行只读候选流量对照，原服务继续承接正式流量；记录错误率、延迟、授权拒绝和来源覆盖。只读阶段的回退是恢复原路由。
3. 切写前建立短时写入边界，再做最终增量捕获和两源逐主键对照。必须知道哪个源还可能写入，不能让两套系统同时对同一承诺/Decision 产生权威更新。
4. 固定切换时间、源末尾审计位置、目标已应用位置、旧/新 API 与 Worker 版本；依次调整 Clark BFF 的服务端配置和个人身份映射。不要通过共用 CEO token 代替真实个人授权。
5. 切换后按相同业务对象验证读取、提交、验收、来源和回执。保留原库和服务以只读方式支持核对，只有另一次明确清理授权才能删除。

**写入开始后的回滚不能只改 URL。** 必须先保存候选库新增的对象版本、回执、effect 状态和证据；决定是向前修复、把可兼容增量映射回旧系统，还是暂时冻结受影响写入。不得丢弃目标数据库、重复执行外部 effect，或把新 gov 权威状态静默回退到旧 WM 草稿。

## 尚未完成的实际门槛

目前已完成拓扑/迁移差异盘点、Working Memory 严格子集对照、全部 public FK 完整性检查，以及两源数据库备份、本地隔离恢复和当前 migrations 演练。尚未完成全局角色/完整部署备份、外部对象存储字节核对、legacy→gov 权威映射导入、员工身份与授权、目标服务器部署验收、生产只读试流及写入切换。这些门槛不能由本文件或一份 `plan_only` JSON 自动判定通过。

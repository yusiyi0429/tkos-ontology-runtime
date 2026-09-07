# 运行期服务收敛：只读预检与迁移计划

**最新完整保全演练 PASS：`convergence-d31068bce435`。** 从原始两源 dump 恢复到全新本地库，按固定 [53 表 manifest](merge-manifest-v1.json) 保留 Memory 的 Working Memory 超集、追加 AW 独有运行历史，其余 42 表完全一致。插入后、原插入计划重放后、0015/0016/0017 迁移后均验证全部历史 union；158 条 FK 已验证且无孤儿，未产生 gov 身份或权威事实，也未访问生产。

[最新验收报告](rehearsal-d31068bce435.json) · [迁移及回滚步骤](migration-plan.md)。追加量为 104 conversations、104 runs、904 run_events、16 evals_sync_state；原缺失行插入计划重放新增 0 行。真实数据库负向注入覆盖共享内容冲突、未知表、NOT VALID FK，全部拒绝并回滚。application role 对 27 张 legacy public 表只有 SELECT 权限，治理表仍按 Runtime 可变/追加分类授权。

这证明本地历史保全和迁移可行，不包含外部对象字节、生产全局角色恢复、员工身份验收或线上切换。`plan.py` 没有应用能力；`rehearse.py` 默认只出计划，必须给出显式执行选项。

复跑最新保全流程（只用私有原始 dump，不联系生产；每次创建全新本地库）：

```sh
.venv/bin/python deploy/convergence/rehearse.py --execute-local-restore \
  --from-state .runtime-acceptance/convergence-74faddfde9b8/state.json \
  --preserve-aw-history
```

manifest 绑定原始 dump SHA256、完整 schema/PK、53 表分类、共享/独有数量及两源/union 全行 SHA256。任何未知表、未审查的新行、共享主键冲突、错误子集方向或错误约束均拒绝；只在一个本地事务中按 conversations → runs → run_events 与独立 evals_sync_state 顺序插入缺失行，绝不覆盖。JSON 保留为 PostgreSQL 原始文本，避免 numeric 转换损失；仅主键冲突可用于幂等，其他 UNIQUE/FK 约束仍生效。

## 使用

在 Runtime 仓库内运行，Python 标准库即可：

```sh
# 默认：不联系服务器，输出未应用的计划。
python3 deploy/convergence/plan.py

# 使用已脱敏的现场记录生成计划；不联系服务器。
python3 deploy/convergence/plan.py --snapshot deploy/convergence/preflight-20260907.json

# 对固定 SSH alias tokenhub-prod 和固定容器执行只读预检。
# 输出路径必须尚不存在，避免覆盖已有证据。
python3 deploy/convergence/plan.py --collect --output /tmp/tkos-convergence-preflight-new.json

python3 -m unittest discover -s deploy/convergence -p 'test_*.py'
```

`--collect` 只执行以下操作：

- 对固定容器读取 image/image ID、端口、network 名和运行状态；不读取 Docker 环境变量。
- 读取 `aw-memory-api.service` 的四项非敏感状态与路径；不读取 Environment、环境文件或日志正文。
- 使用容器已有数据库管理身份建立会话，所有查询都包在 `REPEATABLE READ READ ONLY` 中，设置单条语句 8 秒、锁等待 1 秒超时，最后 `ROLLBACK`。连接身份只在容器内部使用，不输出用户名、数据库名或密码。
- 输出 public 表元数据、迁移文件名、行数、scope 数量、图版本状态、FK 定义及孤儿数量。对固定业务表，在数据库内计算稳定 SHA256 指纹，最多 10,000 行；超限明确标记不完整。
- 对七张 Working Memory 表按主键比较集合；只有主键/行的摘要短暂进入检查进程，结果只保留相同、独有、冲突数量，不保留摘要列表或业务正文。凭据和用户身份表不计算内容指纹。

`--collect` 会使用服务器现有管理连接读取全部 public 行，因此它只证明库存和关系完整性，**不证明应用角色 RLS 授权正确**。当前 legacy 外键均为 MATCH SIMPLE；孤儿检查按其 NULL 语义执行。两个数据库的事务各自一致，但不是跨库同时快照；切换前必须建立写入边界并重新比较。

`ready_for_production_replacement` 始终为 `false`。即便表指纹一致，工具也不会把静态检查提升为生产验收。发生 SSH/SQL 错误时仅输出固定错误码，不打印可能包含连接信息的 stderr。

## 可复跑的本地数据库迁移演练

前提是现有 Runtime `.venv` 与独立 acceptance PostgreSQL 已可用。脚本不会安装依赖、创建或重设 role，也不会启动/停止任何已有容器。

```sh
# 默认无网络、无数据库改动。
.venv/bin/python deploy/convergence/rehearse.py

# 已授权的独立演练：只读生产数据库，新建两个本地候选库。
.venv/bin/python deploy/convergence/rehearse.py --execute-local-restore

# 复用原始dump，执行已审查保全manifest，不访问生产。
.venv/bin/python deploy/convergence/rehearse.py --execute-local-restore \
  --from-state .runtime-acceptance/convergence-74faddfde9b8/state.json \
  --preserve-aw-history
```

执行路径：验证原 dump SHA256 → 全新 `tkos_convergence_*` 本地库 → restore与源指纹验证 → 新库对象转给既有 owner → **53 表 schema/PK 与审查清单一致；42 表相等、7 表 Memory 超集、4 表 AW 超集** → 事务内只插 AW 独有历史 → 53 表 union/FK 校验 → 原缺失行插入计划重放新增 0 行 → 本地负向注入与回滚 → migrations 与空重放 → 再验 union/FK/owner/app 最小权限/gov 空表。任一门槛失败不得报告 passed。完整 `preserve()` 只接受全新恢复库，不是任意断点续跑接口；幂等证据专指原缺失行插入计划重放。

原始 dump 与真实 scope/连接配置保存于 `.runtime-acceptance/convergence-<id>/`（0700/0600），不进入公开报告。`state.json` 包含 `MIGRATION_DATABASE_URL`、`APP_DATABASE_URL`、唯一 `primary_scope` 或待选择的 `scopes`，供后续仅在该新库建立受限读服务身份与真实数据回放。生产只读备份不调用叙述 API 或外部 LLM。

## 历史证据

- [只读预检](preflight-20260907.json)：2026-09-07 生产拓扑、迁移与局部表对照；局部 WM 子集结论不能替代全库保全。
- [74f 初次演练](rehearsal-74faddfde9b8.json)：两源各 53 表独立恢复、局部 WM 子集与迁移验证，保留原始 dump 作为后续输入。
- [e57 严格选源失败](rehearsal-e57e466e4099.json)：新增非 WM 全表门槛后，正确阻止直接择一；[逐主键差异](non-wm-diagnostics-e57e466e4099.json) 发现 AW 独有运行历史。
- [d310 完整保全通过](rehearsal-d31068bce435.json)：显式双源 manifest、事务合并、union/FK、幂等与负向验证通过。旧库和旧报告保持不变。

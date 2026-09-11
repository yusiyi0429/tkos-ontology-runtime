# A3 执行责任交接独立验收

状态：A3 完整本地矩阵已通过；旧协议／A2 兼容回归及总体验收结论见 [A3 验收报告](../../docs/runtime-a3-acceptance-report.md)。未发布、未部署。

规范来自契约包 A §1、§5 和独立验收契约 A3-01–14。`frozen-oracle.json` 在实现前固定 14 类、55 项必验检查和来源 hash；生产模块不得作为 oracle。Kimi Code 完成初始模型、事件评估和部分模块；额度耗尽后，经用户明确授权由 Codex 接手实现、审查及真实 HTTP／SQL 验证。独立矩阵在实现前冻结，未降低预期；客户端仅修正既有回执包装和返回字段名称。

首版仅支持实验 Profile 下正常交接：DRI／IC／验收者三位不同自然人，固定持续有效验收任命；Plan 必需且只表达 How。旧 v0.2 与 A2 的当前能力和历史必须保持。

全部业务状态由授权 HTTP 创建；SQL 仅受控初始化 synthetic 主体／策略、迁移、读取判定与明确的负向授权夹具。新库固定为 `tkos_a1_a2_a3_*`，复用现有 PostgreSQL／MinIO 端口，不改原演示数据库、容器或 Clark。来源凭据及测试密钥仅存本机忽略的私有目录。

不包含替岗、临时代理、验收人变更、暂停／恢复、Profile 迁移、正式调整、外部业务派发、Clark 页面或部署。阶段通过与旧交付回归、A2 回归分别报告，不把缺失或未执行检查计作通过。

## 运行入口

先由主审确认 `0020_execution_handover.sql`，在新建的独立库执行迁移并只为两个新 state 表授予 UPDATE。不得将本矩阵指向演示或生产库。入口自身不会迁移、启动容器或伪造成功业务记录。

```sh
.venv/bin/python -B -m acceptance.execution_a3_independent.run \
  --env-file .runtime-acceptance/a3-database-20260911/env.json \
  --source src \
  --private .runtime-acceptance/a3-run-UNIQUE \
  --output artifacts/runtime-acceptance/a3-20260911/run-UNIQUE
```

调试可追加 `--scenario chain` 等单场景选项；部分通过不构成 A3 通过。每次使用新的输出目录，保留失败证据。完整矩阵包含双 API 并发、真实 PostgreSQL 锁等待、最终写入栅栏处自然到期、首条业务写后失败、原始 S3 字节与版本核对以及进程重启。`read_authority_rechecks` 等额外质量门禁也必须通过。

`history.py capture/verify` 保存并核对升级前真实 A2 激活；旧 v0.2 交付另用既有 `composition_a2_independent.legacy` 捕获与回归。`database.py` 只负责显式创建独立 acceptance 数据库，不由测试入口隐式调用。

P01/P02 均为合成机制验收：P01 两个客户具有完整双事件，第三个不完整；P02 使用新目标、新周期与三个新客户。一个同 scope 的历史 MF 通过真实 legacy HTTP 创建并观察其状态保留，并不声称 A3 已实现 MF 的正式反馈关联或关闭流程。

## A2 兼容回归

`python -m acceptance.execution_a3_independent.compatibility` 使用同样的 env/source/private/output 参数，运行原 A2 全部业务矩阵。仅扩充当前迁移的 schema inventory 与两个 A3 state 表的 UPDATE 白名单；A2 的用例、结果、并发与权限断言保持原样，仍须全部通过。

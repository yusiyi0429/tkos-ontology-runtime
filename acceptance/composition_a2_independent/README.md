# A2 独立验收

实施者：Kimi Code（额度中断期间由用户明确切换 MiniMax，随后切回 Kimi）。审查、独立用例、HTTP 客户端和 SQL 判定：Codex。

验收依据为 2026-09-10 冻结的《契约包 A》《独立验收契约》A2-01–18 和实验 Profile；`matrix.py` 在实现前固定必需断言。接口字段按 `docs/runtime-a2-api.md` 的工程映射接入，不能以实现输出的 expected/pass 标签替代判定。

所有测试使用本机独立 PostgreSQL 数据库、普通应用角色和合成主体。仅身份／授权／Profile／支持登记属于受控初始化；容量来源、正式域提交、签认、组合生效均须走真实授权 HTTP。现有演示数据库、容器和部署目录不由本套测试升级或重启。

生产校验模块不作为测试 oracle。范围内所有 governed 表、业务历史、成功回执及 runtime_tasks 由只读 SQL 快照核对；故障／屏障仅可暂停或失败，不能修改业务判定结果。没有运行全部必需断言不得宣称 A2 通过。

签认撤回、受控 Profile 迁移、暂停、A3 执行授权和 IC 交接均为尚未交付的能力；未知动作拒绝只验证围栏，不能计作这些能力已经实现。远程部署不在本轮范围。

`run.py` 仅连接显式传入的 `tkos_a1_a2_*` 隔离库，不执行迁移；运行前由协调者核对结构与应用角色最小授权。每次使用新的 private/output 目录，失败证据不得覆盖。完整运行：

```sh
.venv/bin/python -B -m acceptance.composition_a2_independent.run \
  --env-file .runtime-acceptance/a2-database-20260910/env.json \
  --source src \
  --private .runtime-acceptance/a2-matrix-r1 \
  --output artifacts/runtime-acceptance/a2-20260910/matrix-r1
```

可用 `--scenario versions` 先运行真实容量 ABA 闭环；该部分运行不会得到完整通过结论。默认首个失败后停止；`--keep-going` 在独立场景间继续取证。并发用例使用两个不同 PostgreSQL application_name 的真实 API 进程，故障注入仍受私有密钥约束，仅在测试启动器中启用。

最终本地验收结果及范围见 [status-20260910.md](status-20260910.md)，可随仓库查阅的机器可读摘要和源码 hash 清单见 [acceptance-summary-20260910.json](acceptance-summary-20260910.json)。原始证据位于本机忽略的 `artifacts/` 中，未随 Git 提交；新克隆需重新运行验收以生成原始报告。

在冻结的18类/66项检查之外，当前运行还要求受控来源发布与重放、精确角色及最新读取策略、CEO判断引用的共享/有效期、形成时审查上下文、来源在最终事务屏障自然到期等补充门禁通过。所有选中场景必须完成且无运行异常；不同源码或不同轮次的部分结果不能拼成最终通过。

`readiness.basis=at_form` 表示候选形成时的审查记录，不能代替当前的授权、代次、来源、签认和容量准入。旧交付历史回归与新旧序列化另行执行，矩阵报告中的 `runtime_accepted=false` 不自动推断全套Runtime验收；最终组合结论须同时附这些独立结果及同一源码清单。
